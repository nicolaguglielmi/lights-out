"""The logbook's reader (docs/adr/0047 §2): stdlib only.

    python3 core/logbook.py --out <file>

Assembles `lights-out/logbook/1.8` (core/logbook/1.8.schema.json) from the
tower's documents (through the STATE_STORE port's get verb), the release
log and the latest release record, the post mortems, the policy files and
the plane's status (through the DELIVERY port's status verb) and the
project map's ADRs and tests (MAP_FILE, docs/adr/0059 §3, docs/adr/0071 §1), what the
contract gives the agents (core/agents.py, docs/adr/0073 §10), and per ticket
what its last run used: the task's `mcp` and the review's `instructions`
(docs/adr/0073 §10, the ticket's back), the spend per ticket, role and day
and the delivery measures in the words `./go spend` and `./go dora` print
(docs/adr/0083 and 0082, amendments of 2026-11-13), and each ticket's flow
strip, its fourteen nodes from the request to 100% (docs/adr/0085), the role
matrix (docs/adr/0086), the burn and the day's projection, the dev-worker's
attempt in flight from the budget hooks' count (docs/adr/0087), and the
intakes, each with its questions, proposal and confirmation (docs/adr/0090 §7), and the
screen before a role read a ticket's or an intake's text (docs/adr/0091 §9). Every rule
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
import fnmatch
import glob
import hashlib
import json
import os
import re
import sqlite3
import subprocess  # nosec B404 - git and the adapter verbs the profile names (docs/adr/0052 §3)
import sys

try:
    from .policy import PolicyError, effective
    from .spec import fields
    from .assign import HOLDING, overlaps
    from .map import SCHEMA as MAP_SCHEMA
    from . import agents as contract_agents
    from . import dora as delivery
    from . import spend as ledger
    from . import network as contract_network
    from . import thresholds as contract_thresholds
except ImportError:  # run as a script: core/ is sys.path[0]
    from policy import PolicyError, effective
    from spec import fields
    from assign import HOLDING, overlaps
    from map import SCHEMA as MAP_SCHEMA
    import agents as contract_agents
    import dora as delivery
    import spend as ledger
    import network as contract_network
    import thresholds as contract_thresholds

SCHEMA = "lights-out/logbook/1.8"
# The kanban's columns, the tower's states in its table's order (docs/adr/
# 0055 §7); a state outside the list gets a column after them.
COLUMNS = ("requested", "questions", "spec-ready", "opened", "pr-opened", "review", "ci-passed", "ci-failed",
           "needs-human", "merged", "main-green", "held", "rolled-back")
_DECIMAL = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_PROVENANCE = re.compile(r"^#\s*Provenance, (measurement|external|decision):\s*(.*)$")
_KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
# A gate's one line (CLAUDE.md, the gate output contract).
_GATE_LINE = re.compile(r"^(GO|NO-GO): ([a-z0-9-]+)(?: — .*)?$")
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
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30)  # nosec B603 - an argv list, no shell, ADR 0052 §3
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


# --- the project map's ADRs (docs/adr/0059 §3) -----------------------------


def adrs():
    """{map_sha, items} from MAP_FILE, read-only; unread when there is none."""
    path = env("MAP_FILE", ".crew/map.sqlite")
    if not os.path.isfile(path):
        return unread(f"no project map at {path}: ./go map builds it")
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
            if meta.get("schema") != MAP_SCHEMA:
                return unread(f"{path} has map schema {meta.get('schema')}, the logbook reads {MAP_SCHEMA}: "
                              "./go map rebuilds it")
            nodes = {i: (kind, name) for i, kind, name in db.execute("SELECT id, kind, name FROM node")}
            rows = db.execute("SELECT n.id, n.name, n.title, d.status, d.text FROM node n JOIN doc d ON d.node = n.id "
                              "WHERE n.kind = 'adr' ORDER BY n.name").fetchall()
            files, links = {}, {}
            for src, target in db.execute("SELECT src, target FROM cite WHERE kind = 'path' AND ok = 1"):
                files.setdefault(src, set()).add(target)
            for src, dst in db.execute("SELECT src, dst FROM edge WHERE kind = 'mentions'"):
                links.setdefault(src, set()).add(dst)
                links.setdefault(dst, set()).add(src)
        finally:
            db.close()
    except sqlite3.Error as exc:
        return unread(f"{path} is not a project map: {exc}")

    def number(name):
        return os.path.basename(name)[:4]

    items = []
    for i, name, title, status, text in rows:
        near = [nodes[j] for j in links.get(i, ()) if j in nodes]
        items.append({"number": number(name), "path": name, "title": title, "status": status, "text": text,
                      "files": sorted(files.get(i, ())),
                      "adrs": sorted({number(n) for k, n in near if k == "adr"}),
                      "tickets": sorted({n for k, n in near if k == "ticket"})})
    return {"map_sha": meta.get("sha"), "items": items}


def map_tests():
    """{path: sorted tests} for every file the project map knows, from its
    `tests` edges (docs/adr/0054, docs/adr/0071 §1); None when the map is
    unread, as `adrs` says why."""
    path = env("MAP_FILE", ".crew/map.sqlite")
    if not os.path.isfile(path):
        return None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
            if meta.get("schema") != MAP_SCHEMA:
                return None
            out = {name: [] for (name,) in db.execute("SELECT name FROM node WHERE kind IN ('file', 'test')")}
            for test, target in db.execute("SELECT t.name, f.name FROM edge e JOIN node t ON t.id = e.src "
                                           "JOIN node f ON f.id = e.dst WHERE e.kind = 'tests'"):
                out.setdefault(target, []).append(test)
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return {k: sorted(set(v)) for k, v in out.items()}


def governing(book_adrs, tickets):
    """Each ticket's `adrs`, the ADRs citing a file its perimeter matches;
    each ADR's `tickets` gains the tickets it governs."""
    items = book_adrs.get("items") if isinstance(book_adrs, dict) else None
    for t in tickets if isinstance(tickets, list) else []:
        globs = t["perimeter"] if isinstance(t.get("perimeter"), list) else []
        t["adrs"] = sorted(a["number"] for a in items or []
                           if any(fnmatch.fnmatchcase(f, g) for f in a["files"] for g in globs))
        for a in items or []:
            if a["number"] in t["adrs"]:
                a["tickets"] = sorted(set(a["tickets"]) | {t["id"]})


# --- releases -------------------------------------------------------------


# What an answer records beside who and how (docs/adr/0079 §1); null when
# a record from before it has none.
ANSWER_WHY = ("reason", "brief_sha256", "brief_author")


def answer_of(obj):
    """{answer, by, via, at, reason, brief_sha256, brief_author} from a
    record's promoted/rejected object, else None."""
    for key, answer in (("promoted", "GO"), ("rejected", "NO-GO")):
        a = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(a, dict) and all(k in a for k in ("by", "via", "at")):
            return dict({"answer": answer, "by": a["by"], "via": a["via"], "at": a["at"]},
                        **{k: a.get(k) for k in ANSWER_WHY})
    return None


def release_entry(record, at, ticket, answer, origin):
    return {
        "version": record.get("version"),
        "stable": record.get("stable"),
        "image": record.get("image"),
        "stages": record.get("stages") or [],
        "verdict": record.get("verdict"),
        "held_at": record.get("held_at"),
        "reason": record.get("reason"),
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
                answer = {k: later.get(k) for k in ("answer", "by", "via", "at") + ANSWER_WHY}
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
    """The globs under `## Perimeter`, as core/perimeter.sh reads them: each
    `- ` line up to the next `## `, backticks dropped."""
    globs, inside = [], False
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("## Perimeter"):
                inside = True
            elif inside and line.startswith("## "):
                break
            elif inside and line.startswith("- "):
                globs.append(line[2:].lstrip(" ").replace("`", ""))
    return globs or unread(f"ticket {path} has no ## Perimeter section")


TASK_KEYS = ("role", "harness", "provider", "model", "thinking", "turns", "tokens", "duration_s",
             "input_sha256", "replayed")


def outcome(doc, tid, rels):
    history = doc.get("history") or []
    last = history[-1] if history else {}
    state = doc.get("state")
    if isinstance(last, dict) and last.get("error"):
        return {"verdict": "stranded", "judge": "step", "line": last["error"]}
    if state == "needs-human":
        # The budget's reason, when it is what stopped the next attempt (docs/adr/0083,
        # amendment §4); the judge stays the one that rejected the last attempt.
        r = (doc.get("rework") or [{}])[-1]
        budget = doc.get("budget") if isinstance(doc.get("budget"), dict) else {}
        if budget.get("line"):
            return {"verdict": "needs-human", "judge": r.get("judge"), "line": budget["line"], "stop": "budget"}
        return {"verdict": "needs-human", "judge": r.get("judge"), "line": r.get("line"), "stop": "cap"}
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


_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_JURY_FIELDS = ("mode", "cross_vendor", "author_provider", "reviewer_provider", "reviewer_model", "reviewer_harness", "reason")


def _normalize_jury(raw):
    if not isinstance(raw, dict):
        return None
    out = {}
    for key in _JURY_FIELDS:
        if key in raw and raw[key] is not None:
            val = raw[key]
            if isinstance(val, (str, bool)):
                out[key] = val
    return out or None


def ticket_jury(doc, tid):
    """Cross-provider review jury metadata from ticket doc or crew review output."""
    for candidate in (
        doc.get("jury") if isinstance(doc, dict) else None,
        (doc.get("review") or {}).get("jury") if isinstance(doc, dict) and isinstance(doc.get("review"), dict) else None,
    ):
        norm = _normalize_jury(candidate)
        if norm is not None:
            return norm
    crew_base = env("CREW_DIR", ".crew")
    if crew_base and tid and _SAFE_ID.match(tid) and not tid.startswith("."):
        run_obj = read_json(os.path.join(crew_base, tid, "review", "out", "run.json"))
        if isinstance(run_obj, dict) and not is_unread(run_obj):
            for candidate in (
                run_obj.get("jury"),
                (run_obj.get("recorded") or {}).get("jury") if isinstance(run_obj.get("recorded"), dict) else None,
            ):
                norm = _normalize_jury(candidate)
                if norm is not None:
                    return norm
    return None


def ticket_provenance_summary(doc, tid):
    """Compact deterministic SLSA/in-toto receipt summary when present."""
    if isinstance(doc, dict):
        direct = doc.get("provenance_summary")
        if isinstance(direct, dict):
            return direct
    prov_dir = env("PROVENANCE_DIR", ".crew/provenance")
    version = (doc.get("release") or {}).get("version") if isinstance(doc, dict) and isinstance(doc.get("release"), dict) else None
    for subject_id in (version, tid):
        if not isinstance(subject_id, str) or not subject_id or not _SAFE_ID.match(subject_id) or subject_id.startswith("."):
            continue
        stmt_path = os.path.join(prov_dir, f"{subject_id}.intoto.json")
        if not os.path.isfile(stmt_path):
            continue
        try:
            with open(stmt_path, "rb") as fh:
                stmt_bytes = fh.read()
            stmt = json.loads(stmt_bytes.decode("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(stmt, dict) or stmt.get("_type") != "https://in-toto.io/Statement/v1":
            continue
        pred = stmt.get("predicate") if isinstance(stmt.get("predicate"), dict) else {}
        bundle_path = f"{stmt_path}.bundle"
        has_bundle = os.path.isfile(bundle_path) and os.path.getsize(bundle_path) > 0
        bundle_sha = None
        if has_bundle:
            try:
                with open(bundle_path, "rb") as bfh:
                    bundle_sha = hashlib.sha256(bfh.read()).hexdigest()
            except OSError:
                has_bundle = False
        missing_list = [str(m) for m in (pred.get("missing") or []) if m is not None]
        review_pred = pred.get("review") if isinstance(pred.get("review"), dict) else {}
        summary = {
            "subject": subject_id,
            "statement": stmt_path,
            "statement_sha256": hashlib.sha256(stmt_bytes).hexdigest(),
            "predicate_type": stmt.get("predicateType"),
            "signed": has_bundle,
            "bundle": bundle_path if has_bundle else None,
            "bundle_sha256": bundle_sha,
            "missing": missing_list,
            "missing_count": len(missing_list),
            "complete": len(missing_list) == 0,
            "adrs_count": len(pred.get("adrs") or []) if isinstance(pred.get("adrs"), list) else 0,
            "agents_count": len(pred.get("agents") or []) if isinstance(pred.get("agents"), list) else 0,
            "gates_count": len(pred.get("gates") or []) if isinstance(pred.get("gates"), list) else 0,
        }
        review_jury = _normalize_jury(review_pred.get("jury"))
        if review_jury is not None:
            summary["review_jury"] = review_jury
        if isinstance(pred.get("attestation"), dict):
            summary["codehost_attestation"] = pred["attestation"]
        return summary
    return None


def tickets(rels, docs=None):
    """The tickets the tower holds; DOCS, when given, collects each one's tower document by id."""
    store = env("STATE_STORE")
    if not store:
        return unread("STATE_STORE not set; run this via ./go logbook")
    get = f"adapters/{store}/get.sh"
    out = []
    for path in sorted(glob.glob(os.path.join(env("TICKETS_DIR", "tickets"), "*.md"))):
        if path.endswith(INTAKE_SUFFIX):
            continue  # an intake is never a ticket (docs/adr/0090 §7)
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
        if docs is not None and isinstance(doc, dict):
            docs[tid] = doc
        file_title, origin = ticket_file_facts(path)
        with open(path, encoding="utf-8") as fh:
            declared = fields(fh.read())
        history = doc.get("history") or []
        spec = pick(doc.get("spec"), ("title", "sha256", "criteria", "by", "verdict", "line", "at"))
        entry = {
            "id": tid,
            "title": (spec or {}).get("title") or file_title or tid,
            "origin": origin,
            "feature": declared["feature"],
            "priority": declared["priority"],
            "perimeter": perimeter(path),
            "state": doc.get("state"),
            "attempt": doc.get("attempt"),
            "opened_at": history[0].get("at") if history else None,
            "updated_at": history[-1].get("at") if history else None,
            "outcome": outcome(doc, tid, rels),
            "spec": spec,
            "task": pick(doc.get("task"), TASK_KEYS + ("mcp",)),
            "rework": [judged(r) for r in doc.get("rework") or []],
            "pr": pick(doc.get("pr"), ("number", "url", "base_sha", "head_sha")),
            "ci": ci_entry(doc),
            "review": pick(doc.get("review"), ("role", "verdict", "reason", "harness", "model", "instructions")),
            "merge": pick(doc.get("merge"), ("sha", "head_sha")),
            "map": pick(doc.get("map"), ("sha", "docs_ticket")),
            "release": pick(doc.get("release"), ("version", "image", "verdict", "numbers")),
            "history": history,
            "questions": doc.get("questions") or [],
            "product": pick(doc.get("product"), TASK_KEYS[1:] + ("consulted", "why")),
            "tasks": attempts(doc),
            "timeline": timeline(doc, tid, rels, perimeter(path)),
            "flow": flow(doc, tid, rels, perimeter(path)),
            "live": live(doc, tid),
            "intake": doc.get("intake") if isinstance(doc.get("intake"), str) else None,
            "screen": pick(doc.get("screen"), SCREEN_KEYS),
        }
        prov_sum = ticket_provenance_summary(doc, tid)
        if prov_sum is not None:
            entry["provenance_summary"] = prov_sum
        jury_info = ticket_jury(doc, tid)
        if jury_info is not None:
            entry["jury"] = jury_info
        out.append(entry)
    return out


# --- the intakes (docs/adr/0090 §7) ---------------------------------------

INTAKE_SUFFIX = ".intake.md"
PROPOSED_KEYS = ("n", "title", "problem", "criteria", "feature", "priority", "perimeter", "check", "decision",
                 "ticket", "uncertain")
# The screen before a role read a text (docs/adr/0091 §7, §9), on tickets and intakes.
SCREEN_KEYS = ("verdict", "p_yes", "confidence", "sha256", "adapter", "replayed", "why", "by", "via", "at")


def intakes(docs=None):
    """One entry per TICKETS_DIR/*.intake.md the tower holds; DOCS collects each document by id."""
    store = env("STATE_STORE")
    if not store:
        return unread("STATE_STORE not set; run this via ./go logbook")
    get = f"adapters/{store}/get.sh"
    out = []
    for path in sorted(glob.glob(os.path.join(env("TICKETS_DIR", "tickets"), "*" + INTAKE_SUFFIX))):
        iid = os.path.basename(path)[:-len(INTAKE_SUFFIX)]
        rc, stdout, err = run("sh", get, "tickets", iid)
        if rc == 1:
            continue  # absent: written, never started
        if rc != 0:
            return unread(err)
        try:
            doc = json.loads(stdout)
        except ValueError:
            return unread(f"{get} printed no JSON for {iid}")
        if not isinstance(doc, dict) or doc.get("kind") != "intake":
            continue
        if docs is not None:
            docs[iid] = doc
        with open(path, encoding="utf-8") as fh:
            words = fh.read()
        history = doc.get("history") or []
        last = history[-1] if history and isinstance(history[-1], dict) else {}
        started = doc.get("intake") if isinstance(doc.get("intake"), dict) else {}
        out.append({
            "id": iid,
            "state": doc.get("state"),
            "text": words,
            "by": started.get("by"),
            "via": started.get("via"),
            "at": started.get("at"),
            "questions": doc.get("questions") or [],
            "product": pick(doc.get("product"), TASK_KEYS[1:] + ("consulted", "why")),
            "architect": pick(doc.get("architect"), TASK_KEYS[1:] + ("consulted", "why", "by", "questions")),
            "screen": pick(doc.get("screen"), SCREEN_KEYS),
            "proposal": [pick(t, PROPOSED_KEYS) for t in doc.get("proposal") or [] if isinstance(t, dict)],
            "confirmation": pick(doc.get("confirmation"), ("by", "via", "at")),
            "tickets": doc.get("tickets") if isinstance(doc.get("tickets"), list) else [],
            "history": history,
            "stranded": pick(last, ("step", "error", "at")) if last.get("error") else None,
        })
    return out


def spend(held, docs):
    """`./go spend`'s report and words over the documents read for `tickets`
    and optional `release` brief runs from RELEASES_LOG (docs/adr/0083, 0135);
    the tickets' error when they are unread."""
    if is_unread(held):
        return held
    rel_runs = ledger.release_ledger(env("RELEASES_LOG", ".crew/releases.jsonl"))
    got = ledger.report(docs, release_runs=rel_runs)
    try:
        lim = ledger.limits()
    except ValueError as exc:
        lim = str(exc)
    b = ledger.burn(docs, lim, release_runs=rel_runs)
    got.update(limits=unread(lim) if isinstance(lim, str) else lim, lines=ledger.lines(got, lim, b),
               summary=ledger.summary(got), burn=b)
    return got


def last_event(rels, held):
    """The record's last time: the tickets' history, the releases and their answers."""
    times = []
    for t in held if isinstance(held, list) else []:
        times += [h.get("at") for h in t.get("history") or [] if isinstance(h, dict)]
    for r in rels if isinstance(rels, list) else []:
        times += [r.get("at"), (r.get("answer") or {}).get("at")]
    moments = [m for m in (delivery.when(x) for x in times) if m is not None]
    return max(moments) if moments else None


def dora(rels, held):
    """`./go dora`'s measures over the logbook's own releases and tickets, the
    window ending at the record's last event, not a clock (docs/adr/0082,
    amendment §1-§2)."""
    raw = env("WINDOW_DAYS") or "30"
    if not raw.isdigit() or int(raw) < 1:
        return unread(f"WINDOW_DAYS={raw!r} is not a whole number of days, 1 or more")
    end = last_event(rels, held)
    if end is None:
        return unread("no time in the record to end the window at: no ticket history and no release")
    got = delivery.measures({"releases": rels, "tickets": held}, int(raw), end)
    got["lines"] = [line[len("[dora] "):] for line in delivery.lines(got)]
    return got


def ci_entry(doc):
    """The ticket's ci, judged; its `why` only on a NO-GO, for the attempt
    the ticket is at (docs/adr/0078 §2)."""
    ci = pick(doc.get("ci"), ("verdict", "failed", "risk_level", "decision", "line", "details", "head_sha", "lines",
                              "sast", "seconds"))
    if ci is None:
        return None
    return judged(ci, doc.get("attempt"), ci.get("verdict") == "NO-GO")


def gate_rows(lines):
    """[{gate, verdict, line}] from a judge's gate lines, by the gate output
    contract; a line off the contract keeps its text with gate and verdict
    None (docs/adr/0071 §1). None when there are no lines."""
    if not isinstance(lines, list):
        return None
    rows = []
    for line in lines:
        m = _GATE_LINE.match(line) if isinstance(line, str) else None
        rows.append({"gate": m.group(2) if m else None, "verdict": m.group(1) if m else None, "line": line})
    return rows


def secrets_go(gates):
    """Whether the attempt's secrets gate said GO (docs/adr/0071 §1)."""
    return any(g.get("gate") == "secrets" and g.get("verdict") == "GO" for g in gates or [])


def withheld(attempt):
    return unread(f"withheld: the secrets gate did not say GO on attempt {attempt}")


def why(entry, attempt, judge):
    """Why an attempt was rejected (docs/adr/0078 §2-§3): the gate, its
    line, its details and the new SAST findings, the last two withheld
    until the same attempt's secrets gate said GO."""
    gates = gate_rows(entry.get("lines")) or []
    gate = entry.get("failed") if isinstance(entry.get("failed"), str) and entry.get("failed") else None
    if gate is None:
        gate = next((g["gate"] for g in gates if g["verdict"] == "NO-GO"), None)
    shown = secrets_go(gates)
    details, sast = entry.get("details"), entry.get("sast")
    return {
        "attempt": attempt,
        "judge": entry.get("judge") or judge,
        "gate": gate,
        "line": entry.get("line"),
        "details": (details if isinstance(details, list) else []) if shown else withheld(attempt),
        "sast": None if sast is None else sast if shown else withheld(attempt),
    }


def judged(entry, attempt=None, rejected=True):
    """A ci or rework entry with `gates` in place of its raw `lines`, and
    its `why` when it rejected the attempt (docs/adr/0078 §2): its 1.3
    `details` null where `why` withholds them."""
    if not isinstance(entry, dict):
        return entry
    out = {k: v for k, v in entry.items() if k != "lines"}
    out["gates"] = gate_rows(entry.get("lines"))
    out["why"] = why(entry, entry.get("attempt", attempt), "gates") if rejected else None
    if out["why"] and is_unread(out["why"]["details"]):
        out["details"] = None
    if out["why"] and is_unread(out["why"]["sast"]):
        out["sast"] = None
    return out


def shown_patch(patch, gates, attempt):
    """The attempt's patch only once its secrets gate said GO, else why not
    (docs/adr/0071 §1): a key a worker pasted never reaches a snapshot."""
    if patch is None:
        return None
    if secrets_go(gates):
        return patch
    return unread(f"withheld: the secrets gate did not say GO on attempt {attempt}")


def attempts(doc):
    """Every attempt's run (tower `tasks`), with its workers, its patch as
    shown_patch allows and its files (docs/adr/0071 §1)."""
    rework = {r.get("attempt"): r for r in doc.get("rework") or [] if isinstance(r, dict)}
    ci = doc.get("ci") if isinstance(doc.get("ci"), dict) else None
    # The ci on the document judged the PR's head: it vouches for the last
    # attempt only when that is the head it judged.
    head = (doc.get("pr") or {}).get("head_sha") if isinstance(doc.get("pr"), dict) else None
    if ci is not None and ci.get("head_sha") and head and ci.get("head_sha") != head:
        ci = None
    out = []
    for t in doc.get("tasks") or []:
        row = pick(t, TASK_KEYS + ("attempt",))
        if row is None:
            out.append(t)
            continue
        n = row["attempt"]
        source = rework.get(n) or (ci if ci is not None and n == doc.get("attempt") else None)
        row["members"] = t.get("members")
        row["patch"] = shown_patch(t.get("patch"), gate_rows((source or {}).get("lines")), n)
        row["files"] = t.get("files")
        out.append(row)
    return out


def ticket_files(ticket, adrs_items, holds, tests_of):
    """One row per path any attempt of TICKET touched (docs/adr/0071 §1):
    the perimeter glob, the worker, the attempts, the tests the map knows,
    the ADRs citing it and who holds it. None where the source is unread."""
    globs = ticket["perimeter"] if isinstance(ticket.get("perimeter"), list) else []
    adrs_items = None if is_unread(adrs_items) else adrs_items
    holds = None if is_unread(holds) else holds
    rows = {}
    tasks = [t for t in ticket.get("tasks") or [] if isinstance(t, dict)]
    for t in sorted(tasks, key=lambda t: t.get("attempt") if isinstance(t.get("attempt"), int) else -1):
        for f in t.get("files") or []:
            row = rows.setdefault(f["path"], {"path": f["path"], "attempts": []})
            row["member"] = f.get("member")
            if t.get("attempt") not in row["attempts"]:
                row["attempts"].append(t.get("attempt"))
    out = []
    for path in sorted(rows):
        row = rows[path]
        out.append({
            "path": path,
            "glob": next((g for g in globs if fnmatch.fnmatchcase(path, g)), None),
            "member": row["member"],
            "attempts": sorted(a for a in row["attempts"] if a is not None),
            "tests": None if tests_of is None or path not in tests_of else tests_of[path],
            "adrs": None if adrs_items is None else sorted(a["number"] for a in adrs_items if path in a["files"]),
            "held_by": None if not isinstance(holds, list) else sorted(
                h["ticket"] for h in holds if h["holding"] and any(fnmatch.fnmatchcase(path, g) for g in h["globs"])),
        })
    return out


def kanban(tickets):
    """One column per state, every ticket on one card in its state's column,
    P0 first and unprioritised last, then the oldest, then the id (§7)."""
    if not isinstance(tickets, list):
        return tickets
    states = list(COLUMNS) + sorted({t["state"] for t in tickets if t["state"] not in COLUMNS}, key=str)
    rank = {f"P{n}": n for n in range(4)}

    def order(t):
        return rank.get(t["priority"], 4), t["opened_at"] or "~", t["id"]

    return [{"state": st, "tickets": [t["id"] for t in sorted(tickets, key=order) if t["state"] == st]}
            for st in states]


def perimeters_held(tickets):
    """M27's record of who holds which files (docs/adr/0053 §5): the
    StateStore's perimeters/held, each holder with its ticket's state now
    and whether that state holds."""
    store = env("STATE_STORE")
    if not store or not isinstance(tickets, list):
        return unread("STATE_STORE not set; run this via ./go logbook") if not store else tickets
    rc, stdout, err = run("sh", f"adapters/{store}/get.sh", "perimeters", "held")
    if rc == 1:
        return []
    if rc != 0:
        return unread(err)
    try:
        held = (json.loads(stdout) or {}).get("tickets") or {}
    except (ValueError, AttributeError):
        return unread("perimeters/held is not a JSON object")
    states = {t["id"]: t["state"] for t in tickets}
    return [{"ticket": tid, "globs": globs, "state": states.get(tid), "holding": states.get(tid) in HOLDING}
            for tid, globs in sorted(held.items())]


def waiting(ticket, holds):
    """A ticket at `opened` whose last assign was refused waits (ADR 0053
    §5-§6); the holders are the holding tickets whose globs overlap its own
    perimeter now, none when the refusal was the base's."""
    history = ticket.get("history") or []
    last = history[-1] if history else {}
    if ticket.get("state") != "opened" or last.get("step") != "assign" or not last.get("error"):
        return None
    refused = [h for h in history if h.get("step") == "assign" and h.get("error")]
    mine = ticket.get("perimeter") if isinstance(ticket.get("perimeter"), list) else []
    holders = sorted({h["ticket"] for h in holds if isinstance(holds, list) and h["holding"]
                      and h["ticket"] != ticket["id"] and any(overlaps(a, b) for a in mine for b in h["globs"])})
    return {"since": refused[0].get("at"), "refusals": len(refused), "holders": holders,
            "record": "tower history, perimeters/held"}


def docs_to_realign(tickets):
    """M28's docs tickets (docs/adr/0054 §4): each merge that left docs to
    realign, with the ticket it filed and that ticket's state."""
    if not isinstance(tickets, list):
        return tickets
    states = {t["id"]: t["state"] for t in tickets}
    return [{"ticket": t["id"], "merge_sha": t["map"].get("sha"), "docs_ticket": t["map"]["docs_ticket"],
             "docs_ticket_state": states.get(t["map"]["docs_ticket"])}
            for t in tickets if isinstance(t.get("map"), dict) and t["map"].get("docs_ticket")]


# --- the timeline (docs/adr/0055 §6) -----------------------------------------


def row(phase, at, line, record, verdict=None, **more):
    return dict({"phase": phase, "at": at, "verdict": verdict, "line": line, "record": record}, **more)


def cost(task):
    t = task or {}
    return {k: t.get(k) for k in ("attempt", "harness", "model", "turns", "tokens", "duration_s", "replayed")}


def timeline(doc, tid, rels, globs):
    """One row per phase, in the order the tower's history holds them; each
    names the record it comes from. A phase with no record has no row."""
    history = [h for h in doc.get("history") or [] if isinstance(h, dict)]
    questions = [q for q in doc.get("questions") or [] if isinstance(q, dict)]
    asked = {src: [q for q in questions if q.get("from") == src] for src in ("product", "spec")}
    answered = [q for q in questions if q.get("answer")]
    tasks = {t.get("attempt"): t for t in doc.get("tasks") or [] if isinstance(t, dict)}
    spec, product = doc.get("spec") or {}, doc.get("product") or {}
    last = {h.get("step"): i for i, h in enumerate(history)}
    rows = []
    for i, h in enumerate(history):
        step, at, state = h.get("step"), h.get("at"), h.get("state")
        if h.get("error") and step == "assign":
            rows.append(row("wait", at, f"assign refused ({h['error']}): the ticket waits at opened for "
                            "./go resume (docs/adr/0053 §5-§6)", "tower history", "waiting"))
            continue
        if h.get("error"):
            rows.append(row(step, at, h["error"], "tower history", "failed", attempt=h.get("attempt")))
            continue
        if step == "request":
            who = {"by": h["by"], "via": h.get("via")} if h.get("by") else {}
            rows.append(row("request", at, "requested", "tower history", **who))
        elif step == "open":
            rows.append(row("open", at, "opened directly: no request, no spec gate", "tower history"))
        elif step in ("clarify", "answer"):
            latest = i == last.get(step) and i >= max(last.get("clarify", -1), last.get("answer", -1))
            if latest and product.get("consulted") is False:
                line = f"not consulted: {product.get('why')}"
            elif state == "questions":
                line = "asked a question"
            else:
                line = f"confirmed {len(spec.get('criteria') or [])} criteria" if latest else "confirmed the criteria"
            rows.append(row("product", at, line, "tower product", state,
                            **(cost(dict(product, attempt=None)) if latest else {})))
            if state == "questions" and asked["product"]:
                q = asked["product"].pop(0)
                rows.append(row("question", q.get("asked_at"), q.get("question"), "tower questions", by="product"))
        elif step == "answered" and answered:
            q = answered.pop(0)
            rows.append(row("answer", q.get("answered_at"), q.get("answer"), "tower questions",
                            by=q.get("answered_by"), via=q.get("answered_via")))
        elif step == "spec":
            if state == "opened":
                rows.append(row("spec", at, spec.get("line") if i == last["spec"] else "GO: spec", "tower spec", "GO"))
            else:
                q = asked["spec"].pop(0) if asked["spec"] else {}
                rows.append(row("spec", at, f"NO-GO: spec — {q.get('question')}", "tower spec", "NO-GO"))
        elif step in ("assign", "rework"):
            n = h.get("attempt")
            if step == "assign":
                pr = doc.get("pr") or {}
                refused = [e for e in history[:i] if e.get("step") == "assign" and e.get("error")]
                waited = {"refusals": len(refused), "since": refused[0].get("at")} if refused else None
                rows.append(row("assign", at, f"PR #{pr.get('number')}" if pr.get("number") else "assigned",
                                "tower history", perimeter=globs, wait=waited))
            else:
                rw = [r for r in doc.get("rework") or [] if isinstance(r, dict)]
                prior = rw[n - 2] if isinstance(n, int) and 0 <= n - 2 < len(rw) else {}
                rows.append(row("rework", at, prior.get("line"), "tower rework", attempt=n, judge=prior.get("judge")))
            task = tasks.get(n) or (doc.get("task") if not tasks and n == doc.get("attempt") else None)
            if task:
                rows.append(row("attempt", at, None, "tower tasks" if tasks else "tower task", **cost(dict(task, attempt=n))))
        elif step == "ci":
            ci = doc.get("ci") or {}
            current = i == last["ci"]
            rows.append(row("gates", at, ci.get("line") if current else None, "tower ci", state, attempt=h.get("attempt")))
            if current:
                shown = why(ci, doc.get("attempt"), "gates")["details"] if ci.get("verdict") == "NO-GO" else ci.get("details")
                rows += [row("gate", at, d, "tower ci details") for d in (shown if isinstance(shown, list) else [])]
        elif step == "review":
            r = doc.get("review") or {}
            rows.append(row("review", at, (r.get("reason") or "").split("\n")[0] or None if i == last["review"] else None,
                            "tower review", r.get("verdict") if i == last["review"] else state))
        elif step == "merge":
            rows.append(row("merge", at, (doc.get("merge") or {}).get("sha"), "tower merge", state))
        elif step == "release":
            rel = doc.get("release") or {}
            mine = [r for r in rels if r["ticket"] == tid and r["version"] == rel.get("version")] \
                if isinstance(rels, list) else []
            for stage in (mine[-1]["stages"] if mine else []):
                said = str(stage.get("verdict") or "")
                # A stage judged after docs/adr/0080 §1 has its own time and numbers.
                more = {"slo": stage["slo"]} if isinstance(stage.get("slo"), list) else {}
                rows.append(row("canary", stage.get("judged_at") or mine[-1]["at"], said, "release log",
                                "GO" if said.startswith("GO") else "NO-GO",
                                pct=stage.get("pct"), numbers=stage.get("numbers") or [], **more))
            rows.append(row("release", at, rel.get("verdict"), "tower release", state))
            answer = mine[-1]["answer"] if mine else None
            if answer:
                rows.append(row("sign-off", answer.get("at"), f"{answer.get('answer')} by {answer.get('by')}",
                                "release log", answer.get("answer"), via=answer.get("via"), reason=answer.get("reason")))
        elif step == "resume":
            rows.append(row("resume", at, f"{h.get('resumed')} requested again", "tower history"))
    end = outcome(doc, tid, rels)
    if end["verdict"] != "pending":
        rows.append(row("outcome", None, end["line"], f"outcome, judged by {end['judge']}", end["verdict"]))
    return rows


# --- the roles and the run in flight (docs/adr/0086, docs/adr/0087 §4) -------

# Provenance, decision: docs/adr/0087 §4, proposed there for the owner to
# confirm: the warning comes at 80% of TOKEN_BUDGET, the provider's when the
# role sets one (docs/adr/0092 §7). On M24's Gemini runs each
# model call resent about 45,000 tokens (measurement, the phase D handoff):
# the last 20% of 2,600,000 is about eleven calls before the stop.
WARN_NUM, WARN_DEN = 4, 5


def role_keys(role):
    """(KEY=value pairs of ROLES_DIR/<role>/role.env, None) or (None, why)."""
    path = f"{env('ROLES_DIR', 'roles')}/{role}/role.env"
    try:
        with open(path, encoding="utf-8") as fh:
            return {m.group(1): m.group(2).strip() for m in (_KEY_LINE.match(l.strip()) for l in fh) if m}, None
    except UnicodeDecodeError:
        return None, f"{path} is not UTF-8"
    except OSError as exc:
        return None, f"{path}: {exc.strerror}"


def whole(value):
    return int(value) if isinstance(value, str) and value.isdigit() else None


def token_budget(keys):
    """The role's TOKEN_BUDGET for the profile's provider (docs/adr/0092 §7):
    TOKEN_BUDGET_<PROVIDER> when the role sets it, else TOKEN_BUDGET."""
    return ledger.token_budget(keys or {}, env("MODEL_PROVIDER"))


def live(doc, tid):
    """The dev-worker's attempt in flight (docs/adr/0087 §4): the budget hooks'
    count while crew.sh has emptied the run's directory and the harness has
    not yet written run.json; None otherwise."""
    state = doc.get("state")
    history = [h for h in doc.get("history") or [] if isinstance(h, dict)]
    if state not in ("opened", "ci-failed") or (history and history[-1].get("error")):
        return None
    out = os.path.join(env("CREW_DIR", ".crew"), tid, "out")
    if os.path.exists(os.path.join(out, "run.json")):
        return None
    try:
        with open(os.path.join(out, "tokens"), encoding="utf-8") as fh:
            tokens = whole(fh.read().strip())
    except (OSError, UnicodeDecodeError):
        return None
    if tokens is None:
        return None
    keys, _ = role_keys("dev-worker")
    budget = token_budget(keys)
    attempt = 1 if state == "opened" else (doc.get("attempt") or 1) + 1
    warn = budget is not None and tokens * WARN_DEN >= budget * WARN_NUM
    if budget is None:
        line = f"dev-worker, attempt {attempt}: {tokens:,} tokens, no budget"
    else:
        line = f"dev-worker, attempt {attempt}: {tokens:,} of {budget:,} tokens ({tokens * 100 // budget}%)"
        if warn:
            line += f" — near TOKEN_BUDGET, the run stops at {budget:,}"
    return {"role": "dev-worker", "attempt": attempt, "tokens": tokens, "budget": budget, "warn": warn, "line": line}


def used_of(tokens):
    """A run's tokens in the budget's unit: total, else input + output, else None."""
    if not isinstance(tokens, dict):
        return None
    if isinstance(tokens.get("total"), int):
        return tokens["total"]
    tin, tout = tokens.get("input"), tokens.get("output")
    return tin + tout if isinstance(tin, int) and isinstance(tout, int) else None


def roles(docs, held):
    """(rows, summary): one row per role of ROLES_DIR (docs/adr/0086 §1), what the
    profile configures, its last run in the ledger and its run in flight."""
    base = env("ROLES_DIR", "roles")
    if not os.path.isdir(base):
        return unread("no roles/ directory: the six roles are its subdirectories"), None
    names = sorted(d for d in os.listdir(base) if os.path.isfile(os.path.join(base, d, "role.env")))
    known = contract_network.TABLE["roles"]
    order = [r for r in known if r in names] + [r for r in names if r not in known]
    runs = [(str(e.get("at") or ""), tid, e) for tid, doc in sorted(docs.items()) for e in ledger.ledger(doc)]
    for e in ledger.release_ledger(env("RELEASES_LOG", ".crew/releases.jsonl")):
        runs.append((str(e.get("at") or ""), str(e.get("ticket") or e.get("version") or "release"), e))
    flying = [(t["id"], t["live"]) for t in held if isinstance(t.get("live"), dict)] if isinstance(held, list) else []
    rows = []
    for role in order:
        keys, error = role_keys(role)
        configured = None if keys is None else {
            "provider": env("MODEL_PROVIDER") or None, "model": env("MODEL") or None,
            "harness": keys.get("HARNESS") or env("AGENT_HARNESS") or None, "thinking": keys.get("THINKING") or None}
        budget = token_budget(keys)
        mine = [x for x in runs if x[2].get("role") == role]
        at, tid, e = max(mine, key=lambda x: (x[0], x[1])) if mine else (None, None, None)
        last = None if e is None else {"ticket": tid, "step": e.get("step"), "attempt": e.get("attempt"), "at": e.get("at"),
                                       "harness": e.get("harness"), "provider": e.get("provider"), "model": e.get("model"),
                                       "replayed": e.get("replayed") is True, "tokens": e.get("tokens"),
                                       "cost_usd": e.get("cost_usd"), "failed": e.get("failed")}
        now_ = next(({"ticket": t, "tokens": lv["tokens"], "budget": lv["budget"], "warn": lv["warn"]}
                     for t, lv in flying if lv.get("role") == role), None)
        used = now_["tokens"] if now_ else used_of(last["tokens"]) if last else None
        if now_:
            state = "running"
            line = (f"running on {now_['ticket']}: {used:,} of {budget:,} tokens" if budget is not None
                    else f"running on {now_['ticket']}: {used:,} tokens, no budget")
            line += " — near the budget" if now_["warn"] else ""
        elif last:
            state = "ran"
            line = (f"last ran on {tid} ({last['step']}, attempt {last['attempt']}) at {last['at']}: "
                    + (f"{used:,} tokens" if used is not None else "tokens unknown")
                    + (f" of {budget:,}" if budget is not None else ", no budget")
                    + (", replayed" if last["replayed"] else "") + (f", stopped: {last['failed']}" if last["failed"] else ""))
        else:
            state, line = "idle", "has not run in this record"
        row = {"role": role, "configured": configured, "budget": budget, "last": last, "running": now_,
               "used": used, "state": state, "line": line}
        if error:
            row["error"] = error
        rows.append(row)
    providers = sorted({r["configured"]["provider"] for r in rows if r["configured"] and r["configured"]["provider"]})
    summary = (f"{len(rows)} roles on {len(providers)} provider{'s' if len(providers) > 1 else ''}: {', '.join(providers)}"
               if providers else f"{len(rows)} roles on no provider: the profile replays recorded runs")
    lasts = sorted({f"{r['last']['provider']}/{r['last']['model']}" for r in rows
                    if r["last"] and r["last"]["provider"] and r["last"]["model"]})
    if lasts:
        summary += "; last runs on " + ", ".join(lasts)
    return rows, summary


# --- the flow strip (docs/adr/0085) ----------------------------------------

# The step whose failure stops each node (§3), and the states at or after a
# merge (the tower's table, docs/adr/0019 §1).
STUCK_AT = {"clarify": "ticket", "answer": "ticket", "spec": "ticket", "assign": "perimeter", "rework": "perimeter",
            "ci": "gate", "review": "pr", "merge": "pr", "mainline": "canary", "release": "canary"}
MERGED = ("merged", "main-green", "held", "rolled-back")


def seconds_between(start, end):
    a, b = delivery.when(start), delivery.when(end)
    return round((b - a).total_seconds()) if a and b else None


def flow_node(node, verdict="not-reached", start=None, end=None, line=None, gate=None, seconds=None, timed=True):
    """One node (§2): its seconds are end - start when both are there, unless
    the rule says the record measured nothing (timed=False) or gives its own."""
    if seconds is None and timed and node != "gate":
        seconds = seconds_between(start, end)
    return {"node": node, "gate": gate, "verdict": verdict, "started_at": start, "ended_at": end,
            "seconds": seconds, "line": line}


def flow(doc, tid, rels, globs):
    """The fourteen nodes of a ticket's path (docs/adr/0085 §1-§3), each from
    the record; the reader's clock is never read."""
    history = [h for h in doc.get("history") or [] if isinstance(h, dict)]
    state, attempt = doc.get("state"), doc.get("attempt")
    last = history[-1] if history else {}
    stuck = STUCK_AT.get(last.get("step")) if last.get("error") else None
    ok = [h for h in history if not h.get("error")]

    def first(pred):
        return next((h for h in ok if pred(h)), None)

    def latest(pred):
        return next((h for h in reversed(ok) if pred(h)), None)

    def at(h):
        return h.get("at") if h else None

    def failed(node, start, gate=None):
        # A gate node's times are its own seconds, never the step's (§2).
        return flow_node(node, "NO-GO", start, None if gate else last.get("at"), last.get("error"), gate=gate)

    out = []
    # ticket: the request and the spec gate.
    opened = first(lambda h: h.get("step") == "open")
    request = first(lambda h: h.get("step") == "request")
    questions = [q for q in doc.get("questions") or [] if isinstance(q, dict)]
    asked = questions[-1].get("question") if questions else None
    if opened:
        out.append(flow_node("ticket", "GO", at(opened), at(opened), "opened directly: no request, no spec gate", timed=False))
    elif stuck == "ticket":
        out.append(failed("ticket", at(request)))
    else:
        passed = latest(lambda h: h.get("step") == "spec" and h.get("state") == "opened")
        spoke = latest(lambda h: h.get("step") in ("clarify", "answer", "spec"))
        if passed:
            line = (doc.get("spec") or {}).get("line") if isinstance(doc.get("spec"), dict) else None
            out.append(flow_node("ticket", "GO", at(request), at(passed), line or "GO: spec"))
        elif state == "questions" and spoke and spoke.get("step") == "spec":
            out.append(flow_node("ticket", "NO-GO", at(request), at(spoke), f"NO-GO: spec — {asked}"))
        elif state == "questions":
            out.append(flow_node("ticket", "running", at(request), None,
                                 f"the product role asked: {asked}; it waits for an answer"))
        elif state in ("requested", "spec-ready"):
            out.append(flow_node("ticket", "running", at(request), None, "the product role and the spec gate"))
        else:
            out.append(flow_node("ticket"))

    # perimeter: assign, up to the PR.
    into = first(lambda h: h.get("state") == "opened")
    assigned = first(lambda h: h.get("step") == "assign")
    ci = doc.get("ci") if isinstance(doc.get("ci"), dict) else None
    pr = doc.get("pr") if isinstance(doc.get("pr"), dict) else {}
    inside = ", ".join(globs) if isinstance(globs, list) and globs else "its perimeter"
    if stuck == "perimeter":
        out.append(failed("perimeter", at(into)))
    elif ci and ci.get("failed") == "perimeter":
        out.append(flow_node("perimeter", "NO-GO", at(into), None, ci.get("line")))
    elif assigned:
        opened_pr = f"PR #{pr['number']} opened" if pr.get("number") is not None else "the PR opened"
        out.append(flow_node("perimeter", "GO", at(into), at(assigned), f"{opened_pr}, every file inside {inside}"))
    elif state == "opened":
        out.append(flow_node("perimeter", "running", at(into), None, "the dev-worker, attempt 1"))
    else:
        out.append(flow_node("perimeter"))

    # the gates, in the Makefile's order.
    gates = contract_network.TABLE["gates"]
    secs = ci.get("seconds") if ci and isinstance(ci.get("seconds"), dict) else {}
    rows = gate_rows(ci.get("lines")) if ci else None
    if stuck == "gate":
        out.append(failed("gate", None, gate=gates[0]))
        out += [flow_node("gate", gate=g) for g in gates[1:]]
    elif ci and ci.get("verdict") in ("GO", "NO-GO") and ci.get("failed") != "perimeter":
        said = {r["gate"]: r for r in rows or [] if r.get("gate")}
        stop = None
        for g in gates:
            if stop:
                out.append(flow_node("gate", gate=g, line=f"not run: {stop} said NO-GO first"))
            elif rows:
                r = said.get(g)
                out.append(flow_node("gate", r["verdict"], line=r["line"], gate=g, seconds=secs.get(g)) if r
                           else flow_node("gate", gate=g))
                stop = g if r and r["verdict"] == "NO-GO" else None
            elif ci.get("verdict") == "GO":
                out.append(flow_node("gate", "GO", line=ci.get("line"), gate=g))
            elif ci.get("failed") == g:
                out.append(flow_node("gate", "NO-GO", line=ci.get("line"), gate=g))
                stop = g
            elif ci.get("failed") in gates:
                out.append(flow_node("gate", "GO", gate=g))
            else:
                out.append(flow_node("gate", gate=g))
    elif state == "pr-opened":
        began = latest(lambda h: h.get("state") == "pr-opened")
        line = f"the gates judge attempt {attempt or 1}; the step reports when all have run"
        out += [flow_node("gate", "running", at(began), None, line, gate=g) for g in gates]
    else:
        out += [flow_node("gate", gate=g) for g in gates]

    # pr: the review when the risk asks for one, then the merge.
    passed = [h for h in ok if h.get("step") == "ci" and h.get("state") in ("review", "ci-passed")]
    began = at(([h for h in passed if h.get("attempt") == attempt] or passed or [None])[-1])
    review = doc.get("review") if isinstance(doc.get("review"), dict) else {}
    level = ci.get("risk_level") if ci else None
    if stuck == "pr":
        out.append(failed("pr", began))
    elif state in MERGED:
        merged = latest(lambda h: h.get("step") == "merge")
        sha = (doc.get("merge") or {}).get("sha") if isinstance(doc.get("merge"), dict) else None
        how = (f", approved by the {review.get('role')}" if review.get("verdict") == "approved"
               else f", no review: risk {level}" if level else "")
        out.append(flow_node("pr", "GO", began, at(merged), f"merged {sha[:7]}{how}" if sha else f"merged{how}"))
    elif review.get("verdict") == "changes-requested":
        judged = latest(lambda h: h.get("step") == "review")
        reason = review.get("reason") or ""
        out.append(flow_node("pr", "NO-GO", began, at(judged), reason.splitlines()[0] if reason else "changes requested"))
    elif state == "review":
        out.append(flow_node("pr", "running", began, None,
                             f"the {review.get('role') or 'test-reviewer'} reviews attempt {attempt} (risk {level})"))
    elif state == "ci-passed":
        out.append(flow_node("pr", "running", began, None, f"the merge (risk {level}, {ci.get('decision') if ci else None})"))
    else:
        out.append(flow_node("pr"))

    # canary, human, full: the ticket's release in the release log.
    rel = doc.get("release") if isinstance(doc.get("release"), dict) else {}
    mine = [r for r in rels if r["ticket"] == tid and r["version"] == rel.get("version")] if isinstance(rels, list) else []
    mine = mine[-1] if mine else {}
    stages = [s for s in mine.get("stages") or [] if isinstance(s, dict)]
    green = first(lambda h: h.get("state") == "main-green")
    released = latest(lambda h: h.get("step") == "release")
    start = (stages[0].get("started_at") if stages else None) or at(green)
    end = (stages[-1].get("judged_at") if stages else None) or at(released)
    if stuck == "canary":
        out.append(failed("canary", start))
    elif state == "held":
        out.append(flow_node("canary", "GO", start, end, f"held at {mine.get('held_at')}%: {len(stages)} stage(s) GO"))
    elif state == "rolled-back":
        why = mine.get("reason") or (stages[-1].get("verdict") if stages else None) or rel.get("verdict")
        out.append(flow_node("canary", "NO-GO", start, end, why))
    elif state == "main-green":
        out.append(flow_node("canary", "running", start, None, "the release step: deploy, then the stages"))
    else:
        out.append(flow_node("canary"))
    answer = mine.get("answer") if isinstance(mine.get("answer"), dict) else None
    held = latest(lambda h: h.get("step") == "release" and h.get("state") == "held")
    if state == "held" and answer:
        said = "promoted" if answer.get("answer") == "GO" else "rejected"
        why = f": {answer['reason']}" if answer.get("reason") else ""
        out.append(flow_node("human", "GO" if said == "promoted" else "NO-GO", at(held), answer.get("at"),
                             f"{said} by {answer.get('by')} via {answer.get('via')}{why}"))
        out.append(flow_node("full", "GO", answer.get("at"), answer.get("at"), f"100% of the traffic on {mine.get('version')}",
                             timed=False) if said == "promoted" else flow_node("full", line="rolled back by a person"))
    elif state == "held":
        out.append(flow_node("human", "running", at(held), None, f"waits for go-for-launch at {mine.get('held_at')}%"))
        out.append(flow_node("full"))
    elif state == "rolled-back":
        out += [flow_node("human", line="the canary rolled back before it"), flow_node("full")]
    else:
        out += [flow_node("human"), flow_node("full")]

    # A failed step: every node after the one it stopped is not reached.
    if stuck:
        at_ = next(i for i, n in enumerate(out) if n["node"] == stuck)
        out = out[:at_ + 1] + [flow_node(n["node"], gate=n["gate"]) for n in out[at_ + 1:]]
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
    is_stub_fixture = (
        platform_text.startswith("# Stub platform policy for ci/logbook.sh")
        and not os.path.isfile("policy/mainline.env")
        and not os.path.isfile(env("START_PLAN_ENV", ".crew/start-plan.env"))
        and not os.path.isdir(env("PROVENANCE_DIR", ".crew/provenance"))
    )
    if not is_stub_fixture:
        try:
            gov = contract_thresholds.collect(
                ".",
                contract_dir=contract,
                env=os.environ,
                include_git=True,
                roles_dir=env("ROLES_DIR", "roles"),
            )
            enriched = []
            for entry in gov.get("entries") or []:
                prov_kind = entry.get("provenance_kind")
                prov_text = entry.get("provenance")
                prov_obj = (
                    {"kind": prov_kind, "text": prov_text}
                    if prov_kind in ("decision", "external", "measurement") and prov_text
                    else None
                )
                judge = (
                    "verify"
                    if entry["key"] in VERIFY_KEYS
                    else ("rework" if entry.get("scope") in ("rework", "role") else "gates")
                )
                enriched.append({
                    "id": entry["id"],
                    "key": entry["key"],
                    "value": entry["active"] if entry["active"] is not None else "",
                    "default": entry["default"],
                    "active": entry["active"],
                    "changed": bool(entry["changed"]),
                    "file": entry["source"],
                    "source": entry["source"],
                    "default_file": entry["default_file"],
                    "overridden": bool(entry["overridden"]),
                    "judge": judge,
                    "scope": entry.get("scope"),
                    "role": entry.get("role"),
                    "provenance_kind": prov_kind,
                    "provenance": prov_obj,
                    "last_commit": entry.get("last_commit"),
                    "history": entry.get("history") or [],
                })
            return enriched
        except (contract_thresholds.ThresholdsError, OSError):
            pass
    out.append({"key": "REWORK_MAX_ATTEMPTS", "value": value, "file": rework, "overridden": False,
                "judge": "rework", "provenance": provenance(rework, "REWORK_MAX_ATTEMPTS")})
    return out


# --- agents ---------------------------------------------------------------


def agents():
    """{lines}: what the local contract gives the agents, the wording `./go
    agents` prints (docs/adr/0073 §10); unread, as thresholds, on a refusal."""
    contract = env("WORKLOAD_CONTRACT")
    if not contract:
        return unread("WORKLOAD_CONTRACT not set; run this via ./go logbook")
    try:
        return {"lines": contract_agents.lines_for(contract)}
    except contract_agents.Refusal as exc:
        return unread(f"NO-GO: agents — {exc}")
    except OSError as exc:
        return unread(f"{exc.filename}: {exc.strerror}")


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
    docs = {}
    held = tickets(rels, docs)
    asked_docs = {}
    asked = intakes(asked_docs)
    # An intake's runs count in the spend and the roles (docs/adr/0090 §7); an
    # intake that ran nothing is no line of the ledger's report.
    docs.update({k: v for k, v in asked_docs.items() if v.get("spend")})
    holds = perimeters_held(held)
    for t in held if isinstance(held, list) else []:
        t["wait"] = waiting(t, holds)
    decisions = adrs()
    governing(decisions, held)
    items = decisions.get("items") if isinstance(decisions, dict) and not is_unread(decisions) else None
    tests_of = map_tests() if items is not None else None
    for t in held if isinstance(held, list) else []:
        t["files"] = ticket_files(t, items, holds, tests_of)
    matrix, matrix_summary = roles(docs, held)
    book = {
        "schema": SCHEMA,
        "source": source(),
        "tickets": held,
        "kanban": kanban(held),
        "releases": rels,
        "postmortems": postmortems(),
        "thresholds": thresholds(),
        "agents": agents(),
        "launch": launch(record),
        "perimeters_held": holds,
        "docs_to_realign": docs_to_realign(held),
        "adrs": decisions,
        "spend": spend(held, docs),
        "dora": dora(rels, held),
        "roles": matrix,
        "roles_summary": matrix_summary,
        "intakes": asked,
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
