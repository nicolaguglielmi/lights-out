#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Zero-dependency Rust SAST runner (ADR 0072 §1, ADR 0110): stdlib only."""

from __future__ import annotations

import json
import os
import re
import sys

SKIP_DIRS = {"target", "tests", "benches", "examples"}
SECRET_ASSIGN_RE = re.compile(
    r'\b(?:let(?:\s+mut)?|const|static)\s+[A-Za-z0-9_]*'
    r'(?:password|passwd|secret|api_?key|token|private_?key)[A-Za-z0-9_]*'
    r'\b[^=\n]*=\s*"[^"\n]+"',
    re.IGNORECASE,
)
UNSAFE_RE = re.compile(r"\bunsafe\b")
CMD_VAR_RE = re.compile(r"\bCommand::new\s*\(\s*(?!\")([A-Za-z_][A-Za-z0-9_]*)")
CMD_SHELL_RE = re.compile(r'\bCommand::new\s*\(\s*"(?:[^"]*/)?(?:sh|bash)"\s*\)')
SHELL_DYNAMIC_ARG_RE = re.compile(r'\.arg\s*\(\s*"-c"\s*\).*?\.arg\s*\(\s*(?!")')
SQL_FORMAT_RE = re.compile(
    r'\bformat!\s*\(\s*"[^"]*\b(?:SELECT|INSERT|UPDATE|DELETE)\b',
    re.IGNORECASE,
)
TLS_DISABLE_RE = re.compile(
    r"\bdanger_accept_invalid_(?:certs|hostnames)\s*\(\s*true\s*\)"
)
WEAK_CRYPTO_RE = re.compile(
    r"\b(?:md5::|Md5::|sha1::|Sha1::|std::mem::transmute\b|mem::transmute\b)"
)
NOSEC_RE = re.compile(r"\bnosec\b", re.IGNORECASE)
CFG_TEST_RE = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]")


def _lex_rust(text: str) -> tuple[list[str], list[str], set[int]]:
    """Return (code_only_lines, no_comment_lines, nosec_lines) preserving line numbers."""
    n = len(text)
    code_only: list[str] = []
    no_comments: list[str] = []
    nosec_lines: set[int] = set()
    i = 0
    line = 1

    def emit(ch_code: str, ch_nocomment: str) -> None:
        code_only.append(ch_code)
        no_comments.append(ch_nocomment)

    while i < n:
        ch = text[i]
        if ch == "\n":
            emit("\n", "\n")
            line += 1
            i += 1
            continue
        # Line comment
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            start = i
            while i < n and text[i] != "\n":
                emit(" ", " ")
                i += 1
            if NOSEC_RE.search(text[start:i]):
                nosec_lines.add(line)
            continue
        # Nested block comment
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            start = i
            start_line = line
            depth = 1
            emit(" ", " ")
            emit(" ", " ")
            i += 2
            while i < n and depth > 0:
                if text[i] == "/" and i + 1 < n and text[i + 1] == "*":
                    depth += 1
                    emit(" ", " ")
                    emit(" ", " ")
                    i += 2
                elif text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                    depth -= 1
                    emit(" ", " ")
                    emit(" ", " ")
                    i += 2
                elif text[i] == "\n":
                    emit("\n", "\n")
                    line += 1
                    i += 1
                else:
                    emit(" ", " ")
                    i += 1
            if NOSEC_RE.search(text[start:i]):
                nosec_lines.add(start_line)
                nosec_lines.add(line)
            continue
        # Raw string literal: r#"..."# or br#"..."#
        raw_match = re.match(r'(?:b)?r(#*)"', text[i:])
        if raw_match and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            hashes = raw_match.group(1)
            prefix_len = len(raw_match.group(0))
            for k in range(prefix_len):
                emit(text[i + k], text[i + k])
            i += prefix_len
            end_seq = '"' + hashes
            while i < n:
                if text.startswith(end_seq, i):
                    for k in range(len(end_seq)):
                        emit(end_seq[k], end_seq[k])
                    i += len(end_seq)
                    break
                if text[i] == "\n":
                    emit("\n", "\n")
                    line += 1
                else:
                    emit(" ", text[i])
                i += 1
            continue
        # Normal or byte string literal: "..." or b"..."
        if ch == '"' or (ch == "b" and i + 1 < n and text[i + 1] == '"' and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_"))):
            if ch == "b":
                emit("b", "b")
                i += 1
            emit('"', '"')
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
                if c == '"':
                    emit('"', '"')
                    i += 1
                    break
                if c == "\n":
                    emit("\n", "\n")
                    line += 1
                else:
                    emit(" ", c)
                i += 1
            continue
        # Char literal vs lifetime ('a)
        if ch == "'":
            if i + 2 < n and text[i + 1] == "\\":
                emit("'", "'")
                i += 1
                while i < n and text[i] not in ("'", "\n"):
                    emit(" ", text[i])
                    i += 1
                if i < n and text[i] == "'":
                    emit("'", "'")
                    i += 1
                continue
            if i + 2 < n and text[i + 2] == "'" and text[i + 1] != "'":
                emit("'", "'")
                emit(" ", text[i + 1])
                emit("'", "'")
                i += 3
                continue
        emit(ch, ch)
        i += 1

    return (
        "".join(code_only).splitlines(),
        "".join(no_comments).splitlines(),
        nosec_lines,
    )


def _cfg_test_lines(code_lines: list[str]) -> set[int]:
    """Return 1-based line numbers belonging to #[cfg(test)] items."""
    test_lines: set[int] = set()
    armed = False
    in_block = False
    depth = 0
    for lineno, line in enumerate(code_lines, 1):
        stripped = line.strip()
        if not in_block:
            if CFG_TEST_RE.search(stripped):
                armed = True
                test_lines.add(lineno)
                continue
            if armed:
                test_lines.add(lineno)
                opens = line.count("{")
                closes = line.count("}")
                if opens > 0:
                    depth = opens - closes
                    if depth > 0:
                        in_block = True
                    else:
                        armed = False
                elif stripped.endswith(";"):
                    armed = False
        else:
            test_lines.add(lineno)
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                in_block = False
                armed = False
    return test_lines


def scan_file(rel: str, src: str) -> list[tuple[str, str, int, str]]:
    code_lines, nocomment_lines, nosec_lines = _lex_rust(src)
    test_lines = _cfg_test_lines(code_lines)
    findings: list[tuple[str, str, int, str]] = []
    for lineno, (code, nc) in enumerate(zip(code_lines, nocomment_lines), 1):
        if lineno in nosec_lines or lineno in test_lines:
            continue
        if UNSAFE_RE.search(code):
            findings.append(("RS001", rel, lineno, "use of unsafe block or declaration"))
        if SECRET_ASSIGN_RE.search(nc):
            findings.append(("RS002", rel, lineno, "potential hardcoded credential literal"))
        if CMD_VAR_RE.search(nc) or (CMD_SHELL_RE.search(nc) and SHELL_DYNAMIC_ARG_RE.search(nc)):
            findings.append(("RS003", rel, lineno, "subprocess spawned with dynamic command or shell argument"))
        if SQL_FORMAT_RE.search(nc):
            findings.append(("RS004", rel, lineno, "SQL query constructed via format! string"))
        if TLS_DISABLE_RE.search(code):
            findings.append(("RS005", rel, lineno, "TLS certificate or hostname verification disabled"))
        if WEAK_CRYPTO_RE.search(code):
            findings.append(("RS006", rel, lineno, "weak cryptographic primitive or mem::transmute"))
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
            if not name.endswith(".rs") or name.endswith("_test.rs"):
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
