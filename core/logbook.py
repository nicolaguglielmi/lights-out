"""The logbook's reader (docs/adr/0047 §2): stdlib only.

    python3 core/logbook.py --out <file>

Assembles `lights-out/logbook/1.0` (core/logbook/1.0.schema.json) from the
tower's documents (through the STATE_STORE port's get verb), the release
log and the latest release record, the post mortems, the policy files and
the plane's status (through the DELIVERY port's status verb). Every rule
is in docs/plans/console.md, "Decided at build"; this file follows it and
adds nothing.

No clock of its own: equal sources give equal bytes. A source it cannot
read becomes {"ok": false, "error": <the source's own last line>}, never
a default. The only place a source's shape change lands: the console and
any consumer read this document and nothing behind it.

Prints the number of tickets on stdout; core/logbook.sh turns it into the
one contract line. Exit 1 with one line on stdout when the file cannot be
written.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

try:
    from core.policy import PolicyError, effective
except ImportError:  # run as a script: core/ is sys.path[0]
    from policy import PolicyError, effective

SCHEMA = "lights-out/logbook/1.0"
_DECIMAL = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_PROVENANCE = re.compile(r"^#\s*Provenance, (measurement|external|decision):\s*(.*)$")
_KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
VERIFY_KEYS = ("ERROR_RATE_MAX_DELTA", "LATENCY_P95_MAX_DELTA")


def env(name, default=""):
    return os.environ.get(name) or default


def unread(error):
    return {"ok": False, "error": error or "unreadable, no reason given"}


def is_unread(value):
    return isinstance(value, dict) and value.get("ok") is False


def run(*argv):
    """(returncode, stdout, last stderr line) of argv, from the working directory."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return 124, "", f"{argv[1] if len(argv) > 1 else argv[0]}: timed out after 30s"
    lines = p.stderr.strip().splitlines()
    return p.returncode, p.stdout, lines[-1] if lines else f"{' '.join(argv)}: exit {p.returncode}"


def read_json(path):
    """The file's JSON, None when absent, unread when not JSON."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError:
        return unread(f"{path} is not JSON")


def pick(obj, keys):
    if not isinstance(obj, dict):
        return None
    return {k: obj.get(k) for k in keys}


# --- source ---------------------------------------------------------------


def source():
    rc, out, _ = run("git", "rev-parse", "HEAD")
    return {
        "commit": out.strip() if rc == 0 and out.strip() else None,
        "level": env("LEVEL"),
        "workload": env("WORKLOAD"),
        "code_host": env("CODE_HOST"),
    }


# --- releases -------------------------------------------------------------


def answer_of(obj):
    """{answer, by, via, at} from a record's promoted/rejected object, else None."""
    for key, answer in (("promoted", "GO"), ("rejected", "NO-GO")):
        a = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(a, dict) and all(k in a for k in ("by", "via", "at")):
            return {"answer": answer, "by": a["by"], "via": a["via"], "at": a["at"]}
    return None


def release_entry(record, at, ticket, answer, origin):
    return {
        "version": record.get("version"),
        "stable": record.get("stable"),
        "image": record.get("image"),
        "stages": record.get("stages") or [],
        "verdict": record.get("verdict"),
        "held_at": record.get("held_at"),
        "at": at,
        "ticket": ticket,
        "answer": answer,
        "from": origin,
    }


def releases(record):
    path = env("RELEASES_LOG", ".crew/releases.jsonl")
    lines = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                if not raw.strip():
                    continue
                try:
                    lines.append(json.loads(raw))
                except ValueError:
                    return unread(f"{path} line {n} is not JSON")
    out = []
    for i, line in enumerate(lines):
        if line.get("kind") != "release" or not isinstance(line.get("record"), dict):
            continue
        rec = line["record"]
        answer = None
        for later in lines[i + 1:]:
            if later.get("kind") == "release":
                break
            if later.get("kind") == "answer" and later.get("version") == rec.get("version"):
                answer = {k: later.get(k) for k in ("answer", "by", "via", "at")}
                break
        out.append(release_entry(rec, line.get("at"), line.get("ticket"), answer, "releases.jsonl"))
    if isinstance(record, dict) and not is_unread(record) and record.get("verdict") in ("held", "rolled-back"):
        last = out[-1] if out else None
        if last is None or (last["version"], last["verdict"]) != (record.get("version"), record.get("verdict")):
            out.append(release_entry(record, None, None, answer_of(record), "release.json"))
    return out


# --- tickets --------------------------------------------------------------


def ticket_file_facts(path):
    """(first `# ` line without it, the `- Origin:` value or None)."""
    title, origin = None, None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if title is None and line.startswith("# "):
                title = line[2:].strip()
            elif origin is None and line.startswith("- Origin: "):
                origin = line[len("- Origin: "):].strip()
    return title, origin


def perimeter(path):
    rc, out, err = run("sh", "core/perimeter.sh", "--list", path)
    if rc != 0:
        return unread(err)
    return [line for line in out.splitlines() if line]


TASK_KEYS = ("role", "harness", "provider", "model", "thinking", "turns", "tokens", "duration_s",
             "input_sha256", "replayed")


def outcome(doc, tid, rels):
    history = doc.get("history") or []
    last = history[-1] if history else {}
    state = doc.get("state")
    if isinstance(last, dict) and last.get("error"):
        return {"verdict": "stranded", "judge": "step", "line": last["error"]}
    if state == "needs-human":
        r = (doc.get("rework") or [{}])[-1]
        return {"verdict": "needs-human", "judge": r.get("judge"), "line": r.get("line")}
    release = doc.get("release") or {}
    if state == "rolled-back":
        return {"verdict": "NO-GO", "judge": "verify", "line": release.get("verdict")}
    if state == "held":
        answered = None
        if isinstance(rels, list):
            for r in rels:
                if r["ticket"] == tid and r["version"] == release.get("version") and r["answer"]:
                    answered = r["answer"]
        if answered and answered["answer"] == "GO":
            return {"verdict": "GO", "judge": "human",
                    "line": f"promoted by {answered['by']} via {answered['via']}"}
        if answered:
            return {"verdict": "NO-GO", "judge": "human",
                    "line": f"rejected by {answered['by']} via {answered['via']}"}
        return {"verdict": "GO", "judge": "verify", "line": release.get("verdict")}
    ci = doc.get("ci") or {}
    if ci.get("verdict") == "NO-GO":
        return {"verdict": "NO-GO", "judge": "gates", "line": ci.get("line")}
    review = doc.get("review") or {}
    if review.get("verdict") == "changes-requested":
        reason = review.get("reason") or ""
        return {"verdict": "NO-GO", "judge": "review", "line": reason.splitlines()[0] if reason else ""}
    return {"verdict": "pending", "judge": None, "line": None}


def tickets(rels):
    store = env("STATE_STORE")
    if not store:
        return unread("STATE_STORE not set; run this via ./go logbook")
    get = f"adapters/{store}/get.sh"
    out = []
    for path in sorted(glob.glob(os.path.join(env("TICKETS_DIR", "tickets"), "*.md"))):
        tid = os.path.basename(path)[:-3]
        rc, stdout, err = run("sh", get, "tickets", tid)
        if rc == 1:
            continue  # absent: the tower never opened this ticket
        if rc != 0:
            return unread(err)
        try:
            doc = json.loads(stdout)
        except ValueError:
            return unread(f"{get} printed no JSON for {tid}")
        file_title, origin = ticket_file_facts(path)
        history = doc.get("history") or []
        spec = pick(doc.get("spec"), ("title", "sha256"))
        out.append({
            "id": tid,
            "title": (spec or {}).get("title") or file_title or tid,
            "origin": origin,
            "perimeter": perimeter(path),
            "state": doc.get("state"),
            "attempt": doc.get("attempt"),
            "opened_at": history[0].get("at") if history else None,
            "updated_at": history[-1].get("at") if history else None,
            "outcome": outcome(doc, tid, rels),
            "spec": spec,
            "task": pick(doc.get("task"), TASK_KEYS),
            "rework": doc.get("rework") or [],
            "pr": pick(doc.get("pr"), ("number", "url", "base_sha", "head_sha")),
            "ci": pick(doc.get("ci"), ("verdict", "failed", "risk_level", "decision", "line", "details", "head_sha")),
            "review": pick(doc.get("review"), ("role", "verdict", "reason", "harness", "model")),
            "merge": pick(doc.get("merge"), ("sha", "head_sha")),
            "release": pick(doc.get("release"), ("version", "image", "verdict", "numbers")),
            "history": history,
        })
    return out


# --- thresholds -----------------------------------------------------------


def provenance(path, key):
    """{kind, text} from the comment block directly above KEY= in path, else None."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    for i, raw in enumerate(lines):
        m = _KEY_LINE.match(raw.strip())
        if not m or m.group(1) != key:
            continue
        block = []
        j = i - 1
        while j >= 0 and lines[j].strip().startswith("#"):
            block.insert(0, lines[j].strip())
            j -= 1
        for k, c in enumerate(block):
            pm = _PROVENANCE.match(c)
            if pm:
                rest = [b.lstrip("#").strip() for b in block[k + 1:]]
                text = " ".join(t for t in [pm.group(2).strip()] + rest if t)
                return {"kind": pm.group(1), "text": text}
        return None
    return None


def keys_in(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return {m.group(1): m.group(2) for m in (_KEY_LINE.match(l.strip()) for l in fh) if m}
    except OSError:
        return {}


def thresholds():
    contract = env("WORKLOAD_CONTRACT")
    if not contract:
        return unread("WORKLOAD_CONTRACT not set; run this via ./go logbook")
    platform, workload_env, overrides = "policy/merge.env", f"{contract}/workload.env", f"{contract}/policy.env"
    try:
        with open(platform, encoding="utf-8") as fh:
            platform_text = fh.read()
        with open(workload_env, encoding="utf-8") as fh:
            workload_text = fh.read()
        overrides_text = ""
        if os.path.isfile(overrides):
            with open(overrides, encoding="utf-8") as fh:
                overrides_text = fh.read()
        policy, _ = effective(platform_text, workload_text, overrides_text)
    except OSError as exc:
        return unread(f"{exc.filename}: {exc.strerror}")
    except PolicyError as exc:
        where = {"platform": platform, "workload": workload_env}.get(exc.source, overrides)
        return unread(f"{where}: {exc}")
    overridden = keys_in(overrides)
    out = []
    for key, value in policy.items():
        if not _DECIMAL.match(value):
            continue
        if key in overridden:
            path, over = overrides, True
        elif key == "MUTATION_BASELINE":
            path, over = workload_env, False
        else:
            path, over = platform, False
        judge = "verify" if key in VERIFY_KEYS else "gates"
        out.append({"key": key, "value": value, "file": path, "overridden": over, "judge": judge,
                    "provenance": provenance(path, key)})
    rework = "policy/rework.env"
    value = keys_in(rework).get("REWORK_MAX_ATTEMPTS")
    if value is None:
        return unread(f"{rework} sets no REWORK_MAX_ATTEMPTS")
    out.append({"key": "REWORK_MAX_ATTEMPTS", "value": value, "file": rework, "overridden": False,
                "judge": "rework", "provenance": provenance(rework, "REWORK_MAX_ATTEMPTS")})
    return out


# --- post mortems ---------------------------------------------------------


def postmortems():
    out = []
    base = env("POSTMORTEM_DIR", ".crew/postmortem")
    for path in sorted(glob.glob(os.path.join(base, "*", "postmortem.json"))):
        pm = read_json(path)
        if is_unread(pm) or not isinstance(pm, dict):
            return pm if is_unread(pm) else unread(f"{path} is not an object")
        run_ = pm.get("run") or {}
        recorded = run_.get("recorded")
        replayed = bool(run_.get("source") and isinstance(recorded, dict))
        who = recorded if replayed else run_
        out.append({
            "name": pm.get("incident"),
            "title": pm.get("title"),
            "window": pm.get("window"),
            "release": pm.get("release"),
            "chain": pm.get("chain"),
            "timeline": pm.get("timeline") or [],
            "causes": pm.get("causes") or [],
            "actions": [pick(a, ("n", "title", "ticket")) for a in pm.get("actions") or []],
            "run": {"provider": who.get("provider"), "model": who.get("model"), "replayed": replayed},
        })
    return out


# --- launch ---------------------------------------------------------------


def plane():
    delivery = env("DELIVERY")
    if not delivery:
        return unread("DELIVERY not set; run this via ./go logbook")
    status = f"adapters/{delivery}/status.sh"
    rc, out, err = run("sh", status)
    if rc != 0:
        return unread(err)
    try:
        value = json.loads(out)
    except ValueError:
        return unread(f"{status} printed no JSON")
    return {k: value.get(k) for k in ("stable", "canary", "weight")}


def brief():
    out_dir = env("PROMOTE_OUT", ".crew/promote")
    path = os.path.join(out_dir, "brief.md")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        data = fh.read()
    author = None
    try:
        with open(os.path.join(out_dir, "brief.author"), encoding="utf-8") as fh:
            author = fh.readline().rstrip("\n") or None
    except OSError:
        pass
    return {"text": data.decode("utf-8"), "sha256": hashlib.sha256(data).hexdigest(), "author": author}


def launch(record):
    p = plane()
    b = brief()
    pressable, preparable, reason = False, False, None
    if record is None:
        reason = "no release record; run ./go release"
    elif is_unread(record):
        reason = record["error"]
    else:
        v = record.get("version")
        answered = answer_of(record)
        if record.get("verdict") != "held":
            reason = f"release {v} is {record.get('verdict')}, nothing to promote"
        elif "promoted" in record or "rejected" in record:
            a = answered or {"answer": "GO" if "promoted" in record else "NO-GO", "by": "?", "via": "?"}
            reason = f"release {v} was already answered {a['answer']} by {a['by']} via {a['via']}"
        elif is_unread(p):
            reason = f"the plane cannot be read: {p['error']}"
        elif p.get("canary") != v or p.get("weight") != record.get("held_at"):
            reason = (f"the record holds {v} at {record.get('held_at')}%, "
                      f"the plane has canary {p.get('canary') or 'none'} at {p.get('weight')}%")
        elif b is None or (v or "") not in b["text"]:
            preparable, reason = True, f"no brief for {v} yet; prepare it"
        else:
            pressable = True
    return {"record": record, "plane": p, "brief": b, "pressable": pressable,
            "preparable": preparable, "disabled_reason": reason}


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    record = read_json(env("RELEASE_RECORD", ".crew/release.json"))
    rels = releases(record)
    book = {
        "schema": SCHEMA,
        "source": source(),
        "tickets": tickets(rels),
        "releases": rels,
        "postmortems": postmortems(),
        "thresholds": thresholds(),
        "launch": launch(record),
    }
    text = json.dumps(book, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    tmp = args.out + ".tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, args.out)
    except OSError as exc:
        print(f"cannot write {args.out}: {exc.strerror}")
        return 1
    print(len(book["tickets"]) if isinstance(book["tickets"], list) else 0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
