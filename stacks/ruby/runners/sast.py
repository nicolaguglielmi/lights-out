#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Zero-dependency Ruby SAST runner (ADR 0072 §1, ADR 0129): stdlib only."""

from __future__ import annotations

import json
import os
import re
import sys

SKIP_DIRS = {"vendor", "spec", "test", "tests", "bin", "db", "config", "tmp", "log"}

CMD_INJECT_RE = re.compile(
    r"\b(?:Kernel\.)?eval\s*\("
    r"|\b(?:instance_eval|class_eval|module_eval)\s*\(\s*[A-Za-z_@]"
    r"|\bsystem\s*\(\s*(?!['\"])"
    r"|\bOpen3\.(?:capture2|capture2e|capture3|popen3)\s*\(\s*(?!['\"])"
    r"|`[^`\n]*#\{"
    r"|%x[\(\[\{]"
)
SQL_INJECT_RE = re.compile(
    r'"[^"]*\b(?:SELECT|INSERT|UPDATE|DELETE)\b[^"]*#\{'
    r'|\.(?:where|find_by_sql|execute)\s*\(\s*"[^"]*#\{',
    re.IGNORECASE,
)
SECRET_ASSIGN_RE = re.compile(
    r"\b[A-Za-z0-9_]*(?:password|passwd|secret|api_?key|token|private_?key)[A-Za-z0-9_]*"
    r"""\s*=\s*['"][^'"\n]+['"]""",
    re.IGNORECASE,
)
WEAK_CRYPTO_RE = re.compile(
    r"\bDigest::(?:MD5|SHA1)\b"
    r"|\bOpenSSL::SSL::VERIFY_NONE\b"
)
UNSAFE_DESER_RE = re.compile(
    r"\b(?:Marshal\.load|YAML\.load)\s*\("
)
NOSEC_RE = re.compile(r"\b(?:nosec|NOSONAR|rubocop:disable\s+Security|brakeman:ignore)\b", re.IGNORECASE)


def _is_ruby_test(filename: str) -> bool:
    return filename.endswith(("_spec.rb", "_test.rb")) or filename.startswith("test_")


def _lex_ruby(text: str) -> tuple[list[str], list[str], set[int]]:
    """Return (code_only_lines, no_comment_lines, nosec_lines) preserving line numbers."""
    n = len(text)
    code_only: list[str] = []
    no_comments: list[str] = []
    nosec_lines: set[int] = set()
    i = 0
    line = 1
    at_line_start = True

    def emit(c_code: str, c_nc: str) -> None:
        code_only.append(c_code)
        no_comments.append(c_nc)

    while i < n:
        ch = text[i]
        if ch == "\n":
            emit("\n", "\n")
            line += 1
            at_line_start = True
            i += 1
            continue
        if at_line_start and text.startswith("=begin", i) and (i + 6 == n or text[i + 6].isspace()):
            start = i
            start_line = line
            while i < n:
                if at_line_start and text.startswith("=end", i) and (i + 4 == n or text[i + 4].isspace()):
                    while i < n and text[i] != "\n":
                        emit(" ", " ")
                        i += 1
                    break
                if text[i] == "\n":
                    emit("\n", "\n")
                    line += 1
                    at_line_start = True
                else:
                    emit(" ", " ")
                    at_line_start = False
                i += 1
            if NOSEC_RE.search(text[start:i]):
                nosec_lines.add(start_line)
                nosec_lines.add(line)
            continue
        if ch == "#":
            start = i
            while i < n and text[i] != "\n":
                emit(" ", " ")
                i += 1
            if NOSEC_RE.search(text[start:i]):
                nosec_lines.add(line)
            continue
        if ch in ('"', "'", "`"):
            quote = ch
            emit(quote, quote)
            at_line_start = False
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
        if not ch.isspace():
            at_line_start = False
        emit(ch, ch)
        i += 1

    return (
        "".join(code_only).splitlines(),
        "".join(no_comments).splitlines(),
        nosec_lines,
    )


def scan_file(rel: str, src: str) -> list[tuple[str, str, int, str]]:
    code_lines, nc_lines, nosec_lines = _lex_ruby(src)
    findings: list[tuple[str, str, int, str]] = []
    for lineno, (code, nc) in enumerate(zip(code_lines, nc_lines), 1):
        if lineno in nosec_lines:
            continue
        if CMD_INJECT_RE.search(nc):
            findings.append(("RB001", rel, lineno, "dynamic Ruby eval or shell command execution"))
        if SQL_INJECT_RE.search(nc):
            findings.append(("RB002", rel, lineno, "SQL query constructed via Ruby string interpolation"))
        if SECRET_ASSIGN_RE.search(nc):
            findings.append(("RB003", rel, lineno, "potential hardcoded credential literal"))
        if WEAK_CRYPTO_RE.search(code):
            findings.append(("RB004", rel, lineno, "weak Digest primitive or OpenSSL VERIFY_NONE"))
        if UNSAFE_DESER_RE.search(code):
            findings.append(("RB005", rel, lineno, "unsafe Marshal.load or YAML.load deserialization"))
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
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(files):
            if not name.endswith(".rb") or _is_ruby_test(name):
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
