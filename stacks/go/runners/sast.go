// SPDX-License-Identifier: Apache-2.0
//
// Zero-dependency Go SAST runner (ADR 0072 §1, ADR 0102): combines go/ast
// security checks and `go vet -json` diagnostics over a directory scope,
// emitting {"findings": N, "items": [{"rule", "file", "line"}]} on stdout.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type item struct {
	Rule string `json:"rule"`
	File string `json:"file"`
	Line int    `json:"line"`
}

type finding struct {
	Item item
	Msg  string
}

type report struct {
	Findings int    `json:"findings"`
	Items    []item `json:"items"`
}

var (
	secretNameRe = regexp.MustCompile(`(?i)(password|passwd|secret|api_?key|token|private_?key)`)
	sqlVerbRe    = regexp.MustCompile(`(?i)\b(select|insert|update|delete)\b`)
	nosecRe      = regexp.MustCompile(`(?i)\bnosec\b`)
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: sast <scope>")
		os.Exit(2)
	}
	cwd, err := os.Getwd()
	if err != nil {
		fmt.Fprintf(os.Stderr, "sast: getwd: %v\n", err)
		os.Exit(1)
	}
	scopeArg := os.Args[1]
	scopeAbs, err := filepath.Abs(scopeArg)
	if err != nil {
		fmt.Fprintf(os.Stderr, "sast: abs %s: %v\n", scopeArg, err)
		os.Exit(1)
	}
	info, err := os.Stat(scopeAbs)
	if err != nil || !info.IsDir() {
		fmt.Fprintf(os.Stderr, "sast: %s is not a directory\n", scopeArg)
		os.Exit(1)
	}

	var all []finding
	var goFiles []string
	nosecByFile := make(map[string]map[int]bool)

	err = filepath.WalkDir(scopeAbs, func(path string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		name := d.Name()
		if d.IsDir() {
			if path != scopeAbs && (strings.HasPrefix(name, ".") || name == "vendor" || name == "testdata") {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			return nil
		}
		goFiles = append(goFiles, path)
		return nil
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "sast: walk %s: %v\n", scopeArg, err)
		os.Exit(1)
	}

	fset := token.NewFileSet()
	for _, absPath := range goFiles {
		rel, err := relPath(cwd, absPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "sast: rel %s: %v\n", absPath, err)
			os.Exit(1)
		}
		src, err := os.ReadFile(absPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "sast: read %s: %v\n", rel, err)
			os.Exit(1)
		}
		file, err := parser.ParseFile(fset, absPath, src, parser.ParseComments)
		if err != nil {
			fmt.Fprintf(os.Stderr, "sast: parse %s: %v\n", rel, err)
			os.Exit(1)
		}
		nosec := collectNosec(fset, file)
		nosecByFile[rel] = nosec
		all = append(all, inspectAST(fset, file, rel, nosec)...)
	}

	if len(goFiles) > 0 {
		vetFindings, err := runGoVet(cwd, scopeAbs, scopeArg, nosecByFile)
		if err != nil {
			fmt.Fprintf(os.Stderr, "sast: go vet failed: %v\n", err)
			os.Exit(1)
		}
		all = append(all, vetFindings...)
	}

	sort.Slice(all, func(i, j int) bool {
		if all[i].Item.File != all[j].Item.File {
			return all[i].Item.File < all[j].Item.File
		}
		if all[i].Item.Line != all[j].Item.Line {
			return all[i].Item.Line < all[j].Item.Line
		}
		return all[i].Item.Rule < all[j].Item.Rule
	})

	items := make([]item, 0, len(all))
	for _, f := range all {
		fmt.Fprintf(os.Stderr, "%s:%d %s %s\n", f.Item.File, f.Item.Line, f.Item.Rule, f.Msg)
		items = append(items, f.Item)
	}
	out, err := json.Marshal(report{Findings: len(items), Items: items})
	if err != nil {
		fmt.Fprintf(os.Stderr, "sast: marshal: %v\n", err)
		os.Exit(1)
	}
	fmt.Println(string(out))
}

func relPath(cwd, path string) (string, error) {
	rel, err := filepath.Rel(cwd, path)
	if err != nil {
		return "", err
	}
	return filepath.ToSlash(filepath.Clean(rel)), nil
}

func collectNosec(fset *token.FileSet, file *ast.File) map[int]bool {
	lines := make(map[int]bool)
	for _, cg := range file.Comments {
		for _, c := range cg.List {
			if nosecRe.MatchString(c.Text) {
				lines[fset.Position(c.Pos()).Line] = true
			}
		}
	}
	return lines
}

func inspectAST(fset *token.FileSet, file *ast.File, rel string, nosec map[int]bool) []finding {
	var out []finding
	add := func(pos token.Pos, rule, msg string) {
		line := fset.Position(pos).Line
		if nosec[line] {
			return
		}
		out = append(out, finding{
			Item: item{Rule: rule, File: rel, Line: line},
			Msg:  msg,
		})
	}

	ast.Inspect(file, func(n ast.Node) bool {
		switch node := n.(type) {
		case *ast.ImportSpec:
			p, err := strconv.Unquote(node.Path.Value)
			if err != nil {
				return true
			}
			switch p {
			case "unsafe":
				add(node.Pos(), "G103", "use of unsafe package")
			case "crypto/md5", "crypto/sha1", "crypto/des", "crypto/rc4":
				add(node.Pos(), "G401", "use of weak cryptographic primitive "+p)
			case "math/rand", "math/rand/v2":
				add(node.Pos(), "G404", "use of weak random number generator "+p)
			case "net/http/cgi":
				add(node.Pos(), "G504", "use of net/http/cgi (HTTPoxy risk)")
			}
		case *ast.KeyValueExpr:
			if id, ok := node.Key.(*ast.Ident); ok && id.Name == "InsecureSkipVerify" {
				if val, ok := node.Value.(*ast.Ident); ok && val.Name == "true" {
					add(node.Pos(), "G402", "TLS InsecureSkipVerify set to true")
				}
			}
		case *ast.ValueSpec:
			for i, name := range node.Names {
				if i < len(node.Values) && name.Name != "_" && secretNameRe.MatchString(name.Name) {
					if isNonEmptyStringLit(node.Values[i]) {
						add(name.Pos(), "G101", "potential hardcoded credential in "+name.Name)
					}
				}
			}
		case *ast.AssignStmt:
			if node.Tok == token.DEFINE || node.Tok == token.ASSIGN {
				for i, lhs := range node.Lhs {
					if i >= len(node.Rhs) {
						continue
					}
					if id, ok := lhs.(*ast.Ident); ok && id.Name != "_" && secretNameRe.MatchString(id.Name) {
						if isNonEmptyStringLit(node.Rhs[i]) {
							add(id.Pos(), "G101", "potential hardcoded credential in "+id.Name)
						}
					}
				}
			}
		case *ast.CallExpr:
			checkCallExpr(node, add)
		}
		return true
	})
	return out
}

func checkCallExpr(call *ast.CallExpr, add func(token.Pos, string, string)) {
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok {
		return
	}
	pkgIdent, ok := sel.X.(*ast.Ident)
	if ok {
		switch pkgIdent.Name + "." + sel.Sel.Name {
		case "exec.Command":
			if len(call.Args) >= 1 && (!isStringLit(call.Args[0]) || isShellCmdWithDynamicArg(call.Args)) {
				add(call.Pos(), "G204", "subprocess launched with variable or dynamic shell command")
			}
		case "exec.CommandContext":
			if len(call.Args) >= 2 && (!isStringLit(call.Args[1]) || isShellCmdWithDynamicArg(call.Args[1:])) {
				add(call.Pos(), "G204", "subprocess launched with variable or dynamic shell command")
			}
		case "http.Get", "http.Post":
			if len(call.Args) >= 1 && !isStringLit(call.Args[0]) {
				add(call.Pos(), "G107", "HTTP request with variable URL")
			}
		}
	}

	switch sel.Sel.Name {
	case "Query", "QueryRow", "Exec", "QueryContext", "QueryRowContext", "ExecContext":
		for _, arg := range call.Args {
			if containsDynamicSQL(arg) {
				add(call.Pos(), "G201", "SQL query constructed via string formatting or concatenation")
				break
			}
		}
	}
}

func isStringLit(expr ast.Expr) bool {
	lit, ok := expr.(*ast.BasicLit)
	return ok && lit.Kind == token.STRING
}

func isNonEmptyStringLit(expr ast.Expr) bool {
	lit, ok := expr.(*ast.BasicLit)
	if !ok || lit.Kind != token.STRING {
		return false
	}
	s, err := strconv.Unquote(lit.Value)
	return err == nil && len(s) > 0
}

func isShellCmdWithDynamicArg(args []ast.Expr) bool {
	if len(args) < 3 {
		return false
	}
	cmdLit, ok0 := stringVal(args[0])
	flagLit, ok1 := stringVal(args[1])
	if !ok0 || !ok1 {
		return false
	}
	base := filepath.Base(cmdLit)
	if (base == "sh" || base == "bash") && flagLit == "-c" && !isStringLit(args[2]) {
		return true
	}
	return false
}

func stringVal(expr ast.Expr) (string, bool) {
	lit, ok := expr.(*ast.BasicLit)
	if !ok || lit.Kind != token.STRING {
		return "", false
	}
	s, err := strconv.Unquote(lit.Value)
	return s, err == nil
}

func containsDynamicSQL(expr ast.Expr) bool {
	switch e := expr.(type) {
	case *ast.BinaryExpr:
		if e.Op == token.ADD {
			return exprHasSQLString(e)
		}
	case *ast.CallExpr:
		if sel, ok := e.Fun.(*ast.SelectorExpr); ok {
			if pkg, ok := sel.X.(*ast.Ident); ok && pkg.Name == "fmt" && strings.HasPrefix(sel.Sel.Name, "Sprint") {
				return exprHasSQLString(e)
			}
		}
	}
	return false
}

func exprHasSQLString(node ast.Node) bool {
	found := false
	ast.Inspect(node, func(n ast.Node) bool {
		if found {
			return false
		}
		if lit, ok := n.(*ast.BasicLit); ok && lit.Kind == token.STRING {
			if s, err := strconv.Unquote(lit.Value); err == nil && sqlVerbRe.MatchString(s) {
				found = true
				return false
			}
		}
		return true
	})
	return found
}

func runGoVet(cwd, scopeAbs, scopeArg string, nosecByFile map[string]map[int]bool) ([]finding, error) {
	target := "./..."
	cleanScope := filepath.ToSlash(filepath.Clean(scopeArg))
	if cleanScope != "." {
		target = "./" + strings.TrimPrefix(cleanScope, "./") + "/..."
	}
	cmd := exec.Command("go", "vet", "-json", target)
	var buf bytes.Buffer
	cmd.Stdout = &buf
	cmd.Stderr = &buf
	err := cmd.Run()

	raw := buf.Bytes()
	// `go vet -json` prepends `# <pkg>` lines on stderr before each JSON object.
	var cleaned bytes.Buffer
	for _, line := range bytes.Split(raw, []byte("\n")) {
		trimmed := bytes.TrimSpace(line)
		if len(trimmed) == 0 || trimmed[0] == '#' {
			continue
		}
		cleaned.Write(line)
		cleaned.WriteByte('\n')
	}
	var findings []finding
	dec := json.NewDecoder(bytes.NewReader(cleaned.Bytes()))
	parsedAny := false
	for {
		var pkgMap map[string]map[string][]struct {
			Posn    string `json:"posn"`
			Message string `json:"message"`
		}
		if derr := dec.Decode(&pkgMap); derr != nil {
			if derr == io.EOF {
				break
			}
			if err != nil && !parsedAny {
				return nil, fmt.Errorf("%v: %s", err, strings.TrimSpace(string(raw)))
			}
			break
		}
		parsedAny = true
		for _, analyzers := range pkgMap {
			for analyzer, diags := range analyzers {
				for _, d := range diags {
					file, line, ok := parsePosn(d.Posn)
					if !ok {
						continue
					}
					abs, aerr := filepath.Abs(file)
					if aerr != nil {
						continue
					}
					if !withinScope(scopeAbs, abs) {
						continue
					}
					rel, rerr := relPath(cwd, abs)
					if rerr != nil || strings.HasSuffix(rel, "_test.go") {
						continue
					}
					if nosecByFile[rel][line] {
						continue
					}
					findings = append(findings, finding{
						Item: item{Rule: "vet/" + analyzer, File: rel, Line: line},
						Msg:  d.Message,
					})
				}
			}
		}
	}
	if err != nil && !parsedAny {
		return nil, fmt.Errorf("%v: %s", err, strings.TrimSpace(string(raw)))
	}
	return findings, nil
}

func parsePosn(posn string) (string, int, bool) {
	// Format: /path/to/file.go:line:col or /path/to/file.go:line
	parts := strings.Split(posn, ":")
	if len(parts) < 2 {
		return "", 0, false
	}
	for i := len(parts) - 1; i >= 1; i-- {
		if ln, err := strconv.Atoi(parts[i]); err == nil && i >= 1 {
			if i >= 2 {
				if prev, err2 := strconv.Atoi(parts[i-1]); err2 == nil {
					return strings.Join(parts[:i-1], ":"), prev, true
				}
			}
			return strings.Join(parts[:i], ":"), ln, true
		}
	}
	return "", 0, false
}

func withinScope(scopeAbs, targetAbs string) bool {
	rel, err := filepath.Rel(scopeAbs, targetAbs)
	if err != nil {
		return false
	}
	return rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}
