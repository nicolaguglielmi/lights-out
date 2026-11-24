"""The incident document (docs/adr/0044 §1, §2): stdlib only.

A release record, a time window and a log excerpt in, one incident
document out: what `./go postmortem` hands the ops-triage role, holding
everything the role may cite and nothing it could make up. Deterministic:
the same inputs give the same bytes. Only TICKET touches anything beyond
files (the state store, through its get verb); without it chain is null.
core/incident.sh is the entry point (docs/adr/0052 §3).

    NAME=<slug> FROM=<utc> TO=<utc> [LOGS=<file>] [TICKET=<id> STATE_STORE=<adapter>] \
        [RELEASE_RECORD=.crew/release.json] [INCIDENT_OUT=.crew/incidents] python3 core/incident.py

Exit 0 prints the document's path on stdout and a summary on stderr; exit
1 prints the reason on stdout and writes nothing.
"""

import json
import os
import re
import subprocess  # nosec B404 - only for PIPE; tower.py's sh() runs the verbs
import sys

try:
    from .obs import extract_trace_exemplars
    from .tower import sh
except ImportError:  # run as a script: core/ is sys.path[0]
    from obs import extract_trace_exemplars
    from tower import sh

NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
USAGE = "./go incident NAME=<slug> FROM=<utc> TO=<utc>"


class Refused(Exception):
    pass


def release_record(path):
    """The record, refused unless it carries a verdict (ticket 0008: in-progress is none)."""
    if not os.path.isfile(path):
        raise Refused(f"no release record at {path}; run a release first (./go demo-rollback)")
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except ValueError:
        raise Refused(f"{path} is not JSON") from None
    if not isinstance(record, dict):
        raise Refused(f"{path} is not a JSON object")
    verdict = record.get("verdict")
    version = record.get("version") or ""
    if verdict == "in-progress":
        raise Refused(f"release {version or '?'} is in-progress: a run died before its verdict (ticket 0008)")
    if verdict not in ("held", "rolled-back"):
        shown = "" if verdict is None else verdict
        raise Refused(f'{path} has no verdict of held or rolled-back (got "{shown}")')
    if not version:
        raise Refused(f"{path} names no version")
    return record


def log_lines(path):
    """The excerpt's lines verbatim; a final newline ends the last line, it adds none."""
    if not os.path.isfile(path):
        raise Refused(f"no log excerpt at {path}")
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        lines = f.read().split("\n")
    return lines[:-1] if lines[-1] == "" else lines


def alerts(record):
    """One alert per stage whose verdict is not GO, today's alert shape without chain."""
    return [{"headline": stage.get("verdict"), "version": record.get("version"),
             "stable": record.get("stable"), "stage": stage}
            for stage in record.get("stages") or []
            if not (isinstance(stage.get("verdict"), str) and stage["verdict"].startswith("GO"))]


def chain(ticket, store, version):
    """{ticket, pr, merge} from the tower's document, which must be about this release."""
    got = sh(f"adapters/{store}/get.sh", "tickets", ticket, stdout=subprocess.PIPE, text=True)
    try:
        doc = json.loads(got.stdout) if got.returncode == 0 else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        raise Refused(f"no document for ticket {ticket} in {store}; TICKET needs the plane")
    doc_version = (doc.get("release") or {}).get("version") or ""
    if doc_version != version:
        raise Refused(f'ticket {ticket}\'s document records release "{doc_version}", '
                      f'the release record is "{version}"')
    pr = (doc.get("pr") or {}).get("number")
    merge = (doc.get("merge") or {}).get("sha")
    if pr is None or merge is None:
        raise Refused(f"ticket {ticket}'s document has no PR or merge to anchor to")
    return {"ticket": ticket, "pr": pr, "merge": merge}


def incident(env):
    name, start, end = env.get("NAME", ""), env.get("FROM", ""), env.get("TO", "")
    for var, value in (("NAME", name), ("FROM", start), ("TO", end)):
        if not value:
            raise Refused(f"{var} not set; {USAGE}")
    if not NAME.fullmatch(name):
        raise Refused(f'NAME "{name}" is not a slug: lowercase letters, digits and dashes, '
                      "not starting with a dash")
    for stamp in (start, end):
        if not STAMP.fullmatch(stamp):
            raise Refused(f'"{stamp}" is not a UTC time like 2026-11-04T09:30:00Z')
    # The fixed-width format sorts as time does.
    if start > end:
        raise Refused(f"the window ends ({end}) before it starts ({start})")
    record = release_record(env.get("RELEASE_RECORD") or ".crew/release.json")
    logs = log_lines(env["LOGS"]) if env.get("LOGS") else []
    alert_list = alerts(record)
    anchored = None
    if env.get("TICKET"):
        if not env.get("STATE_STORE"):
            raise Refused("STATE_STORE not set; run this via ./go, not directly")
        anchored = chain(env["TICKET"], env["STATE_STORE"], record["version"])
    doc = {"kind": "incident", "name": name, "window": {"from": start, "to": end},
           "release": record, "alerts": alert_list, "logs": logs, "chain": anchored}
    traces = extract_trace_exemplars(list(alert_list) + list(logs))
    if traces:
        doc["traces"] = traces
    return doc


def main(env=None):
    env = os.environ if env is None else env
    try:
        doc = incident(env)
    except Refused as refused:
        print(refused)
        return 1
    out_dir = env.get("INCIDENT_OUT") or ".crew/incidents"
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{doc['name']}.json")
    with open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    chain_text = json.dumps(doc["chain"], ensure_ascii=False, separators=(",", ":"))
    print(f"[incident] {doc['name']}: {len(doc['alerts'])} alert(s), {len(doc['logs'])} log line(s), "
          f"chain {chain_text}", file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
