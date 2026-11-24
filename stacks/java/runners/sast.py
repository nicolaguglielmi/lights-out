#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Zero-dependency Java/Kotlin SAST runner (ADR 0072 §1, ADR 0110): stdlib only."""

from __future__ import annotations

import json
import os
import re
import sys

SKIP_DIRS = {"target", "build", "out", ".gradle", "test"}
TEST_SUFFIXES = ("Test.java", "Tests.java", "Test.kt", "Tests.kt", "Spec.java", "Spec.kt")

EXEC_CALL_RE = re.compile(
    r"\b(?:Runtime\.getRuntime\(\)\.exec|new\s+ProcessBuilder|ProcessBuilder)\s*\("
)
EXEC_SAFE_LITERAL_RE = re.compile(
    r'\b(?:Runtime\.getRuntime\(\)\.exec|ProcessBuilder)\s*\(\s*"[^"$]*"\s*\)'
)
SQL_CALL_RE = re.compile(
    r"\b(?:createStatement|executeQuery|executeUpdate|execute|prepareStatement|createQuery|createNativeQuery)\s*\("
)
SQL_ASSIGN_RE = re.compile(
    r'\b(?:sql|query)\s*=\s*"[^"]*\b(?:SELECT|INSERT|UPDATE|DELETE)\b[^"]*"\s*\+'
    r'|"\s*[^"]*\b(?:SELECT|INSERT|UPDATE|DELETE)\b[^"]*"\s*\+',
    re.IGNORECASE,
)
SQL_VERB_RE = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
SECRET_ASSIGN_RE = re.compile(
    r"\b(?:String|val|var)\s+[A-Za-z0-9_]*"
    r"(?:password|passwd|secret|api_?key|token|private_?key)[A-Za-z0-9_]*"
    r'\s*(?::\s*String\s*)?=\s*"[^"\n]+"',
    re.IGNORECASE,
)
WEAK_CRYPTO_RE = re.compile(
    r'\bMessageDigest\.getInstance\s*\(\s*"(?:MD5|SHA-?1)"\s*\)'
    r'|\bCipher\.getInstance\s*\(\s*"(?:DES|DESede|AES/ECB)[^"]*"\s*\)'
    r"|\bALLOW_ALL_HOSTNAME_VERIFIER\b",
    re.IGNORECASE,
)
UNSAFE_DESER_RE = re.compile(r"\bnew\s+ObjectInputStream\s*\(")
NOSEC_RE = re.compile(r"\b(?:nosec|NOSONAR)\b", re.IGNORECASE)


def _lex_jvm(text: str) -> tuple[list[str], list[str], set[int]]:
    """Return (code_only_lines, no_comment_lines, nosec_lines) preserving line numbers."""
    n = len(text)
    code_only: list[str] = []
    no_comments: list[str] = []
    nosec_lines: set[int] = set()
    i = 0
    line = 1

    def emit(ch_code: str, ch_nc: str) -> None:
        code_only.append(ch_code)
        no_comments.append(ch_nc)

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
        if text.startswith('"""', i):
            for _ in range(3):
                emit('"', '"')
            i += 3
            while i < n:
                if text.startswith('"""', i):
                    for _ in range(3):
                        emit('"', '"')
                    i += 3
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
    code_lines, nocomment_lines, nosec_lines = _lex_jvm(src)
    findings: list[tuple[str, str, int, str]] = []
    for lineno, (code, nc) in enumerate(zip(code_lines, nocomment_lines), 1):
        if lineno in nosec_lines:
            continue
        if EXEC_CALL_RE.search(nc) and not EXEC_SAFE_LITERAL_RE.search(nc):
            findings.append(("JAVA001", rel, lineno, "subprocess launched with dynamic command"))
        if SQL_ASSIGN_RE.search(nc) or (
            SQL_CALL_RE.search(nc) and SQL_VERB_RE.search(nc) and ("+" in nc or "${" in nc or "$" in nc)
        ):
            findings.append(("JAVA002", rel, lineno, "SQL query constructed via string concatenation or interpolation"))
        if SECRET_ASSIGN_RE.search(nc):
            findings.append(("JAVA003", rel, lineno, "potential hardcoded credential literal"))
        if WEAK_CRYPTO_RE.search(nc):
            findings.append(("JAVA004", rel, lineno, "weak cryptographic algorithm or hostname verification bypass"))
        if UNSAFE_DESER_RE.search(code):
            findings.append(("JAVA005", rel, lineno, "unfiltered Java ObjectInputStream deserialization"))
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
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
        )
        for name in sorted(files):
            if not name.endswith((".java", ".kt")) or name.endswith(TEST_SUFFIXES):
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
