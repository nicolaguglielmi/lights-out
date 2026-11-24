#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Deterministic stdlib bundler for the single-file Console (docs/adr/0124).

Assembles the ordered source modules under console/src/ into console/index.html
with zero external packages, zero runtime CDN/network dependencies, and byte-
identical output across platforms.

Usage:
    python3 console/build.py [--write] [--src DIR] [--out FILE]
    python3 console/build.py --check   [--src DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
import difflib
import pathlib
import sys

CONSOLE_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_SRC = CONSOLE_DIR / "src"
DEFAULT_OUT = CONSOLE_DIR / "index.html"

# Provenance, decision: docs/adr/0124 §1, nine ordered single-responsibility
# modules compiled into one offline/CSP-safe HTML document.
MANIFEST: tuple[str, ...] = (
    "00-head.html",
    "01-styles.css",
    "02-shell.html",
    "03-state.js",
    "04-screens.js",
    "05-kanban-network.js",
    "06-hive-wizard.js",
    "07-runtime-sse.js",
    "99-tail.html",
)

# Provenance, decision: docs/adr/0124, standard 3-line unified diff context
# capped at 40 output lines so CI drift failures stay concise and actionable.
DIFF_CONTEXT_LINES = 3
MAX_DIFF_LINES = 40


class BuildError(Exception):
    """Raised when console/src/ cannot be deterministically bundled."""


def _normalize(text: str) -> str:
    clean = text.replace("\r\n", "\n").replace("\r", "\n")
    return clean if clean.endswith("\n") else clean + "\n"


def validate_sources(src_dir: pathlib.Path) -> list[pathlib.Path]:
    if not src_dir.is_dir():
        raise BuildError(f"source directory not found: {src_dir}")
    present = sorted(p.name for p in src_dir.iterdir() if p.is_file())
    expected = list(MANIFEST)
    missing = [name for name in expected if name not in present]
    if missing:
        raise BuildError(f"missing required module(s) in {src_dir}: {', '.join(missing)}")
    orphans = [name for name in present if name not in expected]
    if orphans:
        raise BuildError(
            f"unlisted module(s) in {src_dir}: {', '.join(orphans)}; "
            "register in console/build.py MANIFEST or remove"
        )
    return [src_dir / name for name in MANIFEST]


def assemble(src_dir: pathlib.Path = DEFAULT_SRC) -> str:
    paths = validate_sources(src_dir)
    chunks: list[str] = []
    js_buffer: list[str] = []

    def flush_js() -> None:
        if not js_buffer:
            return
        chunks.append("<script>\n")
        chunks.append("\n".join(part.rstrip("\n") for part in js_buffer) + "\n")
        chunks.append("</script>\n")
        js_buffer.clear()

    for path in paths:
        content = _normalize(path.read_text(encoding="utf-8"))
        if path.suffix == ".js":
            js_buffer.append(content)
            continue
        flush_js()
        if path.suffix == ".css":
            chunks.append("<style>\n")
            chunks.append(content)
            chunks.append("</style>\n")
        elif path.suffix == ".html":
            chunks.append(content)
        else:
            raise BuildError(f"unsupported module extension: {path.name}")

    flush_js()
    return "".join(chunks)


def check_bundle(src_dir: pathlib.Path, out_file: pathlib.Path) -> int:
    expected = assemble(src_dir)
    if not out_file.is_file():
        print(
            f"console-build: {out_file} does not exist; regenerate with: "
            "python3 console/build.py --write",
            file=sys.stderr,
        )
        return 1
    actual = out_file.read_text(encoding="utf-8")
    if actual.encode("utf-8") == expected.encode("utf-8"):
        return 0

    diff = list(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(out_file),
            tofile=f"{src_dir} (assembled)",
            n=DIFF_CONTEXT_LINES,
        )
    )
    preview = "".join(diff[:MAX_DIFF_LINES])
    more = (
        f"\n... ({len(diff) - MAX_DIFF_LINES} more diff lines omitted)"
        if len(diff) > MAX_DIFF_LINES
        else ""
    )
    print(
        f"console-build: {out_file} has drifted from {src_dir}; "
        "run `python3 console/build.py --write` to update.\n"
        f"{preview}{more}",
        file=sys.stderr,
    )
    return 1


def write_bundle(src_dir: pathlib.Path, out_file: pathlib.Path) -> int:
    rendered = assemble(src_dir)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(rendered.encode("utf-8"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="console/build.py",
        description="Deterministic bundler for console/src/ -> console/index.html",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write",
        action="store_true",
        help="assemble console/src/ and write console/index.html (default)",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify console/index.html matches assembled console/src/ byte-for-byte",
    )
    parser.add_argument(
        "--src",
        type=pathlib.Path,
        default=DEFAULT_SRC,
        help="path to modular source directory (default: console/src)",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=DEFAULT_OUT,
        help="path to bundled HTML output (default: console/index.html)",
    )
    args = parser.parse_args(argv)
    try:
        if args.check:
            return check_bundle(args.src, args.out)
        return write_bundle(args.src, args.out)
    except BuildError as exc:
        print(f"console-build: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
