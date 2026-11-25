# SPDX-License-Identifier: Apache-2.0
"""Read-only multi-repo fleet portfolio federation hook (docs/adr/0138): stdlib only.

Reads each target repository's contract (`workload.env` under the default
contract directory, `.crew/attached` fallback, or repo root) and runtime
telemetry (`.crew/logbook.json`, optional `.crew/dora.json` when logbook
omits `dora`, and read-only mainline state if present), never writing,
locking, or modifying any target repository.

    python3 core/portfolio.py [--repos <p1>,<p2>] [--repo <path>] [--json] [--contract]

Emits `lights-out/portfolio/1.0` JSON with `--json`, or a compact fleet
summary line `<n> repos, <held> held, <your_turn> your-turn` with `--contract`.
Strictly vendor-free, app-free, and model-free (ci/vendor-free.sh,
ci/app-free.sh, ci/model-free.sh).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

try:
    from . import contract_spec
except ImportError:  # run as a script: core/ is sys.path[0]
    import contract_spec

SCHEMA = "lights-out/portfolio/1.0"
DORA_KEYS: tuple[str, ...] = (
    "deployment_frequency",
    "lead_time",
    "change_fail_rate",
    "recovery_time",
)

_TERMINAL_TICKET_STATES = frozenset(
    {
        "rolled-back",
        "closed",
        "done",
        "completed",
        "promoted",
        "rejected",
        "cancelled",
        "aborted",
    }
)
_KIND_URGENCY = {
    "go-for-launch": 0,
    "ticket-held": 0,
    "needs-human": 1,
    "stranded": 2,
    "ticket-question": 3,
    "intake": 4,
}
_PRIORITY_RANK = {
    "p0": 0,
    "critical": 0,
    "p1": 1,
    "high": 1,
    "p2": 2,
    "medium": 2,
    "p3": 3,
    "low": 3,
}


def _is_unread(value: Any) -> bool:
    return isinstance(value, dict) and value.get("ok") is False


def _unread_reason(value: Any, default: str = "unreadable") -> str:
    if isinstance(value, dict):
        return str(value.get("error") or default)
    return default


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _priority_rank(value: Any) -> int:
    if not isinstance(value, str):
        return 4
    return _PRIORITY_RANK.get(value.strip().lower(), 4)


def _clean_line(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _read_json_readonly(path: Path) -> tuple[Any | None, str | None]:
    """Return (parsed_json, error_or_none); returns (None, None) when file is absent."""
    if not path.exists():
        return None, None
    if not path.is_file():
        return None, f"{path} is not a regular file"
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"cannot read {path}: {exc}"
    try:
        return json.loads(raw), None
    except ValueError as exc:
        return None, f"{path} is not valid JSON: {exc}"


def _find_workload_env(repo_root: Path) -> Path | None:
    """Locate a repository's workload.env read-only without literal app assumptions."""
    contract_dir = contract_spec.DEFAULT_CONTRACT_DIRNAME
    candidates: list[Path] = [
        repo_root / contract_dir / "workload.env",
        repo_root / ".crew" / "attached" / contract_dir / "workload.env",
        repo_root / ".crew" / "attached" / "workload.env",
    ]
    attached_marker = repo_root / ".crew" / "attached"
    if attached_marker.is_file():
        try:
            target_text = attached_marker.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            target_text = ""
        if target_text:
            target_path = Path(target_text)
            if not target_path.is_absolute():
                target_path = repo_root / target_path
            candidates.extend(
                [
                    target_path / contract_dir / "workload.env",
                    target_path / "workload.env",
                ]
            )
    candidates.append(repo_root / "workload.env")

    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def _read_mainline_from_sqlite(db_path: Path) -> bool | None:
    if not db_path.is_file():
        return None
    abs_uri = quote(os.path.abspath(os.fspath(db_path)))
    for suffix in ("mode=ro", "mode=ro&immutable=1"):
        try:
            conn = sqlite3.connect(f"file:{abs_uri}?{suffix}", uri=True)
            try:
                has_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
                ).fetchone()
                if not has_table:
                    return None
                row = conn.execute(
                    "SELECT body FROM documents WHERE collection = 'mainline' AND id = 'main'"
                ).fetchone()
                if not row:
                    return None
                doc = json.loads(row[0])
                if isinstance(doc, dict):
                    if doc.get("state") == "red" or doc.get("red") is True or doc.get("verdict") == "NO-GO":
                        return True
                    if doc.get("state") == "green":
                        return False
                return None
            finally:
                conn.close()
        except (sqlite3.OperationalError, sqlite3.DatabaseError, ValueError):
            continue
    return None


def _detect_mainline_red(repo_root: Path, book: dict[str, Any] | None) -> bool:
    if isinstance(book, dict):
        if isinstance(book.get("mainline_red"), bool):
            return bool(book["mainline_red"])
        ml = book.get("mainline")
        if isinstance(ml, dict) and not _is_unread(ml):
            if ml.get("state") == "red" or ml.get("red") is True or ml.get("verdict") == "NO-GO":
                return True
            if ml.get("state") == "green":
                return False

    crew_dir = repo_root / ".crew"
    from_db = _read_mainline_from_sqlite(crew_dir / "state.sqlite")
    if from_db is not None:
        return from_db

    for rel in ("mainline.json", "state/mainline.main.json"):
        doc, err = _read_json_readonly(crew_dir / rel)
        if err is None and isinstance(doc, dict):
            if doc.get("state") == "red" or doc.get("red") is True or doc.get("verdict") == "NO-GO":
                return True
            if doc.get("state") == "green":
                return False
    return False


def _extract_spend(book: dict[str, Any]) -> tuple[float | None, float | None]:
    spend = book.get("spend")
    if not isinstance(spend, dict) or _is_unread(spend):
        return None, None

    total_obj = spend.get("total")
    spend_total: float | None = None
    if isinstance(total_obj, dict) and _is_number(total_obj.get("cost_usd")):
        spend_total = round(float(total_obj["cost_usd"]), 6)

    spend_day: float | None = None
    burn = spend.get("burn")
    if isinstance(burn, dict) and _is_number(burn.get("spent_usd")):
        spend_day = round(float(burn["spent_usd"]), 6)
    elif isinstance(spend.get("days"), dict) and spend["days"]:
        latest_day = max(str(k) for k in spend["days"].keys())
        day_entry = spend["days"].get(latest_day)
        if isinstance(day_entry, dict) and _is_number(day_entry.get("cost_usd")):
            spend_day = round(float(day_entry["cost_usd"]), 6)
    elif isinstance(total_obj, dict) and total_obj.get("runs") == 0 and total_obj.get("unknown", 0) == 0:
        spend_day = 0.0

    return spend_day, spend_total


def _extract_dora(repo_root: Path, book: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_dora = book.get("dora")
    if raw_dora is None:
        file_dora, err = _read_json_readonly(repo_root / ".crew" / "dora.json")
        if err is None and isinstance(file_dora, dict):
            raw_dora = file_dora
    if not isinstance(raw_dora, dict) or _is_unread(raw_dora):
        return {}
    measures = raw_dora.get("measures")
    if not isinstance(measures, dict):
        return {}
    measured: dict[str, dict[str, Any]] = {}
    for key in DORA_KEYS:
        entry = measures.get(key)
        if isinstance(entry, dict) and entry.get("measured") is True:
            measured[key] = dict(entry)
    return measured


def _answered_tickets(book: dict[str, Any]) -> set[str]:
    releases = book.get("releases")
    if not isinstance(releases, list):
        return set()
    return {
        str(r.get("ticket"))
        for r in releases
        if isinstance(r, dict) and isinstance(r.get("answer"), dict) and r.get("ticket")
    }


def _count_open_tickets(book: dict[str, Any]) -> int | None:
    raw_tickets = book.get("tickets")
    if raw_tickets is None or _is_unread(raw_tickets) or not isinstance(raw_tickets, list):
        return None
    answered = _answered_tickets(book)
    open_count = 0
    for ticket in raw_tickets:
        if not isinstance(ticket, dict):
            continue
        state = str(ticket.get("state") or "").strip()
        tid = str(ticket.get("id") or "")
        outcome = ticket.get("outcome") if isinstance(ticket.get("outcome"), dict) else {}
        if state in _TERMINAL_TICKET_STATES:
            continue
        if state == "held" and (outcome.get("judge") == "human" or tid in answered):
            continue
        open_count += 1
    return open_count


def _count_held_canaries(book: dict[str, Any]) -> int | None:
    launch = book.get("launch")
    releases = book.get("releases")
    raw_tickets = book.get("tickets")

    launch_rec = launch.get("record") if isinstance(launch, dict) and not _is_unread(launch) else None
    launch_unreadable = _is_unread(launch) or _is_unread(launch_rec)
    if launch_unreadable and _is_unread(releases) and _is_unread(raw_tickets):
        return None
    if launch is None and releases is None and raw_tickets is None:
        return 0

    held_versions: set[str] = set()
    answered_versions: set[str] = set()
    if isinstance(launch_rec, dict):
        ver = str(launch_rec.get("version") or "__launch__")
        if "promoted" in launch_rec or "rejected" in launch_rec:
            answered_versions.add(ver)
        elif launch_rec.get("verdict") == "held":
            held_versions.add(ver)

    if isinstance(releases, list):
        for rel in releases:
            if not isinstance(rel, dict):
                continue
            ver = str(rel.get("version") or "")
            if rel.get("verdict") == "held":
                if isinstance(rel.get("answer"), dict):
                    if ver:
                        answered_versions.add(ver)
                elif ver and ver not in answered_versions:
                    held_versions.add(ver)
                elif not ver and not held_versions:
                    held_versions.add("__release__")

    answered = _answered_tickets(book)
    if isinstance(raw_tickets, list):
        for ticket in raw_tickets:
            if not isinstance(ticket, dict):
                continue
            if str(ticket.get("state") or "") != "held":
                continue
            tid = str(ticket.get("id") or "")
            outcome = ticket.get("outcome") if isinstance(ticket.get("outcome"), dict) else {}
            if outcome.get("judge") == "human" or tid in answered:
                continue
            rel_obj = ticket.get("release") if isinstance(ticket.get("release"), dict) else {}
            ver = str(rel_obj.get("version") or "")
            if ver:
                if ver not in answered_versions:
                    held_versions.add(ver)
            elif not held_versions:
                held_versions.add(f"ticket:{tid}")

    return len(held_versions)


def _extract_your_turn_items(
    repo_name: str,
    repo_path: str,
    book: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Return (items, unreadable_all)."""
    launch = book.get("launch")
    raw_tickets = book.get("tickets")
    raw_intakes = book.get("intakes")
    launch_rec = launch.get("record") if isinstance(launch, dict) and not _is_unread(launch) else None

    if _is_unread(raw_tickets) and (_is_unread(launch) or _is_unread(launch_rec)) and _is_unread(raw_intakes):
        return [], True

    items: list[dict[str, Any]] = []
    if isinstance(launch_rec, dict) and launch_rec.get("verdict") == "held":
        if "promoted" not in launch_rec and "rejected" not in launch_rec:
            ver = _clean_line(launch_rec.get("version") or "?")
            pct = launch_rec.get("held_at")
            press = (
                "ready to press"
                if launch.get("pressable")
                else _clean_line(launch.get("disabled_reason") or "prepare brief")
            )
            stages = launch_rec.get("stages") if isinstance(launch_rec.get("stages"), list) else []
            last_stage_at = stages[-1].get("judged_at") if stages and isinstance(stages[-1], dict) else None
            items.append(
                {
                    "repo": repo_name,
                    "repo_path": repo_path,
                    "kind": "go-for-launch",
                    "id": ver,
                    "state": "held",
                    "action": "promote or rollback",
                    "priority": "P0",
                    "at": last_stage_at,
                    "summary": f"release {ver} held at {pct}% ({press})",
                }
            )

    answered = _answered_tickets(book)
    if isinstance(raw_tickets, list):
        for ticket in raw_tickets:
            if not isinstance(ticket, dict):
                continue
            tid = _clean_line(ticket.get("id") or "?")
            state = _clean_line(ticket.get("state") or "unopened")
            outcome = ticket.get("outcome") if isinstance(ticket.get("outcome"), dict) else {}
            prio = ticket.get("priority") if isinstance(ticket.get("priority"), str) else None
            updated_at = ticket.get("updated_at") if isinstance(ticket.get("updated_at"), str) else None

            if state == "questions":
                qs = [q for q in (ticket.get("questions") or []) if isinstance(q, dict) and not q.get("answer")]
                qtext = _clean_line(qs[-1].get("question")) if qs else "open question"
                asked_at = qs[-1].get("asked_at") if qs and isinstance(qs[-1].get("asked_at"), str) else updated_at
                items.append(
                    {
                        "repo": repo_name,
                        "repo_path": repo_path,
                        "kind": "ticket-question",
                        "id": tid,
                        "state": state,
                        "action": "answer question",
                        "priority": prio,
                        "at": asked_at,
                        "summary": f"ticket {tid} [questions]: {qtext}",
                    }
                )
            elif state == "needs-human":
                why = _clean_line(outcome.get("line") or "rework or spend cap reached")
                items.append(
                    {
                        "repo": repo_name,
                        "repo_path": repo_path,
                        "kind": "needs-human",
                        "id": tid,
                        "state": state,
                        "action": "human decision",
                        "priority": prio,
                        "at": updated_at,
                        "summary": f"ticket {tid} [needs-human]: {why}",
                    }
                )
            elif state == "held" and outcome.get("judge") != "human" and tid not in answered:
                items.append(
                    {
                        "repo": repo_name,
                        "repo_path": repo_path,
                        "kind": "ticket-held",
                        "id": tid,
                        "state": state,
                        "action": "go-for-launch",
                        "priority": prio or "P0",
                        "at": updated_at,
                        "summary": f"ticket {tid} [held]: waiting for go-for-launch",
                    }
                )
            elif outcome.get("verdict") == "stranded" or isinstance(ticket.get("wait"), dict):
                wait = ticket.get("wait") if isinstance(ticket.get("wait"), dict) else {}
                why = _clean_line(outcome.get("line") or ("waiting on overlapping perimeter" if wait else "step failed"))
                items.append(
                    {
                        "repo": repo_name,
                        "repo_path": repo_path,
                        "kind": "stranded",
                        "id": tid,
                        "state": state,
                        "action": "resume",
                        "priority": prio,
                        "at": wait.get("since") if isinstance(wait.get("since"), str) else updated_at,
                        "summary": f"ticket {tid} [{state}, stranded]: {why}",
                    }
                )

    if isinstance(raw_intakes, list):
        for intake in raw_intakes:
            if not isinstance(intake, dict):
                continue
            iid = _clean_line(intake.get("id") or "?")
            istate = _clean_line(intake.get("state") or "intake")
            if istate in ("intake-questions", "proposed") or intake.get("stranded"):
                action = (
                    "answer intake"
                    if istate == "intake-questions"
                    else ("confirm intake" if istate == "proposed" else "resume intake")
                )
                qs = [q for q in (intake.get("questions") or []) if isinstance(q, dict) and not q.get("answer")]
                detail = _clean_line(qs[-1].get("question")) if qs else _clean_line(intake.get("text") or istate)
                items.append(
                    {
                        "repo": repo_name,
                        "repo_path": repo_path,
                        "kind": "intake",
                        "id": iid,
                        "state": istate,
                        "action": action,
                        "priority": None,
                        "at": intake.get("at") if isinstance(intake.get("at"), str) else None,
                        "summary": f"intake {iid} [{istate}]: {detail}",
                    }
                )

    return items, (_is_unread(raw_tickets) and not items)


def _inspect_repo(raw_path: str | os.PathLike[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    abs_path = os.path.abspath(os.fspath(raw_path))
    repo_root = Path(abs_path)
    repo_name = repo_root.name or abs_path

    if not repo_root.is_dir():
        return (
            {
                "name": repo_name,
                "path": abs_path,
                "status": "unread",
                "error": f"repository path does not exist or is not a directory: {abs_path}",
                "contract_path": None,
                "contract_version": None,
                "contract_valid": False,
                "contract_errors": [f"repository path does not exist: {abs_path}"],
                "open_tickets": None,
                "held_canaries": None,
                "your_turn_count": None,
                "mainline_red": False,
                "spend_day_usd": None,
                "spend_total_usd": None,
                "dora": {},
            },
            [],
        )

    contract_path = _find_workload_env(repo_root)
    if contract_path is None:
        contract_valid = False
        contract_version: str | None = None
        contract_errors = [f"no {contract_spec.DEFAULT_CONTRACT_DIRNAME}/workload.env contract found"]
        contract_path_str: str | None = None
    else:
        validation = contract_spec.validate_workload_contract(
            contract_path,
            contract_dir=contract_path.parent,
            check_compose=False,
        )
        contract_valid = bool(validation["valid"])
        contract_version = validation["contract_version"]
        contract_errors = list(validation["errors"])
        contract_path_str = contract_path.as_posix()

    logbook_path = repo_root / ".crew" / "logbook.json"
    book, lb_err = _read_json_readonly(logbook_path)
    if lb_err is not None:
        read_error: str | None = lb_err
    elif book is None:
        read_error = f"missing logbook: {logbook_path.as_posix()}"
    elif not isinstance(book, dict):
        read_error = f"{logbook_path.as_posix()} is not a JSON object"
    else:
        schema = book.get("schema")
        if not isinstance(schema, str) or not schema.startswith("lights-out/logbook/1."):
            read_error = f"{logbook_path.as_posix()} has unsupported schema {schema!r}"
        else:
            read_error = None

    if read_error is not None or not isinstance(book, dict):
        mainline_red = _detect_mainline_red(repo_root, None)
        return (
            {
                "name": repo_name,
                "path": abs_path,
                "status": "unread",
                "error": read_error or f"{logbook_path.as_posix()} is not a JSON object",
                "contract_path": contract_path_str,
                "contract_version": contract_version,
                "contract_valid": contract_valid,
                "contract_errors": contract_errors,
                "open_tickets": None,
                "held_canaries": None,
                "your_turn_count": None,
                "mainline_red": mainline_red,
                "spend_day_usd": None,
                "spend_total_usd": None,
                "dora": {},
            },
            [],
        )

    open_tickets = _count_open_tickets(book)
    held_canaries = _count_held_canaries(book)
    your_turn_items, yt_unread = _extract_your_turn_items(repo_name, abs_path, book)
    your_turn_count = None if yt_unread else len(your_turn_items)
    mainline_red = _detect_mainline_red(repo_root, book)
    spend_day_usd, spend_total_usd = _extract_spend(book)
    dora = _extract_dora(repo_root, book)

    return (
        {
            "name": repo_name,
            "path": abs_path,
            "status": "ok",
            "error": None,
            "contract_path": contract_path_str,
            "contract_version": contract_version,
            "contract_valid": contract_valid,
            "contract_errors": contract_errors,
            "open_tickets": open_tickets,
            "held_canaries": held_canaries,
            "your_turn_count": your_turn_count,
            "mainline_red": mainline_red,
            "spend_day_usd": spend_day_usd,
            "spend_total_usd": spend_total_usd,
            "dora": dora,
        },
        your_turn_items,
    )


def _aggregate_dora(repos: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate DORA metrics over measured repos only; never average over unmeasured."""
    out: dict[str, dict[str, Any]] = {}

    freq_entries = [r["dora"]["deployment_frequency"] for r in repos if "deployment_frequency" in (r.get("dora") or {})]
    per_days = [float(e["per_day"]) for e in freq_entries if _is_number(e.get("per_day"))]
    counts = [int(e["count"]) for e in freq_entries if isinstance(e.get("count"), int) and not isinstance(e.get("count"), bool)]
    if per_days:
        out["deployment_frequency"] = {
            "measured": True,
            "repos_measured": len(per_days),
            "mean_per_day": round(sum(per_days) / len(per_days), 6),
            "total_count": sum(counts),
        }
    else:
        out["deployment_frequency"] = {"measured": False, "repos_measured": 0}

    for key in ("lead_time", "recovery_time"):
        entries = [r["dora"][key] for r in repos if key in (r.get("dora") or {})]
        medians = [float(e["median_s"]) for e in entries if _is_number(e.get("median_s"))]
        if medians:
            out[key] = {
                "measured": True,
                "repos_measured": len(medians),
                "mean_median_s": round(sum(medians) / len(medians), 2),
            }
        else:
            out[key] = {"measured": False, "repos_measured": 0}

    cfr_entries = [r["dora"]["change_fail_rate"] for r in repos if "change_fail_rate" in (r.get("dora") or {})]
    rates = [float(e["rate"]) for e in cfr_entries if _is_number(e.get("rate"))]
    if rates:
        out["change_fail_rate"] = {
            "measured": True,
            "repos_measured": len(rates),
            "mean_rate": round(sum(rates) / len(rates), 6),
        }
    else:
        out["change_fail_rate"] = {"measured": False, "repos_measured": 0}

    return out


def _split_repo_tokens(raw_value: str) -> list[str]:
    out: list[str] = []
    for chunk in raw_value.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        if ":" in piece and not os.path.exists(piece):
            for sub in piece.split(":"):
                sub_clean = sub.strip()
                if sub_clean:
                    out.append(sub_clean)
        else:
            out.append(piece)
    return out


def resolve_repo_paths(
    repos: Sequence[str | os.PathLike[str]] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Resolve and deduplicate target repository paths from args, PORTFOLIO_REPOS, or '.'."""
    cur_env = os.environ if env is None else env
    raw_list: list[str] = []
    if repos:
        for item in repos:
            raw_list.extend(_split_repo_tokens(os.fspath(item)))
    elif cur_env.get("PORTFOLIO_REPOS", "").strip():
        raw_list.extend(_split_repo_tokens(cur_env["PORTFOLIO_REPOS"]))
    else:
        raw_list.append(".")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in raw_list:
        abs_p = os.path.abspath(item)
        real_p = os.path.realpath(abs_p)
        if real_p in seen:
            continue
        seen.add(real_p)
        deduped.append(abs_p)
    return deduped


def collect(
    repos: Sequence[str | os.PathLike[str]] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Inspect all target repositories read-only and return lights-out/portfolio/1.0 payload."""
    paths = resolve_repo_paths(repos, env=env)
    repo_rows: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []
    for path in paths:
        summary, yt_items = _inspect_repo(path)
        repo_rows.append(summary)
        queue.extend(yt_items)

    queue.sort(
        key=lambda item: (
            _KIND_URGENCY.get(str(item.get("kind")), 9),
            _priority_rank(item.get("priority")),
            str(item.get("at") or "~"),
            str(item.get("repo") or ""),
            str(item.get("kind") or ""),
            str(item.get("id") or ""),
        )
    )

    healthy_repos = sum(
        1 for r in repo_rows if r["status"] == "ok" and r["contract_valid"] and not r["mainline_red"]
    )
    unread_repos = sum(1 for r in repo_rows if r["status"] != "ok")
    held_canaries_total = sum(int(r["held_canaries"]) for r in repo_rows if isinstance(r["held_canaries"], int))
    your_turn_total = sum(int(r["your_turn_count"]) for r in repo_rows if isinstance(r["your_turn_count"], int))
    mainline_red_repos = sum(1 for r in repo_rows if r["mainline_red"])
    spend_day_usd_total = round(
        sum(float(r["spend_day_usd"]) for r in repo_rows if _is_number(r["spend_day_usd"])),
        6,
    )
    spend_total_usd_total = round(
        sum(float(r["spend_total_usd"]) for r in repo_rows if _is_number(r["spend_total_usd"])),
        6,
    )
    contract_drift_repos = sum(1 for r in repo_rows if not r["contract_valid"])

    return {
        "schema": SCHEMA,
        "contract_version": contract_spec.CONTRACT_VERSION,
        "repo_count": len(repo_rows),
        "healthy_repos": healthy_repos,
        "unread_repos": unread_repos,
        "held_canaries_total": held_canaries_total,
        "your_turn_total": your_turn_total,
        "mainline_red_repos": mainline_red_repos,
        "spend_day_usd_total": spend_day_usd_total,
        "spend_total_usd_total": spend_total_usd_total,
        "contract_drift_repos": contract_drift_repos,
        "dora": _aggregate_dora(repo_rows),
        "your_turn_queue": queue,
        "repos": repo_rows,
    }


def summary_line(report: Mapping[str, Any]) -> str:
    """Return `<n> repos, <held> held, <your_turn> your-turn` for core/portfolio.sh."""
    return (
        f"{report.get('repo_count', 0)} repos, "
        f"{report.get('held_canaries_total', 0)} held, "
        f"{report.get('your_turn_total', 0)} your-turn"
    )


def format_table(report: Mapping[str, Any]) -> str:
    """Format a compact fixed-width fleet overview table for stderr/terminal output."""
    headers = ["REPO", "STATUS", "CONTRACT", "OPEN", "HELD", "YOUR-TURN", "MAIN", "DAY-USD", "TOTAL-USD"]
    rows: list[list[str]] = []
    for r in report.get("repos") or []:
        c_txt = f"v{r['contract_version']}" if r.get("contract_valid") else "DRIFT"
        open_txt = "-" if r.get("open_tickets") is None else str(r["open_tickets"])
        held_txt = "-" if r.get("held_canaries") is None else str(r["held_canaries"])
        yt_txt = "-" if r.get("your_turn_count") is None else str(r["your_turn_count"])
        main_txt = "RED" if r.get("mainline_red") else "green"
        day_txt = "-" if r.get("spend_day_usd") is None else f"${r['spend_day_usd']:.2f}"
        tot_txt = "-" if r.get("spend_total_usd") is None else f"${r['spend_total_usd']:.2f}"
        rows.append([str(r["name"]), str(r["status"]), c_txt, open_txt, held_txt, yt_txt, main_txt, day_txt, tot_txt])

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = min(max(widths[i], len(cell)), 32)

    def _fmt(vals: Sequence[str]) -> str:
        return "  ".join(v[: widths[i]].ljust(widths[i]) for i, v in enumerate(vals))

    lines = [
        f"Portfolio ({report.get('schema')}): {summary_line(report)} | "
        f"healthy={report.get('healthy_repos', 0)}/{report.get('repo_count', 0)} | "
        f"drift={report.get('contract_drift_repos', 0)} | "
        f"red-main={report.get('mainline_red_repos', 0)} | "
        f"spend-day=${report.get('spend_day_usd_total', 0.0):.2f} | "
        f"spend-total=${report.get('spend_total_usd_total', 0.0):.2f}",
        _fmt(headers),
        _fmt(["-" * w for w in widths]),
    ]
    for row in rows:
        lines.append(_fmt(row))
    queue = report.get("your_turn_queue") or []
    if queue:
        lines.append(f"Your-turn queue ({len(queue)}):")
        for item in queue[:10]:
            lines.append(f"  - [{item['repo']}] {item['action']}: {item['summary']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repos",
        action="append",
        default=[],
        help="Comma-separated repository paths (may be repeated)",
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="Single repository path (may be repeated)",
    )
    parser.add_argument("--json", action="store_true", help="Emit lights-out/portfolio/1.0 JSON")
    parser.add_argument(
        "--contract",
        action="store_true",
        help="Emit fleet details to stderr and one summary line to stdout",
    )
    parser.add_argument("paths", nargs="*", help="Optional positional repository paths")
    return parser


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    explicit_repos = [*args.repos, *args.repo, *args.paths]
    report = collect(explicit_repos or None, env=env)

    if args.contract:
        detail = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) if args.json else format_table(report)
        print(detail, file=sys.stderr)
        print(summary_line(report), file=sys.stdout)
        return 0

    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False))
    else:
        print(format_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
