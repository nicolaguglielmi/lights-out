"""Dual-mode guided end-to-end setup and lifecycle navigation (ADR 0103).

Stdlib only, strictly vendor-free, app-free, and model-free: provider metadata
is loaded dynamically from profiles/providers.json, contract paths come from
WORKLOAD_CONTRACT / --contract, and .env is inspected strictly for non-empty
variable presence without ever storing or printing secret values.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess  # nosec B404 - invokes core/doctor.sh as an argv list without shell=True
import sys
from pathlib import Path

TOTAL_STAGES = 6
MODES = ("junior", "expert")
PRESETS = ("junior", "strict", "custom")
PLACEHOLDER_VALUES = {"", "replace-me", "changeme", "todo"}
_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_SECRET_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
_EXACT_SECRETS = ("METRICS_AUTH_HEADER",)
_DECIMAL_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")

STAGES_META = (
    (1, "connect", "Connect & init contract"),
    (2, "provider", "Provider, harness & budget"),
    (3, "intake", "Intake & spec gate"),
    (4, "build", "Agentic build, 8 gates & rework"),
    (5, "release", "Canary deploy, SLO verify & human gate"),
    (6, "observe", "Observe, DORA, spend, evidence & postmortem"),
)

STAGE3_PAST = {
    "spec-ready", "opened", "pr-opened", "review", "ci-passed",
    "ci-failed", "needs-human", "merged", "main-green", "held", "rolled-back",
}
STAGE4_PAST = {"ci-passed", "merged", "main-green", "held", "rolled-back"}


class StartError(Exception):
    """One-line user-facing error rendered as NO-GO."""


class _CleanParser(argparse.ArgumentParser):
    """ArgumentParser that raises StartError instead of printing raw stderr usage."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise StartError(message)


def _is_secret_key(key: str) -> bool:
    return key.endswith(_SECRET_SUFFIXES) or key in _EXACT_SECRETS


def parse_plain_env(path: Path, *, reject_secrets: bool = False) -> dict[str, str]:
    """Read non-secret KEY=VALUE files (profiles, policy, workload, start-plan)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if reject_secrets and line.startswith("export "):
            line = line[7:].strip()
        match = _KEY_LINE.match(line)
        if match:
            key = match.group(1)
            if reject_secrets and _is_secret_key(key):
                raise StartError(f"{path} must never hold secret key {key}; move secrets to .env")
            val = match.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            out[key] = val
    return out


def parse_env_presence(path: Path) -> dict[str, bool]:
    """Return ONLY {var_name: is_non_empty_non_placeholder} from .env; never keep values."""
    out: dict[str, bool] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        match = _KEY_LINE.match(line)
        if not match:
            continue
        key = match.group(1)
        val = match.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val.strip().lower() not in PLACEHOLDER_VALUES and bool(val.strip())
    return out


def is_var_configured(dot_env_presence: dict[str, bool], var_name: str) -> bool:
    """Check whether VAR_NAME is non-empty and non-placeholder in .env or process env."""
    if not var_name:
        return True
    if dot_env_presence.get(var_name):
        return True
    proc = os.environ.get(var_name, "").strip()
    return bool(proc) and proc.lower() not in PLACEHOLDER_VALUES


def load_providers(root: Path) -> dict[str, dict]:
    """Load provider metadata from profiles/providers.json."""
    path = root / "profiles" / "providers.json"
    if not path.is_file():
        raise StartError(f"missing provider metadata: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StartError(f"cannot read {path}: {exc}") from exc
    providers = doc.get("providers") if isinstance(doc, dict) else None
    if not isinstance(providers, dict) or not providers:
        raise StartError(f"{path} has no providers map")
    return providers


def discover_adapters(root: Path, providers: dict[str, dict] | None = None) -> dict[str, list[str]]:
    """Discover adapters under adapters/ and optional cloud plugins under plugins/cloud/."""
    adapters_dir = root / "adapters"
    cloud_dir = root / "plugins" / "cloud"
    deliveries: list[str] = []
    harnesses: list[str] = []
    registries: list[str] = []
    flags: list[str] = []
    all_adapters: list[str] = []
    known_harnesses = {
        h
        for info in (providers or {}).values()
        for h in (info.get("harnesses") or [])
        if isinstance(h, str)
    }

    if adapters_dir.is_dir():
        for entry in sorted(adapters_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith(".") or entry.name == "outside":
                continue
            all_adapters.append(entry.name)
            if (entry / "deploy.sh").is_file():
                deliveries.append(entry.name)
            if (entry / "publish.sh").is_file() and (entry / "verify.sh").is_file():
                registries.append(entry.name)
            if (entry / "evaluate.sh").is_file() and (entry / "set.sh").is_file():
                flags.append(entry.name)
            if (
                entry.name in known_harnesses
                or (entry / "conformance.env").is_file()
                or (entry / "mcp.sh").is_file()
                or (entry / "thinking.env").is_file()
            ):
                harnesses.append(entry.name)

    cloud_plugins: list[str] = []
    cloud_slo_ingress: list[str] = []
    if cloud_dir.is_dir():
        for entry in sorted(cloud_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith(".") and (
                (entry / "deploy.sh").is_file() or (entry / "README.md").is_file()
            ):
                cloud_plugins.append(entry.name)
                if (entry / "slo-ingress.promql").is_file():
                    cloud_slo_ingress.append(entry.name)

    return {
        "all": all_adapters,
        "deliveries": deliveries,
        "harnesses": harnesses,
        "registries": registries,
        "flags": flags,
        "cloud_plugins": cloud_plugins,
        "cloud_slo_ingress": cloud_slo_ingress,
    }


def _resolve_contract_dir(root: Path, explicit: str | None) -> tuple[Path | None, str]:
    raw = explicit or os.environ.get("WORKLOAD_CONTRACT") or os.environ.get("WORKLOAD") or ""
    if not raw:
        return None, ""
    p = Path(raw)
    resolved = p if p.is_absolute() else (root / p)
    return resolved, raw


def inspect_contract(root: Path, explicit: str | None) -> dict:
    """Inspect target contract directory without hardcoding repo-local paths."""
    contract_path, raw_label = _resolve_contract_dir(root, explicit)
    if contract_path is None or not contract_path.is_dir():
        return {
            "path": raw_label,
            "exists": False,
            "has_workload_env": False,
            "has_baseline": False,
            "baseline": "",
            "has_compose": False,
            "has_policy_override": False,
            "has_mcp": False,
            "role_overrides": [],
            "attached": bool(os.environ.get("WORKLOAD_ATTACHED")),
        }

    wenv_path = contract_path / "workload.env"
    wenv = parse_plain_env(wenv_path)
    roles_dir = contract_path / "roles"
    role_overrides = (
        sorted(p.name for p in roles_dir.glob("*.md") if p.is_file())
        if roles_dir.is_dir()
        else []
    )
    return {
        "path": raw_label or str(contract_path),
        "exists": True,
        "has_workload_env": wenv_path.is_file(),
        "has_baseline": bool(wenv.get("MUTATION_BASELINE", "").strip()),
        "baseline": wenv.get("MUTATION_BASELINE", "").strip(),
        "has_compose": (contract_path / "compose.yaml").is_file(),
        "has_policy_override": (contract_path / "policy.env").is_file(),
        "has_mcp": (contract_path / "mcp.json").is_file(),
        "role_overrides": role_overrides,
        "attached": bool(os.environ.get("WORKLOAD_ATTACHED")),
    }


def _read_json_file(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StartError(f"{path} is not valid JSON: {exc}") from exc


def _read_state_docs(db_path: Path) -> list[dict]:
    if not db_path.is_file():
        return []
    docs: list[dict] = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "documents" not in tables:
                return []
            for doc_id, body in conn.execute(
                "SELECT id, body FROM documents WHERE collection = 'tickets' ORDER BY id"
            ).fetchall():
                try:
                    obj = json.loads(body)
                except ValueError as exc:
                    raise StartError(f"corrupt state document tickets/{doc_id}: {exc}") from exc
                if isinstance(obj, dict):
                    obj.setdefault("id", doc_id)
                    docs.append(obj)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise StartError(f"cannot read state database {db_path}: {exc}") from exc
    return docs


def _read_releases_log(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    entries: list[dict] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            entries.append(json.loads(raw))
        except ValueError as exc:
            raise StartError(f"{path} line {n} is not valid JSON: {exc}") from exc
    return entries


def inspect_runtime(root: Path, tickets_dir_arg: str | None, logbook_arg: str | None) -> dict:
    """Inspect .crew/ state, releases, logbook, DORA, evidence, postmortems, and ticket specs."""
    crew = root / ".crew"
    state_docs = _read_state_docs(crew / "state.sqlite")
    logbook_path = Path(logbook_arg) if logbook_arg else (
        Path(os.environ["LOGBOOK_FILE"]) if os.environ.get("LOGBOOK_FILE") else crew / "logbook.json"
    )
    if not logbook_path.is_absolute():
        logbook_path = root / logbook_path
    logbook = _read_json_file(logbook_path)
    if logbook is not None and not isinstance(logbook, dict):
        raise StartError(f"{logbook_path} must be a JSON object")

    release_record = _read_json_file(crew / "release.json")
    release_events = _read_releases_log(crew / "releases.jsonl")

    intakes: list[dict] = [d for d in state_docs if d.get("kind") == "intake"]
    tickets: list[dict] = [d for d in state_docs if d.get("kind") != "intake"]

    if not state_docs and isinstance(logbook, dict):
        lb_intakes = logbook.get("intakes")
        lb_tickets = logbook.get("tickets")
        if isinstance(lb_intakes, list):
            intakes = [x for x in lb_intakes if isinstance(x, dict)]
        if isinstance(lb_tickets, list):
            tickets = [x for x in lb_tickets if isinstance(x, dict)]

    # Build unified release view (from release_events, release_record, and logbook fallback)
    releases: list[dict] = []
    answers_by_ver: dict[str, dict] = {}
    for ev in release_events:
        if ev.get("kind") == "answer" and ev.get("version"):
            answers_by_ver[str(ev["version"])] = ev
        elif ev.get("kind") == "release" and isinstance(ev.get("record"), dict):
            rec = dict(ev["record"])
            rec["_ticket"] = ev.get("ticket")
            releases.append(rec)
    for rec in releases:
        ver = str(rec.get("version") or "")
        if ver in answers_by_ver and "answer" not in rec:
            rec["answer"] = answers_by_ver[ver]

    if isinstance(release_record, dict):
        ver = release_record.get("version")
        if not any(r.get("version") == ver and r.get("verdict") == release_record.get("verdict") for r in releases):
            releases.append(release_record)
    if not releases and isinstance(logbook, dict) and isinstance(logbook.get("releases"), list):
        releases = [r for r in logbook["releases"] if isinstance(r, dict)]

    launch_record = release_record if isinstance(release_record, dict) else None
    if launch_record is None and isinstance(logbook, dict):
        launch = logbook.get("launch")
        if isinstance(launch, dict) and isinstance(launch.get("record"), dict):
            launch_record = launch["record"]

    evidence_dir = crew / "evidence"
    evidence_versions = (
        {p.stem for p in evidence_dir.glob("*.json") if p.is_file()}
        if evidence_dir.is_dir()
        else set()
    )
    pm_dir = crew / "postmortem"
    has_postmortem = False
    if pm_dir.is_dir():
        has_postmortem = any(pm_dir.glob("*/postmortem.md")) or any(pm_dir.glob("*.md"))

    tdir_raw = tickets_dir_arg or os.environ.get("TICKETS_DIR", "")
    ticket_ids: list[str] = []
    if tdir_raw:
        tdir = Path(tdir_raw) if Path(tdir_raw).is_absolute() else (root / tdir_raw)
        if tdir.is_dir():
            ticket_ids = sorted(
                p.stem for p in tdir.glob("*.md")
                if p.is_file() and not p.name.endswith(".intake.md")
            )

    return {
        "has_state_db": (crew / "state.sqlite").is_file(),
        "has_logbook": logbook is not None,
        "has_dora": (crew / "dora.json").is_file(),
        "has_postmortem": has_postmortem,
        "evidence_versions": sorted(evidence_versions),
        "intakes": intakes,
        "tickets": tickets,
        "releases": releases,
        "launch_record": launch_record,
        "ticket_files": ticket_ids,
        "has_any_runtime": bool(
            state_docs or logbook is not None or release_record is not None or release_events
        ),
    }


def _resolve_active_config(root: Path, providers: dict[str, dict], adapters: dict[str, list[str]]) -> dict:
    """Resolve active provider/harness/delivery/profile and check .env key presence (booleans only)."""
    plan = parse_plain_env(root / ".crew" / "start-plan.env", reject_secrets=True)
    profile_name = plan.get("START_PROFILE") or os.environ.get("PROFILE") or "l0"
    profile_env = parse_plain_env(root / "profiles" / f"{profile_name}.env")

    provider = (
        plan.get("MODEL_PROVIDER")
        or os.environ.get("MODEL_PROVIDER")
        or profile_env.get("MODEL_PROVIDER")
        or ("replay" if profile_env.get("AGENT_HARNESS") == "replay" else "")
        or "replay"
    )
    harness = (
        plan.get("AGENT_HARNESS")
        or os.environ.get("AGENT_HARNESS")
        or profile_env.get("AGENT_HARNESS")
        or providers.get(provider, {}).get("default_harness", "replay")
    )
    delivery = (
        plan.get("DELIVERY_ADAPTER")
        or plan.get("DELIVERY")
        or os.environ.get("DELIVERY")
        or profile_env.get("DELIVERY")
        or (adapters["deliveries"][0] if adapters["deliveries"] else "")
    )
    dot_env = parse_env_presence(root / ".env")
    pinfo = providers.get(provider)
    if pinfo is None:
        return {
            "has_plan": bool(plan),
            "profile": profile_name,
            "provider": provider,
            "harness": harness,
            "delivery": delivery,
            "provider_valid": False,
            "env_var": "",
            "key_set": False,
            "budget_guidance": "",
            "preset": plan.get("START_PRESET", ""),
            "plan": plan,
        }
    env_var = str(pinfo.get("env_var") or "")
    key_set = is_var_configured(dot_env, env_var)
    return {
        "has_plan": bool(plan),
        "profile": profile_name,
        "provider": provider,
        "harness": harness,
        "delivery": delivery,
        "provider_valid": True,
        "env_var": env_var,
        "key_set": key_set,
        "budget_guidance": str(pinfo.get("budget_guidance") or ""),
        "preset": plan.get("START_PRESET", ""),
        "plan": plan,
    }


def _last_error(doc: dict) -> str:
    hist = doc.get("history")
    if isinstance(hist, list) and hist and isinstance(hist[-1], dict):
        err = hist[-1].get("error")
        if isinstance(err, str) and err:
            return err
    return ""


def _release_is_decided(rel: dict) -> bool:
    if rel.get("verdict") == "rolled-back":
        return True
    if rel.get("promoted") or rel.get("rejected"):
        return True
    ans = rel.get("answer")
    if isinstance(ans, dict) and ans.get("answer") in ("GO", "NO-GO"):
        return True
    return False


def _release_rolled_back(rel: dict) -> bool:
    if rel.get("verdict") == "rolled-back":
        return True
    if rel.get("rejected"):
        return True
    ans = rel.get("answer")
    return isinstance(ans, dict) and ans.get("answer") == "NO-GO"


def evaluate_next(
    root: Path,
    contract_arg: str | None = None,
    tickets_dir_arg: str | None = None,
    logbook_arg: str | None = None,
    mode: str = "junior",
) -> dict:
    """Determine the current lifecycle stage (1..6), exact next command, and checklist."""
    if mode not in MODES:
        raise StartError(f"unknown MODE '{mode}'; choose one of: {', '.join(MODES)}")
    providers = load_providers(root)
    adapters = discover_adapters(root, providers)
    contract = inspect_contract(root, contract_arg)
    runtime = inspect_runtime(root, tickets_dir_arg, logbook_arg)
    cfg = _resolve_active_config(root, providers, adapters)
    rework_policy = parse_plain_env(root / "policy" / "rework.env")

    # Stage 1
    s1_done = contract["has_workload_env"] and contract["has_baseline"]
    if not contract["has_workload_env"]:
        s1_cmd = "./go init" if contract["attached"] else "./go attach <repo-path>"
        s1_why = "No workload contract (workload.env) found at WORKLOAD_CONTRACT."
    elif not contract["has_baseline"]:
        s1_cmd = "./go mutation-baseline"
        s1_why = "MUTATION_BASELINE is empty in workload.env; measure it before running gates."
    else:
        s1_cmd = "./go gates"
        s1_why = f"Contract ready (MUTATION_BASELINE={contract['baseline']})."

    # Stage 2
    s2_done = (
        s1_done
        and (cfg["has_plan"] or runtime["has_any_runtime"])
        and cfg["provider_valid"]
        and cfg["key_set"]
    )
    if not cfg["provider_valid"]:
        s2_cmd = "./go start"
        s2_why = f"Unknown provider '{cfg['provider']}'; re-run ./go start."
    elif not cfg["has_plan"] and not runtime["has_any_runtime"]:
        s2_cmd = "./go start"
        s2_why = "Run the setup wizard to select provider, harness, delivery, and budgets."
    elif not cfg["key_set"]:
        s2_cmd = f"export {cfg['env_var']}=<secret>  # or set in .env, then ./go doctor --level {cfg['profile']}"
        s2_why = f"{cfg['env_var']} is unset or still a placeholder in .env."
    else:
        s2_cmd = f"./go doctor --level {cfg['profile']}"
        s2_why = f"Provider {cfg['provider']} ({cfg['harness']}), delivery {cfg['delivery']}."

    # Stage 3
    intakes = runtime["intakes"]
    tickets = runtime["tickets"]
    s3_done = s2_done and (
        any(i.get("state") == "confirmed" for i in intakes)
        or any(t.get("state") in STAGE3_PAST for t in tickets)
    )
    if not s3_done:
        q_intake = next((i for i in intakes if i.get("state") == "intake-questions"), None)
        p_intake = next((i for i in intakes if i.get("state") == "proposed"), None)
        q_ticket = next((t for t in tickets if t.get("state") == "questions"), None)
        err_doc = next((d for d in intakes + tickets if _last_error(d)), None)
        if q_intake:
            s3_cmd = f'./go answer TICKET={q_intake["id"]} ANSWER="<your answer>"'
            s3_why = f"Intake {q_intake['id']} is waiting for your answer."
        elif p_intake:
            s3_cmd = f"./go intake-confirm TICKET={p_intake['id']} DECISIONS='<json>'"
            s3_why = f"Intake {p_intake['id']} proposed tickets; confirm, edit, or drop them."
        elif q_ticket:
            s3_cmd = f'./go answer TICKET={q_ticket["id"]} ANSWER="<your answer>"'
            s3_why = f"Ticket {q_ticket['id']} is waiting for your answer."
        elif err_doc:
            s3_cmd = f"./go resume TICKET={err_doc['id']}"
            s3_why = f"Item {err_doc['id']} stopped on an error; inspect and resume."
        elif runtime["ticket_files"]:
            s3_cmd = f'./go intake TEXT="<what you need>"  # or ./go request TICKET={runtime["ticket_files"][0]}'
            s3_why = "Start an intake interview or request an existing ticket."
        else:
            s3_cmd = './go intake TEXT="<what you need>"'
            s3_why = "Start an intake interview to draft spec-checked tickets."
    else:
        s3_cmd = "./go logbook"
        s3_why = "Spec gate passed and tickets entered delivery."

    # Stage 4
    releases = runtime["releases"]
    launch_rec = runtime["launch_record"]
    s4_done = s3_done and (
        any(t.get("state") in STAGE4_PAST for t in tickets)
        or bool(releases)
        or launch_rec is not None
    )
    if not s4_done:
        stranded = next((t for t in tickets if _last_error(t)), None)
        stopped = next((t for t in tickets if t.get("state") == "needs-human"), None)
        failed = next((t for t in tickets if t.get("state") == "ci-failed"), None)
        opened = next((t for t in tickets if t.get("state") in ("opened", "spec-ready")), None)
        pr_open = next((t for t in tickets if t.get("state") == "pr-opened"), None)
        in_rev = next((t for t in tickets if t.get("state") == "review"), None)
        if stranded:
            s4_cmd = f"./go resume TICKET={stranded['id']}"
            s4_why = f"Ticket {stranded['id']} stopped mid-step; resume it."
        elif stopped:
            s4_cmd = f"./go audit TICKET={stopped['id']}"
            s4_why = f"Ticket {stopped['id']} hit needs-human (rework cap or budget)."
        elif failed:
            s4_cmd = f"./go resume TICKET={failed['id']}"
            s4_why = f"Ticket {failed['id']} failed gates and needs rework/resume."
        elif opened:
            s4_cmd = f"./go assign TICKET={opened['id']}"
            s4_why = f"Ticket {opened['id']} is ready for agentic build."
        elif pr_open:
            s4_cmd = "./go gates"
            s4_why = f"Ticket {pr_open['id']} has a PR waiting on the eight gates."
        elif in_rev:
            s4_cmd = f"./go review TICKET={in_rev['id']}"
            s4_why = f"Ticket {in_rev['id']} is waiting on review."
        else:
            s4_cmd = "./go e2e"
            s4_why = "Run agentic build, 8 PR gates, and rework."
    else:
        s4_cmd = "./go gates"
        s4_why = "At least one ticket passed the 8 PR gates."

    # Stage 5
    decided_releases = [r for r in releases if _release_is_decided(r)]
    s5_done = s4_done and (
        bool(decided_releases)
        or any(t.get("state") == "rolled-back" for t in tickets)
    )
    if not s5_done:
        if isinstance(launch_rec, dict) and launch_rec.get("verdict") == "held" and not _release_is_decided(launch_rec):
            s5_cmd = "./go promote"
            s5_why = f"Canary {launch_rec.get('version', '')} is held at 25% awaiting human approval."
        elif isinstance(launch_rec, dict) and launch_rec.get("verdict") == "in-progress":
            s5_cmd = "./go verify"
            s5_why = f"Canary {launch_rec.get('version', '')} rollout is in progress."
        else:
            s5_cmd = "./go release"
            s5_why = "Deploy canary through staging, 5%, 25% SLO checks, and human gate."
    else:
        s5_cmd = "./go promote"
        s5_why = "Canary rollout reached a release verdict."

    # Stage 6
    latest_rel = decided_releases[-1] if decided_releases else (releases[-1] if releases else None)
    rel_ver = str(latest_rel.get("version") or "") if isinstance(latest_rel, dict) else ""
    has_rollback = any(_release_rolled_back(r) for r in releases) or any(
        t.get("state") == "rolled-back" for t in tickets
    )
    needs_evidence = bool(rel_ver) and rel_ver not in runtime["evidence_versions"]
    needs_postmortem = has_rollback and not runtime["has_postmortem"]

    s6_done = (
        s5_done
        and runtime["has_logbook"]
        and runtime["has_dora"]
        and not needs_evidence
        and not needs_postmortem
    )
    if not runtime["has_logbook"]:
        s6_cmd = "./go logbook"
        s6_why = "Assemble the versioned logbook for DORA, spend, and the console."
    elif not runtime["has_dora"]:
        s6_cmd = "./go dora"
        s6_why = "Compute the four DORA delivery measures and inspect spend (./go spend)."
    elif needs_evidence:
        s6_cmd = f"./go evidence VERSION={rel_ver}"
        s6_why = f"Build and sign the release evidence pack for {rel_ver}."
    elif needs_postmortem:
        s6_cmd = "./go postmortem INCIDENT=<incident-file>"
        s6_why = "A release rolled back; capture an incident and generate a postmortem ticket."
    else:
        s6_cmd = "./go console"
        s6_why = "All 6 lifecycle stages complete; inspect metrics, logbook, and board in console."

    stage_defs = (
        (1, "connect", STAGES_META[0][2], s1_done, s1_cmd, s1_why),
        (2, "provider", STAGES_META[1][2], s2_done, s2_cmd, s2_why),
        (3, "intake", STAGES_META[2][2], s3_done, s3_cmd, s3_why),
        (4, "build", STAGES_META[3][2], s4_done, s4_cmd, s4_why),
        (5, "release", STAGES_META[4][2], s5_done, s5_cmd, s5_why),
        (6, "observe", STAGES_META[5][2], s6_done, s6_cmd, s6_why),
    )

    stages_out: list[dict] = []
    current_stage: dict | None = None
    for num, sid, title, done, cmd, why in stage_defs:
        if done:
            status = "done"
        elif current_stage is None:
            status = "current"
        else:
            status = "pending"
        item = {
            "stage": num,
            "id": sid,
            "title": title,
            "status": status,
            "command": cmd,
            "reason": why,
        }
        if current_stage is None and not done:
            current_stage = item
        stages_out.append(item)

    all_done = current_stage is None
    if current_stage is None:
        current_stage = stages_out[-1]

    verdict_line = (
        f"GO: next — stage {current_stage['stage']}/{TOTAL_STAGES} "
        f"({current_stage['title']}): {current_stage['command']}"
    )

    return {
        "verdict_line": verdict_line,
        "stage": current_stage["stage"],
        "total_stages": TOTAL_STAGES,
        "stage_id": current_stage["id"],
        "title": current_stage["title"],
        "command": current_stage["command"],
        "reason": current_stage["reason"],
        "all_done": all_done,
        "mode": mode,
        "stages": stages_out,
        "contract": contract,
        "provider": {
            "name": cfg["provider"],
            "harness": cfg["harness"],
            "delivery": cfg["delivery"],
            "profile": cfg["profile"],
            "env_var": cfg["env_var"],
            "key_set": cfg["key_set"],
            "has_plan": cfg["has_plan"],
            "budget_guidance": cfg["budget_guidance"],
        },
        "policy": {
            "rework_max_attempts": rework_policy.get("REWORK_MAX_ATTEMPTS", ""),
            "spend_budget_ticket_usd": (
                cfg["plan"].get("SPEND_BUDGET_TICKET_USD")
                or rework_policy.get("SPEND_BUDGET_TICKET_USD", "")
            ),
            "spend_budget_day_usd": (
                cfg["plan"].get("SPEND_BUDGET_DAY_USD")
                or rework_policy.get("SPEND_BUDGET_DAY_USD", "")
            ),
        },
        "adapters": adapters["all"],
        "deliveries": adapters["deliveries"],
        "harnesses": adapters["harnesses"],
        "registries": adapters["registries"],
        "flags": adapters["flags"],
        "cloud_plugins": adapters["cloud_plugins"],
        "cloud_slo_ingress": adapters["cloud_slo_ingress"],
    }


def render_next_checklist(info: dict) -> str:
    """Render the full 6-stage checklist for stderr in junior or expert mode."""
    mode = info["mode"]
    prov = info["provider"]
    contract = info["contract"]
    lines = [
        f"next ({mode} mode): stage {info['stage']}/{TOTAL_STAGES} — {info['title']}",
        f"contract: {contract['path'] or '(unset)'} | provider: {prov['name']} ({prov['harness']}) | delivery: {prov['delivery']}",
        "",
    ]

    junior_notes = {
        1: "Why: lights-out records test/SAST/mutation commands and baseline mutation score in a contract without modifying your main branch.",
        2: "Why: choose a replay (zero cost) or live AI provider and set spend budgets; API keys stay only in gitignored .env.",
        3: "Why: the intake interview and spec gate turn plain needs into verifiable Given/When/Then criteria and an explicit file perimeter.",
        4: "Why: every PR runs 8 gates (including mutation testing, which injects tiny faults to verify your tests catch them) with bounded automatic rework.",
        5: "Why: releases shift traffic incrementally (5% -> 25%), check error-rate and latency SLOs for automatic rollback, and wait for human approval before 100%.",
        6: "Why: inspect the single versioned logbook, 4 DORA keys, USD spend ledger, signed evidence pack, and automated postmortem tickets.",
    }

    for st in info["stages"]:
        tag = "[done]" if st["status"] == "done" else ("[now ]" if st["status"] == "current" else "[todo]")
        lines.append(f"  {tag} {st['stage']}/6 {st['title']}: {st['command']}")
        if mode == "junior":
            lines.append(f"         {st['reason']}")
            lines.append(f"         {junior_notes[st['stage']]}")
        elif st["status"] == "current":
            lines.append(f"         {st['reason']}")

    if mode == "expert":
        cbase = contract["path"] or "<WORKLOAD_CONTRACT>"
        lines.extend([
            "",
            "Override & customization pointers:",
            f"  - {cbase}/policy.env      (per-repo risk paths, mutation margin, SLO deltas)",
            f"  - {cbase}/roles/*.md      (custom reviewer guidance: architect, test-reviewer)",
            f"  - {cbase}/mcp.json        (declared MCP servers/tools for dev-worker)",
            "  - policy/rework.env       (REWORK_MAX_ATTEMPTS, SPEND_BUDGET_TICKET_USD, SPEND_BUDGET_DAY_USD)",
            f"  - profiles/{prov['profile']}.env & profiles/providers.json",
            f"  - adapters/ ({', '.join(info['deliveries']) or 'none'})",
            f"  - plugins/cloud/ ({', '.join(info['cloud_plugins']) or 'none detected'})",
        ])
    else:
        lines.extend([
            "",
            "Tip: run `MODE=expert ./go next` for terse commands and override file pointers.",
        ])
    return "\n".join(lines) + "\n"


def _resolve_delivery(delivery: str, adapters: dict[str, list[str]]) -> tuple[str, str]:
    """Return (resolved_delivery_value, source_kind) or raise StartError."""
    if delivery.startswith("outside/"):
        return delivery, "outside"
    if delivery in adapters["deliveries"]:
        return delivery, "adapters"
    if delivery in adapters["cloud_plugins"]:
        return f"outside/{delivery}", "plugins/cloud"
    known = ", ".join(adapters["deliveries"] + adapters["cloud_plugins"]) or "(none)"
    raise StartError(f"unknown delivery adapter '{delivery}'; available: {known}")


def _validate_doctor(root: Path, profile: str) -> tuple[str, list[str]]:
    """Run core/doctor.sh (or consume host pre-check from ./go); return (GO|NO-GO, stderr lines)."""
    host_rc = os.environ.get("LIGHTS_OUT_DOCTOR_RC")
    host_out = os.environ.get("LIGHTS_OUT_DOCTOR_OUT", "")
    host_err = os.environ.get("LIGHTS_OUT_DOCTOR_ERR", "")
    if host_rc is not None:
        status = "GO" if host_rc == "0" else "NO-GO"
        lines = [ln for ln in host_err.splitlines() if ln.strip()]
        if host_out.strip():
            lines.append(host_out.strip())
        return status, lines

    doctor_sh = root / "core" / "doctor.sh"
    if not doctor_sh.is_file():
        return "SKIPPED", ["doctor: core/doctor.sh not found"]
    env = dict(os.environ, PROFILE=profile)
    sh_bin = shutil.which("sh") or "/bin/sh"
    done = subprocess.run(  # nosec B603 B607 - fixed core/doctor.sh invocation with argv list
        [sh_bin, str(doctor_sh)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [ln for ln in done.stderr.splitlines() if ln.strip()]
    if done.stdout.strip():
        lines.append(done.stdout.strip())
    return ("GO" if done.returncode == 0 else "NO-GO"), lines


def _prompt_choice(prompt: str, options: list[str], default: str) -> str:
    sys.stderr.write(f"\n{prompt} [{', '.join(options)}] (default: {default}): ")
    sys.stderr.flush()
    ans = input().strip()
    if not ans:
        return default
    if ans not in options:
        raise StartError(f"invalid choice '{ans}'; expected one of: {', '.join(options)}")
    return ans


def _interactive_choices(providers: dict[str, dict], adapters: dict[str, list[str]]) -> dict[str, str]:
    sys.stderr.write(
        "lights-out guided setup (ADR 0103)\n"
        "Security note: never enter secret API keys here; keep credentials only in gitignored .env.\n"
    )
    preset = _prompt_choice("1) Preset", list(PRESETS), "junior")
    default_prov = "replay" if preset == "junior" else next((k for k in providers if k != "replay"), "replay")
    provider = _prompt_choice("2) AI provider", list(providers.keys()), default_prov)
    pinfo = providers[provider]
    harnesses = list(pinfo.get("harnesses") or ["replay"])
    harness = _prompt_choice("3) Agent harness", harnesses, str(pinfo.get("default_harness") or harnesses[0]))
    deliv_choices = adapters["deliveries"] + adapters["cloud_plugins"]
    default_deliv = deliv_choices[0] if deliv_choices else ""
    if not deliv_choices:
        raise StartError("no delivery adapters found under adapters/ or plugins/cloud/")
    delivery = _prompt_choice("4) Delivery adapter", deliv_choices, default_deliv)
    return {
        "preset": preset,
        "provider": provider,
        "harness": harness,
        "delivery": delivery,
    }


def _validate_budget(raw: str | None, label: str) -> str:
    if raw is None:
        return ""
    val = raw.strip()
    if not val:
        return ""
    if not _DECIMAL_RE.fullmatch(val) or float(val) <= 0:
        raise StartError(f"{label} must be a positive decimal number of USD")
    return val


def _write_plan_env(path: Path, fields: dict[str, str]) -> None:
    for key, val in fields.items():
        if _is_secret_key(key):
            raise StartError(f"refusing to write secret key {key} to {path}")
        if any(ord(ch) < 32 for ch in val) or "$" in val or "#" in val:
            raise StartError(f"unsafe characters in plan value for {key}")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated by ./go start — non-secret setup summary only (ADR 0103, ADR 0108).",
        "# Never write API keys or tokens here; keep credentials only in gitignored .env.",
    ] + [f"{k}={v}" for k, v in fields.items()]
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def run_start(
    root: Path,
    *,
    non_interactive: bool,
    preset: str | None,
    provider: str | None,
    harness: str | None,
    delivery: str | None,
    profile_override: str | None,
    mode_override: str | None,
    contract_arg: str | None,
    ticket_budget_usd: str | None,
    day_budget_usd: str | None,
) -> dict:
    """Validate configuration, write non-secret .crew/start-plan.env, check doctor, return summary."""
    plan_path = root / ".crew" / "start-plan.env"
    parse_plain_env(plan_path, reject_secrets=True)
    providers = load_providers(root)
    adapters = discover_adapters(root, providers)

    if not non_interactive:
        if not sys.stdin.isatty():
            raise StartError(
                "no TTY on stdin; pass --non-interactive --preset <junior|strict|custom> "
                "--provider <p> --harness <h> --delivery <d>"
            )
        chosen = _interactive_choices(providers, adapters)
        preset = preset or chosen["preset"]
        provider = provider or chosen["provider"]
        harness = harness or chosen["harness"]
        delivery = delivery or chosen["delivery"]

    preset = preset or "junior"
    if preset not in PRESETS:
        raise StartError(f"unknown preset '{preset}'; choose one of: {', '.join(PRESETS)}")

    if not provider:
        provider = "replay" if preset == "junior" else next((k for k in providers if k != "replay"), "replay")
    if provider not in providers:
        raise StartError(f"unknown provider '{provider}'; choose one of: {', '.join(providers)}")
    pinfo = providers[provider]

    supported_harnesses = list(pinfo.get("harnesses") or [])
    harness = harness or str(pinfo.get("default_harness") or "")
    if harness not in supported_harnesses:
        raise StartError(
            f"harness '{harness}' is not supported for provider '{provider}'; "
            f"choose one of: {', '.join(supported_harnesses)}"
        )
    if harness not in adapters["harnesses"]:
        raise StartError(f"harness adapter directory adapters/{harness} not found")

    profile = profile_override or str(pinfo.get("default_profile") or "l0")
    profile_env = parse_plain_env(root / "profiles" / f"{profile}.env")
    if not delivery:
        delivery = profile_env.get("DELIVERY") or (adapters["deliveries"][0] if adapters["deliveries"] else "")
    if not delivery:
        raise StartError("no delivery adapter specified or discovered")
    resolved_delivery, delivery_source = _resolve_delivery(delivery, adapters)

    mode = mode_override or ("junior" if preset == "junior" else "expert")
    if mode not in MODES:
        raise StartError(f"unknown mode '{mode}'; choose one of: {', '.join(MODES)}")

    env_var = str(pinfo.get("env_var") or "")
    dot_env = parse_env_presence(root / ".env")
    key_configured = is_var_configured(dot_env, env_var)
    doctor_status, doctor_lines = _validate_doctor(root, profile)
    if env_var and not key_configured:
        doctor_status = "NO-GO"
        doctor_lines.append(
            f"doctor: key       FAIL, {profile} ({provider}, {harness}) needs {env_var} in .env"
        )
    contract = inspect_contract(root, contract_arg)
    clean_ticket_budget = _validate_budget(ticket_budget_usd, "--ticket-budget-usd")
    clean_day_budget = _validate_budget(day_budget_usd, "--day-budget-usd")
    decide_adapter = str(pinfo.get("decide") or "replay")
    auto_incident = os.environ.get("AUTO_INCIDENT_ON_ROLLBACK", "").strip()
    if auto_incident not in ("on", "off"):
        auto_incident = "on"

    plan_fields: dict[str, str] = {
        "START_PRESET": preset,
        "START_MODE": mode,
        "CONSOLE_PERSONA": "junior" if mode == "junior" else "senior",
        "START_PROFILE": profile,
        "MODEL_PROVIDER": provider,
        "MODEL": str(pinfo.get("default_model") or ""),
        "AGENT_HARNESS": harness,
        "DELIVERY": resolved_delivery,
        "DELIVERY_ADAPTER": delivery,
        "DELIVERY_TARGET": resolved_delivery,
        "DELIVERY_SOURCE": delivery_source,
        "DECIDE": decide_adapter,
        "DECIDE_ADAPTER": decide_adapter,
        "AUTO_INCIDENT_ON_ROLLBACK": auto_incident,
        "PROVIDER_KEY_VAR": env_var,
        "PROVIDER_KEY_CONFIGURED": "yes" if key_configured else "no",
        "DOCTOR_STATUS": doctor_status,
        "START_WORKLOAD_CONTRACT": contract["path"],
        "SUGGESTED_TICKET_BUDGET_USD": clean_ticket_budget,
        "SUGGESTED_DAY_BUDGET_USD": clean_day_budget,
    }
    if delivery_source == "plugins/cloud":
        plan_fields["ADAPTERS_PATH"] = str((root / "plugins" / "cloud").resolve())
    if clean_ticket_budget:
        plan_fields["SPEND_BUDGET_TICKET_USD"] = clean_ticket_budget
    if clean_day_budget:
        plan_fields["SPEND_BUDGET_DAY_USD"] = clean_day_budget
    _write_plan_env(plan_path, plan_fields)

    has_proxy_slo = delivery_source == "plugins/cloud" and delivery in adapters["cloud_slo_ingress"]
    slo_promql = f"plugins/cloud/{delivery}/slo-ingress.promql" if has_proxy_slo else ""
    cloud_registry = (
        "oci-registry"
        if "oci-registry" in adapters["registries"]
        else (adapters["registries"][0] if adapters["registries"] else "")
    )
    cloud_flags = [f for f in ("flags-file", "flags-http") if f in adapters["flags"]]
    if not cloud_flags:
        cloud_flags = list(adapters["flags"])

    # Build copy-paste E2E commands
    commands: list[str] = []
    if env_var and not key_configured:
        commands.append(f"# Add {env_var}=<your-key> to gitignored .env (never commit it)")
    commands.append(f"./go doctor --level {profile}")
    if not contract["has_workload_env"]:
        commands.append("./go try <path-to-your-git-repo>  # or ./go attach <path> && ./go init")
    elif not contract["has_baseline"]:
        commands.append("./go mutation-baseline")
    if delivery_source == "plugins/cloud":
        setup_cmd = f"./go cloud-setup CLOUD={delivery} --slo-mode proxy" if has_proxy_slo else f"./go cloud-setup CLOUD={delivery}"
        commands.append(setup_cmd)
        if cloud_registry == "oci-registry":
            commands.append("./go registry-conformance REGISTRY=oci-registry")
        if cloud_flags:
            if len(cloud_flags) > 1:
                commands.append(f"./go flags-conformance FLAGS={cloud_flags[0]}  # or FLAGS={cloud_flags[1]}")
            else:
                commands.append(f"./go flags-conformance FLAGS={cloud_flags[0]}")
        bridge_parts = [f"DELIVERY=outside/{delivery}", "ADAPTERS_PATH=$(pwd)/plugins/cloud"]
        if cloud_registry:
            bridge_parts.append(f"REGISTRY={cloud_registry}")
        if cloud_flags:
            bridge_parts.append(f"FLAGS={cloud_flags[0]}")
        if slo_promql:
            bridge_parts.append(f"SLO_PROMQL={slo_promql}")
        commands.append(f"./go delivery-conformance {' '.join(bridge_parts)}")
    commands.extend([
        f"MODE={mode} ./go next",
        f'PROFILE={profile} MODEL_PROVIDER={provider} AGENT_HARNESS={harness} ./go intake TEXT="Describe your feature"',
        "./go gates",
        "./go up && ./go release",
        "./go promote",
        "./go logbook && ./go dora && ./go spend && ./go console",
        f"# Ops loop: ./go watch [ONCE=1] and ./go alert-webhook (AUTO_INCIDENT_ON_ROLLBACK={auto_incident})",
    ])

    verdict_line = (
        f"GO: start — preset={preset}, provider={provider}, harness={harness}, "
        f"delivery={delivery}, doctor={doctor_status} (.crew/start-plan.env)"
    )
    return {
        "verdict_line": verdict_line,
        "plan_path": str(plan_path),
        "plan": plan_fields,
        "doctor_status": doctor_status,
        "doctor_lines": doctor_lines,
        "budget_guidance": str(pinfo.get("budget_guidance") or ""),
        "discovered": {
            "registries": adapters["registries"],
            "flags": adapters["flags"],
            "cloud_slo_ingress": adapters["cloud_slo_ingress"],
            "slo_promql": slo_promql,
        },
        "commands": commands,
    }


def render_start_summary(result: dict) -> str:
    """Format non-secret wizard summary and copy-paste E2E workflow commands for stderr."""
    plan = result["plan"]
    lines = [
        f"start: non-secret plan written to {result['plan_path']}",
        f"  preset={plan['START_PRESET']} mode={plan['START_MODE']} profile={plan['START_PROFILE']}",
        f"  provider={plan['MODEL_PROVIDER']} harness={plan['AGENT_HARNESS']} delivery={plan['DELIVERY_ADAPTER']} ({plan['DELIVERY_SOURCE']})",
        f"  defaults: decide={plan['DECIDE_ADAPTER']} AUTO_INCIDENT_ON_ROLLBACK={plan['AUTO_INCIDENT_ON_ROLLBACK']}",
        f"  budget guidance: {result['budget_guidance']}",
        f"  doctor status: {result['doctor_status']}",
    ]
    disc = result.get("discovered") or {}
    if disc.get("registries") or disc.get("flags") or disc.get("slo_promql"):
        parts = []
        if disc.get("registries"):
            parts.append(f"registries={','.join(disc['registries'])}")
        if disc.get("flags"):
            parts.append(f"flags={','.join(disc['flags'])}")
        if disc.get("slo_promql"):
            parts.append(f"slo={disc['slo_promql']} (--slo-mode proxy)")
        lines.append(f"  discovered: {' '.join(parts)}")
    for dl in result["doctor_lines"]:
        lines.append(f"    {dl}")
    lines.append("")
    lines.append("Copy-paste E2E workflow commands:")
    for cmd in result["commands"]:
        lines.append(f"  {cmd}")
    return "\n".join(lines) + "\n"


def _build_parser() -> _CleanParser:
    p = _CleanParser(prog="start.py", add_help=True)
    p.add_argument("--root", default=".")
    p.add_argument("--contract", default=None)
    p.add_argument("--tickets-dir", default=None)
    p.add_argument("--logbook", default=None)
    sub = p.add_subparsers(dest="command")

    p_next = sub.add_parser("next")
    p_next.add_argument("--mode", default=None)
    p_next.add_argument("--json", action="store_true", dest="as_json")

    p_start = sub.add_parser("start")
    p_start.add_argument("--non-interactive", action="store_true")
    p_start.add_argument("--preset", default=None)
    p_start.add_argument("--provider", default=None)
    p_start.add_argument("--harness", default=None)
    p_start.add_argument("--delivery", default=None)
    p_start.add_argument("--profile", default=None)
    p_start.add_argument("--mode", default=None)
    p_start.add_argument("--ticket-budget-usd", default=None)
    p_start.add_argument("--day-budget-usd", default=None)
    p_start.add_argument("--json", action="store_true", dest="as_json")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    cmd_name = next((a for a in raw_argv if a in ("next", "start")), "start")
    try:
        ns = parser.parse_args(raw_argv)
        if not ns.command:
            raise StartError("subcommand required: next | start")
        cmd_name = ns.command
        root = Path(ns.root).resolve()
        if ns.command == "next":
            mode = ns.mode or os.environ.get("MODE") or "junior"
            info = evaluate_next(
                root,
                contract_arg=ns.contract,
                tickets_dir_arg=ns.tickets_dir,
                logbook_arg=ns.logbook,
                mode=mode,
            )
            if ns.as_json:
                sys.stdout.write(json.dumps(info, indent=2) + "\n")
            else:
                sys.stdout.write(info["verdict_line"] + "\n")
                sys.stderr.write(render_next_checklist(info))
            return 0

        res = run_start(
            root,
            non_interactive=ns.non_interactive,
            preset=ns.preset,
            provider=ns.provider,
            harness=ns.harness,
            delivery=ns.delivery,
            profile_override=ns.profile,
            mode_override=ns.mode or os.environ.get("MODE") or None,
            contract_arg=ns.contract,
            ticket_budget_usd=ns.ticket_budget_usd,
            day_budget_usd=ns.day_budget_usd,
        )
        if ns.as_json:
            sys.stdout.write(json.dumps(res, indent=2) + "\n")
        else:
            sys.stdout.write(res["verdict_line"] + "\n")
            sys.stderr.write(render_start_summary(res))
        return 0
    except StartError as exc:
        sys.stdout.write(f"NO-GO: {cmd_name} — {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
