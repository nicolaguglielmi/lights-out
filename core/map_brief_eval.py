#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Measure MAP_BRIEF=off vs MAP_BRIEF=on brief footprint via core/map.py (ADR 0054 §5).

    python3 core/map_brief_eval.py [--repo DIR] [--rev REV] [--map FILE]
                                   [--tickets DIR] [--exclude GLOBS]
                                   [--worker-brief FILE] [--write-map]
                                   [--no-determinism-check] [--json]

Stdlib only, strictly vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import sqlite3
import sys
import tempfile
from typing import Any, Sequence

try:
    from . import map as project_map
except ImportError:  # run as a script: core/ is sys.path[0]
    import map as project_map

SCHEMA = "lights-out/map-brief-eval/1"
_MAKE_WORKLOAD_RE = re.compile(r"^\s*WORKLOAD\s*\?=.*,\s*([A-Za-z0-9_.-]+)\s*\)\s*$")


class EvalRefused(Exception):
    """Raised when evaluation cannot run cleanly (mapped to NO-GO exit 1)."""


def approx_tokens(text_or_bytes: int | str | bytes) -> float:
    """Estimate token count using exact bytes / 4."""
    if isinstance(text_or_bytes, int):
        return text_or_bytes / 4
    raw = text_or_bytes.encode("utf-8") if isinstance(text_or_bytes, str) else text_or_bytes
    return len(raw) / 4


def _resolve_worker_brief(repo: str, explicit: str | None) -> tuple[str | None, str | None]:
    """Locate the workload's worker.md without hardcoding workload paths."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit if os.path.isabs(explicit) else os.path.join(repo, explicit))
    else:
        for var in ("WORKLOAD_CONTRACT", "WORKLOAD"):
            val = (os.environ.get(var) or "").strip()
            if val:
                base = val if os.path.isabs(val) else os.path.join(repo, val)
                candidates.append(os.path.join(base, "worker.md"))
        makefile = os.path.join(repo, "Makefile")
        if os.path.isfile(makefile):
            try:
                with open(makefile, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        m = _MAKE_WORKLOAD_RE.match(line)
                        if m:
                            candidates.append(os.path.join(repo, m.group(1), "worker.md"))
                            break
            except OSError:
                pass
        candidates.append(os.path.join(repo, "worker.md"))

    for cand in candidates:
        if os.path.isfile(cand):
            with open(cand, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(cand, repo)
            return text, (rel if not rel.startswith("..") else cand)
    if explicit:
        raise EvalRefused(f"worker brief not found at {explicit}")
    return None, None


def _compose_crew_input(ticket_text: str, worker_brief: str | None, slice_text: str | None) -> str:
    """Match core/crew.sh input.md concatenation byte-for-byte."""
    parts: list[str] = []
    if worker_brief is not None:
        parts.append(worker_brief)
    if slice_text is not None:
        parts.append(slice_text)
    out = ticket_text
    for part in parts:
        out += "\n---\n\n" + part
    return out


def _slice_coverage(db: sqlite3.Connection, globs: Sequence[str]) -> dict[str, Any]:
    """Return structured files/ADRs/pages/tests/history/symbols linked to GLOBS."""
    names = [
        r[0]
        for r in db.execute(
            "SELECT name FROM node WHERE kind IN ('file', 'test', 'adr', 'page') ORDER BY name"
        )
    ]
    matched_files = sorted({n for n in names for g in globs if fnmatch.fnmatchcase(n, g)})
    missing_globs = sorted(g for g in globs if not any(fnmatch.fnmatchcase(n, g) for n in names))
    marks = ",".join("?" * len(matched_files)) or "''"

    found = project_map.covered(db, matched_files)
    adrs = sorted(n for (k, n) in found if k == "adr")
    pages = sorted(n for (k, n) in found if k == "page")

    tests_rows = db.execute(
        f"SELECT DISTINCT t.name, f.name FROM edge e "  # nosec B608 - placeholders only
        f"JOIN node t ON t.id = e.src JOIN node f ON f.id = e.dst "
        f"WHERE e.kind = 'tests' AND f.name IN ({marks}) ORDER BY t.name, f.name",
        matched_files,
    ).fetchall()
    by_test: dict[str, list[str]] = {}
    for test_name, path_name in tests_rows:
        by_test.setdefault(test_name, []).append(path_name)
    for path_name in matched_files:
        if db.execute("SELECT 1 FROM node WHERE kind = 'test' AND name = ?", (path_name,)).fetchone():
            by_test.setdefault(path_name, [])
    tests = sorted(by_test.keys())

    history_rows = db.execute(
        f"SELECT DISTINCT t.name FROM edge e "  # nosec B608 - placeholders only
        f"JOIN node t ON t.id = e.src JOIN node f ON f.id = e.dst "
        f"WHERE e.kind IN ('perimeter', 'history') AND f.name IN ({marks}) ORDER BY t.name",
        matched_files,
    ).fetchall()
    history_tickets = [r[0] for r in history_rows]

    symbol_rows = db.execute(
        f"SELECT count(*) FROM edge e JOIN node f ON f.id = e.src "  # nosec B608 - placeholders only
        f"WHERE e.kind = 'defines' AND f.name IN ({marks})",
        matched_files,
    ).fetchone()
    symbols_count = int(symbol_rows[0]) if symbol_rows else 0

    return {
        "files": matched_files,
        "missing_globs": missing_globs,
        "adrs": adrs,
        "pages": pages,
        "tests": tests,
        "history_tickets": history_tickets,
        "symbols_count": symbols_count,
    }


def _discover_tickets(repo: str, tickets_arg: str) -> tuple[str, list[tuple[str, str]]]:
    tickets_dir = tickets_arg if os.path.isabs(tickets_arg) else os.path.join(repo, tickets_arg)
    if not os.path.isdir(tickets_dir):
        raise EvalRefused(f"tickets directory not found: {tickets_dir}")
    found: list[tuple[str, str]] = []
    for entry in sorted(os.listdir(tickets_dir)):
        m = project_map.TICKET_ID.match(entry)
        if m and os.path.isfile(os.path.join(tickets_dir, entry)):
            found.append((m.group(1), os.path.join(tickets_dir, entry)))
    if not found:
        raise EvalRefused(f"no ticket files matching NNNN.md in {tickets_dir}")
    rel = os.path.relpath(tickets_dir, repo).replace(os.sep, "/")
    return rel, found


def _open_or_build_map(
    repo: str,
    sha: str,
    map_path: str,
    tickets_rel: str,
    exclude: Sequence[str],
    write_map: bool,
    tmp_dir: str,
) -> tuple[sqlite3.Connection, str, dict[str, int]]:
    if not write_map and os.path.isfile(map_path):
        try:
            db = project_map.open_map(map_path, sha)
            counts = dict(db.execute("SELECT kind, count(*) FROM node GROUP BY kind ORDER BY kind").fetchall())
            return db, "existing", counts
        except project_map.Refused:
            pass
    target = map_path if write_map else os.path.join(tmp_dir, "primary.sqlite")
    _, counts = project_map.build(repo, sha, target, tickets=tickets_rel, targets=None, exclude=list(exclude))
    db = project_map.open_map(target, sha)
    return db, ("written" if write_map else "temp-built"), counts


def evaluate_map_brief(
    repo: str = ".",
    rev: str = "HEAD",
    map_path: str = os.path.join(".crew", "map.sqlite"),
    tickets: str = "tickets",
    exclude: Sequence[str] = ("docs/PLAN.md", "docs/plans/*", ".claude/*"),
    worker_brief: str | None = None,
    write_map: bool = False,
    check_determinism: bool = True,
) -> dict[str, Any]:
    """Measure MAP_BRIEF=off vs MAP_BRIEF=on across all repository tickets."""
    repo_abs = os.path.abspath(repo)
    try:
        sha = project_map.git(repo_abs, "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    except project_map.Refused as exc:
        raise EvalRefused(str(exc)) from None

    tickets_rel, ticket_files = _discover_tickets(repo_abs, tickets)
    brief_text, brief_rel = _resolve_worker_brief(repo_abs, worker_brief)
    map_abs = map_path if os.path.isabs(map_path) else os.path.join(repo_abs, map_path)

    with tempfile.TemporaryDirectory(prefix="lights-out-map-brief-") as tmp_dir:
        db, map_source, map_counts = _open_or_build_map(
            repo_abs, sha, map_abs, tickets_rel, exclude, write_map, tmp_dir
        )
        det_db: sqlite3.Connection | None = None
        try:
            if check_determinism:
                det_path = os.path.join(tmp_dir, "determinism.sqlite")
                project_map.build(
                    repo_abs, sha, det_path, tickets=tickets_rel, targets=None, exclude=list(exclude)
                )
                det_db = project_map.open_map(det_path, sha)

            rows: list[dict[str, Any]] = []
            unique_adrs: set[str] = set()
            unique_pages: set[str] = set()
            unique_tests: set[str] = set()
            deterministic = True
            mismatches: list[str] = []

            for tid, path in ticket_files:
                with open(path, encoding="utf-8") as fh:
                    t_text = fh.read()
                globs = project_map.perimeter(t_text)
                title = project_map.title_of(t_text)
                off_text = _compose_crew_input(t_text, brief_text, None)
                t_bytes = len(t_text.encode("utf-8"))
                off_bytes = len(off_text.encode("utf-8"))

                if globs:
                    slice_text = project_map.slice_of(db, globs)
                    if det_db is not None and project_map.slice_of(det_db, globs) != slice_text:
                        deterministic = False
                        mismatches.append(tid)
                    on_text = _compose_crew_input(t_text, brief_text, slice_text)
                    cov = _slice_coverage(db, globs)
                    slice_bytes = len(slice_text.encode("utf-8"))
                    on_bytes = len(on_text.encode("utf-8"))
                    slice_sha = hashlib.sha256(slice_text.encode("utf-8")).hexdigest()
                else:
                    cov = {
                        "files": [],
                        "missing_globs": [],
                        "adrs": [],
                        "pages": [],
                        "tests": [],
                        "history_tickets": [],
                        "symbols_count": 0,
                    }
                    slice_bytes = 0
                    on_bytes = off_bytes
                    slice_sha = None

                delta_bytes = on_bytes - off_bytes
                unique_adrs.update(cov["adrs"])
                unique_pages.update(cov["pages"])
                unique_tests.update(cov["tests"])

                rel_ticket = posixpath.join(tickets_rel, os.path.basename(path))
                rows.append(
                    {
                        "ticket": tid,
                        "path": rel_ticket,
                        "title": title,
                        "has_perimeter": bool(globs),
                        "perimeter_globs": len(globs),
                        "ticket_bytes": t_bytes,
                        "off_bytes": off_bytes,
                        "off_tokens": approx_tokens(off_bytes),
                        "on_bytes": on_bytes,
                        "on_tokens": approx_tokens(on_bytes),
                        "slice_bytes": slice_bytes,
                        "slice_tokens": approx_tokens(slice_bytes),
                        "delta_bytes": delta_bytes,
                        "delta_tokens": approx_tokens(delta_bytes),
                        "pct_increase": round((delta_bytes / off_bytes) * 100.0, 1) if off_bytes else 0.0,
                        "files_count": len(cov["files"]),
                        "missing_globs_count": len(cov["missing_globs"]),
                        "adrs_count": len(cov["adrs"]),
                        "pages_count": len(cov["pages"]),
                        "tests_count": len(cov["tests"]),
                        "history_count": len(cov["history_tickets"]),
                        "symbols_count": cov["symbols_count"],
                        "adrs": cov["adrs"],
                        "pages": cov["pages"],
                        "tests": cov["tests"],
                        "slice_sha256": slice_sha,
                    }
                )
        finally:
            db.close()
            if det_db is not None:
                det_db.close()

    if check_determinism and not deterministic:
        raise EvalRefused(f"non-deterministic slice across fresh map builds for ticket(s): {', '.join(mismatches)}")

    with_perim = [r for r in rows if r["has_perimeter"]]
    n_all = len(rows)
    n_perim = len(with_perim)
    tot_off = sum(r["off_bytes"] for r in rows)
    tot_on = sum(r["on_bytes"] for r in rows)
    tot_slice = sum(r["slice_bytes"] for r in rows)
    tot_delta = sum(r["delta_bytes"] for r in rows)
    perim_off = sum(r["off_bytes"] for r in with_perim)
    perim_on = sum(r["on_bytes"] for r in with_perim)
    perim_slice = sum(r["slice_bytes"] for r in with_perim)

    return {
        "schema": SCHEMA,
        "sha": sha,
        "map_path": os.path.relpath(map_abs, repo_abs) if os.path.isfile(map_abs) else None,
        "map_source": map_source,
        "map_counts": map_counts,
        "worker_brief": {
            "path": brief_rel,
            "bytes": len(brief_text.encode("utf-8")) if brief_text is not None else 0,
            "tokens": approx_tokens(brief_text) if brief_text is not None else 0.0,
        },
        "determinism": {
            "checked": check_determinism,
            "deterministic": deterministic,
            "checked_tickets": n_perim if check_determinism else 0,
        },
        "summary": {
            "tickets_total": n_all,
            "tickets_with_perimeter": n_perim,
            "tickets_without_perimeter": n_all - n_perim,
            "total_off_bytes": tot_off,
            "total_off_tokens": approx_tokens(tot_off),
            "total_on_bytes": tot_on,
            "total_on_tokens": approx_tokens(tot_on),
            "total_slice_bytes": tot_slice,
            "total_slice_tokens": approx_tokens(tot_slice),
            "total_delta_bytes": tot_delta,
            "total_delta_tokens": approx_tokens(tot_delta),
            "mean_off_bytes_all": round(tot_off / n_all, 1) if n_all else 0.0,
            "mean_on_bytes_all": round(tot_on / n_all, 1) if n_all else 0.0,
            "mean_delta_bytes_all": round(tot_delta / n_all, 1) if n_all else 0.0,
            "mean_off_bytes_perimeter": round(perim_off / n_perim, 1) if n_perim else 0.0,
            "mean_on_bytes_perimeter": round(perim_on / n_perim, 1) if n_perim else 0.0,
            "mean_slice_bytes_perimeter": round(perim_slice / n_perim, 1) if n_perim else 0.0,
            "mean_slice_tokens_perimeter": round((perim_slice / n_perim) / 4, 1) if n_perim else 0.0,
            "min_slice_bytes_perimeter": min((r["slice_bytes"] for r in with_perim), default=0),
            "max_slice_bytes_perimeter": max((r["slice_bytes"] for r in with_perim), default=0),
            "tickets_with_adrs": sum(1 for r in with_perim if r["adrs_count"] > 0),
            "tickets_with_pages": sum(1 for r in with_perim if r["pages_count"] > 0),
            "tickets_with_tests": sum(1 for r in with_perim if r["tests_count"] > 0),
            "unique_adrs": len(unique_adrs),
            "unique_pages": len(unique_pages),
            "unique_tests": len(unique_tests),
        },
        "tickets": rows,
    }


def _emit_human(report: dict[str, Any]) -> None:
    s = report["summary"]
    for row in report["tickets"]:
        print(
            f"[map-brief-eval] {row['ticket']}: off={row['off_bytes']}B (~{row['off_tokens']:.1f} tok) "
            f"on={row['on_bytes']}B (~{row['on_tokens']:.1f} tok) "
            f"delta=+{row['delta_bytes']}B adrs={row['adrs_count']} pages={row['pages_count']} tests={row['tests_count']}",
            file=sys.stderr,
        )
    det = "yes" if report["determinism"]["deterministic"] else "no"
    sys.stderr.flush()
    print(
        f"GO: map-brief-eval — {s['tickets_total']} tickets ({s['tickets_with_perimeter']} with perimeter), "
        f"off={s['total_off_bytes']}B (~{s['total_off_tokens']:.1f} tok) -> "
        f"on={s['total_on_bytes']}B (~{s['total_on_tokens']:.1f} tok), "
        f"delta=+{s['total_delta_bytes']}B (+{s['total_delta_tokens']:.1f} tok), deterministic={det}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="core/map_brief_eval.py")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--rev", default="HEAD")
    parser.add_argument("--map", dest="map_path", default=os.path.join(".crew", "map.sqlite"))
    parser.add_argument("--tickets", default="tickets")
    parser.add_argument("--exclude", default="docs/PLAN.md docs/plans/* .claude/*")
    parser.add_argument("--worker-brief", default=None)
    parser.add_argument("--write-map", action="store_true")
    parser.add_argument("--no-determinism-check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = evaluate_map_brief(
            repo=args.repo,
            rev=args.rev,
            map_path=args.map_path,
            tickets=args.tickets,
            exclude=args.exclude.split(),
            worker_brief=args.worker_brief,
            write_map=args.write_map,
            check_determinism=not args.no_determinism_check,
        )
    except (EvalRefused, project_map.Refused, OSError, ValueError) as exc:
        print(f"NO-GO: map-brief-eval — {exc}")
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _emit_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
