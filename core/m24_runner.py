"""Deterministic M24 benchmark evaluator and logbook ingester (docs/adr/0120): stdlib only.

    python3 core/m24_runner.py evaluate [--data <dataset.json>] [--logbook <file> ...] [--min-providers 3] [--allow-incomplete] [--json]
    python3 core/m24_runner.py ingest-logbook --logbook <file> [--data <dataset.json>] [--out <out.json>] [--handoff <text>] [--replace] [--json]

Evaluates multi-provider benchmark datasets and run logbooks against
docs/PLAN.md §14 (>= 2/3 auto-mergeable PRs without human code edits across at
least M24_MIN_PROVIDERS=3 providers), reporting `pass` (exit 0), `fail` (exit 1),
or `incomplete` (exit 3, listing `not_run` providers without fabricating data).
Strictly vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Sequence

try:
    from . import scoreboard
except ImportError:  # run as a script: core/ is sys.path[0]
    import scoreboard

# Provenance, external/decision (docs/PLAN.md §14, docs/adr/0120): M24 requires
# evaluating the ticket suite across at least three independent providers.
M24_MIN_PROVIDERS = 3
# Provenance, external (docs/PLAN.md §14): exit criterion requires at least
# two out of three (2/3) auto-mergeable PRs without human code edits.
M24_PASS_NUMERATOR = 2
M24_PASS_DENOMINATOR = 3

EVAL_SCHEMA = "lights-out/m24-evaluation/1"
DATASET_SCHEMA = scoreboard.SCHEMA
DEFAULT_DATASET = os.path.join("docs", "scoreboard", "m24-tenacity.json")

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_INCOMPLETE = 3


def is_auto_mergeable(row: dict[str, Any]) -> bool:
    """True when the row merged autonomously without human code edits."""
    return bool(row.get("merged")) and not bool(row.get("human_edits") or row.get("human_edited"))


def meets_threshold(
    auto_merged: int,
    total: int,
    numerator: int = M24_PASS_NUMERATOR,
    denominator: int = M24_PASS_DENOMINATOR,
) -> bool:
    """Exact rational comparison: auto_merged / total >= numerator / denominator."""
    if total <= 0 or denominator <= 0:
        return False
    return (auto_merged * denominator) >= (total * numerator)


def _read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        raise scoreboard.Refused(f"{path}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise scoreboard.Refused(f"{path} is not JSON: {exc}") from exc


def validate_logbook(book: Any, name: str) -> None:
    """Verify logbook schema and non-empty commit provenance before ingestion."""
    if not isinstance(book, dict):
        raise scoreboard.Refused(f"{name}: logbook is not a JSON object")
    schema = str(book.get("schema") or "")
    if not schema.startswith("lights-out/logbook/"):
        raise scoreboard.Refused(f"{name}: schema {schema!r} is not lights-out/logbook/1.x")
    src = book.get("source")
    commit = str(src.get("commit") or "").strip() if isinstance(src, dict) else ""
    if not commit or commit in ("?", "unknown"):
        raise scoreboard.Refused(f"{name}: source.commit is missing; cannot record source.run provenance")


def _extract_validated_logbook_rows(
    book: Any,
    name: str,
    handoff: str | None = None,
    repository: str | None = None,
    kind: str | None = None,
    phase: str | None = None,
    require_non_empty: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    validate_logbook(book, name)
    rows, replayed = scoreboard.from_logbook(book, name)
    if require_non_empty and not rows:
        raise scoreboard.Refused(
            f"{name}: no non-replayed model runs found ({replayed} replayed ticket(s) skipped)"
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        src = dict(item.get("source") or {})
        if handoff:
            src["handoff"] = handoff.strip()
        item["source"] = src
        if repository and not item.get("repository"):
            item["repository"] = repository
        if kind and not item.get("kind"):
            item["kind"] = kind
        if phase and not item.get("phase"):
            item["phase"] = phase
        for field in ("ticket", "provider", "harness", "model"):
            if not str(item.get(field) or "").strip():
                raise scoreboard.Refused(
                    f"{name}: ticket {item.get('ticket') or '?'} is missing required {field} provenance"
                )
        for part in ("handoff", "run"):
            val = str(src.get(part) or "").strip()
            if not val or (part == "run" and val.startswith("?")):
                raise scoreboard.Refused(f"{name}: ticket {item.get('ticket')} has invalid source.{part}")
        out.append(item)
    return out, replayed


def evaluate_dataset(
    data: dict[str, Any],
    logbooks: Sequence[tuple[str, dict[str, Any]]] | None = None,
    min_providers: int = M24_MIN_PROVIDERS,
    numerator: int = M24_PASS_NUMERATOR,
    denominator: int = M24_PASS_DENOMINATOR,
) -> dict[str, Any]:
    """Evaluate a curated dataset + optional logbooks against PLAN.md §14."""
    if min_providers < 1:
        raise ValueError(f"min_providers must be >= 1, got {min_providers}")
    if numerator < 1 or denominator < 1 or numerator > denominator:
        raise ValueError(f"invalid fraction {numerator}/{denominator}")

    rows, _, not_run, scope = scoreboard.load_dataset(data)
    all_rows = list(rows)
    replayed_skipped = 0
    for name, book in logbooks or []:
        more, rep = _extract_validated_logbook_rows(book, name, require_non_empty=False)
        all_rows.extend(more)
        replayed_skipped += rep

    all_tickets = sorted({str(r["ticket"]) for r in all_rows if r.get("ticket")})
    providers_order = list(dict.fromkeys(str(r["provider"]) for r in all_rows if r.get("provider")))
    effective_not_run = [dict(n) for n in not_run if n.get("provider") not in providers_order]

    provider_reports: list[dict[str, Any]] = []
    for prov in providers_order:
        p_rows = [r for r in all_rows if r.get("provider") == prov]
        total = len(p_rows)
        merged_count = sum(1 for r in p_rows if r.get("merged"))
        human_edits = sum(1 for r in p_rows if r.get("human_edits") or r.get("human_edited"))
        auto_merged = sum(1 for r in p_rows if is_auto_mergeable(r))
        first_attempt = sum(1 for r in p_rows if is_auto_mergeable(r) and not r.get("reworks"))
        reworks = sum(int(r.get("reworks") or 0) for r in p_rows)
        budget_stops = sum(
            1 for r in p_rows if (r.get("stop") or {}).get("kind") in scoreboard.BUDGET_STOPS
        )
        seen_tickets = {str(r["ticket"]) for r in p_rows if r.get("ticket")}
        missing_tickets = [t for t in all_tickets if t not in seen_tickets]
        passed = meets_threshold(auto_merged, total, numerator, denominator)
        provider_reports.append(
            {
                "provider": prov,
                "harnesses": sorted({str(r["harness"]) for r in p_rows if r.get("harness")}),
                "models": sorted({str(r["model"]) for r in p_rows if r.get("model")}),
                "tickets": total,
                "merged": merged_count,
                "auto_merged": auto_merged,
                "human_edits": human_edits,
                "first_attempt": first_attempt,
                "reworks": reworks,
                "budget_stops": budget_stops,
                "rate": round(auto_merged / total, 6) if total else 0.0,
                "passed": passed,
                "missing_tickets": missing_tickets,
            }
        )

    ticket_reports: list[dict[str, Any]] = []
    for tid in all_tickets:
        t_rows = [r for r in all_rows if r.get("ticket") == tid]
        t_total = len(t_rows)
        t_auto = sum(1 for r in t_rows if is_auto_mergeable(r))
        ticket_reports.append(
            {
                "ticket": tid,
                "providers_run": t_total,
                "auto_merged": t_auto,
                "rate": round(t_auto / t_total, 6) if t_total else 0.0,
                "meets_threshold": meets_threshold(t_auto, t_total, numerator, denominator),
            }
        )

    failed_providers = [p["provider"] for p in provider_reports if not p["passed"]]
    incomplete_coverage = [p["provider"] for p in provider_reports if p["missing_tickets"]]

    if failed_providers:
        verdict = "fail"
        reason = (
            f"provider(s) below {numerator}/{denominator} auto-mergeable threshold: "
            + ", ".join(failed_providers)
        )
    elif len(provider_reports) < min_providers or incomplete_coverage:
        verdict = "incomplete"
        parts = []
        if len(provider_reports) < min_providers:
            parts.append(f"{len(provider_reports)}/{min_providers} providers run")
        if effective_not_run:
            parts.append("not run: " + ", ".join(str(n.get("provider")) for n in effective_not_run))
        if incomplete_coverage:
            parts.append("incomplete ticket coverage: " + ", ".join(incomplete_coverage))
        reason = "; ".join(parts) if parts else "missing required benchmark runs"
    else:
        verdict = "pass"
        reason = (
            f"{len(provider_reports)} providers >= {min_providers} met "
            f"{numerator}/{denominator} auto-mergeable criterion across {len(all_tickets)} ticket(s)"
        )

    return {
        "schema": EVAL_SCHEMA,
        "scope": scope,
        "verdict": verdict,
        "reason": reason,
        "criterion": {
            "numerator": numerator,
            "denominator": denominator,
            "min_ratio": round(numerator / denominator, 6),
            "min_providers": min_providers,
        },
        "providers_run": len(provider_reports),
        "providers_passed": sum(1 for p in provider_reports if p["passed"]),
        "providers_failed": len(failed_providers),
        "tickets_total": len(all_tickets),
        "providers": provider_reports,
        "tickets": ticket_reports,
        "not_run": effective_not_run,
        "replayed_skipped": replayed_skipped,
    }


def ingest_logbooks(
    logbooks: Sequence[tuple[str, dict[str, Any]]],
    dataset: dict[str, Any] | None = None,
    handoff: str | None = None,
    scope: str | None = None,
    repository: str | None = None,
    kind: str | None = None,
    phase: str | None = None,
    replace: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Validate real logbook runs and merge rows into a lights-out/scoreboard-data/1 dataset."""
    if not logbooks:
        raise scoreboard.Refused("ingest-logbook requires at least one logbook")

    if dataset is not None:
        base_rows, totals, not_run, base_scope = scoreboard.load_dataset(dataset)
        merged_rows = [dict(r) for r in base_rows]
        final_totals = [dict(t) for t in totals]
        final_not_run = [dict(n) for n in not_run]
        final_scope = scope.strip() if scope and scope.strip() else base_scope
    else:
        merged_rows = []
        final_totals = []
        final_not_run = []
        final_scope = (
            scope.strip()
            if scope and scope.strip()
            else f"Ingested M24 benchmark runs from {len(logbooks)} logbook(s)."
        )

    ingested: list[dict[str, Any]] = []
    replayed_total = 0
    index = {
        (r.get("ticket"), r.get("provider"), r.get("harness"), r.get("model")): i
        for i, r in enumerate(merged_rows)
    }

    for name, book in logbooks:
        new_rows, rep = _extract_validated_logbook_rows(
            book,
            name,
            handoff=handoff,
            repository=repository,
            kind=kind,
            phase=phase,
            require_non_empty=True,
        )
        replayed_total += rep
        for row in new_rows:
            key = (row.get("ticket"), row.get("provider"), row.get("harness"), row.get("model"))
            if key in index:
                if not replace:
                    raise scoreboard.Refused(
                        f"duplicate row for {row.get('ticket')} ({row.get('provider')}/{row.get('harness')}/{row.get('model')}); use --replace to overwrite"
                    )
                merged_rows[index[key]] = row
            else:
                index[key] = len(merged_rows)
                merged_rows.append(row)
            ingested.append(row)

    ingested_providers = {r["provider"] for r in ingested}
    final_not_run = [n for n in final_not_run if n.get("provider") not in ingested_providers]

    out_dataset = {
        "schema": DATASET_SCHEMA,
        "scope": final_scope,
        "rows": merged_rows,
        "totals": final_totals,
        "not_run": final_not_run,
    }
    scoreboard.load_dataset(out_dataset)
    return out_dataset, ingested, replayed_total


def format_evaluation_text(report: dict[str, Any]) -> str:
    """Format a deterministic human-readable M24 evaluation report."""
    lines = [
        f"m24: {report['verdict']} — {report['reason']}",
        f"criterion: >= {report['criterion']['numerator']}/{report['criterion']['denominator']} auto-mergeable across >= {report['criterion']['min_providers']} providers",
    ]
    for p in report["providers"]:
        status = "PASS" if p["passed"] else "FAIL"
        pct = round(p["rate"] * 100, 1)
        lines.append(
            f"- {p['provider']} ({'/'.join(p['harnesses'])} · {'/'.join(p['models'])}): "
            f"{p['auto_merged']}/{p['tickets']} ({pct}%) [{status}] "
            f"reworks={p['reworks']} budget_stops={p['budget_stops']} human_edits={p['human_edits']}"
        )
    for n in report["not_run"]:
        lines.append(f"- {n.get('provider')} ({n.get('harness')}): not_run — {n.get('why')}")
    if report["replayed_skipped"]:
        lines.append(f"- skipped {report['replayed_skipped']} replayed ticket(s)")
    return "\n".join(lines)


def _cmd_evaluate(args: argparse.Namespace) -> int:
    data = _read_json(args.data)
    logbook_paths = list(args.logbook or []) + list(args.logbooks or [])
    books = [(p, _read_json(p)) for p in logbook_paths]
    report = evaluate_dataset(data, logbooks=books, min_providers=args.min_providers)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_evaluation_text(report))
    if report["verdict"] == "pass":
        return EXIT_PASS
    if report["verdict"] == "incomplete":
        return EXIT_PASS if args.allow_incomplete else EXIT_INCOMPLETE
    return EXIT_FAIL


def _cmd_ingest(args: argparse.Namespace) -> int:
    logbook_paths = list(args.logbook or []) + list(args.logbooks or [])
    if not logbook_paths:
        print("m24-runner: ingest-logbook requires --logbook <file>", file=sys.stderr)
        return EXIT_USAGE
    books = [(p, _read_json(p)) for p in logbook_paths]
    existing = _read_json(args.data) if args.data else None
    dataset, ingested, replayed = ingest_logbooks(
        books,
        dataset=existing,
        handoff=args.handoff,
        scope=args.scope,
        repository=args.repository,
        kind=args.kind,
        phase=args.phase,
        replace=args.replace,
    )
    if args.out:
        parent = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(parent, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    if args.json:
        payload = {
            "schema": DATASET_SCHEMA,
            "ingested": len(ingested),
            "replayed_skipped": replayed,
            "total_rows": len(dataset["rows"]),
            "out": args.out,
            "dataset": dataset,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.out:
        print(f"ingested {len(ingested)} row(s) into {args.out} (total {len(dataset['rows'])})")
    else:
        print(json.dumps(dataset, ensure_ascii=False, indent=2))
    return EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or (args_list[0].startswith("-") and args_list[0] not in ("-h", "--help")):
        args_list = ["evaluate", *args_list]

    parser = argparse.ArgumentParser(prog="core/m24_runner.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_eval = sub.add_parser("evaluate", help="Evaluate M24 dataset and optional logbooks")
    p_eval.add_argument("--data", default=DEFAULT_DATASET, help="Curated dataset path (lights-out/scoreboard-data/1)")
    p_eval.add_argument("--logbook", action="append", default=[], help="Additional run logbook JSON")
    p_eval.add_argument("--logbooks", nargs="+", default=[], help="Additional run logbook JSON files")
    p_eval.add_argument("--min-providers", type=int, default=M24_MIN_PROVIDERS)
    p_eval.add_argument("--allow-incomplete", action="store_true")
    p_eval.add_argument("--json", action="store_true")

    p_ing = sub.add_parser("ingest-logbook", help="Validate and ingest real run logbooks into dataset rows")
    p_ing.add_argument("--logbook", action="append", default=[], help="Run logbook JSON file")
    p_ing.add_argument("--logbooks", nargs="+", default=[], help="Run logbook JSON files")
    p_ing.add_argument("--data", default=None, help="Existing dataset JSON to merge ingested rows into")
    p_ing.add_argument("--out", default=None, help="Output dataset JSON path")
    p_ing.add_argument("--handoff", default=None, help="Override source.handoff provenance label")
    p_ing.add_argument("--scope", default=None, help="Dataset scope statement")
    p_ing.add_argument("--repository", default=None, help="Optional repository name metadata")
    p_ing.add_argument("--kind", default=None, help="Optional ticket kind metadata")
    p_ing.add_argument("--phase", default=None, help="Optional benchmark phase metadata")
    p_ing.add_argument("--replace", action="store_true", help="Overwrite matching existing rows")
    p_ing.add_argument("--json", action="store_true")

    args = parser.parse_args(args_list)
    try:
        if args.cmd == "evaluate":
            return _cmd_evaluate(args)
        if args.cmd == "ingest-logbook":
            return _cmd_ingest(args)
    except (scoreboard.Refused, ValueError, OSError) as exc:
        print(f"m24-runner: {exc}", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
