"""The control plane's state machine (docs/adr/0019 §1, §2): stdlib only.

Vendor-free: it only calls the STATE_STORE and EVENT_BUS adapter verbs the
profile names, and the step scripts under $TOWER_STEPS. core/tower.sh is
its entry point (docs/adr/0052 §3); callers keep calling that.

    python3 core/tower.py open <ticket>          write the document at `opened`, publish `assign`
    python3 core/tower.py request <ticket>       write the document at `requested`, publish `clarify`
    python3 core/tower.py answer <ticket>        record ANSWER on the open question, publish `answer`
                                                 (`intake-answer` for an intake, docs/adr/0090 §2)
    python3 core/tower.py intake <id>            write the intake at `intake`, publish `interview` (docs/adr/0090 §1)
    python3 core/tower.py intake-confirm <id>    apply DECISIONS to a `proposed` intake: the kept tickets
                                                 at `spec-ready`, `spec` published for each (docs/adr/0090 §5)
    python3 core/tower.py step <ticket> <step>   apply one step: precondition, effect, write, publish next
    python3 core/tower.py resume <ticket>        request again the step whose last attempt failed
    python3 core/tower.py task [--patch] <run.json>  print the task fields a role's run leaves in the document,
                                                 with --patch also the patch beside it (docs/adr/0071 §1)

The document is plain JSON, one per ticket, id = ticket id. A step's
precondition is the state it starts from; an event whose precondition is
not the document's current state is dropped with one stderr line and exit
0 -- the idempotency rule, covering a duplicate event and a redelivery
after a slow step. A step script gets the document on $DOC_FILE (plus
TICKET, PR_NUMBER, BASE_SHA, HEAD_SHA) and prints a JSON object on stdout:
at least {"state": "<next>"}, plus the fields it recorded, deep-merged
into the document. A step that exits non-zero, prints no state, or whose
next event cannot be published leaves the state and every other field as
they were, appends one history entry {step, state, at, error: <why>}, and
exits 1: no retries, no timeouts of the tower's own (docs/adr/0034 §1).
`resume` requires that entry to be the last one and appends {step:
"resume", resumed: <step>} before publishing the step again (docs/adr/0034
§2). Every history entry carries the document's `attempt` once it has one
(docs/adr/0038 §9). All output is on stderr, one `[tower]` line each.

Before `opened` (docs/adr/0055 §1, §2): `request` starts a ticket at
`requested`, with who requested and through what (REQUEST_BY, default
`unauthenticated`; REQUEST_VIA, default `cli`; docs/adr/0059 §4); `questions` waits for a person, and `answer` writes the
person's ANSWER (ANSWER_BY, default `unauthenticated`; ANSWER_VIA, default
`cli`) on the last question, which must be unanswered, then requests the
`answer` step. A step result that carries `task` is also appended, with
the document's attempt, to `tasks` (§5): every attempt's run is kept.

Before `requested` (docs/adr/0090): `intake` starts an intake document,
`kind: "intake"`, from TICKETS_DIR/<id>.intake.md, with INTAKE_BY and
INTAKE_VIA; `intake-questions` and `proposed` wait for a person;
`intake-confirm` writes each ticket the person keeps (DECISIONS,
CONFIRM_BY, CONFIRM_VIA) at `spec-ready` and the intake at `confirmed`.
Every step gets STEP_WHY, a file: a failing step that wrote a line there
is recorded `step exited <n>: <line>` (§6). And STEP_RUN, a file: a failing
step that wrote there the run a role made, `{<field>: <task>}`, has it
appended to `spend` with `failed` its `stop` (amendment A3).

Spend (docs/adr/0083): every run a step's result carries (`task`,
`review`, `product`) is appended to `spend` with its cost from
profiles/prices.json. Before `assign` and `rework` the spend is checked
against policy/rework.env's SPEND_BUDGET_TICKET_USD and
SPEND_BUDGET_DAY_USD: over a limit, `assign` is refused like a red main
and `rework` runs with SPEND_STOP=<the line>, which ends the ticket at
`needs-human`. When `assign` or `rework` fails, the run the crew left in
.crew/<ticket>/out/run.json during the step is appended too, with `failed`
the budget marker in out/stop (docs/adr/0087 §1): a run stopped on its
TOKEN_BUDGET spent money the limits must count.
"""

import fnmatch
import glob
import hashlib
import json
import os
import re
import subprocess  # nosec B404 - adapter verbs and steps are scripts (docs/adr/0052 §3)
import sys
import tempfile
import time
from datetime import datetime, timezone

COLLECTION = "tickets"
USAGE = ("usage: tower.sh open <ticket> | request <ticket> | answer <ticket> | step <ticket> <step>"
         " | resume <ticket> | intake <id> | intake-confirm <id> | screen-clear <ticket>"
         " | task [--patch] <run.json>")

# The transition table. PRECONDITION: step -> the state it starts from.
# NEXT: state -> the step requested once the document is there; a state
# that is not a key is terminal. ci/board.sh checks the board against both.
PRECONDITION = {
    "interview": "intake",
    "intake-answer": "intake-questions",
    "perimeter": "drafted",
    "clarify": "requested",
    "answer": "questions",
    "spec": "spec-ready",
    "assign": "opened",
    "ci": "pr-opened",
    "review": "review",
    "merge": "ci-passed",
    "mainline": "merged",
    "release": "main-green",
    "rework": "ci-failed",
}
NEXT = {
    "intake": "interview",
    "drafted": "perimeter",
    "requested": "clarify",
    "spec-ready": "spec",
    "opened": "assign",
    "pr-opened": "ci",
    "review": "review",
    "ci-passed": "merge",
    "merged": "mainline",
    "main-green": "release",
    "ci-failed": "rework",
}
# The state a person answers in, and the step the answer requests
# (docs/adr/0055 §2, 0090 §2).
ANSWERED = {"questions": "answer", "intake-questions": "intake-answer"}
# Refused while main is red (docs/adr/0056 §5): the `mainline`/`main`
# document core/mainline.py keeps.
BLOCKED_BY_RED_MAIN = ("assign", "rework", "release")
# The steps that launch a dev-worker attempt, checked against the spend
# budget first (docs/adr/0083 §4).
BUDGETED = ("assign", "rework")


# The task fields copied from a role's run.json (docs/adr/0047 §2, P1):
# who ran it and what it cost, as the harness reported it, or under replay
# as the recorded run did -- the rule `model` follows. A key the harness
# does not report is null, never a guess. `mcp` is the dev-worker's MCP
# servers and calls (docs/adr/0073 §9), null when none were declared.
RUN_KEYS = ("model", "provider", "thinking", "turns", "tokens", "duration_s", "input_sha256", "mcp")
# Provenance, decision: docs/adr/0071 §1 and open item 2: about 12.5 times
# the largest recorded patch (5,244 bytes, fixtures/0001), and five copies
# stay under a third of Firestore's 1 MiB document (external).
PATCH_CAP = 65536
_MBOX = re.compile(r"^From [0-9a-f]{40} ")


def task(run):
    """{harness, replayed, model, provider, ...} from a run.json object; a
    network's run also gives each member's, with its id and perimeter
    (docs/adr/0071 §1, docs/adr/0036 §4)."""
    recorded = run.get("recorded") if isinstance(run.get("recorded"), dict) else {}
    out = {"harness": text(run.get("harness")) or "unknown", "replayed": run.get("harness") == "replay"}
    for key in RUN_KEYS:
        value = run.get(key)
        if value is None or value is False:
            value = recorded.get(key)
        out[key] = None if value is False else value
    if isinstance(run.get("members"), list):
        out["members"] = []
        for m in run["members"]:
            m = m if isinstance(m, dict) else {}
            mrun = m.get("run") if isinstance(m.get("run"), dict) else {}
            mrec = mrun.get("recorded") if isinstance(mrun.get("recorded"), dict) else {}
            hand = mrun.get("hand_written") is True or mrec.get("hand_written") is True
            out["members"].append(dict(task(mrun), id=m.get("id"), perimeter=list(m.get("perimeter") or []),
                                       hand_written=True if hand else None))
    return out


def owner(path, members):
    """The member whose perimeter globs match PATH, as core/perimeter.sh
    matches (`*` crosses `/`); None for a single worker."""
    for m in members or []:
        if any(fnmatch.fnmatchcase(path, g) for g in m.get("perimeter") or []):
            return m.get("id")
    return None


def patch_fields(data, members):
    """{patch: {text, bytes, truncated}, files: [...]} from an attempt's
    patch file (docs/adr/0071 §1): the text cut at the last newline within
    PATCH_CAP, the files counted on the whole patch."""
    cut = data if len(data) <= PATCH_CAP else data[:data.rfind(b"\n", 0, PATCH_CAP + 1) + 1]
    files, order, current, hunk = {}, [], None, False
    lines = data.decode("utf-8", errors="replace").splitlines()
    for i, raw in enumerate(lines):
        after = lines[i + 1] if i + 1 < len(lines) else ""
        if raw.startswith("diff --git "):
            parts = raw.split(" b/", 1)
            current, hunk = (parts[1] if len(parts) == 2 else raw[len("diff --git "):]), False
            if current not in files:
                files[current] = {"path": current, "added": 0, "removed": 0, "member": owner(current, members)}
                order.append(current)
        elif _MBOX.match(raw) or (raw in ("--", "-- ") and not after.startswith((" ", "+", "-", "\\", "@@", "diff --git "))):
            # A commit's mbox header, or the `-- ` signature format-patch ends
            # it with (the line after it is git's version, never a hunk line).
            current, hunk = None, False
        elif current is None:
            continue
        elif raw.startswith("@@ "):
            hunk = True
        elif not hunk and (raw.startswith("Binary files ") or raw == "GIT binary patch"):
            files[current]["added"] = files[current]["removed"] = None
        elif hunk and files[current]["added"] is not None and raw[:1] in ("+", "-"):
            files[current]["added" if raw[0] == "+" else "removed"] += 1
    return {"patch": {"text": cut.decode("utf-8", errors="replace"), "bytes": len(data), "truncated": len(data) > PATCH_CAP},
            "files": [files[p] for p in order]}


def intake_module():
    """core/intake.py, imported where it is needed."""
    try:
        from . import intake
    except ImportError:  # run as a script: core/ is sys.path[0]
        import intake
    return intake


def spend_module():
    """core/spend.py (docs/adr/0083), imported only where a step needs it: a
    module or a check that loads the tower for its tables, `sh` or `task`,
    or copies it alone into a tree, needs no copy of spend.py."""
    try:
        from . import spend
    except ImportError:  # run as a script: core/ is on sys.path
        import spend
    return spend


def spec_module():
    """core/spec.py, imported only where ticket metadata headers are read."""
    try:
        from . import spec
    except ImportError:  # run as a script: core/ is on sys.path
        import spec
    return spec


def epics_module():
    """core/epics.py (docs/adr/0119), imported only when mainline unblocks dependents."""
    try:
        from . import epics
    except ImportError:  # run as a script: core/ is on sys.path
        import epics
    return epics


class Refused(Exception):
    """One `[tower]` line on stderr and an exit code."""

    def __init__(self, line, code=1):
        super().__init__(line)
        self.line = line
        self.code = code


def text(value):
    """A field as `jq -r '<path> // empty'` prints it: null and false are absent."""
    if value is None or value is False:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def dig(doc, *keys):
    """doc[k1][k2]..., or None where any level is missing or not an object."""
    for key in keys:
        if not isinstance(doc, dict):
            return None
        doc = doc.get(key)
    return doc


def merge(left, right):
    """jq's `left * right`: objects merge recursively, anything else is replaced."""
    if not (isinstance(left, dict) and isinstance(right, dict)):
        return right
    out = dict(left)
    for key, value in right.items():
        out[key] = merge(out[key], value) if key in out else value
    return out


def attempt(doc):
    """{attempt: n} once the document has one (docs/adr/0038 §9), {} before."""
    value = doc.get("attempt")
    if value is None or value is False:
        return {}
    return {"attempt": value}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact(doc):
    """The document as `jq -c` writes it."""
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


def first_line(path):
    """The first non-empty line of PATH, or "" (a step's STEP_WHY, docs/adr/0090 §6)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return next((line.strip() for line in fh if line.strip()), "")
    except OSError:
        return ""


def say(line):
    print(line, file=sys.stderr, flush=True)


def sh(script, *args, **kwargs):
    """Run SCRIPT with sh, an argument list and never a shell line: the one process call."""
    # The script is an adapter verb the profile names (docs/adr/0024) or a
    # TOWER_STEPS step; sh is the workbench's.
    return subprocess.run(["sh", script, *args], check=False, **kwargs)  # nosec B603 B607 - an argv list, no shell, ADR 0052 §3


class Tower:
    def __init__(self, ticket, env):
        self.ticket = ticket
        self.env = dict(env)
        self.store_adapter = env["STATE_STORE"]
        self.bus = env["EVENT_BUS"]
        self.steps = env.get("TOWER_STEPS") or "core/steps"
        self.tickets_dir = env.get("TICKETS_DIR") or "tickets"

    def ticket_metadata(self, tid=None):
        """Return (depends_on, epic) from TICKETS_DIR/<tid>.md when present, else ([], None)."""
        tid = tid or self.ticket
        path = os.path.join(self.tickets_dir, f"{tid}.md")
        if not os.path.isfile(path):
            return [], None
        try:
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            return [], None
        try:
            return spec_module().dependencies(content, own_id=tid)
        except ImportError:
            return [], None
        except ValueError as exc:
            raise Refused(f"[tower] {tid}: {exc}") from None

    @staticmethod
    def _dep_is_main_green(dep_doc):
        """True when dependency has reached `main-green` (or passed mainline before `release`)."""
        if not isinstance(dep_doc, dict):
            return False
        st = text(dep_doc.get("state"))
        if st == "main-green":
            return True
        if st in ("held", "rolled-back"):
            if dig(dep_doc, "mainline", "verdict") == "GO":
                return True
            return any(
                isinstance(h, dict) and h.get("step") == "mainline" and h.get("state") == "main-green"
                for h in (dep_doc.get("history") or [])
            )
        return False

    def store_get(self, ticket=None):
        """The document, or None when the store has none (get.sh exit non-zero)."""
        ticket = ticket or self.ticket
        got = sh(f"adapters/{self.store_adapter}/get.sh", COLLECTION, ticket,
                 stdout=subprocess.PIPE, text=True)
        if got.returncode != 0:
            return None
        try:
            doc = json.loads(got.stdout)
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            raise Refused(f"[tower] {ticket}: the stored document is not a JSON object")
        return doc

    def over_budget(self, doc):
        """Why the next attempt is not launched (docs/adr/0083 §4), or "".
        The day counts every ticket the Tracker lists, as the logbook reads them."""
        spend = spend_module()
        lim = spend.limits()  # ValueError on a malformed limit: the caller refuses the step
        today = []
        if lim.get("SPEND_BUDGET_DAY_USD") is not None:
            docs = {self.ticket: doc}
            for path in sorted(glob.glob(os.path.join(self.tickets_dir, "*.md"))):
                tid = os.path.basename(path)[:-3].removesuffix(".intake")  # an intake's runs count (docs/adr/0090 §7)
                if tid not in docs:
                    other = self.store_get(tid)
                    if other is not None:
                        docs[tid] = other
            today = spend.on_day(docs, now()[:10])
        return spend.stop(spend.ledger(doc), today, lim)

    def store_put(self, doc):
        put = sh(f"adapters/{self.store_adapter}/put.sh", COLLECTION, self.ticket,
                 input=compact(doc) + "\n", text=True)
        return put.returncode == 0

    def publish(self, step):
        message = compact({"ticket": self.ticket, "step": step})
        return sh(f"adapters/{self.bus}/publish.sh", message).returncode == 0

    def red_main(self):
        """Why main is NO-GO (docs/adr/0056 §5), or "" when it is not red."""
        got = sh(f"adapters/{self.store_adapter}/get.sh", "mainline", "main",
                 stdout=subprocess.PIPE, text=True)
        try:
            main = json.loads(got.stdout) if got.returncode == 0 else {}
        except ValueError:
            main = {}
        if not isinstance(main, dict) or main.get("state") != "red":
            return ""
        return f"main is NO-GO since {text(main.get('green')) or 'unknown'}: {text(main.get('line'))}"

    def tell(self, line):
        say(f"[tower] {self.ticket}: {line}")

    def get(self):
        doc = self.store_get()
        if doc is None:
            raise Refused(f"[tower] {self.ticket}: no document; open the ticket first")
        return doc

    def failed_run(self, doc, step, began):
        """The ledger entry of the run a failed `assign` or `rework` left in
        .crew/<ticket>/out/run.json since BEGAN (docs/adr/0087 §1), or None."""
        out = os.path.join(".crew", self.ticket, "out")
        path = os.path.join(out, "run.json")
        try:
            if os.path.getmtime(path) < began:
                return None
            with open(path, encoding="utf-8") as fh:
                run = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(run, dict):
            return None
        try:
            with open(os.path.join(out, "stop"), encoding="utf-8") as fh:
                marker = fh.readline().strip()
        except OSError:
            marker = ""
        n = 1 if step == "assign" else (doc.get("attempt") or 1) + 1
        got = spend_module().entries(step, {"task": dict(task(run), role=os.environ.get("ROLE") or "dev-worker")}, n, now())
        return dict(got[0], failed=marker or "the run failed") if got else None

    def stopped_runs(self, step, doc, path):
        """The ledger entries of the runs a failed step wrote to STEP_RUN (amendment A3)."""
        try:
            with open(path, encoding="utf-8") as fh:
                result = json.load(fh)
        except (OSError, ValueError):
            return []
        if not isinstance(result, dict):
            return []
        out = []
        for entry in spend_module().entries(step, result, doc.get("attempt"), now()):
            field = next((k for k, v in result.items() if isinstance(v, dict)
                          and (v.get("role") or k) == entry["role"]), None)
            stop = text(dig(result, field, "stop")) if field else ""
            out.append(dict(entry, failed=stop or "the run failed"))
        return out

    def record_failure(self, doc, step, state, why, run=None):
        """Append the failed attempt to doc's history and write it; the state does not move.
        RUN, a failed attempt's ledger entry or a list of them, is appended to `spend` in the same write."""
        failed = dict(doc)
        runs = run if isinstance(run, list) else [run] if run else []
        if runs:
            failed["spend"] = spend_module().ledger(doc) + runs
        failed["history"] = (doc.get("history") or []) + [
            {"step": step, "state": state, "at": now(), **attempt(doc), "error": why}]
        if not self.store_put(failed):
            self.tell(f"could not record the failed {step} either")
        self.tell(f"resume with ./go resume TICKET={self.ticket}")

    def open(self):
        deps, epic_id = self.ticket_metadata()
        doc = {"id": self.ticket, "state": "opened",
               "history": [{"step": "open", "state": "opened", "at": now()}]}
        if deps:
            doc["depends_on"] = deps
        if epic_id:
            doc["epic"] = epic_id
        if not self.store_put(doc):
            raise Refused(f"[tower] {self.ticket}: could not write the document")
        if not self.publish("assign"):
            raise Refused(f"[tower] {self.ticket}: could not publish assign")
        self.tell("opened, assign requested")

    def request(self, env):
        by, via = env.get("REQUEST_BY") or "unauthenticated", env.get("REQUEST_VIA") or "cli"
        deps, epic_id = self.ticket_metadata()
        doc = {"id": self.ticket, "state": "requested",
               "history": [{"step": "request", "state": "requested", "at": now(), "by": by, "via": via}]}
        if deps:
            doc["depends_on"] = deps
        if epic_id:
            doc["epic"] = epic_id
        if not self.store_put(doc):
            raise Refused(f"[tower] {self.ticket}: could not write the document")
        if not self.publish("clarify"):
            raise Refused(f"[tower] {self.ticket}: could not publish clarify")
        self.tell(f"requested by {by} via {via}, clarify requested")

    def intake(self, env):
        """The intake document (docs/adr/0090 §1): the client's words are TICKETS_DIR/<id>.intake.md."""
        path = os.path.join(self.tickets_dir, f"{self.ticket}.intake.md")
        if not os.path.isfile(path):
            raise Refused(f"[tower] {self.ticket}: no {path}; write it with core/intake.py new")
        if self.store_get() is not None:
            raise Refused(f"[tower] {self.ticket}: a document with this id exists; nothing written")
        by, via, at = env.get("INTAKE_BY") or "unauthenticated", env.get("INTAKE_VIA") or "cli", now()
        doc = {"id": self.ticket, "kind": "intake", "state": "intake", "intake": {"by": by, "via": via, "at": at},
               "history": [{"step": "intake", "state": "intake", "at": at, "by": by, "via": via}]}
        if not self.store_put(doc):
            raise Refused(f"[tower] {self.ticket}: could not write the document")
        if not self.publish("interview"):
            self.tell("could not publish interview")
            self.record_failure(doc, "interview", "intake", "could not publish interview")
            raise Refused(None)
        self.tell(f"intake by {by} via {via}, interview requested")

    def intake_confirm(self, env):
        """The person's decisions on a proposed intake (docs/adr/0090 §5): every kept
        ticket written and started at `spec-ready`, then the intake at `confirmed`."""
        intake = intake_module()
        doc = self.get()
        try:
            decided = intake.decide(doc, intake.parse_decisions(env.get("DECISIONS")))
        except intake.Refused as exc:
            raise Refused(f"[tower] {self.ticket}: {exc}") from None
        by, via, at = env.get("CONFIRM_BY") or "unauthenticated", env.get("CONFIRM_VIA") or "cli", now()
        proposal, born = [], []
        for ticket, action in decided:
            tid = None
            if action != "drop":
                try:
                    tid = intake.write_ticket(ticket, self.ticket, self.tickets_dir)
                except intake.Refused as exc:
                    raise Refused(f"[tower] {self.ticket}: {exc}; tickets written so far: {born or 'none'}") from None
                child = Tower(tid, {"STATE_STORE": self.store_adapter, "EVENT_BUS": self.bus,
                                    "TOWER_STEPS": self.steps, "TICKETS_DIR": self.tickets_dir})
                if child.store_get() is not None:
                    raise Refused(f"[tower] {tid}: a document with this id exists; tickets written so far: {born}")
                kid = {"id": tid, "state": "spec-ready", "intake": self.ticket,
                       "spec": {"criteria": ticket.get("criteria") or [], "by": "intake"},
                       "history": [{"step": "request", "state": "spec-ready", "at": at, "by": by, "via": via,
                                    "intake": self.ticket}]}
                deps, epic_id = self.ticket_metadata(tid)
                if deps:
                    kid["depends_on"] = deps
                if epic_id:
                    kid["epic"] = epic_id
                if not child.store_put(kid):
                    raise Refused(f"[tower] {tid}: could not write the document; tickets written so far: {born}")
                born.append(tid)
            proposal.append(dict(ticket, decision=action, ticket=tid))
        done = dict(doc, state="confirmed", proposal=proposal, tickets=born,
                    confirmation={"by": by, "via": via, "at": at},
                    history=(doc.get("history") or []) + [
                        {"step": "confirm", "state": "confirmed", "at": at, "by": by, "via": via}])
        if not self.store_put(done):
            raise Refused(f"[tower] {self.ticket}: could not record the confirmation; tickets written: {born or 'none'}")
        self.tell(f"confirmed by {by} via {via}: {', '.join(born) or 'no ticket kept'}")
        for tid in born:
            if not Tower(tid, {"STATE_STORE": self.store_adapter, "EVENT_BUS": self.bus}).publish("spec"):
                raise Refused(f"[tower] {tid}: could not publish spec; ./go resume does not apply, publish it again")
            say(f"[tower] {tid}: spec-ready from intake {self.ticket}, spec requested")

    def answer(self, env):
        said = (env.get("ANSWER") or "").strip()
        if not said:
            raise Refused(f"[tower] {self.ticket}: no ANSWER; nothing recorded", 2)
        doc = self.get()
        have = text(doc.get("state")) or "none"
        questions = [dict(q) for q in doc.get("questions") or [] if isinstance(q, dict)]
        if have not in ANSWERED or not questions or questions[-1].get("answer"):
            raise Refused(f"[tower] {self.ticket}: no open question (state {have}); nothing recorded")
        step = ANSWERED[have]
        at = now()
        by, via = env.get("ANSWER_BY") or "unauthenticated", env.get("ANSWER_VIA") or "cli"
        questions[-1].update(answer=said, answered_by=by, answered_via=via, answered_at=at)
        doc = dict(doc, questions=questions, history=(doc.get("history") or []) + [
            {"step": "answered", "state": have, "at": at, "by": by, "via": via}])
        if not self.store_put(doc):
            raise Refused(f"[tower] {self.ticket}: could not record the answer; nothing requested")
        if not self.publish(step):
            self.tell(f"could not publish {step}")
            self.record_failure(doc, step, have, f"could not publish {step}")
            raise Refused(None)
        self.tell(f"answered by {by} via {via}, {step} requested")

    def step(self, step):
        want = PRECONDITION.get(step)
        if want is None:
            raise Refused(f'[tower] {self.ticket}: unknown step "{step}"', 2)
        doc = self.get()
        have = text(doc.get("state")) or "none"
        if have != want:
            self.tell(f"{step} already applied (state {have}), ignoring")
            return
        deps = [d for d in (doc.get("depends_on") or []) if isinstance(d, str) and d]
        if step == "assign" and deps:
            blocking = [d for d in deps if not self._dep_is_main_green(self.store_get(d))]
            if blocking:
                waiting = dict(doc, wait={"kind": "depends_on", "blocking": blocking})
                if waiting != doc and not self.store_put(waiting):
                    raise Refused(f"[tower] {self.ticket}: could not record depends_on wait")
                self.tell(f"assign blocked by depends_on ({', '.join(blocking)}); waiting at {have}")
                return
            if isinstance(doc.get("wait"), dict) and doc["wait"].get("kind") == "depends_on":
                doc = {k: v for k, v in doc.items() if k != "wait"}
                if not self.store_put(doc):
                    raise Refused(f"[tower] {self.ticket}: could not clear depends_on wait")
        script = f"{self.steps}/{step}.sh"
        if not os.path.isfile(script):
            raise Refused(f"[tower] {self.ticket}: no {script}")
        red = self.red_main() if step in BLOCKED_BY_RED_MAIN else ""
        if red:
            self.tell(f"{step} refused: {red}; state stays {have}")
            self.record_failure(doc, step, have, red)
            raise Refused(None)
        try:
            budget = self.over_budget(doc) if step in BUDGETED else ""
        except ValueError as exc:
            self.tell(f"{step} refused: {exc}; state stays {have}")
            self.record_failure(doc, step, have, str(exc))
            raise Refused(None) from None
        if budget and step == "assign":
            # No attempt yet: refused like a red main, resumable (docs/adr/0083 §4).
            self.tell(f"{step} refused: {budget}; state stays {have}")
            self.record_failure(doc, step, have, budget)
            raise Refused(None)
        if budget:
            self.tell(budget)

        self.tell(f"{step} (state {have})")
        began = time.time() - 1  # the file system's clock is coarser than ours
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as doc_file, \
                tempfile.TemporaryDirectory(prefix="lights-out-step-") as why_dir:
            doc_file.write(compact(doc) + "\n")
            doc_file.flush()
            why_file = os.path.join(why_dir, "why")
            run_file = os.path.join(why_dir, "run.json")
            env = dict(os.environ, TICKET=self.ticket, DOC_FILE=doc_file.name, SPEND_STOP=budget, STEP_WHY=why_file,
                       STEP_RUN=run_file,
                       PR_NUMBER=text(dig(doc, "pr", "number")),
                       BASE_SHA=text(dig(doc, "pr", "base_sha")),
                       HEAD_SHA=text(dig(doc, "pr", "head_sha")))
            ran = sh(script, stdout=subprocess.PIPE, text=True, env=env)
            named = first_line(why_file) if ran.returncode != 0 else ""
            stopped = self.stopped_runs(step, doc, run_file) if ran.returncode != 0 else []
        if ran.returncode != 0:
            rc = ran.returncode if ran.returncode > 0 else 128 - ran.returncode
            self.tell(f"step {step} failed; state stays {have}")
            run = self.failed_run(doc, step, began) if step in BUDGETED else None
            if run:
                self.tell(f"the failed attempt's run is in the ledger ({run['failed']})")
            for entry in stopped:
                self.tell(f"the stopped {entry['role']} run is in the ledger ({entry['failed']})")
            self.record_failure(doc, step, have, f"step exited {rc}" + (f": {named}" if named else ""),
                                ([run] if run else []) + stopped)
            raise Refused(None)
        try:
            result = json.loads(ran.stdout)
        except ValueError:
            result = None
        new_state = text(result.get("state")) if isinstance(result, dict) else ""
        if not new_state:
            self.tell(f"step {step} printed no state; state stays {have}")
            self.record_failure(doc, step, have, "step printed no state")
            raise Refused(None)

        updated = merge(doc, result)
        if step == "spec" and new_state == "opened":
            deps, epic_id = self.ticket_metadata()
            if deps and not updated.get("depends_on"):
                updated["depends_on"] = deps
            if epic_id and not updated.get("epic"):
                updated["epic"] = epic_id
        if isinstance(result.get("task"), dict):
            updated["tasks"] = (doc.get("tasks") or []) + [dict(result["task"], **attempt(updated))]
        # Every run's cost beside its tokens, appended, never rewritten (docs/adr/0083 §3).
        spend = spend_module()
        runs = spend.entries(step, result, updated.get("attempt"), now())
        if runs:
            updated["spend"] = spend.ledger(doc) + runs
        updated["history"] = (updated.get("history") or []) + [
            {"step": step, "state": result["state"], "at": now(), **attempt(updated)}]
        if not self.store_put(updated):
            raise Refused(f"[tower] {self.ticket}: could not write the document after {step}")
        self.tell(f"{step} → {new_state}")

        if step == "mainline" and new_state == "main-green":
            try:
                epics = epics_module()
            except ImportError:
                epics = None
            if epics is not None:
                try:
                    epics.on_main_green(self.ticket, env=self.env, tickets_dir=self.tickets_dir)
                except Exception as exc:
                    self.tell(f"epics on_main_green warning: {exc}")

        following = NEXT.get(new_state)
        if following is None:
            self.tell(f"{new_state} is terminal, nothing requested")
        elif not self.publish(following):
            self.tell(f"could not publish {following}")
            self.record_failure(updated, following, new_state, f"could not publish {following}")
            raise Refused(None)

    def screen_clear(self, env):
        """A person clears a text the screen stopped (docs/adr/0091 §7): the
        clearance holds for this text's sha256, and the failed step is
        requested again."""
        doc = self.get()
        have = text(doc.get("state")) or "none"
        history = doc.get("history") or []
        last = history[-1] if history and isinstance(history[-1], dict) else {}
        step, why = text(last.get("step")), text(last.get("error"))
        if not why.startswith("step exited 1: screen:") or PRECONDITION.get(step) != have:
            raise Refused(f"[tower] {self.ticket}: its last step did not stop at the screen "
                          f"(state {have}); nothing cleared")
        name = f"{self.ticket}.intake.md" if doc.get("kind") == "intake" else f"{self.ticket}.md"
        try:
            with open(os.path.join(self.tickets_dir, name), "rb") as fh:
                sha = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            raise Refused(f"[tower] {self.ticket}: no {self.tickets_dir}/{name}; nothing cleared") from None
        at = now()
        by, via = env.get("CLEAR_BY") or "unauthenticated", env.get("CLEAR_VIA") or "cli"
        doc = dict(doc, screen={"verdict": "cleared", "sha256": sha, "by": by, "via": via, "at": at},
                   history=history + [{"step": "screen-clear", "state": have, "at": at, "by": by, "via": via,
                                       **attempt(doc)}])
        if not self.store_put(doc):
            raise Refused(f"[tower] {self.ticket}: could not record the clearance; nothing requested")
        if not self.publish(step):
            self.tell(f"could not publish {step}")
            self.record_failure(doc, step, have, f"could not publish {step}")
            raise Refused(None)
        self.tell(f"screen cleared by {by} via {via}, {step} requested again")

    def resume(self):
        doc = self.get()
        have = text(doc.get("state")) or "none"
        history = doc.get("history") or []
        last = history[-1] if history else {}
        why = text(dig(last, "error"))
        step = text(dig(last, "step"))
        if not why:
            raise Refused(f"[tower] {self.ticket}: not stranded: state {have}, last history entry "
                          f"{step or 'none'} did not fail; nothing to resume")
        if PRECONDITION.get(step) != have:
            raise Refused(f"[tower] {self.ticket}: the failed step {step} does not start from "
                          f"state {have}; not resuming")
        # Written before publishing: a step that starts at once (a job at L2)
        # must not have its write overwritten by this one. A failed publish
        # records itself, so the ticket stays resumable.
        doc = dict(doc, history=history + [
            {"step": "resume", "state": have, "at": now(), **attempt(doc), "resumed": step}])
        if not self.store_put(doc):
            raise Refused(f"[tower] {self.ticket}: could not record the resume; nothing requested")
        if not self.publish(step):
            self.tell(f"could not publish {step}")
            self.record_failure(doc, step, have, f"could not publish {step}")
            raise Refused(None)
        self.tell(f"{step} requested again (state {have}; last attempt: {why})")


def main(argv, env=None):
    env = os.environ if env is None else env
    if argv[:1] == ["task"] and len(argv) in (2, 3) and (len(argv) == 2 or argv[1] == "--patch"):
        path = argv[-1]
        try:
            with open(path, encoding="utf-8") as fh:
                out = task(json.load(fh))
            if len(argv) == 3:
                beside = os.path.join(os.path.dirname(path), "patch")
                if os.path.isfile(beside):
                    with open(beside, "rb") as fh:
                        out.update(patch_fields(fh.read(), out.get("members")))
                else:
                    out.update(patch=None, files=None)
            print(compact(out))
        except (OSError, ValueError, AttributeError) as exc:
            say(f"tower: cannot read the run {path}: {exc}")
            return 1
        return 0
    for var in ("STATE_STORE", "EVENT_BUS"):
        if not env.get(var):
            say(f"tower: {var} not set; run this via ./go, not directly")
            return 2
    cmd = argv[0] if argv else ""
    ticket = argv[1] if len(argv) > 1 else ""
    if not cmd or not ticket or cmd not in ("open", "request", "answer", "step", "resume", "intake", "intake-confirm",
                                            "screen-clear"):
        say(USAGE)
        return 2
    tower = Tower(ticket, env)
    try:
        if cmd == "open":
            tower.open()
        elif cmd == "request":
            tower.request(env)
        elif cmd == "answer":
            tower.answer(env)
        elif cmd == "intake":
            tower.intake(env)
        elif cmd == "intake-confirm":
            tower.intake_confirm(env)
        elif cmd == "step":
            tower.step(argv[2] if len(argv) > 2 else "")
        elif cmd == "screen-clear":
            tower.screen_clear(env)
        else:
            tower.resume()
    except Refused as refused:
        if refused.line:
            say(refused.line)
        return refused.code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
