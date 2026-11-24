"""The incident artefact of a triage (docs/adr/0020 §3): stdlib only.

core/triage.sh runs the ops-triage role on an alert; this judges what the
role left and writes `incident.json` = {alert, title, hypotheses (by
rank), proposal: {action: rollback|hold, version, reason}, run}. The role
proposes; nothing here acts on the proposal. Refused: fewer than two
ranked hypotheses, a proposal without file_incident before it, a rollback
of a version the alert did not name.

    python3 core/triage.py alert <alert.json>
    python3 core/triage.py check <alert.json> <out-dir> <incident.json>

Exit 0, or exit 1 with the reason on stdout; `check` prints the incident
on stderr and writes nothing on a refusal.
"""

import json
import os
import re
import signal
import subprocess  # nosec B404 - only the two port adapters of docs/adr/0121 §3, explicit argv
import sys
from datetime import datetime, timezone

# Provenance: docs/adr/0121, "Thresholds and provenance".
MITIGATE_TIMEOUT_S = 120

SEQUENCES = (["file_incident", "propose_rollback"], ["file_incident", "propose_hold"])


class Refused(Exception):
    pass


def load_alert(path):
    """The alert, which must be a JSON object naming a version."""
    try:
        with open(path, encoding="utf-8") as f:
            alert = json.load(f)
    except OSError:
        raise Refused(f"no alert at {path}") from None
    except ValueError:
        raise Refused(f"{path} is not JSON") from None
    if not isinstance(alert, dict) or not alert.get("version"):
        raise Refused(f"{path} names no version")
    return alert


def hypotheses_of(call):
    """The incident's hypotheses, refused unless at least two carry distinct numeric ranks."""
    hypotheses = (call.get("args") or {}).get("hypotheses") or []
    if len(hypotheses) < 2:
        raise Refused(f"an incident needs at least two ranked hypotheses, got {len(hypotheses)}")
    ranks = [h.get("rank") if isinstance(h, dict) else None for h in hypotheses]
    numeric = all(isinstance(r, (int, float)) and not isinstance(r, bool) for r in ranks)
    if not numeric or len(set(ranks)) != len(ranks):
        raise Refused("hypotheses are not uniquely ranked")
    return sorted(hypotheses, key=lambda h: h["rank"])


def judge(alert, calls, run):
    """The incident document from the role's tool calls, or Refused."""
    names = [c.get("name") if isinstance(c, dict) else None for c in calls]
    if names not in SEQUENCES:
        got = " ".join(str(n) for n in names)
        raise Refused(f"expected file_incident then one proposal, got: {got or 'nothing'}")
    filed, proposed = calls
    hypotheses = hypotheses_of(filed)
    args = proposed.get("args") or {}
    action = "rollback" if proposed["name"] == "propose_rollback" else "hold"
    proposal = {"action": action, "version": args.get("version"), "reason": args.get("reason")}
    if action == "rollback" and proposal["version"] != alert["version"]:
        shown = proposal["version"] or ""
        raise Refused(f'rollback proposed for "{shown}", the alert is about {alert["version"]}')
    title = (filed.get("args") or {}).get("title") or ""
    return {"alert": alert, "title": title, "hypotheses": hypotheses, "proposal": proposal, "run": run}


def interpolated(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def summary(case, doc, path):
    proposal = doc["proposal"]
    lines = [f'[triage] {case}: incident "{doc["title"]}"']
    lines += [f"         {interpolated(h.get('rank'))}. {interpolated(h.get('cause'))} — "
              f"{interpolated(h.get('evidence'))}" for h in doc["hypotheses"]]
    lines.append(f"         proposal: {proposal['action']} {proposal['version'] or ''} — "
                 f"{interpolated(proposal['reason'])}")
    lines.append(f"         written to {path}")
    return lines


def check(alert_path, out_dir, incident_path):
    alert = load_alert(alert_path)
    calls_path = os.path.join(out_dir, "calls.json")
    if not os.path.isfile(calls_path):
        raise Refused("the ops-triage role left no calls.json")
    try:
        with open(calls_path, encoding="utf-8") as f:
            calls = json.load(f)
        with open(os.path.join(out_dir, "run.json"), encoding="utf-8") as f:
            run = json.load(f)
    except OSError as e:
        raise Refused(f"the ops-triage role left no {os.path.basename(e.filename)}") from None
    except ValueError:
        raise Refused("the ops-triage role left calls.json or run.json that is not JSON") from None
    if not isinstance(calls, list):
        raise Refused("the ops-triage role's calls.json is not a list of calls")
    doc = judge(alert, calls, run)
    with open(incident_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    case = os.path.basename(alert_path)
    case = case[:-len(".json")] if case.endswith(".json") and case != ".json" else case
    for line in summary(case, doc, incident_path):
        print(line, file=sys.stderr)


# --- policy-gated mitigation (docs/adr/0121 §3) ---

ACTIONS = ("kill-switch", "rollback")  # the order they run in
_PLAIN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(path, alert, version, action, verdict, rc):
    """Append one decision; the file is only ever opened to append."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    line = json.dumps({"ts": _stamp(), "alert": alert, "version": version, "action": action,
                       "verdict": verdict, "rc": rc}, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _plan(cfg, requested):
    """[(action, argv)] for the requested actions, or a refusal reason string."""
    names = [a.strip() for a in requested.split(",") if a.strip()] or ["rollback"]
    unknown = [a for a in names if a not in ACTIONS]
    if unknown:
        return f"unknown mitigation action {unknown[0]!r}"
    plan = []
    for action in (a for a in ACTIONS if a in names):
        if action == "rollback":
            port, script, extra = cfg.get("DELIVERY") or "", "rollback.sh", []
        else:
            port, script = cfg.get("FLAGS") or "", "set.sh"
            flag = cfg.get("WORKLOAD_KILL_SWITCH") or ""
            if not _PLAIN.fullmatch(flag):
                return "WORKLOAD_KILL_SWITCH is not a plain flag name"
            extra = [flag, "off"]
        if not _PLAIN.fullmatch(port):
            return f"{action}: its port is not set to a plain adapter name"
        path = os.path.join("adapters", port, script)
        if not os.path.isfile(path):
            return f"{action}: {path} not found"
        plan.append((action, ["sh", path, *extra]))
    return plan


def _run(argv, cfg):
    """The adapter's exit status, None on a timeout; never a shell, never stored output."""
    try:
        seconds = int(cfg.get("MITIGATE_TIMEOUT_S") or MITIGATE_TIMEOUT_S)
    except ValueError:
        seconds = MITIGATE_TIMEOUT_S
    proc = subprocess.Popen(  # nosec B603 - explicit argv of a port adapter, ADR 0121 §3
        argv, env=dict(cfg), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, start_new_session=True)
    try:
        return proc.wait(timeout=max(seconds, 1))
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        return None


def _roll_back_record(path, record, alert, cfg):
    """The record as rolled-back, its log line appended; as core/releases.py writes both."""
    try:
        from . import releases
    except ImportError:  # run as a script: core/ is sys.path[0]
        import releases
    record = dict(record, verdict="rolled-back", reason=f"mitigated on triage of {alert}")
    tmp = f"{path}.tmp-{os.getpid()}"
    releases.write_record(tmp, record)
    os.replace(tmp, path)
    releases.append(releases.paths(cfg)[1], {"kind": "release", "at": _stamp(),
                                             "ticket": cfg.get("RELEASE_TICKET") or None, "record": record})


def mitigate(triage_result, release_record_path, env=None):
    """Execute a checked rollback proposal through the ports; see docs/adr/0121 §3."""
    cfg = os.environ if env is None else env
    if cfg.get("AUTO_MITIGATE_ON_TRIAGE") != "on":
        return {"status": "disabled"}
    log = cfg.get("MITIGATIONS_LOG") or os.path.join(".crew", "mitigations.jsonl")
    doc = _load_json(triage_result) if isinstance(triage_result, str) else triage_result
    doc = doc if isinstance(doc, dict) else {}
    alert = doc.get("alert") if isinstance(doc.get("alert"), dict) else {}
    proposal = doc.get("proposal") if isinstance(doc.get("proposal"), dict) else {}
    version = proposal.get("version") if isinstance(proposal.get("version"), str) else ""
    name = str(alert.get("headline") or alert.get("title") or alert.get("version") or "")

    def settle(status, verdict, reason):
        _audit(log, name, version, "none", verdict, None)
        print(f"[triage] mitigation {status}: {reason}", file=sys.stderr)
        return {"status": status, "reason": reason}

    if proposal.get("action") != "rollback":
        return settle("refused", "refused", "the proposal is not a rollback")
    if not version or alert.get("version") != version:
        return settle("refused", "refused", "the proposal's version is not the alert's version")
    record = _load_json(release_record_path)
    on_record = record is not None and record.get("version") == version
    if version in (cfg.get("STABLE_VERSION"), (record or {}).get("stable")):
        return settle("refused", "refused", f"{version} is the stable version")
    if on_record:
        verdict = record.get("verdict")
        if verdict == "rolled-back":
            return settle("already-rolled-back", "skipped", f"{version} is already rolled back")
        if verdict not in ("held", "in-progress"):
            return settle("refused", "refused", f"the record's verdict is {verdict!r}, not held or in-progress")
    elif cfg.get("CANARY_VERSION") != version:
        return settle("refused", "refused", f"{version} is not the live canary")
    plan = _plan(cfg, cfg.get("MITIGATE_ACTIONS") or "")
    if isinstance(plan, str):
        return settle("refused", "refused", plan)

    done = []
    for action, argv in plan:
        rc = _run(argv, cfg)
        verdict = "applied" if rc == 0 else "failed"
        _audit(log, name, version, action, verdict, rc)
        done.append({"action": action, "verdict": verdict, "rc": rc})
    out = {"status": "applied" if all(d["verdict"] == "applied" for d in done) else "failed",
           "version": version, "actions": done}
    rolled = any(d["action"] == "rollback" and d["verdict"] == "applied" for d in done)
    if rolled and on_record:
        _roll_back_record(release_record_path, record, name, cfg)
        if cfg.get("AUTO_INCIDENT_ON_ROLLBACK") == "on":
            try:
                from . import ops_loop
            except ImportError:
                import ops_loop
            try:
                out["incident"] = ops_loop.on_rollback(release_record_path, cfg, strict=False, release_hook=True)["incident_path"]
            except (ops_loop.Refused, OSError) as exc:  # the mitigation stands whatever the paperwork does
                print(f"[triage] auto-incident skipped: {exc}", file=sys.stderr)
    return out


def main(argv, env=None):
    try:
        if len(argv) in (2, 3) and argv[0] == "mitigate":
            cfg = os.environ if env is None else env
            record = argv[2] if len(argv) == 3 else (cfg.get("RELEASE_RECORD") or os.path.join(".crew", "release.json"))
            result = mitigate(argv[1], record, env)
            print(f"mitigate: {result['status']}" + (f" — {result['reason']}" if result.get("reason") else ""))
            return 0 if result["status"] in ("disabled", "applied", "already-rolled-back") else 1
        if len(argv) == 2 and argv[0] == "alert":
            load_alert(argv[1])
        elif len(argv) == 4 and argv[0] == "check":
            check(*argv[1:])
        else:
            print("usage: triage.py alert <alert> | check <alert> <out-dir> <incident> | mitigate <incident> [<record>]")
            return 1
    except Refused as refused:
        print(refused)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
