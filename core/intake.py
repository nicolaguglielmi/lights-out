"""The intake interview (docs/adr/0090): stdlib only. The logic of the
intake, interview, intake-answer and perimeter steps and of the
confirmation; core/tower.py and the step scripts are its entry points
(docs/adr/0052 §3).

    python3 core/intake.py new <text file> <tickets dir> [<id>]
        the client's words to <tickets dir>/<id>.intake.md, exclusive
        create, the id the next free one when not given; prints the id (§1)
    python3 core/intake.py first <doc.json> <tickets dir>
        the interview's first user message on stdout: `# Intake request`,
        a blank line, the raw text (§2)
    python3 core/intake.py answers <doc.json>
        the answers so far, as later user turns: a JSON list (§2)
    python3 core/intake.py interview <doc.json> <run dir> <exit code>
        the interview/intake-answer step's result: `intake-questions` or
        `drafted` (§2, §4)
    python3 core/intake.py proposal <doc.json>
        the architect's first message: the proposed tickets as JSON (§3)
    python3 core/intake.py perimeter <doc.json> <run dir> <exit code>
        the perimeter step's result: `proposed`, each ticket checked by
        gates/spec.sh (§3)
    python3 core/intake.py compose <ticket.json> <id> <intake id>
        the Markdown a proposed ticket becomes (§5)
    python3 core/intake.py perimeter-request <doc.json> <map.sqlite> <scope>
        the Decide request of the perimeter asked file by file: one yes/no
        question per proposed ticket and per file of the map under <scope>
        (docs/adr/0091 §6); exit 1 when it would be over the port's limit
    python3 core/intake.py decide-perimeter <doc.json> <map.sqlite> <scope> <run dir>
        the perimeter step asked file by file: the request, the Decide port,
        the result; exit 3 with one stderr line when the architect is to
        search instead (over the port's limit, or no recording at L0)
    python3 core/intake.py perimeter-decided <doc.json> <request.json> <answer.json>
        the perimeter step's result from the answer: `proposed`, the paths at
        or over PERIMETER_CONFIDENCE in the perimeter, those under it
        `uncertain` (docs/adr/0091 §6)

A step result is the JSON object core/tower.py merges into the document.
A refusal is one stderr line and exit 1, which fails the step (ADR 0034);
its line is also written to $STEP_WHY when the tower gives one (§6).
"""

import json
import os
import re
import sqlite3
import subprocess  # nosec B404 - gates/spec.sh, an argv list (docs/adr/0052 §3)
import sys
import tempfile
from datetime import datetime, timezone

try:
    from .tower import task
    from . import spec
except ImportError:  # run as a script: core/ is sys.path[0]
    from tower import task
    import spec

# Provenance, decision: docs/adr/0090 §2, one question per topic of the
# interview's agenda (who, the main scenario, out of scope, how we know it
# works, constraints).
CAP = 5
LAST = "That was the last question this interview allows: propose the tickets now."
FIRST = "# Intake request"
SUFFIX = ".intake.md"
DECISIONS = ("ask_product_owner", "propose_ticket", "confirm_acceptance")
EDITABLE = ("title", "problem", "criteria", "perimeter", "feature", "priority")
_ID = re.compile(r"^\d{4}$")


class SearchInstead(Exception):
    """The perimeter cannot be asked file by file here: the step searches (docs/adr/0091 §6)."""


class Refused(Exception):
    """A refusal; RUN, when given, is the step result's run field the tower
    ledgers for a stopped run (`{"architect": {...}}`, amendment A3)."""

    def __init__(self, message, run=None):
        super().__init__(message)
        self.run = run


class OverLimit(Refused):
    """More questions than the Decide port takes (docs/adr/0091 §1, §6): the step searches instead."""


def stopped(field, run):
    """The STEP_RUN object of a role's run that failed: its task and its stop."""
    return {field: dict(task(run), stop=run.get("stop"))}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def intake_file(tickets_dir, tid):
    return os.path.join(tickets_dir, f"{tid}{SUFFIX}")


# --- §1 the client's words -------------------------------------------------


def new(text, tickets_dir, tid=None):
    """Write TEXT to <tickets dir>/<id>.intake.md, exclusive create; return the id."""
    body = (text or "").rstrip("\r\n")
    if not body.strip():
        raise Refused("an intake needs the client's words; the text is empty")
    tid = tid or spec.next_id(tickets_dir)
    if not _ID.match(tid):
        raise Refused(f'"{tid}" is not a four-digit id')
    if os.path.exists(os.path.join(tickets_dir, f"{tid}.md")):
        raise Refused(f"{tickets_dir}/{tid}.md exists: {tid} is a ticket")
    os.makedirs(tickets_dir, exist_ok=True)
    try:
        with open(intake_file(tickets_dir, tid), "x", encoding="utf-8", newline="") as fh:
            fh.write(body + "\n")
    except FileExistsError:
        raise Refused(f"{intake_file(tickets_dir, tid)} exists: another intake took {tid}") from None
    return tid


def first(text):
    """The interview's first user message: its replay key is the raw text alone (§2)."""
    return f"{FIRST}\n\n{text.strip()}\n"


# --- §2 the interview -------------------------------------------------------


def answers(doc):
    """The answered questions' answers, in order; the CAP-th carries LAST."""
    out = [q["answer"] for q in doc.get("questions") or [] if isinstance(q, dict) and q.get("answer")]
    if len(out) >= CAP:
        out[CAP - 1] = f"{out[CAP - 1]}\n\n{LAST}"
    return out


def _run(run_dir):
    run = load(os.path.join(run_dir, "out", "run.json"), {})
    return run if isinstance(run, dict) else {}


def _calls(run_dir):
    calls = load(os.path.join(run_dir, "out", "calls.json"), [])
    return [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []


def _last_line(path):
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [line for line in fh.read().splitlines() if line.strip()]
    except OSError:
        return ""
    return lines[-1] if lines else ""


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def proposed_ticket(args, n):
    """One propose_ticket call's arguments as a proposed ticket, or Refused."""
    title, problem = _text(args.get("title")), _text(args.get("problem"))
    raw = args.get("criteria")
    criteria = [" ".join(c.split()) for c in raw if isinstance(c, str) and c.strip()] if isinstance(raw, list) else []
    if not title:
        raise Refused(f"proposed ticket {n} has no title")
    if not problem:
        raise Refused(f'proposed ticket {n} ("{title[:40]}") has no problem')
    if not criteria:
        raise Refused(f'proposed ticket {n} ("{title[:40]}") has no criterion')
    return {"n": n, "title": title, "problem": problem, "criteria": criteria,
            "feature": _text(args.get("feature")) or None, "priority": _text(args.get("priority")) or None}


def interview(doc, run_dir, rc, env):
    """The interview/intake-answer step's result (docs/adr/0090 §2, §4)."""
    run = _run(run_dir)
    if rc != 0:
        why = _last_line(os.path.join(run_dir, "run.err")) or f"the product role exited {rc}"
        if env.get("AGENT_HARNESS") == "replay" and run.get("no_recording"):
            raise Refused("no recording of this intake's interview at L0 (fixtures/adk/product); record it at L1 "
                          f"with ./go intake ... ADK_RECORD=extend, then ./go resume TICKET={doc.get('id', '<id>')}")
        raise Refused(f"the product role failed: {why}", stopped("product", run))
    calls = [c for c in _calls(run_dir) if c.get("name") in DECISIONS]
    if not calls:
        raise Refused("the product role neither asked nor proposed")
    asks = [i for i, c in enumerate(calls) if c["name"] == "ask_product_owner"]
    after = calls[asks[-1] + 1:] if asks else calls
    proposed = [c for c in after if c["name"] == "propose_ticket"]
    product = dict(task(run), consulted=True)
    if any(c["name"] == "confirm_acceptance" for c in after):
        raise Refused("the product role confirmed one ticket's criteria instead of proposing tickets")
    if proposed:
        tickets = [proposed_ticket(c.get("args") or {}, n) for n, c in enumerate(proposed, 1)]
        return {"state": "drafted", "product": product, "proposal": tickets}
    question = _text((calls[-1].get("args") or {}).get("question"))
    if not question:
        raise Refused("the product role asked an empty question")
    questions = [q for q in doc.get("questions") or [] if isinstance(q, dict)]
    if len(questions) >= CAP:
        raise Refused(f"the product role asked question {len(questions) + 1}, past the cap of {CAP} (docs/adr/0090 §2)")
    questions.append({"question": question, "from": "product", "asked_at": now()})
    return {"state": "intake-questions", "questions": questions, "product": product}


def screened(result, run_dir):
    """The step result with the screen core/screen.py wrote beside the run (docs/adr/0091 §7)."""
    screen = load(os.path.join(run_dir, "screen.json"), None)
    return dict(result, screen=screen) if isinstance(screen, dict) else result


# --- §3 the perimeter -------------------------------------------------------


def proposal_input(doc):
    """The architect's first message: the proposal, perimeter-free, as JSON."""
    items = [{"n": t.get("n"), "title": t.get("title"), "problem": t.get("problem"), "criteria": t.get("criteria")}
             for t in doc.get("proposal") or [] if isinstance(t, dict)]
    return json.dumps(items, indent=2, ensure_ascii=False) + "\n"


def _spec_split():
    try:
        from . import spec_split
    except ImportError:  # run as a script: core/ is sys.path[0]
        import spec_split
    return spec_split


def _network_split_enabled(env):
    cfg = os.environ if env is None else env
    raw = (cfg.get("INTAKE_NETWORK_SPLIT") or "off").strip().lower()
    if raw not in ("on", "off"):
        raise Refused(f"INTAKE_NETWORK_SPLIT must be on or off, got {raw!r}")
    return raw == "on"


def _resolve_network(ticket, env):
    if "network" in ticket and ticket["network"] is not None:
        try:
            return _spec_split().normalize_network(
                ticket["network"],
                perimeter=ticket.get("perimeter") or [],
                criteria=ticket.get("criteria"),
            )
        except ValueError as exc:
            raise Refused(f"ticket {ticket.get('n') or ''} invalid network split: {exc}") from None
    if _network_split_enabled(env):
        return _spec_split().propose_network_split(
            ticket.get("perimeter") or [],
            criteria=ticket.get("criteria"),
        )
    return None


def compose(ticket, tid, intake_id, env=None):
    """The Markdown of a proposed ticket, the shape of every committed one (§5)."""
    head = ["- Status: open", f"- Intake: {intake_id}"]
    head += [f"- Feature: {ticket['feature']}"] if ticket.get("feature") else []
    head += [f"- Priority: {ticket['priority']}"] if ticket.get("priority") else []
    body = [f"# {tid} — {ticket.get('title') or ''}".rstrip(), ""] + head + [
        "", "## Problem", "", (ticket.get("problem") or "").strip(), "", spec.CRITERIA, ""]
    for c in ticket.get("criteria") or []:
        body += [c, ""]
    body += [spec.PERIMETER, ""] + [f"- `{p}`" for p in ticket.get("perimeter") or []]
    members = _resolve_network(ticket, env)
    if members:
        body += ["", _spec_split().render_network_section(members).rstrip("\n")]
    return "\n".join(body).rstrip("\n") + "\n"


def gate_line(markdown):
    """The one line gates/spec.sh prints for MARKDOWN, judged as a file."""
    with tempfile.TemporaryDirectory(prefix="lights-out-intake-") as tmp:
        path = os.path.join(tmp, "draft.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        g = subprocess.run(["sh", "gates/spec.sh", path], capture_output=True, text=True,  # nosec B603 B607
                           check=False, stdin=subprocess.DEVNULL)
    lines = g.stdout.strip().splitlines()
    return lines[-1] if lines else f"gates/spec.sh exit {g.returncode}"


def perimeter(doc, run_dir, rc, env, check=gate_line):
    """The perimeter step's result (docs/adr/0090 §3)."""
    run = _run(run_dir)
    paths = {}
    net_hints = {}
    if rc != 0:
        why = _last_line(os.path.join(run_dir, "run.err")) or f"the architect role exited {rc}"
        if not (env.get("AGENT_HARNESS") == "replay" and run.get("no_recording")):
            raise Refused(f"the architect role failed: {why}", stopped("architect", run))
        architect = dict(task(run), consulted=False, why=why)
    else:
        for c in _calls(run_dir):
            if c.get("name") != "propose_perimeter":
                continue
            args = c.get("args") or {}
            try:
                n = int(args.get("ticket"))
            except (TypeError, ValueError):
                continue
            raw = args.get("paths")
            if isinstance(raw, list):
                paths[n] = [p.strip() for p in raw if isinstance(p, str) and p.strip()]
            if "network" in args and args.get("network") is not None:
                net_hints[n] = args["network"]
        architect = dict(task(run), consulted=True)
    out = []
    for t in doc.get("proposal") or []:
        if not isinstance(t, dict):
            continue
        n = t.get("n")
        t = dict(t, perimeter=paths.get(n, []))
        if n in net_hints:
            t["network"] = net_hints[n]
        members = _resolve_network(t, env)
        if members is not None:
            t["network"] = members
        t["check"] = check(compose(t, "draft", doc.get("id") or "", env=env))
        out.append(t)
    if not out:
        raise Refused("the intake holds no proposed ticket")
    return {"state": "proposed", "architect": architect, "proposal": out}


# --- docs/adr/0091 §6: the perimeter asked file by file ---------------------

# The map's node kinds the architect may name, the ones find_files searches.
KINDS = ("file", "test", "page", "adr")
# Provenance, external: the Decide port's limit (docs/adr/0091 §1).
MAX_QUESTIONS = 1000
POLICY = "policy/decide.env"
QUESTION = "Must the file `{path}` change for ticket {n} ({title}) to meet its acceptance criteria?"
YES = "The ticket's code, tests or the one document a user reads to learn the behaviour must change in this file."
NO = "The ticket can meet every criterion without touching this file."


def candidates(map_path, scope):
    """The map's files under SCOPE, sorted: the paths the questions name."""
    scope = (scope or "").strip("/")
    db = sqlite3.connect(f"file:{os.path.abspath(map_path)}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT DISTINCT name FROM node WHERE kind IN (?, ?, ?, ?) ORDER BY name", KINDS).fetchall()
    finally:
        db.close()
    return [r[0] for r in rows if not scope or r[0] == scope or r[0].startswith(scope + "/")]


def perimeter_request(doc, paths):
    """One request: the architect's first message as the input, one yes/no
    question per ticket and per path, keyed t<n>-f<i> (i from 1)."""
    tickets = [t for t in doc.get("proposal") or [] if isinstance(t, dict)]
    if not tickets:
        raise Refused("the intake holds no proposed ticket")
    if not paths:
        raise Refused("the workload's map holds no file to ask about")
    if len(tickets) * len(paths) > MAX_QUESTIONS:
        raise OverLimit(f"{len(tickets)} tickets × {len(paths)} files is {len(tickets) * len(paths)} questions, "
                      f"over the port's {MAX_QUESTIONS}")
    questions = {}
    for t in tickets:
        for i, path in enumerate(paths, 1):
            questions[f"t{t.get('n')}-f{i}"] = {
                "type": "yes_no", "instructions": QUESTION.format(path=path, n=t.get("n"), title=t.get("title")),
                "criteria": {"true": YES, "false": NO}}
    return {"input": proposal_input(doc), "questions": questions}


def confidence_threshold(path=POLICY):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("PERIMETER_CONFIDENCE="):
                return float(line.split("=", 1)[1])
    raise Refused(f"{path} has no PERIMETER_CONFIDENCE")


def perimeter_decided(doc, req, ans, check=gate_line, threshold=None, env=None):
    """The perimeter step's result from the Decide answer (docs/adr/0091 §6)."""
    t_min = confidence_threshold() if threshold is None else threshold
    paths = {}
    for key, q in (req.get("questions") or {}).items():
        m = re.match(r"^t(\d+)-f\d+$", key)
        p = re.search(r"`([^`]+)`", q.get("instructions") or "")
        if m and p:
            paths[key] = (int(m.group(1)), p.group(1))
    inside, unsure = {}, {}
    for key, a in (ans.get("answers") or {}).items():
        if key not in paths:
            continue
        n, path = paths[key]
        if a["confidence"] < t_min:
            unsure.setdefault(n, []).append((a["p_yes"], path))
        elif a["p_yes"] >= 0.5:
            inside.setdefault(n, []).append(path)
    recorded = ans.get("recorded") if isinstance(ans.get("recorded"), dict) else {}
    run = {"harness": ans.get("adapter"), "replayed": ans.get("replayed") is True, "provider": ans.get("provider"),
           "model": ans.get("model"), "tokens": ans.get("tokens"), "duration_s": ans.get("duration_s"),
           "turns": 1 if not ans.get("replayed") else 0}
    if recorded:
        run["recorded"] = recorded
    architect = dict(task(run), consulted=True, by="decide", questions=len(paths))
    out = []
    for t in doc.get("proposal") or []:
        if not isinstance(t, dict):
            continue
        n = t.get("n")
        t = dict(t, perimeter=sorted(inside.get(n, [])),
                 uncertain=[p for _, p in sorted(unsure.get(n, []), key=lambda x: (-x[0], x[1]))])
        members = _resolve_network(t, env)
        if members is not None:
            t["network"] = members
        t["check"] = check(compose(t, "draft", doc.get("id") or "", env=env))
        out.append(t)
    if not out:
        raise Refused("the intake holds no proposed ticket")
    return {"state": "proposed", "architect": architect, "proposal": out}


def decide_perimeter(doc, map_path, scope, run_dir, env=None):
    """The perimeter step's result asked file by file, or SearchInstead, or Refused."""
    try:
        req = perimeter_request(doc, candidates(map_path, scope))
    except OverLimit as exc:
        raise SearchInstead(f"perimeter: {exc}; the architect searches instead") from None
    with open(os.path.join(run_dir, "request.json"), "w", encoding="utf-8") as fh:
        json.dump(req, fh, ensure_ascii=False)
    r = subprocess.run([sys.executable, "core/decide.py", "ask"], input=json.dumps(req), capture_output=True,  # nosec B603
                       text=True, check=False)
    lines = [x for x in r.stderr.splitlines() if x.strip()]
    line = (lines[-1] if lines else f"core/decide.py exit {r.returncode}").removeprefix("decide: ")
    if r.returncode == 3:
        raise SearchInstead(f"perimeter: the decision is not recorded at L0 ({line}); the architect searches instead")
    if r.returncode != 0:
        raise Refused(f"the architect's decision failed: {line}")
    return perimeter_decided(doc, req, json.loads(r.stdout), env=env)


# --- §5 the confirmation ----------------------------------------------------


def _edit(ticket, fields, n):
    if not isinstance(fields, dict) or set(fields) - set(EDITABLE):
        raise Refused(f"the edit of ticket {n} is not an object of {', '.join(EDITABLE)}")
    out = dict(ticket)
    for key, value in fields.items():
        if key in ("criteria", "perimeter"):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise Refused(f"the edit of ticket {n}: {key} is not a list of strings")
            out[key] = [" ".join(v.split()) if key == "criteria" else v.strip() for v in value if v.strip()]
            if key == "perimeter":
                out.pop("network", None)
        elif value is None or isinstance(value, str):
            out[key] = (value or "").strip() or (None if key in ("feature", "priority") else "")
        else:
            raise Refused(f"the edit of ticket {n}: {key} is not a string")
    if not out.get("title"):
        raise Refused(f"the edit of ticket {n} leaves it without a title")
    return out


def decide(doc, decisions):
    """[(ticket, action)] for the intake's proposal, or Refused (§5)."""
    if doc.get("state") != "proposed":
        raise Refused(f"intake {doc.get('id')} is at {doc.get('state') or 'none'}, not proposed; nothing confirmed")
    proposal = [t for t in doc.get("proposal") or [] if isinstance(t, dict)]
    if not isinstance(decisions, list) or len(decisions) != len(proposal):
        raise Refused(f"DECISIONS must be a JSON list of {len(proposal)} entries, one per proposed ticket")
    out = []
    for t, d in zip(proposal, decisions):
        if d in ("confirm", "drop"):
            out.append((t, d))
        elif isinstance(d, dict) and set(d) == {"edit"}:
            out.append((_edit(t, d["edit"], t.get("n")), "edit"))
        else:
            raise Refused(f'ticket {t.get("n")}: "{json.dumps(d)[:40]}" is not "confirm", "drop" or {{"edit": {{...}}}}')
    return out


def parse_decisions(value):
    """DECISIONS as given: JSON text, or @<file>."""
    value = (value or "").strip()
    if value.startswith("@"):
        try:
            with open(value[1:], encoding="utf-8") as fh:
                value = fh.read()
        except OSError as exc:
            raise Refused(f"cannot read DECISIONS file {value[1:]}: {exc.strerror}") from None
    try:
        return json.loads(value)
    except ValueError:
        raise Refused("DECISIONS is not JSON: a list of \"confirm\", \"drop\" or {\"edit\": {...}}") from None


def write_ticket(ticket, intake_id, tickets_dir):
    """Write a kept ticket under the next free id, exclusive create; return the id."""
    tid = spec.next_id(tickets_dir)
    path = os.path.join(tickets_dir, f"{tid}.md")
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(compose(ticket, tid, intake_id))
    except FileExistsError:
        raise Refused(f"{path} exists: another request took {tid}") from None
    return tid


# --- the command line -------------------------------------------------------


def write_step_files(env, why, run=None):
    """A refusal's reason to STEP_WHY (§6) and its stopped run to STEP_RUN (amendment A3)."""
    for var, body in (("STEP_WHY", why + "\n"), ("STEP_RUN", json.dumps(run) + "\n" if run else None)):
        if env.get(var) and body:
            try:
                with open(env[var], "w", encoding="utf-8") as fh:
                    fh.write(body)
            except OSError:
                pass


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def main(argv, env=None):
    env = os.environ if env is None else env
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if cmd == "new" and len(args) in (2, 3):
            with open(args[0], encoding="utf-8", newline="") as fh:  # byte for byte (§1)
                text = fh.read()
            print(new(text, args[1], args[2] if len(args) == 3 else None))
        elif cmd == "first" and len(args) == 2:
            doc = load(args[0], {})
            sys.stdout.write(first(read(intake_file(args[1], doc.get("id") or ""))))
        elif cmd == "answers" and len(args) == 1:
            print(json.dumps(answers(load(args[0], {})), ensure_ascii=False))
        elif cmd == "interview" and len(args) == 3:
            print(json.dumps(screened(interview(load(args[0], {}), args[1], int(args[2]), env), args[1]),
                             ensure_ascii=False))
        elif cmd == "proposal" and len(args) == 1:
            sys.stdout.write(proposal_input(load(args[0], {})))
        elif cmd == "perimeter" and len(args) == 3:
            print(json.dumps(perimeter(load(args[0], {}), args[1], int(args[2]), env), ensure_ascii=False))
        elif cmd == "perimeter-request" and len(args) == 3:
            print(json.dumps(perimeter_request(load(args[0], {}), candidates(args[1], args[2])), ensure_ascii=False))
        elif cmd == "decide-perimeter" and len(args) == 4:
            print(json.dumps(decide_perimeter(load(args[0], {}), args[1], args[2], args[3], env=env),
                             ensure_ascii=False))
        elif cmd == "perimeter-decided" and len(args) == 3:
            print(json.dumps(perimeter_decided(load(args[0], {}), load(args[1], {}), load(args[2], {}), env=env),
                             ensure_ascii=False))
        elif cmd == "compose" and len(args) == 3:
            sys.stdout.write(compose(load(args[0], {}), args[1], args[2], env=env))
        else:
            print("usage: intake.py new <text> <tickets> [<id>] | first <doc> <tickets> | answers <doc>"
                  " | interview <doc> <run dir> <rc> | proposal <doc> | perimeter <doc> <run dir> <rc>"
                  " | compose <ticket.json> <id> <intake id>", file=sys.stderr)
            return 2
    except SearchInstead as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (Refused, OSError, sqlite3.Error) as exc:
        line = f"intake: {exc}"
        print(line, file=sys.stderr)
        write_step_files(env, str(exc), getattr(exc, "run", None))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
