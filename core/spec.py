"""The spec before the ticket (docs/adr/0055): stdlib only. The logic of
gates/spec.sh and of the clarify, answer and spec steps; they are its
entry points (docs/adr/0052 §3).

    python3 core/spec.py check <spec.md>
        the gate: exit 0, or exit 1 with the first failure on stdout (§4)
    python3 core/spec.py answers <doc.json>
        the answers so far, as the later user turns of the product role's
        conversation: a JSON list of strings on stdout (§2)
    python3 core/spec.py clarify <request.md> <doc.json> <run dir> <exit code>
        the clarify/answer step's result from the product role's run:
        `questions` or `spec-ready` (§2, §3)
    python3 core/spec.py compose <request.md> <doc.json>
        the spec: the request with its criteria replaced by the confirmed
        ones, on stdout (§4)
    python3 core/spec.py gated <doc.json> <gate line>
        the spec step's result: `opened` on GO, `questions` on NO-GO (§4)
    python3 core/spec.py draft <fields.json> <tickets dir> [<out dir>]
        a new ticket's request from the console's editor (docs/adr/0059
        §4): writes <out dir, default tickets dir>/<id>.md, the id the
        next free one in <tickets dir>, with an exclusive create, and
        prints the id

A step result is the JSON object core/tower.py merges into the document;
a refusal is one stderr line and exit 1, which fails the step (ADR 0034).
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

try:
    from .tower import task
except ImportError:  # run as a script: core/ is sys.path[0]
    from tower import task

CRITERIA = "## Acceptance criteria"
PERIMETER = "## Perimeter"
# Provenance, decision: docs/adr/0055 §4, the words that say an outcome is
# good without saying what is observed; one joins only with a NO-GO case.
VAGUE = ("works", "correctly", "properly", "as expected", "as intended", "appropriately", "gracefully",
         "seamlessly", "user-friendly", "intuitive", "better", "improved", "fast", "quickly", "robust",
         "handled")
_VAGUE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in VAGUE) + r")(?![\w-])", re.I)
_WORD = {w: re.compile(rf"(?:^|[\s,;.])({w})\b", re.I) for w in ("given", "when", "then")}
DECISIONS = ("ask_product_owner", "confirm_acceptance")
# The two optional header fields (docs/adr/0055 §7): their exact values.
FIELDS = {"Feature": re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$"), "Priority": re.compile(r"^P[0-3]$")}
# Optional DAG/epic header fields (docs/adr/0119): validated by check() and dependencies().
_TICKET_ID = re.compile(r"^\d{4}$")
_TITLE_ID = re.compile(r"^#\s*(\d{4})(?:\s|[—–-]|$)")
_EPIC = re.compile(r"^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$")
_HEADER = re.compile(r"^- ([A-Z][A-Za-z-]*): ?(.*)$")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def section(text, heading):
    """The lines under HEADING up to the next `## `, or None when absent."""
    lines, inside = [], False
    for line in text.splitlines():
        if line.rstrip() == heading or line.startswith(heading + " "):
            inside = True
        elif inside and line.startswith("## "):
            break
        elif inside:
            lines.append(line)
    return lines if inside else None


def paragraphs(lines):
    out, current = [], []
    for line in lines:
        if line.strip():
            current.append(line.strip())
        elif current:
            out.append(" ".join(current))
            current = []
    if current:
        out.append(" ".join(current))
    return out


def criteria_of(text):
    lines = section(text, CRITERIA)
    return None if lines is None else paragraphs(lines)


def flaw(criterion):
    """Why one criterion is not testable, or None."""
    at = []
    for word in ("given", "when", "then"):
        found = _WORD[word].search(criterion, at[-1] if at else 0)
        if not found:
            return f'"{criterion[:60]}" has no {word.capitalize()} after its ' + (
                "start" if not at else ("Given" if word == "when" else "When"))
        at.append(found.end())
    outcome = criterion[at[-1]:].strip(" .,;:")
    if not outcome:
        return f'"{criterion[:60]}" has a Then with no outcome'
    vague = _VAGUE.search(outcome)
    if vague:
        return f'"{criterion[:60]}": "{vague.group(1)}" names no observable outcome'
    return None


def header(text):
    """{field: [values]} from the `- Key: value` lines before the first `## `."""
    out = {}
    for line in text.splitlines():
        if line.startswith("## "):
            break
        m = _HEADER.match(line.rstrip())
        if m:
            out.setdefault(m.group(1), []).append(m.group(2).strip())
    return out


def fields(text):
    """{feature, priority}: each the header's value, or None when absent."""
    head = header(text)
    return {k.lower(): (head.get(k) or [None])[0] for k in FIELDS}


def ticket_id(text):
    """The 4-digit ticket id from the `# <id> ...` heading, or None when absent."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            match = _TITLE_ID.match(stripped)
            return match.group(1) if match else None
        break
    return None


def _parse_dependencies(text, own_id=None):
    """Return (depends_on, epic, error) for optional `- Depends-On:` and `- Epic:` headers."""
    head = header(text)
    known_id = own_id if own_id is not None else ticket_id(text)

    dep_lines = head.get("Depends-On") or []
    if len(dep_lines) > 1:
        return [], None, f"- Depends-On: is set {len(dep_lines)} times"
    deps = []
    if dep_lines:
        raw = dep_lines[0].strip()
        if not raw:
            return [], None, '- Depends-On: "" is not a comma-separated list of 4-digit ticket IDs'
        seen = set()
        for part in raw.split(","):
            item = part.strip()
            if not _TICKET_ID.match(item):
                return [], None, f'- Depends-On: "{item}" is not a 4-digit ticket ID'
            if known_id and item == known_id:
                return [], None, f"- Depends-On: ticket {known_id} cannot depend on itself"
            if item not in seen:
                seen.add(item)
                deps.append(item)

    epic_lines = head.get("Epic") or []
    if len(epic_lines) > 1:
        return [], None, f"- Epic: is set {len(epic_lines)} times"
    epic_val = None
    if epic_lines:
        raw_epic = epic_lines[0].strip()
        if not raw_epic or not _EPIC.match(raw_epic):
            return [], None, f'- Epic: "{raw_epic}" is not a valid id or slug'
        epic_val = raw_epic

    return deps, epic_val, None


def dependencies(text, own_id=None):
    """Return (depends_on, epic) from header fields; raise ValueError when malformed."""
    deps, epic_val, error = _parse_dependencies(text, own_id=own_id)
    if error:
        raise ValueError(error)
    return deps, epic_val


def depends_on(text, own_id=None):
    """Return validated list of 4-digit dependency IDs from `- Depends-On:`."""
    return dependencies(text, own_id=own_id)[0]


def epic(text):
    """Return validated epic id/slug from `- Epic:`, or None when absent."""
    return dependencies(text)[1]


def check(text, own_id=None):
    """The first reason the spec fails the gate, or None (docs/adr/0055 §4)."""
    criteria = criteria_of(text)
    if criteria is None:
        return f"no {CRITERIA} section"
    if not criteria:
        return f"{CRITERIA} has no criterion"
    for criterion in criteria:
        why = flaw(criterion)
        if why:
            return why
    perimeter = section(text, PERIMETER)
    if perimeter is None:
        return f"no {PERIMETER} section"
    if not any(line.startswith("- ") for line in perimeter):
        return f"{PERIMETER} declares no path"
    head = header(text)
    for field, shape in FIELDS.items():
        values = head.get(field) or []
        if len(values) > 1:
            return f"- {field}: is set {len(values)} times"
        if values and not shape.match(values[0]):
            want = "P0, P1, P2 or P3" if field == "Priority" else "a slug, lowercase letters and digits joined by hyphens"
            return f'- {field}: "{values[0]}" is not {want}'
    _, _, dep_error = _parse_dependencies(text, own_id=own_id)
    if dep_error:
        return dep_error
    return None


def answers(doc):
    """The answered questions, in order, as the product role reads them."""
    out = []
    for q in doc.get("questions") or []:
        if not q.get("answer"):
            continue
        if q.get("from") == "spec":
            out.append(f"The spec gate returned the spec: {q.get('question')}\n\n"
                       f"The product owner answers: {q['answer']}")
        else:
            out.append(q["answer"])
    return out


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def last_line(path):
    try:
        lines = [line for line in read(path).splitlines() if line.strip()]
    except OSError:
        return ""
    return lines[-1] if lines else ""


class Refused(Exception):
    """A refusal; RUN, when given, is the stopped run the tower ledgers (docs/adr/0090, amendment A3)."""

    def __init__(self, message, run=None):
        super().__init__(message)
        self.run = run


def clarify(request, doc, run_dir, rc, env):
    """The clarify/answer step's result (docs/adr/0055 §2, §3)."""
    run = load(os.path.join(run_dir, "out", "run.json"), {})
    product = task(run) if isinstance(run, dict) else task({})
    questions = list(doc.get("questions") or [])
    if rc != 0:
        why = last_line(os.path.join(run_dir, "run.err")) or f"the product role exited {rc}"
        if env.get("AGENT_HARNESS") == "replay" and isinstance(run, dict) and run.get("no_recording"):
            criteria = criteria_of(request) or []
            return {"state": "spec-ready", "product": dict(product, consulted=False, why=why),
                    "spec": {"criteria": criteria, "by": "request"}}
        failed = dict(product, stop=run.get("stop")) if isinstance(run, dict) else product
        raise Refused(f"the product role failed: {why}", {"product": failed})
    calls = load(os.path.join(run_dir, "out", "calls.json"), [])
    decided = [c for c in calls if isinstance(c, dict) and c.get("name") in DECISIONS]
    if not decided:
        raise Refused("the product role neither asked nor confirmed")
    last = decided[-1]
    args = last.get("args") or {}
    if last["name"] == "ask_product_owner":
        question = str(args.get("question") or "").strip()
        if not question:
            raise Refused("the product role asked an empty question")
        questions.append({"question": question, "from": "product", "asked_at": now()})
        return {"state": "questions", "questions": questions, "product": dict(product, consulted=True)}
    criteria = [str(c).strip() for c in args.get("criteria") or [] if str(c).strip()]
    if not criteria:
        raise Refused("the product role confirmed no criteria")
    return {"state": "spec-ready", "product": dict(product, consulted=True),
            "spec": {"criteria": criteria, "by": "product"}}


def compose(request, doc):
    """The request with its criteria section replaced by the confirmed ones."""
    criteria = ((doc.get("spec") or {}).get("criteria")) or []
    block = [CRITERIA, ""] + [line for c in criteria for line in (c, "")]
    out, skipping, placed = [], False, False
    for line in request.splitlines():
        if line.rstrip() == CRITERIA:
            skipping, placed = True, True
            out.extend(block)
        elif skipping and line.startswith("## "):
            skipping = False
            out.append(line)
        elif not skipping:
            out.append(line)
    if not placed:
        out = out + [""] + block
    return "\n".join(out).rstrip("\n") + "\n"


def gated(doc, line):
    """The spec step's result from the gate's one line (docs/adr/0055 §4)."""
    at = now()
    if line.startswith("GO: spec"):
        return {"state": "opened", "spec": {"verdict": "GO", "line": line, "at": at}}
    if not line.startswith("NO-GO: spec — "):
        raise Refused(f"the spec gate printed no verdict: {line or 'nothing'}")
    reason = line[len("NO-GO: spec — "):]
    questions = list(doc.get("questions") or []) + [{"question": reason, "from": "spec", "asked_at": at}]
    return {"state": "questions", "questions": questions, "spec": {"verdict": "NO-GO", "line": line, "at": at}}


# An intake's file takes its id too (docs/adr/0090 §1).
_TICKET_FILE = re.compile(r"^(\d{4})(?:\.intake)?\.md$")


def next_id(tickets_dir):
    """The next four-digit id after the highest in TICKETS_DIR, tickets and intakes (docs/adr/0059 §4, 0090 §1)."""
    try:
        names = os.listdir(tickets_dir)
    except FileNotFoundError:
        names = []
    ids = [int(m.group(1)) for m in map(_TICKET_FILE.match, names) if m]
    return f"{max(ids, default=0) + 1:04d}"


def draft(fields):
    """A request's Markdown from the editor's fields, without its id line's id."""
    if not isinstance(fields, dict):
        raise Refused("the draft is not a JSON object")
    title = fields.get("title")
    if not isinstance(title, str) or not title.strip():
        raise Refused("a draft needs a title")
    text = {}
    for key in ("context", "feature", "priority", "epic"):
        value = fields.get(key) or ""
        if not isinstance(value, str):
            raise Refused(f"{key} is not a string")
        text[key] = value.strip()
    raw_deps = fields.get("depends_on")
    deps = []
    if raw_deps is not None:
        if isinstance(raw_deps, list):
            if not all(isinstance(d, str) for d in raw_deps):
                raise Refused("depends_on is not a list of ticket IDs")
            deps = [d.strip() for d in raw_deps if d.strip()]
        elif isinstance(raw_deps, str):
            deps = [raw_deps.strip()] if raw_deps.strip() else []
        else:
            raise Refused("depends_on is not a list of ticket IDs")
    criteria = fields.get("criteria") or []
    if not isinstance(criteria, list) or not all(isinstance(c, dict) for c in criteria):
        raise Refused("criteria is not a list of {given, when, then}")
    perimeter = fields.get("perimeter") or []
    if not isinstance(perimeter, list) or not all(isinstance(p, str) for p in perimeter):
        raise Refused("perimeter is not a list of paths")
    head = ["- Status: open"]
    head += [f"- Feature: {text['feature']}"] if text["feature"] else []
    head += [f"- Priority: {text['priority']}"] if text["priority"] else []
    head += [f"- Epic: {text['epic']}"] if text["epic"] else []
    head += [f"- Depends-On: {', '.join(deps)}"] if deps else []
    body = [""] + head + ["", "## Problem", "", text["context"] or "(no context given)", "", CRITERIA, ""]
    for c in criteria:
        lines = []
        for word in ("given", "when", "then"):
            value = c.get(word) or ""
            if not isinstance(value, str):
                raise Refused(f"a criterion's {word} is not a string")
            lines.append(f"{word.capitalize()} {' '.join(value.split())}".rstrip())
        body += lines + [""]
    body += [PERIMETER, ""] + [f"- `{p.strip()}`" for p in perimeter if p.strip()]
    return title.strip(), "\n".join(body).rstrip("\n") + "\n"


def write_draft(fields, tickets_dir, out_dir):
    """Write the draft under the next free id; return the id."""
    title, rest = draft(fields)
    tid = next_id(tickets_dir)
    path = os.path.join(out_dir, f"{tid}.md")
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(f"# {tid} — {title}\n{rest}")
    except FileExistsError:
        raise Refused(f"{path} exists: another request took {tid}") from None
    return tid


def main(argv, env=None):
    env = os.environ if env is None else env
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if cmd == "check" and len(args) == 1:
            why = check(read(args[0]))
            if why:
                print(why)
                return 1
            return 0
        if cmd == "answers" and len(args) == 1:
            print(json.dumps(answers(load(args[0], {})), ensure_ascii=False))
        elif cmd == "clarify" and len(args) == 4:
            result = clarify(read(args[0]), load(args[1], {}), args[2], int(args[3]), env)
            # The screen core/screen.py wrote beside the run (docs/adr/0091 §7).
            screen = load(os.path.join(args[2], "screen.json"), None)
            print(json.dumps(dict(result, screen=screen) if isinstance(screen, dict) else result,
                             ensure_ascii=False))
        elif cmd == "compose" and len(args) == 2:
            sys.stdout.write(compose(read(args[0]), load(args[1], {})))
        elif cmd == "gated" and len(args) == 2:
            print(json.dumps(gated(load(args[0], {}), args[1]), ensure_ascii=False))
        elif cmd == "draft" and len(args) in (2, 3):
            try:
                fields = json.loads(read(args[0]))
            except ValueError:
                raise Refused("the draft is not JSON") from None
            print(write_draft(fields, args[1], args[2] if len(args) == 3 else args[1]))
        else:
            print("usage: spec.py check <spec.md> | answers <doc> | clarify <request> <doc> <run dir> <rc>"
                  " | compose <request> <doc> | gated <doc> <line> | draft <fields> <tickets> [<out>]",
                  file=sys.stderr)
            return 2
    except (Refused, OSError) as exc:
        print(f"spec: {exc}", file=sys.stderr)
        for var, body in (("STEP_WHY", str(exc)), ("STEP_RUN", json.dumps(getattr(exc, "run", None) or ""))):
            if env.get(var) and body not in ("", '""'):
                try:
                    with open(env[var], "w", encoding="utf-8") as fh:
                        fh.write(body + "\n")
                except OSError:
                    pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
