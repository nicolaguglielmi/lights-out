#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Zero-dependency C#/.NET SAST runner (ADR 0072 §1, ADR 0129): stdlib only."""

from __future__ import annotations

import json
import os
import re
import sys

SKIP_DIRS = {"bin", "obj", "tests", "test", "Tests", "Test"}
TEST_SUFFIXES = ("Test.cs", "Tests.cs")

PROC_START_RE = re.compile(r"\bProcess\.Start\s*\(")
PROC_SAFE_LITERAL_RE = re.compile(r'\bProcess\.Start\s*\(\s*"[^"{$]*"\s*\)')
SHELL_EXEC_RE = re.compile(r"\bUseShellExecute\s*=\s*true\b")
SQL_CALL_RE = re.compile(
    r"\b(?:new\s+SqlCommand|ExecuteSqlRaw|ExecuteSqlRawAsync|FromSqlRaw)\s*\("
)
SQL_CONCAT_RE = re.compile(
    r'\$"[^"]*\b(?:SELECT|INSERT|UPDATE|DELETE)\b[^"]*\{'
    r'|"[^"]*\b(?:SELECT|INSERT|UPDATE|DELETE)\b[^"]*"\s*\+',
    re.IGNORECASE,
)
SECRET_ASSIGN_RE = re.compile(
    r"\b(?:string|String|var|const\s+string)\s+[A-Za-z0-9_]*"
    r"(?:password|passwd|secret|api_?key|token|private_?key)[A-Za-z0-9_]*"
    r'\s*=\s*"[^"\n]+"',
    re.IGNORECASE,
)
WEAK_CRYPTO_RE = re.compile(
    r"\b(?:MD5|SHA1|DES|TripleDES|RC2)\.Create\s*\("
    r"|\bServerCertificateCustomValidationCallback\b.*=>\s*true\b"
    r"|\bDangerousAcceptAnyServerCertificateValidator\b"
)
UNSAFE_DESER_RE = re.compile(
    r"\bnew\s+(?:BinaryFormatter|LosFormatter|SoapFormatter|NetDataContractSerializer)\s*\("
    r"|\bTypeNameHandling\.(?:All|Auto|Objects|Arrays)\b"
)
NOSEC_RE = re.compile(r"\b(?:nosec|NOSONAR)\b", re.IGNORECASE)


def _is_test_dir(dirname: str) -> bool:
    return dirname in SKIP_DIRS or dirname.endswith(("Tests", ".Tests", "Test", ".Test"))


def _lex_csharp(text: str) -> tuple[list[str], list[str], set[int]]:
    """Return (code_only_lines, no_comment_lines, nosec_lines) preserving line numbers."""
    n = len(text)
    code_only: list[str] = []
    no_comments: list[str] = []
    nosec_lines: set[int] = set()
    i = 0
    line = 1

    def emit(c_code: str, c_nc: str) -> None:
        code_only.append(c_code)
        no_comments.append(c_nc)

    while i < n:
        ch = text[i]
        if ch == "\n":
            emit("\n", "\n")
            line += 1
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            start = i
            while i < n and text[i] != "\n":
                emit(" ", " ")
                i += 1
            if NOSEC_RE.search(text[start:i]):
                nosec_lines.add(line)
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            start = i
            start_line = line
            emit(" ", " ")
            emit(" ", " ")
            i += 2
            while i < n:
                if text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                    emit(" ", " ")
                    emit(" ", " ")
                    i += 2
                    break
                if text[i] == "\n":
                    emit("\n", "\n")
                    line += 1
                else:
                    emit(" ", " ")
                i += 1
            if NOSEC_RE.search(text[start:i]):
                nosec_lines.add(start_line)
                nosec_lines.add(line)
            continue
        # Verbatim string @"..." or $@"..."
        if ch == "@" and i + 1 < n and text[i + 1] == '"':
            emit("@", "@")
            emit('"', '"')
            i += 2
            while i < n:
                if text[i] == '"' and i + 1 < n and text[i + 1] == '"':
                    emit(" ", '"')
                    emit(" ", '"')
                    i += 2
                    continue
                if text[i] == '"':
                    emit('"', '"')
                    i += 1
                    break
                if text[i] == "\n":
                    emit("\n", "\n")
                    line += 1
                else:
                    emit(" ", text[i])
                i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            emit(quote, quote)
            i += 1
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    if nxt == "\n":
                        emit(" ", " ")
                        emit("\n", "\n")
                        line += 1
                    else:
                        emit(" ", "\\")
                        emit(" ", nxt)
                    i += 2
                    continue
                if c == quote:
                    emit(quote, quote)
                    i += 1
                    break
                if c == "\n":
                    emit("\n", "\n")
                    line += 1
                else:
                    emit(" ", c)
                i += 1
            continue
        emit(ch, ch)
        i += 1

    return (
        "".join(code_only).splitlines(),
        "".join(no_comments).splitlines(),
        nosec_lines,
    )


def scan_file(rel: str, src: str) -> list[tuple[str, str, int, str]]:
    code_lines, nc_lines, nosec_lines = _lex_csharp(src)
    findings: list[tuple[str, str, int, str]] = []
    for lineno, (code, nc) in enumerate(zip(code_lines, nc_lines), 1):
        if lineno in nosec_lines:
            continue
        if (PROC_START_RE.search(nc) and not PROC_SAFE_LITERAL_RE.search(nc)) or SHELL_EXEC_RE.search(code):
            findings.append(("CS001", rel, lineno, "dynamic process execution or UseShellExecute enabled"))
        if SQL_CONCAT_RE.search(nc) or (SQL_CALL_RE.search(nc) and ("+" in nc or '$"' in nc)):
            findings.append(("CS002", rel, lineno, "SQL query constructed via string concatenation or interpolation"))
        if SECRET_ASSIGN_RE.search(nc):
            findings.append(("CS003", rel, lineno, "potential hardcoded credential literal"))
        if WEAK_CRYPTO_RE.search(code):
            findings.append(("CS004", rel, lineno, "weak cryptographic primitive or TLS certificate validation bypass"))
        if UNSAFE_DESER_RE.search(code):
            findings.append(("CS005", rel, lineno, "unsafe .NET deserialization formatter or TypeNameHandling"))
    return findings


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: sast.py <scope>", file=sys.stderr)
        return 2
    scope = argv[0]
    cwd = os.getcwd()
    scope_abs = os.path.abspath(scope)
    if not os.path.isdir(scope_abs):
        print(f"sast: {scope} is not a directory", file=sys.stderr)
        return 1

    all_findings: list[tuple[str, str, int, str]] = []
    for root, dirs, files in os.walk(scope_abs):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not _is_test_dir(d))
        for name in sorted(files):
            if not name.endswith(".cs") or name.endswith(TEST_SUFFIXES):
                continue
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, cwd).replace(os.sep, "/")
            with open(abs_path, encoding="utf-8", errors="replace") as fh:
                all_findings.extend(scan_file(rel, fh.read()))

    all_findings.sort(key=lambda item: (item[1], item[2], item[0]))
    items = []
    for rule, rel, line, msg in all_findings:
        print(f"{rel}:{line} {rule} {msg}", file=sys.stderr)
        items.append({"rule": rule, "file": rel, "line": line})
    print(json.dumps({"findings": len(items), "items": items}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
