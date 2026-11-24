#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Zero-dependency Rust mutation runner (ADR 0015 §3, ADR 0110): stdlib only."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

SKIP_DIRS = {"target", "tests", "benches", "examples"}
COPY_SKIP = {".git", ".crew", "target"}
CFG_TEST_RE = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]")


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
    return (
        "error[E" in output
        or "could not compile" in output
        or "error: expected" in output
        or "error: mismatched" in output
    )


def _cfg_test_lines(src: str) -> set[int]:
    test_lines: set[int] = set()
    armed = False
    in_block = False
    depth = 0
    for lineno, line in enumerate(src.splitlines(), 1):
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


def _skip_line_mask(lines: list[str], test_lines: set[int]) -> set[int]:
    skip = set(test_lines)
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(("use ", "pub use ", "#[", "#![", "const ", "pub const ", "mod ")):
            skip.add(lineno)
    return skip


def generate_mutants(rel: str, src: str) -> list[tuple[str, int, str, int, int, str]]:
    """Return [(file, line, mutator, start_off, end_off, replacement)]."""
    lines = src.splitlines()
    skip_lines = _skip_line_mask(lines, _cfg_test_lines(src))
    mutants: list[tuple[str, int, str, int, int, str]] = []
    n = len(src)
    i = 0
    line = 1
    prev_sig = ""
    prev_word = ""
    angle_depth = 0
    in_generic_decl = False

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
        # Line comment
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        # Block comment
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            depth = 1
            i += 2
            while i < n and depth > 0:
                if src[i] == "/" and i + 1 < n and src[i + 1] == "*":
                    depth += 1
                    i += 2
                elif src[i] == "*" and i + 1 < n and src[i + 1] == "/":
                    depth -= 1
                    i += 2
                elif src[i] == "\n":
                    line += 1
                    i += 1
                else:
                    i += 1
            continue
        # Raw strings
        raw_m = re.match(r'(?:b)?r(#*)"', src[i:])
        if raw_m and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")):
            end_seq = '"' + raw_m.group(1)
            i += len(raw_m.group(0))
            while i < n:
                if src.startswith(end_seq, i):
                    i += len(end_seq)
                    break
                if src[i] == "\n":
                    line += 1
                i += 1
            prev_sig = '"'
            prev_word = ""
            continue
        # Strings
        if ch == '"' or (ch == "b" and i + 1 < n and src[i + 1] == '"' and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_"))):
            i += 2 if ch == "b" else 1
            while i < n:
                if src[i] == "\\" and i + 1 < n:
                    if src[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                if src[i] == "\n":
                    line += 1
                i += 1
            prev_sig = '"'
            prev_word = ""
            continue
        # Char literal vs lifetime
        if ch == "'":
            if i + 2 < n and src[i + 1] == "\\":
                i += 1
                while i < n and src[i] not in ("'", "\n"):
                    i += 1
                if i < n and src[i] == "'":
                    i += 1
                prev_sig = "'"
                prev_word = ""
                continue
            if i + 2 < n and src[i + 2] == "'" and src[i + 1] != "'":
                i += 3
                prev_sig = "'"
                prev_word = ""
                continue
            i += 1
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
            prev_sig = "'"
            prev_word = ""
            continue

        # Identifiers & numbers
        if ch.isalpha() or ch == "_":
            start = i
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
            word = src[start:i]
            if word in ("fn", "struct", "enum", "trait", "impl", "type"):
                in_generic_decl = True
            elif word in ("true", "false") and prev_sig != ".":
                add(start, word, "false" if word == "true" else "true", "BooleanLiteral")
            prev_sig = "ident"
            prev_word = word
            continue
        if ch.isdigit():
            while i < n and (src[i].isalnum() or src[i] in "._"):
                i += 1
            prev_sig = "num"
            prev_word = ""
            continue

        two = src[i : i + 2]
        if two in ("->", "=>", "::"):
            if two == "->":
                in_generic_decl = False
            prev_sig = two
            prev_word = ""
            i += 2
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
            if not in_generic_decl and angle_depth == 0:
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
        if two in ("<<", ">>"):
            prev_sig = two
            prev_word = ""
            i += 2
            continue

        if ch in "{};":
            in_generic_decl = False
            angle_depth = 0
        elif ch == "<":
            if in_generic_decl or prev_sig == "::":
                angle_depth += 1
            elif prev_sig in ("ident", "num", ")", "]") and prev_word not in ("return", "if", "while", "match"):
                # Require spaces around binary `<` to avoid Type<T> false positives.
                before_sp = i > 0 and src[i - 1].isspace()
                after_sp = i + 1 < n and src[i + 1].isspace()
                if before_sp and after_sp:
                    add(i, "<", "<=", "RelationalOperator")
                    add(i, "<", ">", "RelationalOperator")
        elif ch == ">":
            if angle_depth > 0:
                angle_depth -= 1
            elif prev_sig in ("ident", "num", ")", "]"):
                before_sp = i > 0 and src[i - 1].isspace()
                after_sp = i + 1 < n and src[i + 1].isspace()
                if before_sp and after_sp:
                    add(i, ">", ">=", "RelationalOperator")
                    add(i, ">", "<", "RelationalOperator")
        elif ch in ("+", "-", "*", "/", "%"):
            is_expr_operand = prev_sig in ("ident", "num", ")", "]") and prev_word not in ("return", "if", "while", "else", "match")
            if is_expr_operand:
                if ch == "+":
                    add(i, "+", "-", "ArithmeticOperator")
                elif ch == "-":
                    add(i, "-", "+", "ArithmeticOperator")
                elif ch == "*":
                    add(i, "*", "/", "ArithmeticOperator")
                elif ch == "/":
                    add(i, "/", "*", "ArithmeticOperator")
                elif ch == "%":
                    add(i, "%", "*", "ArithmeticOperator")

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
            if not name.endswith(".rs") or name.endswith("_test.rs"):
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

    with tempfile.TemporaryDirectory(prefix="lights-out-rust-mutate-") as work:
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
