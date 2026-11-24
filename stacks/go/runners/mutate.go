// SPDX-License-Identifier: Apache-2.0
//
// Zero-dependency Go mutation runner (ADR 0015 §3, ADR 0102): parses Go AST
// under <scope>, filters by MUTATION_LINES, generates source-offset mutants,
// runs <test command> in an isolated copy with 10x clean timeout (floor 1s),
// prints {"total","killed","survived","score"} JSON on stdout and
// "SURVIVOR file:line mutator" lines on stderr.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

type lineRange struct {
	Start int
	End   int
}

type mutant struct {
	File        string
	Line        int
	Mutator     string
	StartOff    int
	EndOff      int
	Replacement string
}

type scoreOutput struct {
	Total    int      `json:"total"`
	Killed   int      `json:"killed"`
	Survived int      `json:"survived"`
	Score    *float64 `json:"score"`
}

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, `usage: mutate <scope> "<test command>"`)
		os.Exit(2)
	}
	scope := filepath.Clean(os.Args[1])
	testCmd := os.Args[2]

	cwd, err := os.Getwd()
	if err != nil {
		fmt.Fprintf(os.Stderr, "mutate: getwd: %v\n", err)
		os.Exit(1)
	}
	scopeAbs, err := filepath.Abs(scope)
	if err != nil {
		fmt.Fprintf(os.Stderr, "mutate: abs %s: %v\n", scope, err)
		os.Exit(1)
	}
	info, err := os.Stat(scopeAbs)
	if err != nil || !info.IsDir() {
		fmt.Fprintf(os.Stderr, "mutate: %s is not a directory\n", scope)
		os.Exit(1)
	}

	scoped, rangesByFile, err := parseMutationLines(os.Getenv("MUTATION_LINES"))
	if err != nil {
		fmt.Fprintf(os.Stderr, "mutate: invalid MUTATION_LINES: %v\n", err)
		os.Exit(1)
	}

	// Clean run in cwd first: proves the baseline passes and measures timeout.
	cleanStart := time.Now()
	cleanErr, cleanOut := runSuite(cwd, testCmd, 0)
	if len(cleanOut) > 0 {
		os.Stderr.Write(cleanOut)
	}
	if cleanErr != nil {
		fmt.Fprintf(os.Stderr, "mutation.sh: the unmutated test suite does not pass (%s); not a mutation score\n", testCmd)
		os.Exit(1)
	}
	elapsed := time.Since(cleanStart)
	if elapsed < time.Second {
		elapsed = time.Second
	}
	timeout := 10 * elapsed

	originals := make(map[string][]byte)
	var mutants []mutant

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
		rel, rerr := filepath.Rel(cwd, path)
		if rerr != nil {
			return rerr
		}
		rel = filepath.ToSlash(filepath.Clean(rel))
		if scoped {
			if _, ok := rangesByFile[rel]; !ok {
				return nil
			}
		}
		src, rerr := os.ReadFile(path)
		if rerr != nil {
			return rerr
		}
		fileMutants, perr := generateMutants(rel, src)
		if perr != nil {
			return fmt.Errorf("parse %s: %w", rel, perr)
		}
		if scoped {
			rs := rangesByFile[rel]
			var kept []mutant
			for _, m := range fileMutants {
				if inRanges(m.Line, rs) {
					kept = append(kept, m)
				}
			}
			fileMutants = kept
		}
		if len(fileMutants) > 0 {
			originals[rel] = src
			mutants = append(mutants, fileMutants...)
		}
		return nil
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "mutate: %v\n", err)
		os.Exit(1)
	}

	if len(mutants) == 0 {
		emitScore(0, 0, 0, nil)
		return
	}

	workDir, err := os.MkdirTemp("", "lights-out-go-mutate-*")
	if err != nil {
		fmt.Fprintf(os.Stderr, "mutate: mktemp: %v\n", err)
		os.Exit(1)
	}
	defer os.RemoveAll(workDir)

	if err := copyTree(cwd, workDir); err != nil {
		fmt.Fprintf(os.Stderr, "mutate: copy workspace: %v\n", err)
		os.Exit(1)
	}

	sort.Slice(mutants, func(i, j int) bool {
		if mutants[i].File != mutants[j].File {
			return mutants[i].File < mutants[j].File
		}
		if mutants[i].Line != mutants[j].Line {
			return mutants[i].Line < mutants[j].Line
		}
		if mutants[i].StartOff != mutants[j].StartOff {
			return mutants[i].StartOff < mutants[j].StartOff
		}
		return mutants[i].Mutator < mutants[j].Mutator
	})

	killed := 0
	survived := 0
	excluded := 0
	var survivors []string

	for _, m := range mutants {
		orig := originals[m.File]
		mutated := make([]byte, 0, len(orig)-(m.EndOff-m.StartOff)+len(m.Replacement))
		mutated = append(mutated, orig[:m.StartOff]...)
		mutated = append(mutated, m.Replacement...)
		mutated = append(mutated, orig[m.EndOff:]...)

		target := filepath.Join(workDir, filepath.FromSlash(m.File))
		if err := os.WriteFile(target, mutated, 0644); err != nil {
			fmt.Fprintf(os.Stderr, "mutate: write %s: %v\n", m.File, err)
			os.Exit(1)
		}
		merr, out := runSuite(workDir, testCmd, timeout)
		if werr := os.WriteFile(target, orig, 0644); werr != nil {
			fmt.Fprintf(os.Stderr, "mutate: restore %s: %v\n", m.File, werr)
			os.Exit(1)
		}

		if errors.Is(merr, context.DeadlineExceeded) {
			killed++
		} else if merr == nil {
			survived++
			survivors = append(survivors, fmt.Sprintf("SURVIVOR %s:%d %s", m.File, m.Line, m.Mutator))
		} else if isCompileFailure(out) {
			excluded++
		} else {
			killed++
		}
	}

	emitScore(killed, survived, excluded, survivors)
}

func emitScore(killed, survived, excluded int, survivors []string) {
	for _, s := range survivors {
		fmt.Fprintln(os.Stderr, s)
	}
	fmt.Fprintf(os.Stderr, "score: %d mutant(s) excluded from the population (CompileError)\n", excluded)
	total := killed + survived
	var score *float64
	if total > 0 {
		v := float64(killed) / float64(total)
		score = &v
	}
	b, _ := json.Marshal(scoreOutput{
		Total:    total,
		Killed:   killed,
		Survived: survived,
		Score:    score,
	})
	fmt.Println(string(b))
}

func parseMutationLines(raw string) (bool, map[string][]lineRange, error) {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" || trimmed == "all" {
		return false, nil, nil
	}
	result := make(map[string][]lineRange)
	for _, group := range strings.Fields(trimmed) {
		eq := strings.IndexByte(group, '=')
		if eq < 0 {
			continue
		}
		file := filepath.ToSlash(filepath.Clean(group[:eq]))
		for _, r := range strings.Split(group[eq+1:], ",") {
			if r == "" {
				continue
			}
			dash := strings.IndexByte(r, '-')
			if dash < 0 {
				n, err := strconv.Atoi(r)
				if err != nil {
					return false, nil, err
				}
				result[file] = append(result[file], lineRange{Start: n, End: n})
			} else {
				a, err1 := strconv.Atoi(r[:dash])
				b, err2 := strconv.Atoi(r[dash+1:])
				if err1 != nil || err2 != nil {
					return false, nil, fmt.Errorf("bad range %q", r)
				}
				result[file] = append(result[file], lineRange{Start: a, End: b})
			}
		}
	}
	return true, result, nil
}

func inRanges(line int, ranges []lineRange) bool {
	for _, r := range ranges {
		if line >= r.Start && line <= r.End {
			return true
		}
	}
	return false
}

func runSuite(dir, testCmd string, timeout time.Duration) (error, []byte) {
	ctx := context.Background()
	var cancel context.CancelFunc
	if timeout > 0 {
		ctx, cancel = context.WithTimeout(ctx, timeout)
		defer cancel()
	}
	cmd := exec.Command("sh", "-c", testCmd)
	cmd.Dir = dir
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	var buf bytes.Buffer
	cmd.Stdout = &buf
	cmd.Stderr = &buf
	if err := cmd.Start(); err != nil {
		return err, buf.Bytes()
	}
	done := make(chan error, 1)
	go func() {
		done <- cmd.Wait()
	}()
	select {
	case err := <-done:
		return err, buf.Bytes()
	case <-ctx.Done():
		if cmd.Process != nil {
			_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
		}
		<-done
		return ctx.Err(), buf.Bytes()
	}
}

func isCompileFailure(out []byte) bool {
	return bytes.Contains(out, []byte("[build failed]")) ||
		bytes.Contains(out, []byte("[setup failed]"))
}

func generateMutants(rel string, src []byte) ([]mutant, error) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, rel, src, 0)
	if err != nil {
		return nil, err
	}
	var out []mutant
	addTok := func(pos token.Pos, oldTok, newTok, mutator string) {
		p := fset.Position(pos)
		out = append(out, mutant{
			File:        rel,
			Line:        p.Line,
			Mutator:     mutator,
			StartOff:    p.Offset,
			EndOff:      p.Offset + len(oldTok),
			Replacement: newTok,
		})
	}

	var walk func(node ast.Node, inConst bool)
	walk = func(node ast.Node, inConst bool) {
		if node == nil {
			return
		}
		switch n := node.(type) {
		case *ast.GenDecl:
			childConst := inConst || n.Tok == token.CONST
			for _, spec := range n.Specs {
				walk(spec, childConst)
			}
			return
		case *ast.BinaryExpr:
			if !inConst {
				switch n.Op {
				case token.ADD:
					if !hasStringOrCharLit(n.X) && !hasStringOrCharLit(n.Y) {
						addTok(n.OpPos, "+", "-", "ArithmeticOperator")
					}
				case token.SUB:
					addTok(n.OpPos, "-", "+", "ArithmeticOperator")
				case token.MUL:
					addTok(n.OpPos, "*", "/", "ArithmeticOperator")
				case token.QUO:
					addTok(n.OpPos, "/", "*", "ArithmeticOperator")
				case token.REM:
					addTok(n.OpPos, "%", "*", "ArithmeticOperator")
				case token.EQL:
					addTok(n.OpPos, "==", "!=", "EqualityOperator")
				case token.NEQ:
					addTok(n.OpPos, "!=", "==", "EqualityOperator")
				case token.LSS:
					addTok(n.OpPos, "<", "<=", "RelationalOperator")
					addTok(n.OpPos, "<", ">", "RelationalOperator")
				case token.LEQ:
					addTok(n.OpPos, "<=", "<", "RelationalOperator")
					addTok(n.OpPos, "<=", ">", "RelationalOperator")
				case token.GTR:
					addTok(n.OpPos, ">", ">=", "RelationalOperator")
					addTok(n.OpPos, ">", "<", "RelationalOperator")
				case token.GEQ:
					addTok(n.OpPos, ">=", ">", "RelationalOperator")
					addTok(n.OpPos, ">=", "<", "RelationalOperator")
				case token.LAND:
					addTok(n.OpPos, "&&", "||", "LogicalOperator")
				case token.LOR:
					addTok(n.OpPos, "||", "&&", "LogicalOperator")
				}
			}
		case *ast.UnaryExpr:
			if !inConst {
				if n.Op == token.NOT {
					addTok(n.OpPos, "!", "", "UnaryOperator")
				} else if n.Op == token.SUB {
					addTok(n.OpPos, "-", "+", "UnaryOperator")
				}
			}
		case *ast.IncDecStmt:
			if n.Tok == token.INC {
				addTok(n.TokPos, "++", "--", "IncDec")
			} else if n.Tok == token.DEC {
				addTok(n.TokPos, "--", "++", "IncDec")
			}
		case *ast.AssignStmt:
			switch n.Tok {
			case token.ADD_ASSIGN:
				addTok(n.TokPos, "+=", "-=", "AssignOperator")
			case token.SUB_ASSIGN:
				addTok(n.TokPos, "-=", "+=", "AssignOperator")
			case token.MUL_ASSIGN:
				addTok(n.TokPos, "*=", "/=", "AssignOperator")
			case token.QUO_ASSIGN:
				addTok(n.TokPos, "/=", "*=", "AssignOperator")
			}
		case *ast.Ident:
			if !inConst {
				if n.Name == "true" {
					addTok(n.Pos(), "true", "false", "BooleanLiteral")
				} else if n.Name == "false" {
					addTok(n.Pos(), "false", "true", "BooleanLiteral")
				}
			}
			return
		case *ast.SelectorExpr:
			walk(n.X, inConst)
			return
		case *ast.KeyValueExpr:
			walk(n.Value, inConst)
			return
		}
		ast.Inspect(node, func(child ast.Node) bool {
			if child == nil || child == node {
				return true
			}
			walk(child, inConst)
			return false
		})
	}
	walk(file, false)
	return out, nil
}

func hasStringOrCharLit(expr ast.Expr) bool {
	lit, ok := expr.(*ast.BasicLit)
	return ok && (lit.Kind == token.STRING || lit.Kind == token.CHAR)
}

func copyTree(srcRoot, dstRoot string) error {
	return filepath.WalkDir(srcRoot, func(path string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		rel, err := filepath.Rel(srcRoot, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		name := d.Name()
		if d.IsDir() && (name == ".git" || name == ".crew") {
			return filepath.SkipDir
		}
		dst := filepath.Join(dstRoot, rel)
		if d.Type()&os.ModeSymlink != 0 {
			target, err := os.Readlink(path)
			if err != nil {
				return err
			}
			return os.Symlink(target, dst)
		}
		if d.IsDir() {
			info, err := d.Info()
			if err != nil {
				return err
			}
			return os.MkdirAll(dst, info.Mode().Perm())
		}
		return copyFile(path, dst, d)
	})
}

func copyFile(src, dst string, d fs.DirEntry) error {
	info, err := d.Info()
	if err != nil {
		return err
	}
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, info.Mode().Perm())
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, in)
	return err
}
