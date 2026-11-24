#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Zero-dependency PHP mutation runner (ADR 0015 §3, ADR 0129): stdlib only."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

SKIP_DIRS = {"vendor", "tests", "test", "cache", "var"}
COPY_SKIP = {".git", ".crew"}
TEST_SUFFIXES = ("Test.php", "Tests.php")


def parse_mutation_lines(raw: str) -> tuple[bool, dict[str, list[tuple[int, int]]]]:
    trimmed = raw.strip()
    if not trimmed or trimmed == "all":
        return False, {}
    ranges: dict[str, list[tuple[int, int]]] = {}
    for group in trimmed.split():
        if "=" not in group:
            continue
        file_part, spec = group.split("=", 1)
        rel = os.path.normpath(file_part).replace(os.sep, "/")
        for item in spec.split(","):
            if not item:
                continue
            if "-" in item:
                a, b = item.split("-", 1)
                ranges.setdefault(rel, []).append((int(a), int(b)))
            else:
                n = int(item)
                ranges.setdefault(rel, []).append((n, n))
    return True, ranges


def in_ranges(line: int, ranges: list[tuple[int, int]]) -> bool:
    return any(a <= line <= b for a, b in ranges)


def run_suite(cwd: str, cmd: str, timeout: float | None = None) -> tuple[int, str, bool]:
    proc = subprocess.Popen(
        ["sh", "-c", cmd],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = proc.communicate()
        return 124, out or "", True


def is_compile_failure(output: str) -> bool:
    return "PHP Parse error:" in output or "ParseError:" in output


def _skip_line_mask(lines: list[str]) -> set[int]:
    skip: set[int] = set()
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(("<?", "namespace ", "use ", "declare(", "#[", "const ", "public const ", "private const ")):
            skip.add(lineno)
    return skip


def generate_mutants(rel: str, src: str) -> list[tuple[str, int, str, int, int, str]]:
    """Return [(file, line, mutator, start_off, end_off, replacement)]."""
    skip_lines = _skip_line_mask(src.splitlines())
    mutants: list[tuple[str, int, str, int, int, str]] = []
    n = len(src)
    i = 0
    line = 1
    prev_sig = ""
    prev_word = ""

    def add(start: int, old: str, new: str, mutator: str) -> None:
        if line not in skip_lines:
            mutants.append((rel, line, mutator, start, start + len(old), new))

    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        if src.startswith("<?php", i) or src.startswith("<?=", i):
            i += 5 if src.startswith("<?php", i) else 3
            prev_sig = "<?php"
            prev_word = ""
            continue
        if (ch == "/" and i + 1 < n and src[i + 1] == "/") or (
            ch == "#" and not (i + 1 < n and src[i + 1] == "[")
        ):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i < n:
                if src[i] == "*" and i + 1 < n and src[i + 1] == "/":
                    i += 2
                    break
                if src[i] == "\n":
                    line += 1
                i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < n:
                if src[i] == "\\" and i + 1 < n:
                    if src[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                if src[i] == "\n":
                    line += 1
                i += 1
            prev_sig = quote
            prev_word = ""
            continue
        if ch == "$" and i + 1 < n and (src[i + 1].isalpha() or src[i + 1] == "_"):
            start = i
            i += 1
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
            prev_sig = "ident"
            prev_word = src[start:i]
            continue
        if ch.isalpha() or ch == "_":
            start = i
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
            word = src[start:i]
            low = word.lower()
            if low in ("true", "false") and prev_sig not in ("->", "::"):
                repl = ("false" if low == "true" else "true") if word.islower() else ("FALSE" if low == "true" else "TRUE")
                add(start, word, repl, "BooleanLiteral")
            prev_sig = "ident"
            prev_word = low
            continue
        if ch.isdigit():
            while i < n and (src[i].isalnum() or src[i] in "._"):
                i += 1
            prev_sig = "num"
            prev_word = ""
            continue

        three = src[i : i + 3]
        if three in ("===", "!==", "<=>"):
            if three in ("===", "!=="):
                add(i, three, "!==" if three == "===" else "===", "IdentityOperator")
            prev_sig = three
            prev_word = ""
            i += 3
            continue

        two = src[i : i + 2]
        if two in ("->", "=>", "::", "??", "?->"):
            prev_sig = two
            prev_word = ""
            i += len(two)
            continue
        if two in ("==", "!="):
            add(i, two, "!=" if two == "==" else "==", "EqualityOperator")
            prev_sig = two
            prev_word = ""
            i += 2
            continue
        if two in ("&&", "||"):
            add(i, two, "||" if two == "&&" else "&&", "LogicalOperator")
            prev_sig = two
            prev_word = ""
            i += 2
            continue
        if two in ("+=", "-=", "*=", "/="):
            repl = {"+=": "-=", "-=": "+=", "*=": "/=", "/=": "*="}[two]
            add(i, two, repl, "AssignOperator")
            prev_sig = two
            prev_word = ""
            i += 2
            continue
        if two in ("<=", ">="):
            if two == "<=":
                add(i, "<=", "<", "RelationalOperator")
                add(i, "<=", ">", "RelationalOperator")
            else:
                add(i, ">=", ">", "RelationalOperator")
                add(i, ">=", "<", "RelationalOperator")
            prev_sig = two
            prev_word = ""
            i += 2
            continue

        if ch in ("<", ">"):
            before_sp = i > 0 and src[i - 1].isspace()
            after_sp = i + 1 < n and src[i + 1].isspace()
            if prev_sig in ("ident", "num", ")", "]") and before_sp and after_sp:
                if ch == "<":
                    add(i, "<", "<=", "RelationalOperator")
                    add(i, "<", ">", "RelationalOperator")
                else:
                    add(i, ">", ">=", "RelationalOperator")
                    add(i, ">", "<", "RelationalOperator")
        elif ch in ("+", "-", "*", "/", "%"):
            is_expr = prev_sig in ("ident", "num", ")", "]") and prev_word not in (
                "return",
                "if",
                "elseif",
                "while",
                "else",
                "throw",
                "echo",
            )
            if is_expr:
                repl_map = {"+": "-", "-": "+", "*": "/", "/": "*", "%": "*"}
                add(i, ch, repl_map[ch], "ArithmeticOperator")

        prev_sig = ch
        prev_word = ""
        i += 1

    return mutants


def copy_workspace(src_root: str, dst_root: str) -> None:
    for root, dirs, files in os.walk(src_root):
        dirs[:] = [d for d in dirs if d not in COPY_SKIP]
        rel_dir = os.path.relpath(root, src_root)
        target_dir = dst_root if rel_dir == "." else os.path.join(dst_root, rel_dir)
        os.makedirs(target_dir, exist_ok=True)
        for name in files:
            src_path = os.path.join(root, name)
            dst_path = os.path.join(target_dir, name)
            if os.path.islink(src_path):
                os.symlink(os.readlink(src_path), dst_path)
            else:
                shutil.copy2(src_path, dst_path)


def emit_score(killed: int, survived: int, excluded: int, survivors: list[str]) -> None:
    for s in survivors:
        print(s, file=sys.stderr)
    print(
        f"score: {excluded} mutant(s) excluded from the population (CompileError)",
        file=sys.stderr,
    )
    total = killed + survived
    score = (killed / total) if total > 0 else None
    print(json.dumps({"total": total, "killed": killed, "survived": survived, "score": score}))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('usage: mutate.py <scope> "<test command>"', file=sys.stderr)
        return 2
    scope = os.path.normpath(argv[0])
    test_cmd = argv[1]
    cwd = os.getcwd()
    scope_abs = os.path.abspath(scope)
    if not os.path.isdir(scope_abs):
        print(f"mutate: {scope} is not a directory", file=sys.stderr)
        return 1

    scoped, ranges_by_file = parse_mutation_lines(os.environ.get("MUTATION_LINES", ""))

    t0 = time.monotonic()
    rc, clean_out, _ = run_suite(cwd, test_cmd)
    if clean_out:
        sys.stderr.write(clean_out)
    if rc != 0:
        print(
            f"mutation.sh: the unmutated test suite does not pass ({test_cmd}); not a mutation score",
            file=sys.stderr,
        )
        return 1
    elapsed = max(1.0, time.monotonic() - t0)
    timeout = 10.0 * elapsed

    originals: dict[str, str] = {}
    mutants: list[tuple[str, int, str, int, int, str]] = []
    for root, dirs, files in os.walk(scope_abs):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(files):
            if not name.endswith(".php") or name.endswith(TEST_SUFFIXES):
                continue
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, cwd).replace(os.sep, "/")
            if scoped and rel not in ranges_by_file:
                continue
            with open(abs_path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            found = generate_mutants(rel, src)
            if scoped:
                rs = ranges_by_file[rel]
                found = [m for m in found if in_ranges(m[1], rs)]
            if found:
                originals[rel] = src
                mutants.extend(found)

    if not mutants:
        emit_score(0, 0, 0, [])
        return 0

    mutants.sort(key=lambda m: (m[0], m[1], m[3], m[2]))
    killed = 0
    survived = 0
    excluded = 0
    survivors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="lights-out-php-mutate-") as work:
        copy_workspace(cwd, work)
        for rel, line_no, mutator, start, end, repl in mutants:
            orig = originals[rel]
            mutated = orig[:start] + repl + orig[end:]
            target = os.path.join(work, rel)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(mutated)
            mrc, mout, timed_out = run_suite(work, test_cmd, timeout=timeout)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(orig)
            if timed_out:
                killed += 1
            elif mrc == 0:
                survived += 1
                survivors.append(f"SURVIVOR {rel}:{line_no} {mutator}")
            elif is_compile_failure(mout):
                excluded += 1
            else:
                killed += 1

    emit_score(killed, survived, excluded, survivors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
