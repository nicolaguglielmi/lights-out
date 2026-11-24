#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-logbook: the logbook and the console tell the truth and stay inside
# their perimeter (docs/adr/0047 §2-§5, Consequences; field by field in
# docs/plans/console.md, "Decided at build"), cold: no plane, no socket.
# Needs python3, jq, curl, git and the `jsonschema` package, all in the
# workbench. Written from that ADR and that plan, not from core/logbook.py
# or console/.
#   a. With stub sources in a throwaway root, core/logbook.sh runs twice:
#      one GO line each, the two files byte-equal, and byte-equal to the
#      golden fixtures/logbook/golden-1.6.json, written by hand from the
#      plan at 1.0, its 1.1 rows checked one by one against ADR 0055 §6 and
#      its 1.2 `adrs` against the stub map (docs/adr/0059 §3), its 1.3
#      `agents` against the stub contract (docs/adr/0073 §10), its 1.4 `why`
#      (docs/adr/0078 §2, §3) against the stub's tower documents (below), and
#      its 1.3 fields of ticket 0010, the network ticket, one by one against the
#      stub's tower documents and ADR 0071 (docs/adr/0071 §8): each task's
#      members (ids, harness, model, hand-written), files with their added
#      and removed counts, the patch shown, ci.gates (eight GO in the
#      Makefile's order), rework[0].gates (six GO, then mutation NO-GO), no
#      raw `lines`, the patch of 0004's attempt in flight withheld with the
#      contract's error, and tickets[].files rows (glob, member, attempts,
#      tests, adrs, held_by), each hand check run again on a logbook with
#      one fact planted wrong, which it must refuse. The 1.4 `why`, by hand
#      from ADR 0078 and the stub: ticket 0003's ci.why (attempt 2, gates, the
#      test gate, its line verbatim, its two FAILED lines, sast null) and its
#      rework[0].why (attempt 1, the sast gate, details shown, the two new
#      findings {rule, file, line}); ticket 0002's rework[0].why, recorded with
#      no gate lines, withheld with the exact error and its 1.3 `details`
#      null; ticket 0010's rework[0].why shown (mutation); every ci that said
#      GO has ci.why null; each run again on a logbook with one fact planted
#      wrong. The 1.5 `spend` and `dora` (docs/adr/0083 and 0082, amendments
#      of 2026-11-13), by hand from the stub: the ledger of 0003 and 0010 per
#      ticket, role and day, the summary, the limits and the budget line;
#      0007's stop the cap and, with a budget line in its document, the budget
#      and that line; the window ending at the record's last event and the
#      four measures; each run again with one fact planted wrong.
#      The 1.6 flow (docs/adr/0085 §1-§3), by hand from the stub: 0010's
#      fourteen nodes from the open to 100%, 0003's gates stopping at test,
#      each gate's seconds on ci and rework; the role matrix (docs/adr/0086),
#      0003's attempt in flight and the burn of 2026-11-03 (docs/adr/0087); each
#      run again planted wrong.
#      golden-1.5.json, golden-1.4.json, golden-1.3.json and golden-1.2.json stay in the
#      repository, untouched, as the old-logbook fixtures, and validate against
#      their schemas. The stub root
#      holds three ADRs, the files they cite, the notifier and its test, and
#      a ticket that names one in its one commit; its map is built from that
#      commit with core/map.py,
#      and the logbook's `adrs` and each ticket's `adrs` are worked out
#      again here from the map's rows with sqlite3 and fnmatch, not by
#      calling the reader's functions. Then variants of the sources, each
#      checked on the one field the plan's rule decides: no map, a map of
#      schema 1, a map at another commit, a map with no ADR, a map opened
#      read-only, a wide glob, who requested.
#      With no map, the files' tests and adrs are null; with the perimeters
#      held unread, their held_by is null (docs/adr/0071 §1).
#      The kanban's every card sits in its ticket's state's column, and moves
#      when the tower moves the ticket (docs/adr/0055 §7).
#   b. The golden validates against every committed schema of its major
#      (1.0 to 1.6), and against the schema file of its own minor; so do the
#      old fixtures, golden-1.5.json, golden-1.4.json, golden-1.3.json and golden-1.2.json (their own minors
#      and the ones before).
#   c. The committed snapshot, when there is one, validates, and its
#      spec.sha256 values are the committed tickets' (§4). A README with no
#      snapshot is drift; with neither, this point is skipped.
#   d. console/ names no adapter, no .crew/ and no store; serve.py starts
#      `core/logbook.sh`, `core/promote.sh`, `gates/spec.sh`,
#      `core/spec.py draft` and `make answer` / `make request` only, and has
#      four POST routes (docs/adr/0055 §2, docs/adr/0059 §4); index.html
#      fetches /v1/ only, POSTs to those four routes; `draggable` and the drag or drop handlers
#      appear only in the launch code, and a `draggable` value is conditional,
#      so only the held release's card drags (docs/adr/0079 §3, §5, which
#      amends docs/adr/0059 §1 for that one gesture). The
#      page's `#network-words` is `python3 core/network.py --table` as JSON,
#      its roles are the directories of roles/ and its gates the Makefile's
#      `GATES :=` (docs/adr/0071 §5), a wrong copy refused; and `make network
#      NETWORK_LOGBOOK=<golden>` prints on stdout what core/network.py prints
#      over the golden, for the default view and for TICKET and ATTEMPT.
#   e. The console at runtime: snapshot mode refuses to write and answers the
#      spec check without writing; the ETag answers 304; the start-up
#      refusals (§5); LIVE mode in the stub root refuses every bad press
#      before anything runs, and one good press moves the plane and records
#      who pressed; an answer to anything but the open question runs
#      nothing, and the good one runs `make answer` as `./go answer` does,
#      with who answered and `via: console` (docs/adr/0055 §2); the spec
#      check gives the line gates/spec.sh gives on the same file, on a GO
#      draft and on a NO-GO one per rule of ADR 0055 §4, and leaves the root
#      as it was; a request is refused in snapshot, public and without the
#      proxy header, refuses a NO-GO draft with nothing written, and on a GO
#      draft writes the next id and runs `make request` with who pressed and
#      `via: console` (docs/adr/0059 §4). The press's reason (docs/adr/0079
#      §2): a NO-GO with no reason, a blank one or a reason that is not a
#      string is refused with 400 before anything runs; a NO-GO with a reason
#      runs promote and the record's `rejected.reason` is it, via console; a
#      GO with a reason records it in `promoted.reason`, and both carry the
#      brief's sha256 (docs/adr/0079 §1).
#      The 1.4 canary fields (docs/adr/0080 §1-§4), by hand from the stub's
#      first log line and release.json: 0.2.0-a's reason and stage times and
#      slo, the others' none, launch.record's times, slo, reason and no running,
#      ticket 0001's canary row at the stage's judged_at with its slo, the rows
#      of the older releases at the release's at with none; a record in
#      progress surfaces `running` verbatim in launch.record; each check run
#      again on a logbook with one fact planted wrong.
#   f. Every numeric policy key has a "# Provenance, <kind>:" line above it
#      (Consequences).
# Before any of that, every checker runs on a bad input and must reject it:
# a gate that cannot fail is not a gate.
set -eu
name=logbook
. ci/lib.sh

repo=$(pwd)
stub="$repo/fixtures/logbook/stub"
golden="$repo/fixtures/logbook/golden-1.8.json"
golden17="$repo/fixtures/logbook/golden-1.7.json"
golden16="$repo/fixtures/logbook/golden-1.6.json"
golden15="$repo/fixtures/logbook/golden-1.5.json"
golden14="$repo/fixtures/logbook/golden-1.4.json"
golden13="$repo/fixtures/logbook/golden-1.3.json"
golden12="$repo/fixtures/logbook/golden-1.2.json"
tmp=$(mktemp -d)
pids=""
# shellcheck disable=SC2154 # p is the loop's own variable, assigned when the trap runs
trap 'for p in $pids; do kill "$p" 2>/dev/null || :; done; rm -rf "$tmp"' EXIT

for tool in python3 jq curl git sha256sum timeout; do
  command -v "$tool" >/dev/null 2>&1 || nogo "$tool is not installed; this gate runs in the workbench"
done
python3 -c 'import jsonschema' 2>/dev/null || nogo "python3 cannot import jsonschema; it is in the workbench lock (docs/adr/0047 §2)"
[ -f "$golden" ] || nogo "fixtures/logbook/golden-1.8.json not found"
[ -f "$golden17" ] || nogo "fixtures/logbook/golden-1.7.json not found (the old-logbook fixture)"
[ -f "$golden16" ] || nogo "fixtures/logbook/golden-1.6.json not found (the old-logbook fixture)"
[ -f "$golden15" ] || nogo "fixtures/logbook/golden-1.5.json not found (the old-logbook fixture)"
[ -f "$golden14" ] || nogo "fixtures/logbook/golden-1.4.json not found (the old-logbook fixture)"
[ -f "$golden13" ] || nogo "fixtures/logbook/golden-1.3.json not found (the old-logbook fixture)"
[ -f "$golden12" ] || nogo "fixtures/logbook/golden-1.2.json not found (the old-logbook fixture)"
[ -d "$stub" ] || nogo "fixtures/logbook/stub not found"

# --- the checkers, one python file, one subcommand each ---------------------
cat > "$tmp/checks.py" <<'PY'
import ast, fnmatch, glob, hashlib, json, os, posixpath, re, sqlite3, sys

import jsonschema


def die(msg):
    print(msg)
    sys.exit(1)


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        die(f"{path}: {exc}")


def validate(path):
    """The document against every committed schema of its own major."""
    doc = load(path)
    m = re.fullmatch(r"lights-out/logbook/(\d+)\.(\d+)", str(doc.get("schema") if isinstance(doc, dict) else None))
    if not m:
        die(f"{path}: `schema` is not lights-out/logbook/<major>.<minor>")
    files = sorted(glob.glob(f"core/logbook/{m.group(1)}.*.schema.json"))
    if not files:
        die(f"no core/logbook/{m.group(1)}.*.schema.json to validate {path} against")
    own = f"core/logbook/{m.group(1)}.{m.group(2)}.schema.json"
    if own not in files:
        die(f"{path} says {doc['schema']}, but {own} is not committed")
    for schema_file in files:
        schema = load(schema_file)
        jsonschema.Draft202012Validator.check_schema(schema)
        errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(doc), key=lambda e: list(map(str, e.path)))
        if errors:
            e = errors[0]
            where = "/".join(map(str, e.path)) or "(top)"
            die(f"{path} does not validate against {schema_file}: {where}: {e.message[:160]}")
    return doc


def cmd_valid(path):
    validate(path)


def cmd_snapshot(path, tickets_dir):
    doc = validate(path)
    tickets = doc["tickets"]
    if isinstance(tickets, dict):
        die(f"{path}: tickets is unread ({tickets.get('error')}); a snapshot is taken from a plane that answered")
    for t in tickets:
        want = (t.get("spec") or {}).get("sha256")
        f = os.path.join(tickets_dir, t["id"] + ".md")
        if not os.path.isfile(f):
            die(f"{path}: ticket {t['id']} has no committed {f}")
        have = hashlib.sha256(open(f, "rb").read()).hexdigest()
        if want != have:
            die(f"{path}: ticket {t['id']} spec.sha256 is {want}, {f} is {have}; regenerate the snapshot")


def cmd_names(root):
    bad = []
    for d, dirs, files in os.walk(root):
        if os.path.abspath(d) == os.path.abspath(os.path.join(root, "snapshot")):
            dirs[:] = []
            continue
        for f in files:
            if f.endswith(".md"):
                continue
            p = os.path.join(d, f)
            try:
                text = open(p, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            for word in ("adapters/", ".crew/", "firestore", "STATE_STORE"):
                if word.lower() in text.lower():
                    bad.append(f"{p} names {word}")
    if bad:
        die("; ".join(bad))


# What console/serve.py may run (docs/adr/0047 §3, docs/adr/0055 §2,
# docs/adr/0059 §4): three scripts, one command of core/spec.py, two recipes.
SCRIPTS = {"core/logbook.sh", "core/promote.sh", "gates/spec.sh"}
MAKES = {"answer", "request", "intake", "intake-confirm"}
GETS = {"/v1/logbook.json", "/v1/console.json", "/v1/events"}
WRITES = {"/v1/go-for-launch", "/v1/answer", "/v1/spec-check", "/v1/request", "/v1/intake", "/v1/intake-confirm"}
DRAG = r"(?:drag|dragstart|dragover|dragend|dragenter|dragleave|drop)"


def docstrings(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                ids.add(id(body[0].value))
    return ids


def cmd_serve(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    skip = docstrings(tree)
    consts = [n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip]
    found = set()
    for c in consts:
        found.update(re.findall(r"[\w./-]+\.sh\b", c))
    if found != SCRIPTS:
        die(f"{path} names the scripts {sorted(found)}; want exactly {sorted(SCRIPTS)}")
    for c in consts:
        if c in ("git", "curl", "jq", "docker", "podman", "docker-compose", "bash"):
            die(f"{path} names the program {c}; it starts {sorted(SCRIPTS)}, core/spec.py draft and make only")
    makes, drafts = set(), 0
    for n in ast.walk(tree):
        if not (isinstance(n, ast.List) and n.elts):
            continue
        head, rest = n.elts[0], n.elts[1:]
        word = head.value if isinstance(head, ast.Constant) else None
        if word == "make":
            targets = [e.value for e in rest if isinstance(e, ast.Constant) and not str(e.value).startswith("-") and "=" not in str(e.value)]
            if len(targets) != 1 or targets[0] not in MAKES:
                die(f"{path} runs make {targets}; `make answer`, `make request`, `make intake` and `make intake-confirm` are the recipes it may run")
            makes.add(targets[0])
        elif word == "sh":
            if not (rest and isinstance(rest[0], ast.Constant) and rest[0].value in SCRIPTS):
                die(f"{path} runs sh on {ast.dump(rest[0]) if rest else 'nothing'}; only {sorted(SCRIPTS)}")
        elif word in ("python3", "python") or (isinstance(head, ast.Attribute) and head.attr == "executable"):
            if [e.value if isinstance(e, ast.Constant) else None for e in rest[:2]] != ["core/spec.py", "draft"]:
                die(f"{path} runs python on something but `core/spec.py draft`; that is the one command it may run")
            drafts += 1
    if makes != MAKES:
        die(f"{path} runs make {sorted(makes)}; want exactly {sorted(MAKES)}")
    if not drafts:
        die(f"{path} never runs `python3 core/spec.py draft`; the spec check and the request write their draft with it")
    for n in ast.walk(tree):
        if isinstance(n, ast.keyword) and n.arg == "shell" and not (isinstance(n.value, ast.Constant) and n.value.value is False):
            die(f"{path} passes shell={ast.dump(n.value)}; argv only")
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "os" \
                and (n.attr in ("system", "popen") or n.attr.startswith(("exec", "spawn"))):
            die(f"{path} calls os.{n.attr}; the commands go through subprocess only")
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in ("do_PUT", "do_DELETE", "do_PATCH"):
            die(f"{path} defines {n.name}; POST is the only write")
    v1 = {c for c in consts if re.fullmatch(r"/v1/[\w.\-]+", c)}
    want = GETS | WRITES
    if v1 != want:
        die(f"{path} names the routes {sorted(v1)}; want exactly {sorted(want)}")
    posts = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "do_POST"]
    if len(posts) != 1:
        die(f"{path} has {len(posts)} do_POST; want 1")
    in_post = {n.value for n in ast.walk(posts[0]) if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and n.value.startswith("/")}
    if in_post - WRITES:
        die(f"{path}: do_POST names {sorted(in_post - WRITES)}; the POST routes are {sorted(WRITES)}")


def cmd_index(path):
    src = open(path, encoding="utf-8").read()
    for banned in ("XMLHttpRequest", "sendBeacon", "<form"):
        if banned in src:
            die(f"{path} uses {banned}; the page reads with GET fetch and writes with POST fetch")
    # Drag and drop only for the one gesture (docs/adr/0079 §3, §5; the rest of
    # docs/adr/0059 §1 and 0068 §1 stands): `draggable` and the drag or drop
    # handlers may appear only inside a function of the page that is about the
    # launch (its body names "launch": the held card's, the two targets'), and
    # a `draggable` value must be conditional (`held ? "true" : null`), so a
    # card that is not the held release has none. The property, setAttribute,
    # a handler or attribute outside any function (markup), and a listener
    # named by a string are the same sites and take the same rule; prose and
    # the CSS selector `[draggable=...]` are not read.
    spans = [(m.start(), m.group(1)) for m in re.finditer(r"^function\s+(\w+)", src, re.M)]
    def enclosing(pos):
        start = max((s for s, _ in spans if s <= pos), default=None)
        if start is None:
            return None
        end = re.compile(r"^\}", re.M).search(src, start)
        end = end.end() if end else len(src)
        return src[start:end] if pos < end else None
    for form, what in ((r"(?<!\[)\bdraggable\s*[=:]", "a draggable attribute or key"),
                       (r"\.draggable\b", "the draggable property"),
                       (r"""\bsetAttribute\s*\(\s*['"`]draggable['"`]""", "setAttribute('draggable')"),
                       (r"\bon" + DRAG + r"\s*[=:]", "a drag or drop handler attribute or key"),
                       (r"""['"`]on""" + DRAG + r"""['"`]""", "a drag or drop handler named by a string"),
                       (r"""\baddEventListener\s*\(\s*['"`]""" + DRAG + r"""['"`]""", "a drag or drop listener")):
        for hit in re.finditer(form, src):
            body = enclosing(hit.start())
            line = src[src.rfind("\n", 0, hit.start()) + 1:src.find("\n", hit.end())]
            if body is None or "launch" not in body.lower():
                die(f"{path} has {what} ({hit.group(0).strip()}) outside the launch code; only the held release's card and its two targets may drag or take a drop")
            if what in ("the draggable property", "setAttribute('draggable')"):
                die(f"{path} has {what}; `draggable` is a conditional attribute of the held card only")
            value = re.match(r"draggable\s*[=:]\s*([^,}\n]*)", src[hit.start():]).group(1) if what == "a draggable attribute or key" else "?"
            if "?" not in value:
                die(f"{path} sets draggable with no condition ({line.strip()[:60]}); only the held release's card drags")
    posted = set()
    root_literals = re.findall(r"""(['"`])(/[A-Za-z0-9_.\-][^'"`\s]*)\1""", src)
    stray = sorted({lit for _, lit in root_literals if not lit.startswith("/v1/")})
    for m in re.finditer(r"\bfetch\s*\(", src):
        i, depth, j = m.end(), 1, m.end()
        while j < len(src) and depth:
            depth += {"(": 1, ")": -1}.get(src[j], 0)
            j += 1
        args = src[i:j - 1].strip()
        lit = re.match(r"""['"`](/[^'"`]*)""", args)
        if lit:
            if not lit.group(1).startswith("/v1/"):
                die(f"{path} fetches {lit.group(1)}; /v1/ paths only")
        elif re.match(r"""['"`]""", args):
            die(f"{path} fetches {args[:40]}; /v1/ paths only")
        elif stray:
            die(f"{path} fetches a computed URL next to the literal paths {stray}; /v1/ paths only")
        meth = re.search(r"\bmethod\s*:\s*(\S+)", args)
        goes = lit.group(1) if lit else ""
        if meth:
            if meth.group(1).strip("'\",`") != "POST":
                die(f"{path} fetches with method {meth.group(1)}; POST is the only write")
            if goes not in WRITES:
                die(f"{path}: a POST to {goes or args[:30]}; the POST routes are {sorted(WRITES)}")
            posted.add(goes)
        elif goes in WRITES:
            die(f"{path} fetches {goes} without method POST")
    for m in re.finditer(r"\bmethod\s*:\s*(\S+)", src):
        if m.group(1).strip("'\",`") != "POST":
            die(f"{path} has method {m.group(1)}; POST is the only write")
    if posted != WRITES:
        die(f"{path} POSTs to {sorted(posted)}; want a POST fetch to each of {sorted(WRITES)}")
    if "/v1/logbook.json" not in src:
        die(f"{path} never names /v1/logbook.json")


def cmd_adrs(logbook, mapfile):
    """The logbook's `adrs` and each ticket's `adrs` against the map at MAPFILE
    (docs/adr/0059 §3), worked out again here from the map's rows with sqlite3
    and fnmatch: an ADR's files are the paths it cites that exist, its adrs and
    tickets the ones it mentions or that mention it, plus the logbook's tickets
    its files govern; a ticket's ADRs are those one of whose files one of its
    globs matches."""
    book = load(logbook)
    db = sqlite3.connect(f"file:{mapfile}?mode=ro", uri=True)
    try:
        sha = db.execute("SELECT value FROM meta WHERE key = 'sha'").fetchone()[0]
        nodes = {i: (k, n, t) for i, k, n, t in db.execute("SELECT id, kind, name, title FROM node")}
        docs = {i: (s, t) for i, s, t in db.execute("SELECT node, status, text FROM doc")}
        cited = {}
        for src, target in db.execute("SELECT src, target FROM cite WHERE kind = 'path' AND ok = 1"):
            cited.setdefault(src, set()).add(target)
        mentions = set(db.execute("SELECT src, dst FROM edge WHERE kind = 'mentions'"))
    finally:
        db.close()
    adr_ids = sorted((posixpath.basename(n)[:4], i) for i, (k, n, _) in nodes.items() if k == "adr")
    number = {i: num for num, i in adr_ids}
    tickets = book.get("tickets") if isinstance(book.get("tickets"), list) else []

    def governs(adr, ticket):
        globs = ticket.get("perimeter")
        return isinstance(globs, list) and any(fnmatch.fnmatchcase(f, g) for f in cited.get(adr, ()) for g in globs)

    items = []
    for num, i in adr_ids:
        by_mention, tied = set(), set()
        for src, dst in mentions:
            if i in (src, dst):
                other = dst if src == i else src
                if nodes[other][0] == "adr" and other != i:
                    by_mention.add(number[other])
                if nodes[other][0] == "ticket":
                    tied.add(nodes[other][1])
        tied |= {t["id"] for t in tickets if governs(i, t)}
        items.append({"number": num, "path": nodes[i][1], "title": nodes[i][2], "status": docs[i][0],
                      "text": docs[i][1], "files": sorted(cited.get(i, ())), "tickets": sorted(tied),
                      "adrs": sorted(by_mention)})
    got = book.get("adrs")
    if not isinstance(got, dict) or set(got) != {"map_sha", "items"}:
        die(f"{logbook}: adrs is {json.dumps(got)[:120]}; the map at {mapfile} was readable, want {{map_sha, items}}")
    if got["map_sha"] != sha:
        die(f"{logbook}: adrs.map_sha is {got['map_sha']}, the map says {sha}")
    if not isinstance(got["items"], list) or [i.get("number") if isinstance(i, dict) else i for i in got["items"]] != [i["number"] for i in items]:
        die(f"{logbook}: adrs.items are numbered {[i.get('number') if isinstance(i, dict) else i for i in got['items']] if isinstance(got['items'], list) else got['items']}, "
            f"the map holds {[i['number'] for i in items]}, in that order")
    for have, want in zip(got["items"], items):
        if set(have) != set(want):
            die(f"{logbook}: adrs item {want['number']} has the keys {sorted(have)}, want {sorted(want)}")
        for key in want:
            if have[key] != want[key]:
                die(f"{logbook}: adrs item {want['number']} {key} is {json.dumps(have[key], ensure_ascii=False)[:100]}, "
                    f"the map's rules give {json.dumps(want[key], ensure_ascii=False)[:100]}")
    for t in tickets:
        want = [num for num, i in adr_ids if governs(i, t)]
        if t.get("adrs") != want:
            die(f"{logbook}: ticket {t['id']} adrs is {json.dumps(t.get('adrs'))}, the perimeter {json.dumps(t.get('perimeter'))} gives {want}")


def cmd_unread(logbook, needle=""):
    """No usable map: `adrs` is {ok: false, error} and no ticket has an ADR."""
    book = load(logbook)
    got = book.get("adrs")
    if not (isinstance(got, dict) and set(got) == {"ok", "error"} and got["ok"] is False
            and isinstance(got["error"], str) and got["error"]):
        die(f"{logbook}: adrs is {json.dumps(got)[:120]}, want {{ok: false, error: <why>}}")
    if needle not in got["error"]:
        die(f"{logbook}: adrs.error is {got['error']!r}, want it to mention {needle!r}")
    for t in book.get("tickets") if isinstance(book.get("tickets"), list) else []:
        if t.get("adrs") != []:
            die(f"{logbook}: ticket {t['id']} has adrs {json.dumps(t.get('adrs'))}, want [] with no map")


def cmd_mapset(mapfile, key, value):
    """Change a meta value of a copy of the map, to make a map the readers must refuse."""
    db = sqlite3.connect(mapfile)
    db.execute("UPDATE meta SET value = ? WHERE key = ?", (value, key))
    db.commit()
    db.close()


NUM = re.compile(r"^[0-9]+(\.[0-9]+)?$")
KEY = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
PROV = re.compile(r"^#\s*Provenance, (decision|external|measurement):")


def cmd_prov(mode, *paths):
    """mode all: every numeric key; mode baseline: MUTATION_BASELINE only."""
    bad = []
    for p in paths:
        lines = open(p, encoding="utf-8").read().splitlines()
        for n, raw in enumerate(lines):
            m = KEY.match(raw.strip())
            if not m or not NUM.match(m.group(2).strip()):
                continue
            if mode == "baseline" and m.group(1) != "MUTATION_BASELINE":
                continue
            above, ok = n - 1, False
            while above >= 0 and lines[above].strip().startswith("#"):
                ok = ok or bool(PROV.match(lines[above].strip()))
                above -= 1
            if not ok:
                bad.append(f"{p}:{n + 1} {m.group(1)}")
    if bad:
        die("numeric keys without a '# Provenance, decision|external|measurement:' line directly above: " + ", ".join(bad))


# The tower's table as the console embeds it (docs/adr/0063 §2, the board's
# twin: ci/board.sh a): `next` is core/tower.py's NEXT, `who` names every step
# of PRECONDITION and no other, each with a sentence that carries no number of
# its own, and the four bands' states, concatenated, are the reader's COLUMNS.
def cmd_machine(path):
    page = open(path, encoding="utf-8").read()
    m = re.search(r'<script type="application/json" id="tower-machine">(.*?)</script>', page, re.S)
    if not m:
        die(f"{path} embeds no tower-machine table (<script type=application/json id=tower-machine>)")
    try:
        machine = json.loads(m.group(1))
    except ValueError as exc:
        die(f"{path}: tower-machine is not JSON: {exc}")
    if not isinstance(machine, dict):
        die("tower-machine is not an object")
    sys.path.insert(0, "core")
    import tower
    import logbook
    if machine.get("next") != tower.NEXT:
        die(f"next: console {machine.get('next')} vs core/tower.py {tower.NEXT}")
    who = machine.get("who")
    if not isinstance(who, dict):
        die(f"who is {who!r}, want an object of one sentence per step")
    if sorted(who) != sorted(tower.PRECONDITION):
        die(f"who names {sorted(who)}, core/tower.py's steps are {sorted(tower.PRECONDITION)}")
    for step, sentence in who.items():
        if not isinstance(sentence, str) or not sentence.strip():
            die(f"who[{step}] is {sentence!r}, want a non-empty sentence")
        if re.search(r"[0-9]", sentence):
            die(f"who[{step}] carries a digit: {sentence!r}; numbers come from the logbook")
    actor = machine.get("actor")
    if not isinstance(actor, dict):
        die(f"actor is {actor!r}, want an object of one actor per step")
    if sorted(actor) != sorted(tower.PRECONDITION):
        die(f"actor names {sorted(actor)}, core/tower.py's steps are {sorted(tower.PRECONDITION)}")
    actors = ("product", "architect", "spec", "dev", "gates", "reviewer", "tower", "canary")
    for step, who_acts in actor.items():
        if who_acts not in actors:
            die(f"actor[{step}] is {who_acts!r}, want one of {', '.join(actors)}")
    bands = machine.get("bands")
    if not isinstance(bands, list) or [b.get("band") if isinstance(b, dict) else None for b in bands] != ["spec", "build", "test", "release"]:
        die(f"bands are {bands!r}, want spec, build, test, release in that order")
    for b in bands:
        if not isinstance(b.get("name"), str) or not b["name"].strip() or not isinstance(b.get("states"), list):
            die(f"band {b.get('band')} has no name or no list of states")
    states = [s for b in bands for s in b["states"]]
    if states != list(logbook.COLUMNS):
        die(f"the bands' states {states} are not core/logbook.py's COLUMNS {list(logbook.COLUMNS)}")


# The network screen's words as the console embeds them (docs/adr/0071 §5, the
# tower table's twin): the copy is `core/network.py --table`, its roles are the
# directories of roles/ that hold a role.env and its gates the Makefile's
# `GATES :=` line, in order. ROOT is where core/, roles/ and the Makefile are.
def cmd_words(path, root="."):
    import subprocess
    page = open(path, encoding="utf-8").read()
    m = re.search(r'<script type="application/json" id="network-words">(.*?)</script>', page, re.S)
    if not m:
        die(f"{path} embeds no network-words table (<script type=application/json id=network-words>)")
    try:
        words = json.loads(m.group(1))
    except ValueError as exc:
        die(f"{path}: network-words is not JSON: {exc}")
    if not isinstance(words, dict):
        die("network-words is not an object")
    roles = sorted(os.path.basename(os.path.dirname(f)) for f in glob.glob(os.path.join(root, "roles", "*", "role.env")))
    mk = re.search(r"^GATES := (.*)$", open(os.path.join(root, "Makefile"), encoding="utf-8").read(), re.M)
    if not mk:
        die("the Makefile has no `GATES :=` line")
    gates = mk.group(1).split()
    if words.get("roles") != roles:
        die(f"roles: console {words.get('roles')} vs the directories of roles/ {roles}")
    if words.get("gates") != gates:
        die(f"gates: console {words.get('gates')} vs the Makefile's GATES {gates}")
    ran = subprocess.run([sys.executable, "core/network.py", "--table"], cwd=root, capture_output=True, text=True)
    if ran.returncode != 0:
        die(f"core/network.py --table exited {ran.returncode}: {ran.stderr.strip()[:200]}")
    try:
        table = json.loads(ran.stdout)
    except ValueError as exc:
        die(f"core/network.py --table is not JSON: {exc}")
    if table.get("roles") != roles or table.get("gates") != gates:
        die(f"core/network.py's TABLE has roles {table.get('roles')} and gates {table.get('gates')}, "
            f"want the directories of roles/ {roles} and the Makefile's GATES {gates}")
    if words != table:
        die(f"network-words is not `core/network.py --table`: differs at "
            f"{sorted(k for k in set(words) | set(table) if words.get(k) != table.get(k))}")


if __name__ == "__main__":
    globals()["cmd_" + sys.argv[1]](*sys.argv[2:])
PY
chk() { python3 "$tmp/checks.py" "$@" 2>"$tmp/chk.err"; }

# must_reject WHAT CHECK ARGS...: the checker has to fail, and say so.
must_reject() {
  what=$1
  shift
  if chk "$@" >"$tmp/chk.out"; then
    nogo "the checker for $what accepted a bad input; a gate that cannot fail is not a gate"
  fi
  [ -s "$tmp/chk.out" ] || nogo "the checker for $what rejected a bad input without saying why" "$(cat "$tmp/chk.err")"
}
must_accept() {
  what=$1
  shift
  chk "$@" >"$tmp/chk.out" || nogo "the checker for $what rejected a good input: $(cat "$tmp/chk.out")" "$(cat "$tmp/chk.err")"
}

# --- the checkers reject what they are for ----------------------------------
st="$tmp/selftest"
mkdir -p "$st/console/snapshot" "$st/tickets"
mkdir -p "$st/core"
cp -R "$repo/core/logbook" "$st/core/logbook" 2>/dev/null || :
[ -f "$st/core/logbook/1.0.schema.json" ] || nogo "core/logbook/1.0.schema.json not found"

# schema validation: a verdict outside the enum is refused.
jq '.tickets[0].outcome.verdict = "maybe"' "$golden" > "$st/bad-enum.json"
jq 'del(.launch)' "$golden" > "$st/bad-missing.json"
jq '.schema = "lights-out/logbook/9.0"' "$golden" > "$st/bad-major.json"
# 1.2 (docs/adr/0059 §3): the document's own minor has its schema file, and
# `adrs` is typed.
jq '.schema = "lights-out/logbook/1.9"' "$golden" > "$st/bad-minor.json"
jq '.adrs.items[0].number = 59' "$golden" > "$st/bad-adr-number.json"
jq '.adrs.items[0].files = "app/backend/router.py"' "$golden" > "$st/bad-adr-files.json"
jq '.adrs.map_sha = 5' "$golden" > "$st/bad-adr-sha.json"
jq '.tickets[0].adrs = "0001"' "$golden" > "$st/bad-ticket-adrs.json"
# 1.3 (docs/adr/0071 §1): each new field is typed. The golden's ticket 0010 is
# the one that carries them all.
jq '(.tickets[] | select(.id == "0010") | .ci.gates) = "GO: risk"' "$golden" > "$st/bad-ci-gates.json"
jq '(.tickets[] | select(.id == "0010") | .rework[0].gates) = "NO-GO: mutation"' "$golden" > "$st/bad-rework-gates.json"
jq '(.tickets[] | select(.id == "0010") | .tasks[0].members) = "impl"' "$golden" > "$st/bad-members.json"
jq '(.tickets[] | select(.id == "0010") | .files[0].tests) = "tests/test_notify.py"' "$golden" > "$st/bad-files-tests.json"
jq '(.tickets[] | select(.id == "0010") | .files[0].attempts) = [1, "2"]' "$golden" > "$st/bad-files-attempts.json"
jq '(.tickets[] | select(.id == "0010") | .tasks[0].patch.bytes) = "577"' "$golden" > "$st/bad-patch-bytes.json"
# 1.4 (docs/adr/0078 §2): `why` is typed, on ci and on a rework entry.
jq '(.tickets[] | select(.id == "0003") | .ci.why.attempt) = "2"' "$golden" > "$st/bad-why-attempt.json"
jq '(.tickets[] | select(.id == "0003") | .ci.why.details) = "FAILED tests/a.py::one"' "$golden" > "$st/bad-why-details.json"
jq '(.tickets[] | select(.id == "0003") | .rework[0].why.sast[0].line) = "7"' "$golden" > "$st/bad-why-sast.json"
jq '(.tickets[] | select(.id == "0003") | .rework[0].why.gate) = 5' "$golden" > "$st/bad-why-gate.json"
# 1.5 (docs/adr/0082, 0083, amendments of 2026-11-13): dora, spend and the outcome's stop are typed.
jq '.dora.window_days = "30"' "$golden" > "$st/bad-dora-window.json"
jq '.dora.lines = "window: 30 days"' "$golden" > "$st/bad-dora-lines.json"
jq '.spend.lines = "budget: none"' "$golden" > "$st/bad-spend-lines.json"
jq '.spend.limits.SPEND_BUDGET_TICKET_USD = "1.00"' "$golden" > "$st/bad-spend-limit.json"
jq '(.tickets[] | select(.id == "0007") | .outcome.stop) = "money"' "$golden" > "$st/bad-stop.json"
# 1.6 (docs/adr/0085): the flow and each gate's seconds are typed.
jq '(.tickets[] | select(.id == "0010") | .flow[0].verdict) = "maybe"' "$golden" > "$st/bad-flow-verdict.json"
jq '(.tickets[] | select(.id == "0010") | .flow) |= .[0:13]' "$golden" > "$st/bad-flow-length.json"
jq '(.tickets[] | select(.id == "0010") | .ci.seconds.risk) = "1"' "$golden" > "$st/bad-ci-seconds.json"
# 1.6 (docs/adr/0086, 0087): the roles, the run in flight and the burn are typed.
jq '.roles[0].state = "busy"' "$golden" > "$st/bad-role-state.json"
jq '(.tickets[] | select(.id == "0003") | .live.tokens) = "2210000"' "$golden" > "$st/bad-live-tokens.json"
jq '.spend.burn.hourly |= .[0:23]' "$golden" > "$st/bad-burn-hourly.json"
(
  cd "$st"
  must_reject "schema validation (an enum value)" valid bad-enum.json
  must_reject "schema validation (a missing top-level key)" valid bad-missing.json
  must_reject "schema validation (a major with no schema)" valid bad-major.json
  must_reject "schema validation (a minor with no schema file)" valid bad-minor.json
  must_reject "schema validation (an ADR number that is not a string)" valid bad-adr-number.json
  must_reject "schema validation (an ADR's files that is not a list)" valid bad-adr-files.json
  must_reject "schema validation (a map_sha that is not a string)" valid bad-adr-sha.json
  must_reject "schema validation (a ticket's adrs that is not a list)" valid bad-ticket-adrs.json
  must_reject "schema validation (a ci.gates that is not a list)" valid bad-ci-gates.json
  must_reject "schema validation (a rework entry's gates that is not a list)" valid bad-rework-gates.json
  must_reject "schema validation (members that is not a list)" valid bad-members.json
  must_reject "schema validation (a file's tests that is not a list)" valid bad-files-tests.json
  must_reject "schema validation (a file's attempts that are not integers)" valid bad-files-attempts.json
  must_reject "schema validation (a patch's bytes that is not an integer)" valid bad-patch-bytes.json
  must_reject "schema validation (a why's attempt that is not an integer)" valid bad-why-attempt.json
  must_reject "schema validation (a why's details that is neither a list nor the withheld reason)" valid bad-why-details.json
  must_reject "schema validation (a sast finding's line that is not an integer)" valid bad-why-sast.json
  must_reject "schema validation (a why's gate that is not a string or null)" valid bad-why-gate.json
  must_reject "schema validation (a dora window that is not an integer)" valid bad-dora-window.json
  must_reject "schema validation (dora lines that are not a list)" valid bad-dora-lines.json
  must_reject "schema validation (spend lines that are not a list)" valid bad-spend-lines.json
  must_reject "schema validation (a spend limit that is not a number or null)" valid bad-spend-limit.json
  must_reject "schema validation (an outcome's stop outside budget and cap)" valid bad-stop.json
  must_reject "schema validation (a flow node's verdict outside its four)" valid bad-flow-verdict.json
  must_reject "schema validation (a flow of thirteen nodes)" valid bad-flow-length.json
  must_reject "schema validation (a gate's seconds that is not an integer)" valid bad-ci-seconds.json
  must_reject "schema validation (a role's state outside running, ran and idle)" valid bad-role-state.json
  must_reject "schema validation (a run in flight's tokens that is not an integer)" valid bad-live-tokens.json
  must_reject "schema validation (a burn of 23 hours)" valid bad-burn-hourly.json
  must_accept "schema validation" valid "$golden"
) || exit 1

# snapshot drift: spec.sha256 against the committed ticket.
for f in "$stub"/tickets/*.md; do
  id=$(basename "$f" .md)
  printf '{"%s": "%s"}\n' "$id" "$(sha256sum "$f" | cut -d' ' -f1)"
done | jq -s 'add' > "$st/shas.json"
jq --slurpfile m "$st/shas.json" '.tickets |= (map(select(.spec != null)) | map(.spec.sha256 = $m[0][.id]))' "$golden" > "$st/snap-good.json"
jq '.tickets[0].spec.sha256 = "0000000000000000000000000000000000000000000000000000000000000000"' "$st/snap-good.json" > "$st/snap-drift.json"
jq '.tickets[0].id = "9999"' "$st/snap-good.json" > "$st/snap-unknown.json"
(
  cd "$st"
  must_accept "the snapshot's spec hashes" snapshot snap-good.json "$stub/tickets"
  must_reject "the snapshot's spec hashes (a drifted hash)" snapshot snap-drift.json "$stub/tickets"
  must_reject "the snapshot's spec hashes (a ticket that is not committed)" snapshot snap-unknown.json "$stub/tickets"
) || exit 1

# console/ perimeter.
mkdir -p "$st/names-good/snapshot" "$st/names-bad/snapshot"
printf 'GET /v1/logbook.json only\n' > "$st/names-good/a.py"
printf '{"error": "adapters/x/get.sh failed"}\n' > "$st/names-good/snapshot/logbook.json"
printf 'x = ".crew/release.json"\n' > "$st/names-bad/a.py"
must_accept "the console's names" names "$st/names-good"
must_reject "the console's names (.crew/)" names "$st/names-bad"
printf 'STATE_STORE\n' > "$st/names-bad/a.py"
must_reject "the console's names (STATE_STORE)" names "$st/names-bad"

# serve.py: three scripts, `core/spec.py draft` and four recipes, six POST
# routes (docs/adr/0059 §4, docs/adr/0090 §8).
cat > "$st/serve-good.py" <<'PY'
"""Docstring naming core/anything-else.sh is not an argv."""
import subprocess


class H:
    def do_GET(self):
        if self.path in ("/", "/v1/logbook.json", "/v1/console.json", "/v1/events"):
            return
    def do_POST(self):
        if self.path not in ("/v1/go-for-launch", "/v1/answer", "/v1/spec-check", "/v1/intake", "/v1/intake-confirm", "/v1/request"):
            return
        subprocess.run(["sh", "core/promote.sh"], check=False)
        subprocess.run(["sh", "core/logbook.sh"], check=False)
        subprocess.run(["sh", "gates/spec.sh", "draft.md"], check=False)
        subprocess.run(["python3", "core/spec.py", "draft", "fields.json", "tickets"], check=False)
        subprocess.run(["make", "--no-print-directory", "answer", "TICKET=1"], check=False)
        subprocess.run(["make", "--no-print-directory", "request", "TICKET=1"], check=False)
        subprocess.run(["make", "--no-print-directory", "intake"], check=False)
        subprocess.run(["make", "--no-print-directory", "intake-confirm", "TICKET=1"], check=False)
PY
{ cat "$st/serve-good.py"; printf 'subprocess.run(["sh", "adapters/x/get.sh"])\n'; } > "$st/serve-adapter.py"
sed 's|"/v1/request"):|"/v1/request", "/other"):|' "$st/serve-good.py" > "$st/serve-two-posts.py"
sed 's|"answer", "TICKET=1"|"release", "TICKET=1"|' "$st/serve-good.py" > "$st/serve-make.py"
sed 's|"request", "TICKET=1"|"answer", "TICKET=1"|' "$st/serve-good.py" > "$st/serve-no-request-recipe.py"
sed 's|"request", "TICKET=1"|"request", "deploy", "TICKET=1"|' "$st/serve-good.py" > "$st/serve-two-targets.py"
sed 's|check=False)|check=False, shell=True)|' "$st/serve-good.py" > "$st/serve-shell.py"
sed 's|"/v1/console.json"|"/v1/console.json", "/v1/extra"|' "$st/serve-good.py" > "$st/serve-route.py"
sed 's|, "/v1/events"||' "$st/serve-good.py" > "$st/serve-no-events-route.py"
sed 's|, "/v1/request"):|):|; s|, "/v1/request")|)|' "$st/serve-good.py" > "$st/serve-no-request-route.py"
sed 's|"/v1/spec-check", ||' "$st/serve-good.py" > "$st/serve-no-spec-route.py"
sed 's|\["sh", "core/promote.sh"\]|["git", "core/promote.sh"]|' "$st/serve-good.py" > "$st/serve-git.py"
sed 's|"gates/spec.sh"|"gates/other.sh"|' "$st/serve-good.py" > "$st/serve-gate.py"
sed '/gates\/spec.sh/d' "$st/serve-good.py" > "$st/serve-no-gate.py"
sed 's|"core/spec.py", "draft"|"core/other.py", "draft"|' "$st/serve-good.py" > "$st/serve-python.py"
sed 's|"core/spec.py", "draft"|"-c", "print(1)"|' "$st/serve-good.py" > "$st/serve-python-c.py"
sed 's|"core/spec.py", "draft"|"core/spec.py", "check"|' "$st/serve-good.py" > "$st/serve-spec-command.py"
sed '/core\/spec.py/d' "$st/serve-good.py" > "$st/serve-no-draft.py"
sed 's|\["sh", "core/logbook.sh"\]|["sh", script]|' "$st/serve-good.py" > "$st/serve-computed.py"
{ cat "$st/serve-good.py"; printf '\n\nclass X:\n    def do_DELETE(self):\n        pass\n'; } > "$st/serve-delete.py"
must_accept "serve.py" serve "$st/serve-good.py"
for v in adapter two-posts shell route no-events-route git delete make no-request-recipe two-targets no-request-route no-spec-route gate no-gate python python-c spec-command no-draft computed; do
  must_reject "serve.py ($v)" serve "$st/serve-$v.py"
done

# index.html: GET fetch on /v1/, a POST fetch to each of the six routes, and
# drag and drop only for the held release (docs/adr/0079 §3, §5).
cat > "$st/index-good.html" <<'HTML'
<!-- no drag-and-drop here but the held release's: a dropdown is a select -->
<style>.card[draggable="true"] { cursor: grab; }</style>
<script>
function card(t) {
  const held = launchTicket() === t;
  return h("div", {cls: "card", draggable: held ? "true" : null, "data-launch": held ? "v" : null,
    ondragstart: held ? (e) => begin(e) : null});
}
function launchDrop() {
  return h("button", {id: "drop-rollback", ondragover: (e) => e.preventDefault(), ondragleave: stop, ondrop: (e) => ask(e)});
}
function column(c) {
  return h("div", {cls: "col"});
}
fetch('/v1/logbook.json').then(r => r.json());
fetch("/v1/go-for-launch", {method: 'POST', body: '{}'});
fetch("/v1/answer", {method: 'POST', body: '{}'});
fetch("/v1/spec-check", {method: 'POST', body: '{}'});
fetch("/v1/request", {method: 'POST', body: '{}'});
fetch("/v1/intake", {method: 'POST', body: '{}'});
fetch("/v1/intake-confirm", {method: 'POST', body: '{}'});
const label = "drop";
</script>
HTML
sed "s|fetch('/v1/logbook.json')|fetch('https://example.invalid/l.json')|" "$st/index-good.html" > "$st/index-external.html"
sed "s|fetch('/v1/logbook.json')|fetch('/state.json')|" "$st/index-good.html" > "$st/index-other.html"
sed "s|fetch('/v1/logbook.json')|fetch('/v1/logbook.json', {method: 'PUT'})|" "$st/index-good.html" > "$st/index-put.html"
sed "s|fetch('/v1/logbook.json')|fetch('/v1/console.json', {method: 'POST'})|" "$st/index-good.html" > "$st/index-post-elsewhere.html"
sed "s|\"/v1/go-for-launch\", {method: 'POST', body|\"/v1/go-for-launch\", {body|" "$st/index-good.html" > "$st/index-get-go.html"
sed "s|\"/v1/answer\", {method: 'POST', body|\"/v1/answer\", {body|" "$st/index-good.html" > "$st/index-get-answer.html"
sed "s|\"/v1/spec-check\", {method: 'POST', body|\"/v1/spec-check\", {body|" "$st/index-good.html" > "$st/index-get-spec.html"
sed "s|\"/v1/request\", {method: 'POST', body|\"/v1/request\", {body|" "$st/index-good.html" > "$st/index-get-request.html"
sed '/"\/v1\/request"/d' "$st/index-good.html" > "$st/index-no-request.html"
sed '/"\/v1\/spec-check"/d' "$st/index-good.html" > "$st/index-no-spec.html"
sed 's|<script>|<script>var x = new XMLHttpRequest();|' "$st/index-good.html" > "$st/index-xhr.html"
sed 's|<script>|<li draggable="true">card</li><script>|' "$st/index-good.html" > "$st/index-draggable-attr.html"
sed 's|<script>|<script>h("li", {draggable: true});|' "$st/index-good.html" > "$st/index-draggable-key.html"
sed 's|<script>|<script>card.draggable = true;|' "$st/index-good.html" > "$st/index-draggable-prop.html"
sed 's|<script>|<script>card.setAttribute("draggable", "true");|' "$st/index-good.html" > "$st/index-draggable-set.html"
sed 's|<script>|<div ondrop="move()">x</div><script>|' "$st/index-good.html" > "$st/index-ondrop-attr.html"
sed 's|<script>|<script>h("div", {ondragover: stop});|' "$st/index-good.html" > "$st/index-ondragover-key.html"
sed 's|<script>|<script>h("div", {"ondragstart": begin});|' "$st/index-good.html" > "$st/index-ondragstart-string.html"
sed 's|<script>|<script>card.ondragend = end;|' "$st/index-good.html" > "$st/index-ondragend-prop.html"
sed "s|<script>|<script>col.addEventListener('drop', move);|" "$st/index-good.html" > "$st/index-listener-drop.html"
sed 's|<script>|<script>col.addEventListener("dragover", stop);|' "$st/index-good.html" > "$st/index-listener-dragover.html"
sed 's|<script>|<script>col.addEventListener("dragenter", stop);|' "$st/index-good.html" > "$st/index-listener-dragenter.html"
sed 's|<script>|<script>col.addEventListener("dragleave", stop);|' "$st/index-good.html" > "$st/index-listener-dragleave.html"
sed 's|<script>|<script>card.addEventListener("dragstart", begin);|' "$st/index-good.html" > "$st/index-listener-dragstart.html"
# docs/adr/0079 §3, §5: the same sites, in a function that is not the launch's,
# or with no condition on the card, are refused.
sed 's|{cls: "col"}|{cls: "col", draggable: held ? "true" : null}|' "$st/index-good.html" > "$st/index-draggable-other-card.html"
sed 's|{cls: "col"}|{cls: "col", draggable: "true"}|' "$st/index-good.html" > "$st/index-draggable-column.html"
sed 's|draggable: held ? "true" : null|draggable: "true"|' "$st/index-good.html" > "$st/index-draggable-unconditional.html"
sed 's|{cls: "col"}|{cls: "col", ondrop: move}|' "$st/index-good.html" > "$st/index-ondrop-column.html"
sed 's|{cls: "col"}|{cls: "col", ondragover: stop}|' "$st/index-good.html" > "$st/index-ondragover-column.html"
sed 's|return h("div", {cls: "col"});|col.addEventListener("dragover", stop); return h("div", {cls: "col"});|' "$st/index-good.html" > "$st/index-listener-column.html"
sed 's|<style>|<div draggable="true">x</div><style>|' "$st/index-good.html" > "$st/index-draggable-markup.html"
sed 's|return h("div", {cls: "col"});|x.draggable = true; launch(); return h("div", {cls: "col"});|' "$st/index-good.html" > "$st/index-draggable-prop-launch.html"
must_accept "index.html" index "$st/index-good.html"
for v in external other put post-elsewhere get-go get-answer get-spec get-request no-request no-spec xhr \
  draggable-attr draggable-key draggable-prop draggable-set ondrop-attr ondragover-key ondragstart-string ondragend-prop \
  listener-drop listener-dragover listener-dragenter listener-dragleave listener-dragstart \
  draggable-other-card draggable-column draggable-unconditional ondrop-column ondragover-column listener-column \
  draggable-markup draggable-prop-launch; do
  must_reject "index.html ($v)" index "$st/index-$v.html"
done

# The tower's table in the console (docs/adr/0063 §2): the good one is built
# from the tower's own tables, and each rule is broken once on a copy.
python3 - "$st" <<'PY' || nogo "could not build the good tower-machine"
import json, sys
sys.path.insert(0, "core")
import tower, logbook
cols = list(logbook.COLUMNS)
machine = {"next": tower.NEXT, "who": {s: "The " + s + " step runs" for s in tower.PRECONDITION},
           "actor": {s: "tower" for s in tower.PRECONDITION},
           "bands": [{"band": b, "name": b.title(), "states": cols[i:j]} for b, (i, j) in
                     zip(("spec", "build", "test", "release"), ((0, 3), (3, 5), (5, 9), (9, 13)))]}
open(sys.argv[1] + "/machine-good.json", "w").write(json.dumps(machine))
PY
# wrap_machine JSON_FILE OUT: a page that embeds that JSON as the console does.
wrap_machine() { { printf '<!doctype html><script type="application/json" id="tower-machine">'; cat "$1"; printf '</script>\n'; } > "$2"; }
wrap_machine "$st/machine-good.json" "$st/machine-good.html"
must_accept "the console's tower table" machine "$st/machine-good.html"
# mach NAME JQ: the good table with one rule broken; the checker must refuse it.
mach() { jq -c "$2" "$st/machine-good.json" > "$st/machine-$1.json"; wrap_machine "$st/machine-$1.json" "$st/machine-$1.html"; must_reject "the console's tower table ($1)" machine "$st/machine-$1.html"; }
printf '<!doctype html><p>no table</p>\n' > "$st/machine-none.html"
must_reject "the console's tower table (no table at all)" machine "$st/machine-none.html"
printf '<script type="application/json" id="tower-machine">{not json</script>\n' > "$st/machine-badjson.html"
must_reject "the console's tower table (not JSON)" machine "$st/machine-badjson.html"
mach next-changed '.next["ci-failed"] = "merge"'
mach next-dropped 'del(.next["main-green"])'
mach next-added '.next["held"] = "release"'
mach who-missing 'del(.who.rework)'
mach who-extra '.who.deploy = "The deploy step runs"'
mach who-empty '.who.merge = "  "'
mach who-not-string '.who.merge = 3'
mach who-digit '.who.ci = "The gates run at 5 percent"'
mach actor-missing 'del(.actor.rework)'
mach actor-extra '.actor.deploy = "dev"'
mach actor-outside-list '.actor.ci = "robot"'
mach actor-not-string '.actor.merge = 3'
mach actor-absent 'del(.actor)'
mach actor-not-object '.actor = ["tower"]'
mach bands-order '.bands |= [.[1], .[0], .[2], .[3]]'
mach bands-three '.bands |= .[0:3]'
mach bands-name '.bands[0].name = ""'
mach states-swapped '.bands[2].states |= reverse'
mach states-dropped '.bands[3].states |= .[0:3]'
mach states-extra '.bands[0].states += ["assigned"]'

# The network screen's words (docs/adr/0071 §5): the good copy is the table
# core/network.py prints, and each rule is broken once on a copy.
[ -f "$repo/core/network.py" ] || nogo "core/network.py not found"
(cd "$repo" && python3 core/network.py --table) > "$st/words-good.json" 2>"$tmp/words.err" \
  || nogo "core/network.py --table failed" "$(cat "$tmp/words.err")"
wrap_words() { { printf '<!doctype html><script type="application/json" id="network-words">'; cat "$1"; printf '</script>\n'; } > "$2"; }
wrap_words "$st/words-good.json" "$st/words-good.html"
must_accept "the console's network words" words "$st/words-good.html" "$repo"
# word NAME JQ: the good table with one rule broken; the checker must refuse it.
word() { jq -c "$2" "$st/words-good.json" > "$st/words-$1.json"; wrap_words "$st/words-$1.json" "$st/words-$1.html"; must_reject "the console's network words ($1)" words "$st/words-$1.html" "$repo"; }
printf '<!doctype html><p>no table</p>\n' > "$st/words-none.html"
must_reject "the console's network words (no table at all)" words "$st/words-none.html" "$repo"
printf '<script type="application/json" id="network-words">{not json</script>\n' > "$st/words-badjson.html"
must_reject "the console's network words (not JSON)" words "$st/words-badjson.html" "$repo"
printf '<script type="application/json" id="network-words">[]</script>\n' > "$st/words-array.html"
must_reject "the console's network words (not an object)" words "$st/words-array.html" "$repo"
word sentence-changed '.words[(.words | keys[0])] = "a sentence the CLI does not print"'
word sentence-dropped 'del(.words[(.words | keys[0])])'
word sentence-added '.words.invented = "a sentence of the page itself"'
word role-dropped '.roles |= .[1:]'
word role-added '.roles += ["orchestrator"]'
word roles-reordered '.roles |= reverse'
word gate-dropped '.gates |= .[0:-1]'
word gates-swapped '.gates |= ([.[1], .[0]] + .[2:])'
word gate-added '.gates += ["canary"]'
word roles-absent 'del(.roles)'
word key-added '. + {extra: 1}'

# The checkers the stub root's logbook is judged by (docs/adr/0059 §3): a map
# by hand, a logbook by hand, and each rule broken once.
mkdir -p "$st/adrmap"
python3 - "$st/adrmap" <<'PY'
import json, sqlite3, sys
d = sys.argv[1]
db = sqlite3.connect(d + "/map.sqlite")
db.executescript("""
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE node (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', UNIQUE (kind, name));
CREATE TABLE edge (src INTEGER NOT NULL, dst INTEGER NOT NULL, kind TEXT NOT NULL, PRIMARY KEY (src, dst, kind));
CREATE TABLE cite (src INTEGER NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, line INTEGER NOT NULL, ok INTEGER NOT NULL);
CREATE TABLE doc (node INTEGER PRIMARY KEY, status TEXT NOT NULL, text TEXT NOT NULL);
INSERT INTO meta VALUES ('schema', '2'), ('sha', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'), ('tickets', 'tickets'), ('exclude', '');
INSERT INTO node VALUES (1, 'adr', 'docs/adr/0001-first.md', 'First'), (2, 'adr', 'docs/adr/0002-second.md', 'Second'),
  (3, 'ticket', '0007', 'Seven'), (4, 'ticket', '0008', 'Eight');
INSERT INTO edge VALUES (1, 2, 'mentions'), (2, 3, 'mentions'), (4, 1, 'mentions'), (1, 3, 'perimeter');
INSERT INTO cite VALUES (1, 'path', 'app/a.py', 3, 1), (1, 'path', 'app/gone.py', 4, 0), (1, 'key', 'APP_PORT', 5, 1),
  (2, 'path', 'app/sub/b.py', 3, 1), (2, 'path', 'README.md', 4, 1);
INSERT INTO doc VALUES (1, 'accepted', 'text one'), (2, '', 'text two');
""")
db.commit()
tickets = [
    {"id": "0007", "perimeter": ["app/*"], "adrs": ["0001", "0002"]},
    {"id": "0008", "perimeter": ["README.md", "docs/x.md"], "adrs": ["0002"]},
    {"id": "0009", "perimeter": {"ok": False, "error": "no perimeter"}, "adrs": []},
    {"id": "0010", "perimeter": ["other/*"], "adrs": []},
]
book = {"tickets": tickets, "adrs": {"map_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "items": [
    {"number": "0001", "path": "docs/adr/0001-first.md", "title": "First", "status": "accepted", "text": "text one",
     "files": ["app/a.py"], "tickets": ["0007", "0008"], "adrs": ["0002"]},
    {"number": "0002", "path": "docs/adr/0002-second.md", "title": "Second", "status": "", "text": "text two",
     "files": ["README.md", "app/sub/b.py"], "tickets": ["0007", "0008"], "adrs": ["0001"]}]}}
json.dump(book, open(d + "/good.json", "w"))
none = {"tickets": [dict(t, adrs=[]) for t in tickets],
        "adrs": {"ok": False, "error": "no map at .crew/map.sqlite; build it with ./go map"}}
json.dump(none, open(d + "/none.json", "w"))
PY
am="$st/adrmap"
# bad VARIANT JQ: the hand-made logbook with one rule broken.
bad() { jq "$2" "$am/good.json" > "$am/bad-$1.json"; }
bad status '.adrs.items[0].status = "banana"'
bad title '.adrs.items[0].title = "Second"'
bad text '.adrs.items[0].text = "text on"'
bad path '.adrs.items[0].path = "docs/adr/0001-other.md"'
bad ghost '.adrs.items[0].files += ["app/gone.py"] | .adrs.items[0].files |= sort'
bad key '.adrs.items[0].files += ["APP_PORT"] | .adrs.items[0].files |= sort'
bad no-file '.adrs.items[1].files -= ["README.md"]'
bad unsorted '.adrs.items[1].files |= reverse'
bad self '.adrs.items[0].adrs = ["0001", "0002"]'
bad no-mention '.adrs.items[1].adrs = []'
bad no-governed '.adrs.items[0].tickets -= ["0007"]'
bad no-mentioned '.adrs.items[0].tickets -= ["0008"]'
bad stranger '.adrs.items[0].tickets += ["0010"]'
bad sha '.adrs.map_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"'
bad order '.adrs.items |= reverse'
bad dropped '.adrs.items |= .[0:1]'
bad extra-key '.adrs.items[0].size = 1'
bad extra-top '.adrs.error = "none"'
bad ticket '(.tickets[] | select(.id == "0007") | .adrs) = ["0001"]'
bad ticket-star '(.tickets[] | select(.id == "0007") | .adrs) = ["0002"]'
bad ticket-unread '(.tickets[] | select(.id == "0009") | .adrs) = ["0001"]'
bad ticket-glob '(.tickets[] | select(.id == "0010") | .adrs) = ["0002"]'
bad ticket-missing 'del(.tickets[0].adrs)'
bad unread '.adrs = {ok: false, error: "x"}'
must_accept "the ADRs of a logbook" adrs "$am/good.json" "$am/map.sqlite"
for v in status title text path ghost key no-file unsorted self no-mention no-governed no-mentioned stranger sha order dropped \
  extra-key extra-top ticket ticket-star ticket-unread ticket-glob ticket-missing unread; do
  must_reject "the ADRs of a logbook ($v)" adrs "$am/bad-$v.json" "$am/map.sqlite"
done
# With no map: {ok: false, error}, and no ticket has an ADR.
jq '.adrs.error = ""' "$am/none.json" > "$am/bad-none-silent.json"
jq '.tickets[0].adrs = ["0001"]' "$am/none.json" > "$am/bad-none-ticket.json"
jq '.adrs.ok = true' "$am/none.json" > "$am/bad-none-ok.json"
jq 'del(.adrs.error)' "$am/none.json" > "$am/bad-none-noerror.json"
must_accept "an unread map" unread "$am/none.json" "./go map"
must_reject "an unread map (the ADRs of a map that was read)" unread "$am/good.json"
for v in none-silent none-ticket none-ok none-noerror; do
  must_reject "an unread map ($v)" unread "$am/bad-$v.json"
done
must_reject "an unread map (an error that does not say ./go map)" unread "$am/none.json" "build the moon"
# The map made unreadable on purpose: the schema is changed on a copy.
cp "$am/map.sqlite" "$am/map-1.sqlite"
chk mapset "$am/map-1.sqlite" schema 1 >"$tmp/chk.out" || nogo "could not make a schema 1 copy of the hand-made map" "$(cat "$tmp/chk.out")"
[ "$(python3 -c 'import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute("SELECT value FROM meta WHERE key = ?", ("schema",)).fetchone()[0])' "$am/map-1.sqlite")" = 1 ] \
  || nogo "the schema 1 copy of the hand-made map still says another schema"

# A directory listing and a tree digest: the tools the runtime part judges
# "the root is unchanged" and "the next id" with, each shown to see a change.
tree_digest() { (cd "$1" && find . -path ./.git -prune -o -type f -exec sha256sum {} + | sort -k2 | sha256sum | cut -d' ' -f1); }
next_id() { find "$1" -maxdepth 1 \( -name '[0-9][0-9][0-9][0-9].md' -o -name '[0-9][0-9][0-9][0-9].intake.md' \) | awk -F/ '{ n = substr($NF, 1, 4) + 0; if (n > m) m = n } END { printf "%04d", m + 1 }'; }
mkdir -p "$st/digest/sub" "$st/ids"
printf 'a\n' > "$st/digest/a.txt"
d1=$(tree_digest "$st/digest")
printf 'b\n' > "$st/digest/sub/b.txt"
d2=$(tree_digest "$st/digest")
[ "$d1" != "$d2" ] || nogo "the tree digest does not see a new file"
printf 'c\n' >> "$st/digest/a.txt"
[ "$(tree_digest "$st/digest")" != "$d2" ] || nogo "the tree digest does not see a changed file"
[ "$(tree_digest "$st/digest")" = "$(tree_digest "$st/digest")" ] || nogo "the tree digest is not the same twice over the same tree"
[ "$(next_id "$st/ids")" = 0001 ] || nogo "the next id of an empty tickets directory is not 0001"
for f in 0001.md 0002.md 0007.md README.md notes.txt 0099.txt 12.md 00100.md; do : > "$st/ids/$f"; done
[ "$(next_id "$st/ids")" = 0008 ] || nogo "the next id after 0007 is $(next_id "$st/ids"), want 0008"

# gate_line ROOT FILE: the one line gates/spec.sh prints on FILE, run in ROOT.
# It must tell a good spec from a bad one, or every comparison below is empty.
gate_line() { (cd "$1" && sh gates/spec.sh "$2" 2>/dev/null) || :; }
cat > "$st/spec-good.md" <<'MD'
# 0099 — A request

## Acceptance criteria

Given a meetup at capacity
When a new member signs up
Then they join the waiting list and see their position

## Perimeter

- `app/backend/waitlist/*`
MD
sed 's|^Then .*|Then it works|' "$st/spec-good.md" > "$st/spec-bad.md"
[ "$(gate_line "$repo" "$st/spec-good.md")" = "GO: spec" ] \
  || nogo "gates/spec.sh does not say GO: spec on a good spec: '$(gate_line "$repo" "$st/spec-good.md")'"
case "$(gate_line "$repo" "$st/spec-bad.md")" in
  "NO-GO: spec — "?*) : ;;
  *) nogo "gates/spec.sh does not say NO-GO on a vague spec: '$(gate_line "$repo" "$st/spec-bad.md")'" ;;
esac


# provenance.
cat > "$st/prov-good.env" <<'ENV'
# A note.
# Provenance, decision: a choice
#   over two lines.
A=1
B=text
ENV
printf 'A=1\n' > "$st/prov-none.env"
printf '# Provenance, decision: x\n\nA=1\n' > "$st/prov-blank.env"
printf '# Provenance: x\nA=1\n' > "$st/prov-form.env"
printf '# Provenance, guess: x\nA=1\n' > "$st/prov-kind.env"
printf '# only a comment\nA=0.25\n' > "$st/prov-plain.env"
printf '# only a comment\nA=1\nMUTATION_BASELINE=0.5\n' > "$st/prov-baseline.env"
must_accept "provenance" prov all "$st/prov-good.env"
for v in none blank form kind plain; do
  must_reject "provenance ($v)" prov all "$st/prov-$v.env"
done
must_reject "provenance (a baseline without it)" prov baseline "$st/prov-baseline.env"
printf '# only a comment\nA=1\n' > "$st/prov-other-key.env"
must_accept "provenance in baseline mode ignores other keys" prov baseline "$st/prov-other-key.env"

# --- the stub root ----------------------------------------------------------
# stub_tree DIR: the tracked files of the stub root besides its tickets
# (docs/adr/0059 §3): three ADRs, the files they cite, and a README. The ADRs
# are written here and not kept as fixtures, so that this repository's own
# map and ADR search do not hold them. What the rules must find in them:
#   0001 accepted; cites two files that exist and one that does not; names
#        ADR 0002, ADR 0999 (held nowhere) and ticket 0007
#   0002 two Status lines (the first is the status, stripped); cites three
#        files, the notifier among them; names Ticket 0002, Tickets 0004
#        (and a bare 0008, which no pattern reads) and ADR 0001
#   0003 no Status line; cites the README by a link; names itself, ADR 0001
#        and ticket 0006, which names it back
stub_tree() {
  mkdir -p "$1/docs/adr" "$1/app/backend/meetups" "$1/app/backend/waitlist" "$1/app/backend/migrations" \
    "$1/app/backend/notify" "$1/tests"
  printf '# Stub\n' > "$1/README.md"
  printf '# Venues\n' > "$1/docs/venues.md"
  printf 'def venue():\n    return 404\n' > "$1/app/backend/meetups/views.py"
  printf 'def queue():\n    return []\n' > "$1/app/backend/waitlist/queue.py"
  printf 'def route():\n    return None\n' > "$1/app/backend/router.py"
  printf 'x = 1\n' > "$1/app/backend/migrations/0001_init.py"
  # Ticket 0010's notifier and its test (docs/adr/0071 §8): the test imports
  # the notifier, so the project map records a `tests` edge into it, and
  # ADR 0002 cites the notifier, so a ticket whose perimeter holds it is
  # governed by ADR 0002.
  : > "$1/app/backend/__init__.py"
  : > "$1/app/backend/notify/__init__.py"
  printf 'def notify(queue, send):\n    return send(queue[0]) if queue else None\n' > "$1/app/backend/notify/send.py"
  printf 'from app.backend.notify.send import notify\n\n\ndef test_the_first_waiting_is_told():\n    told = []\n    notify(["ada"], told.append)\n    assert told == ["ada"]\n' > "$1/tests/test_notify.py"
  cat > "$1/docs/adr/0001-venues-are-checked.md" <<'ADR'
# ADR 0001 — Venues are checked at the edge

- Status: accepted
- Date: 2026-11-01

An unknown venue answers 404, not 500. The check is in
`app/backend/meetups/views.py` and the page `docs/venues.md` says so;
`app/backend/ghost.py` was removed. It sits beside ADR 0002, ADR 0999 is
reserved and held nowhere, and ticket 0007 asked for the cache.
ADR
  # Written with printf: its first Status line ends in two spaces.
  # shellcheck disable=SC2016 # the backticks are the ADR's own
  printf '%s\n' '# ADR 0002 — The waitlist is first in, first out' '' \
    '- Status:   proposed — supersedes nothing  ' '- Status: accepted' '' \
    'The queue is `app/backend/waitlist/queue.py`, routed by' \
    '`app/backend/router.py`. Ticket 0002 builds it and tickets 0004 and 0008' \
    'touch the router. It follows ADR 0001. The waitlist is told by' \
    '`app/backend/notify/send.py`.' > "$1/docs/adr/0002-waitlist-order.md"
  cat > "$1/docs/adr/0003-keep-the-readme-short.md" <<'ADR'
# ADR 0003 — Keep the README short

The [README](../../README.md) names the demo command and nothing more.
This is ADR 0003 and it records no status. It builds on ADR 0001 and was
asked for by ticket 0006, which never ran.
ADR
}

# The hash of that one commit, which the golden's source.commit and adrs.map_sha
# name; it changes with any byte of stub_tree, of the tickets or of the commit.
stub_commit=84053a90946597e4bed5eefc189c23465ee97392

# mkroot DIR: core/, ci/, gates/ symlinked from this repository, a copy of
# console/, stub policy files, a stub contract, tickets, tower documents (one
# file per <collection>.<id>), a release log, a record, a post mortem, a brief,
# a stub StateStore and a stub Delivery, a git repository whose one commit is
# the same in every run and holds the tickets and stub_tree's files, and the
# project map core/map.py builds from that commit, in .crew/map.sqlite.
mkroot() {
  r=$1
  mkdir -p "$r/adapters/stub-state" "$r/adapters/stub-delivery" "$r/world" "$r/.crew"
  # The six roles (docs/adr/0086 §1), the release role's harness the stub's own,
  # and the dev-worker's attempt in flight on 0003 (docs/adr/0087 §4).
  cp -R "$stub/roles" "$r/roles"
  cp -R "$stub/crew/." "$r/.crew/"
  printf 'THINKING=low\nTIMEOUT_S=900\nHARNESS=stub-harness\n' > "$r/roles/release/role.env"
  ln -s "$repo/core" "$r/core"
  ln -s "$repo/ci" "$r/ci"
  ln -s "$repo/gates" "$r/gates"
  [ ! -d "$repo/console" ] || cp -R "$repo/console" "$r/console"
  cp -R "$stub/policy" "$stub/contract" "$stub/tickets" "$stub/state" "$stub/postmortem" "$stub/promote" "$r/"
  cp "$stub/releases.jsonl" "$stub/release.json" "$r/"
  stub_tree "$r"
  # Ticket 0006 never ran, so it is no card; it names an ADR that names it.
  printf '\nGoverned by ADR 0003; the router is ticket 0008.\n' >> "$r/tickets/0006.md"
  cat > "$r/adapters/stub-state/get.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
f="$d/state/$1.$2.json"
[ -f "$f" ] || exit 1
cat "$f"
STUB
  cat > "$r/adapters/stub-delivery/status.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cat "$d/world/status.json"
STUB
  cat > "$r/adapters/stub-delivery/shift.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
printf 'shift %s\n' "$1" >> "$d/effects.log"
STUB
  cat > "$r/adapters/stub-delivery/rollback.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
printf 'rollback\n' >> "$d/effects.log"
STUB
  # `make answer`, as the console runs it: records what it was given.
  # shellcheck disable=SC2016 # the stub Makefile expands its own variables
  printf 'answer:\n\t@printf '"'"'answer %%s %%s %%s %%s\\n'"'"' "$(TICKET)" "$$ANSWER" "$$ANSWER_BY" "$$ANSWER_VIA" >> effects.log\n\t@echo "GO: answer"\n' > "$r/Makefile"
  # `make request`, as the console runs it after writing the ticket (docs/adr/0059 §4).
  # shellcheck disable=SC2016 # the stub Makefile expands its own variables
  printf 'request:\n\t@printf '"'"'request %%s %%s %%s\\n'"'"' "$(TICKET)" "$$REQUEST_BY" "$$REQUEST_VIA" >> effects.log\n\t@echo "GO: request"\n' >> "$r/Makefile"
  chmod +x "$r"/adapters/stub-state/get.sh "$r"/adapters/stub-delivery/*.sh
  printf '{"stable":"0.3.0-b","canary":"0.5.0-d","weight":25}\n' > "$r/world/status.json"
  : > "$r/effects.log"
  # One commit with fixed names and dates and the same files: the same hash in
  # every run (the golden names it). Only the tickets and stub_tree's files
  # are added; the symlinks and the rest of the root are not in it.
  git -C "$r" init -q . >/dev/null 2>&1
  git -C "$r" add README.md docs app tests tickets >/dev/null 2>&1
  GIT_AUTHOR_NAME=stub GIT_AUTHOR_EMAIL=stub@example.invalid GIT_COMMITTER_NAME=stub \
    GIT_COMMITTER_EMAIL=stub@example.invalid GIT_AUTHOR_DATE="1793491200 +0000" \
    GIT_COMMITTER_DATE="1793491200 +0000" \
    git -C "$r" -c commit.gpgsign=false commit -q -m stub >/dev/null 2>&1
  [ "$(git -C "$r" rev-parse HEAD 2>/dev/null)" = "$stub_commit" ] \
    || nogo "the stub root's commit is not the one the golden names; this git writes another commit object, or the stub files changed" \
      "the stub root's commit is $(git -C "$r" rev-parse HEAD 2>&1)"
  # The map of that commit (docs/adr/0054, schema 2), where the reader looks by default.
  env -u GIT_DIR -u GIT_WORK_TREE python3 "$repo/core/map.py" build --repo "$r" --rev HEAD --tickets tickets \
    --targets "$tmp/targets.txt" --out "$r/.crew/map.sqlite" >"$r/map.out" 2>"$r/map.err" \
    || nogo "core/map.py could not build the map of the stub root" "$(cat "$r/map.err")"
}

# run_reader [VAR=val...]: core/logbook.sh in $root, the stub sources by
# relative path; sets $out (stdout) and $rc, stderr in $root/reader.err.
run_reader() {
  out=$(cd "$root" && env -u GIT_DIR -u GIT_WORK_TREE LEVEL=l0 WORKLOAD=app CODE_HOST=gitea \
    STATE_STORE=stub-state DELIVERY=stub-delivery TICKETS_DIR=tickets RELEASE_RECORD=release.json \
    RELEASES_LOG=releases.jsonl PROMOTE_OUT=promote POSTMORTEM_DIR=postmortem WORKLOAD_CONTRACT=contract \
    MODEL_PROVIDER=gemini MODEL=gemini-3.8-flash AGENT_HARNESS=gemini-cli ROLES_DIR= CREW_DIR= \
    WINDOW_DAYS= "$@" sh core/logbook.sh 2>reader.err </dev/null) && rc=0 || rc=$?
}

[ -f "$repo/core/logbook.sh" ] || nogo "core/logbook.sh not found"
[ -f "$repo/core/map.py" ] || nogo "core/map.py not found"
printf 'demo\n' > "$tmp/targets.txt"  # the ./go targets the map's command citations are judged by
root="$tmp/root"
mkroot "$root"

# --- a. the reader, twice, against the golden --------------------------------
run_reader LOGBOOK_FILE=out1.json
[ "$out" = "GO: logbook — out1.json, 9 tickets" ] \
  || nogo "core/logbook.sh printed '$out' (exit $rc), want 'GO: logbook — out1.json, 9 tickets'" "$(cat "$root/reader.err")"
run_reader LOGBOOK_FILE=out2.json
[ "$out" = "GO: logbook — out2.json, 9 tickets" ] \
  || nogo "the second run printed '$out' (exit $rc), want 'GO: logbook — out2.json, 9 tickets'" "$(cat "$root/reader.err")"
cmp -s "$root/out1.json" "$root/out2.json" \
  || nogo "two runs over the same sources wrote different bytes (ADR 0047 §2: no clock)" "$(diff "$root/out1.json" "$root/out2.json" | head -20)"
cmp -s "$root/out1.json" "$golden" \
  || nogo "the logbook over the stub sources is not fixtures/logbook/golden-1.8.json" "$(diff "$golden" "$root/out1.json" | head -40)"

# The ADRs (docs/adr/0059 §3). The golden is only the bytes the reader wrote
# the day it was blessed; these are the rules, worked out again from the
# stub map's rows, and then the stub's facts written down by hand.
chk adrs "$root/out1.json" "$root/.crew/map.sqlite" >"$tmp/chk.out" \
  || nogo "the logbook's adrs or a ticket's adrs break the rules over the stub map" "$(cat "$tmp/chk.out")"
jq -e --arg sha "$stub_commit" '
  .adrs.map_sha == $sha and .source.commit == $sha
  and (.adrs.items | map(.number) == ["0001", "0002", "0003"])
  and (.adrs.items | map({(.number): .status}) | add) == {"0001": "accepted", "0002": "proposed — supersedes nothing", "0003": ""}
  and (.adrs.items | map({(.number): .path}) | add) == {"0001": "docs/adr/0001-venues-are-checked.md",
       "0002": "docs/adr/0002-waitlist-order.md", "0003": "docs/adr/0003-keep-the-readme-short.md"}
  and (.adrs.items | map({(.number): .title}) | add) == {"0001": "ADR 0001 — Venues are checked at the edge",
       "0002": "ADR 0002 — The waitlist is first in, first out", "0003": "ADR 0003 — Keep the README short"}
  and (.adrs.items | map({(.number): .files}) | add) == {"0001": ["app/backend/meetups/views.py", "docs/venues.md"],
       "0002": ["app/backend/notify/send.py", "app/backend/router.py", "app/backend/waitlist/queue.py"],
       "0003": ["README.md"]}
  and (.adrs.items | map({(.number): .adrs}) | add) == {"0001": ["0002", "0003"], "0002": ["0001"], "0003": ["0001"]}
  and (.adrs.items | map({(.number): .tickets}) | add) == {"0001": ["0001", "0007"], "0002": ["0002", "0004", "0008", "0010"], "0003": ["0003", "0006"]}
  and (.tickets | map({(.id): .adrs}) | add) == {"0001": ["0001"], "0002": ["0002"], "0003": ["0003"], "0004": [],
       "0005": [], "0007": [], "0008": ["0002"], "0009": [], "0010": ["0002"]}' "$root/out1.json" >/dev/null \
  || nogo "the stub map's ADRs are not what the stub files give" \
    "$(jq -c '{adrs: (.adrs | if has("items") then {map_sha, items: [.items[] | del(.text)]} else . end), tickets: [.tickets[] | {id, adrs}]}' "$root/out1.json")"
for f in "$root"/docs/adr/*.md; do
  n=$(basename "$f" | cut -c1-4)
  jq -j --arg n "$n" '.adrs.items[] | select(.number == $n) | .text' "$root/out1.json" | cmp -s - "$f" \
    || nogo "ADR $n's text in the logbook is not the file's, byte for byte"
done

# The 1.3 fields (docs/adr/0071 §1, §8), worked out from the stub's tower
# documents (state/tickets.0010.json, state/tickets.0004.json), the Makefile's
# GATES line, the project map and ADR 0002 above, and written down by hand; the
# reader's code is not read. Ticket 0010 is the network ticket: attempt 1 has
# two members, `impl` (a model ran) and `tests` (hand-written, no model), the
# mutation gate said NO-GO; attempt 2 has the same two members, the eight gates
# said GO. Its perimeter is app/backend/notify/* and tests/test_notify.py; the
# map knows send.py (cited by ADR 0002, covered by test_notify.py, which
# imports it) and test_notify.py (which no test covers). Ticket 0004's one
# attempt is still in flight and its gates never ran: its patch is withheld.
gate_order=$(awk '/^GATES := / { sub(/^GATES := /, ""); n = split($0, a, " "); printf "["; for (i = 1; i <= n; i++) printf "%s\"%s\"", (i > 1 ? "," : ""), a[i]; printf "]"; exit }' "$repo/Makefile")
[ -n "$gate_order" ] || nogo "the Makefile has no GATES := line"
[ "$(printf '%s' "$gate_order" | jq 'length')" = 8 ] || nogo "the Makefile's GATES line holds $(printf '%s' "$gate_order" | jq 'length') gates, ADR 0071 draws eight: $gate_order"
# h13 WHAT JQ_EXPR FILE: one hand check; WHAT names it when it fails.
h13() {
  jq -e --argjson order "$gate_order" --slurpfile s10 "$stub/state/tickets.0010.json" \
    --slurpfile s4 "$stub/state/tickets.0004.json" --slurpfile old "$repo/fixtures/logbook/golden-1.2.json" \
    "$2" "$3" >/dev/null 2>&1 || { printf '%s\n' "$1"; return 1; }
}
t10='(.tickets[] | select(.id == "0010"))'
t04='(.tickets[] | select(.id == "0004"))'
hand_13() {
  # shellcheck disable=SC2016 # $old is jq's own variable
  # The one top-level key 1.3 adds is ADR 0073's `agents` (§10); ADR 0071 and ADR 0078 add none;
  # 1.5 adds `dora` and `spend` (docs/adr/0082, 0083, amendments of 2026-11-13).
  h13 "nothing new at the top level but agents, dora, spend, roles, roles_summary and intakes, and the schema is 1.8" \
    '.schema == "lights-out/logbook/1.8" and (keys == ($old[0] | keys + ["agents", "dora", "spend", "roles", "roles_summary", "intakes"] | sort))' "$1" || return 1
  h13 "0010's two attempts, as the tower recorded them" \
    "$t10 | (.tasks | map(.attempt)) == [1, 2]" "$1" || return 1
  h13 "0010 attempt 1: members impl and tests, in that order, with their harness, model and perimeter" \
    "$t10 | .tasks[0].members | map(.id) == [\"impl\", \"tests\"]
      and (map({(.id): {harness, replayed, model}}) | add) == {impl: {harness: \"replay\", replayed: true, model: \"gemini-3.8-flash\"},
                                                              tests: {harness: \"replay\", replayed: true, model: null}}
      and (map({(.id): .perimeter}) | add) == {impl: [\"app/backend/notify/*\"], tests: [\"tests/test_notify.py\"]}" "$1" || return 1
  h13 "0010 attempt 1: impl ran a model (hand_written null), tests was written by hand (hand_written true)" \
    "$t10 | .tasks[0].members | (map({(.id): .hand_written}) | add) == {impl: null, tests: true}" "$1" || return 1
  h13 "0010 attempt 1: impl's cost, 3 turns, 42,110 tokens, 48 s; the hand-written member's null" \
    "$t10 | .tasks[0].members | (.[0] | .turns == 3 and .tokens.total == 42110 and .duration_s == 48 and .thinking == \"high\")
      and (.[1] | .turns == null and .tokens == null and .duration_s == null and .provider == null)" "$1" || return 1
  h13 "0010 attempt 2: the same two members, impl's cost 2 turns, 40,220 tokens" \
    "$t10 | .tasks[1].members | map(.id) == [\"impl\", \"tests\"] and (.[0] | .turns == 2 and .tokens.total == 40220)
      and (map({(.id): .hand_written}) | add) == {impl: null, tests: true}" "$1" || return 1
  h13 "0010's members are the tower's own, passed through" \
    "$t10 | (.tasks | map(.members)) == (\$s10[0].tasks | map(.members))" "$1" || return 1
  h13 "0010 attempt 1 touched send.py (+6 -0, impl) and test_notify.py (+8 -0, tests)" \
    "$t10 | .tasks[0].files == [{path: \"app/backend/notify/send.py\", added: 6, removed: 0, member: \"impl\"},
                              {path: \"tests/test_notify.py\", added: 8, removed: 0, member: \"tests\"}]" "$1" || return 1
  h13 "0010 attempt 2 touched send.py only (+2 -1, impl)" \
    "$t10 | .tasks[1].files == [{path: \"app/backend/notify/send.py\", added: 2, removed: 1, member: \"impl\"}]" "$1" || return 1
  h13 "0010's patches are shown whole (1129 and 577 bytes, not truncated), the tower's own text" \
    "$t10 | (.tasks | map(.patch.bytes)) == [1129, 577] and (.tasks | map(.patch.truncated)) == [false, false]
      and (.tasks | map(.patch)) == (\$s10[0].tasks | map(.patch))
      and (.tasks[0].patch.text | contains(\"+++ b/tests/test_notify.py\")) and (.tasks[1].patch.text | startswith(\"From c3c3\"))" "$1" || return 1
  h13 "0010's ci.gates: the eight gates in the Makefile's order, all GO, each line verbatim" \
    "$t10 | .ci.gates | map(.gate) == \$order and all(.verdict == \"GO\")
      and (map(.line) == \$s10[0].ci.lines)
      and .[0] == {gate: \"risk\", verdict: \"GO\", line: \"GO: risk — low, review\"}
      and .[1] == {gate: \"secrets\", verdict: \"GO\", line: \"GO: secrets\"}
      and .[6] == {gate: \"mutation\", verdict: \"GO\", line: \"GO: mutation — score 92% against a baseline of 80%\"}
      and .[7].gate == \"test-proof\"" "$1" || return 1
  h13 "0010's rework[0].gates: risk to test GO, then mutation NO-GO, each line verbatim" \
    "$t10 | .rework[0].gates | map(.gate) == \$order[0:7]
      and (map(.verdict) == [\"GO\", \"GO\", \"GO\", \"GO\", \"GO\", \"GO\", \"NO-GO\"])
      and (map(.line) == \$s10[0].rework[0].lines)
      and .[6].line == \"NO-GO: mutation — 3 of 12 mutants survived, score 75% below the baseline of 80%\"" "$1" || return 1
  h13 "the raw lines are not exposed, on ci nor on a rework entry" \
    "[.tickets[] | (.ci | select(type == \"object\")), (.rework | select(type == \"array\") | .[])] | all(has(\"gates\") and (has(\"lines\") | not))" "$1" || return 1
  # Ticket 0003 is the other one whose stub records gate lines (ADR 0078 §5): hand_14 reads it.
  h13 "a ticket whose gates recorded no lines has gates null, ci and rework alike" \
    "[.tickets[] | select(.id != \"0010\" and .id != \"0003\") | (.ci | select(type == \"object\")), (.rework | select(type == \"array\") | .[])] | all(.gates == null)" "$1" || return 1
  h13 "0004's attempt in flight: the patch is withheld with the contract's error, its files and null members stay" \
    "$t04 | .tasks[0].patch == {ok: false, error: \"withheld: the secrets gate did not say GO on attempt 1\"}
      and .tasks[0].files == \$s4[0].tasks[0].files and .tasks[0].members == null" "$1" || return 1
  h13 "0010's file rows: glob, member of the latest attempt, attempts, tests, ADRs, holders" \
    "$t10 | .files | map(del(.tests)) == [
      {path: \"app/backend/notify/send.py\", glob: \"app/backend/notify/*\", member: \"impl\", attempts: [1, 2],
       adrs: [\"0002\"], held_by: []},
      {path: \"tests/test_notify.py\", glob: \"tests/test_notify.py\", member: \"tests\", attempts: [1],
       adrs: [], held_by: []}]
      and .[0].tests == [\"tests/test_notify.py\"]" "$1" || return 1
  # The map holds test_notify.py as a test, not as a file; whether "knows the
  # file" covers it is not fixed by the ADR, so [] and null are both read (a
  # test no one covers, or a path the map has no file for), and nothing else.
  h13 "0010's test_notify.py row: no test covers it, or the map holds no file for it" \
    "$t10 | .files[1].tests | . == [] or . == null" "$1" || return 1
  h13 "0004's file row: the map does not know the file (tests null), no ADR cites it, nobody holds it" \
    "$t04 | .files == [{path: \"app/backend/migrations/0002_rename.py\", glob: \"app/backend/migrations/*\", member: null,
      attempts: [1], tests: null, adrs: [], held_by: []}]" "$1" || return 1
  h13 "a ticket with no task carrying files has files []" \
    "[.tickets[] | select(.id != \"0010\" and .id != \"0004\") | .files] | all(. == [])" "$1" || return 1
  h13 "a task with no members, patch or files says null for each, 0004's apart" \
    "[.tickets[] | select(.id != \"0010\" and .id != \"0004\") | .tasks | select(type == \"array\") | .[]]
      | all(.members == null and .patch == null and .files == null)" "$1" || return 1
}
bad=$(hand_13 "$root/out1.json") || nogo "the 1.3 fields over the stub sources: $bad" \
  "$(jq -c "$t10 | {tasks: [.tasks[] | {attempt, members: (.members | if type == \"array\" then map(.id) else . end), files, patch: (.patch | if type == \"object\" then del(.text) else . end)}], ci_gates: (.ci.gates | if type == \"array\" then map(.gate + \":\" + .verdict) else . end), files}" "$root/out1.json" 2>&1)"
# The hand checks can fail: one fact planted wrong in a copy of the logbook,
# each refused by the check that owns it.
plant() {
  jq "$2" "$root/out1.json" > "$tmp/plant.json"
  if hand_13 "$tmp/plant.json" >/dev/null; then
    nogo "the hand checks of the 1.3 fields accept a logbook with $1; a gate that cannot fail is not a gate"
  fi
}
tix() { printf '.tickets |= map(if .id == "%s" then %s else . end)' "$1" "$2"; }
plant "a member's hand_written flipped" "$(tix 0010 '.tasks[0].members[1].hand_written = null')"
plant "a member's model changed" "$(tix 0010 '.tasks[1].members[0].model = "other"')"
plant "a member dropped" "$(tix 0010 '.tasks[0].members |= .[0:1]')"
plant "a file's added count changed" "$(tix 0010 '.tasks[0].files[0].added = 7')"
plant "a file's member changed" "$(tix 0010 '.tasks[0].files[1].member = "impl"')"
plant "a patch cut" "$(tix 0010 '.tasks[1].patch.truncated = true')"
plant "a patch withheld that should show" "$(tix 0010 '.tasks[1].patch = {ok: false, error: "withheld: x"}')"
plant "0004's patch shown" "$(tix 0004 '.tasks[0].patch = {text: "x", bytes: 1, truncated: false}')"
plant "0004's withheld reason changed" "$(tix 0004 '.tasks[0].patch.error = "withheld"')"
plant "ci.gates reversed" "$(tix 0010 '.ci.gates |= reverse')"
plant "a ci gate's verdict changed" "$(tix 0010 '.ci.gates[6].verdict = "NO-GO"')"
plant "a ci gate's line changed" "$(tix 0010 '.ci.gates[0].line = "GO: risk"')"
plant "the raw ci lines exposed" "$(tix 0010 '.ci.lines = []')"
plant "a rework entry's NO-GO turned GO" "$(tix 0010 '.rework[0].gates[6].verdict = "GO"')"
plant "a rework entry's gates null" "$(tix 0010 '.rework[0].gates = null')"
plant "the raw rework lines exposed" "$(tix 0010 '.rework[0].lines = []')"
plant "a file row's tests null" "$(tix 0010 '.files[0].tests = null')"
plant "a file row's tests empty" "$(tix 0010 '.files[0].tests = []')"
plant "a file row's attempts changed" "$(tix 0010 '.files[0].attempts = [2]')"
plant "a file row's glob changed" "$(tix 0010 '.files[1].glob = "tests/*"')"
plant "a file row's ADRs empty" "$(tix 0010 '.files[0].adrs = []')"
plant "a file row's holders named" "$(tix 0010 '.files[0].held_by = ["0008"]')"
plant "a file row dropped" "$(tix 0010 '.files |= .[0:1]')"
plant "0004's tests known" "$(tix 0004 '.files[0].tests = []')"
plant "a field new at the top level" '. + {network: {}}'
plant "another schema" '.schema = "lights-out/logbook/1.2"'

# The 1.4 fields (docs/adr/0078 §2, §3, §5), worked out from the stub's tower
# documents (state/tickets.0003.json, 0002, 0010) and written down by hand; the
# reader's code is not read. Ticket 0003 is at attempt 2 (PR 3): attempt 1 was
# sent back by a sast NO-GO (secrets GO, two new findings), the current ci is
# a test NO-GO (secrets GO, two FAILED lines). Ticket 0002's one rework entry
# was recorded with no gate lines, so its details are withheld. Ticket 0010's
# rework entry (the mutation NO-GO) ran secrets GO, so it is shown.
h14() {
  jq -e --slurpfile s3 "$stub/state/tickets.0003.json" --slurpfile s2 "$stub/state/tickets.0002.json" \
    --slurpfile s10 "$stub/state/tickets.0010.json" --slurpfile rel "$stub/releases.jsonl" --slurpfile cur "$stub/release.json" "$2" "$3" >/dev/null 2>&1 || { printf '%s\n' "$1"; return 1; }
}
t03='(.tickets[] | select(.id == "0003"))'
t02='(.tickets[] | select(.id == "0002"))'
hand_14() {
  h14 "the schema is 1.8 (1.4's fields are kept)" '.schema == "lights-out/logbook/1.8"' "$1" || return 1
  h14 "0003 is at attempt 2, PR 3, with two ci runs and one rework in its history" \
    "$t03 | .attempt == 2 and .pr.number == 3 and ([.history[] | select(.step == \"ci\")] | length) == 2
      and ([.history[] | select(.step == \"rework\")] | length) == 1" "$1" || return 1
  h14 "0003's ci.why: attempt 2, gates, the test gate, the line verbatim, the two FAILED lines, sast null" \
    "$t03 | .ci.why == {attempt: 2, judge: \"gates\", gate: \"test\", line: \"NO-GO: gates — test: NO-GO: test — 2 failed\",
        details: [\"FAILED tests/a.py::one\", \"FAILED tests/a.py::two\"], sast: null}
      and .ci.why.line == \$s3[0].ci.line and .ci.details == \$s3[0].ci.details" "$1" || return 1
  h14 "0003's rework[0].why: attempt 1, gates, the sast gate, details shown, the two findings {rule, file, line}" \
    "$t03 | .rework | length == 1 and (.[0].why == {attempt: 1, judge: \"gates\", gate: \"sast\", line: \$s3[0].rework[0].line,
        details: [\"new: B602 README.md:7\", \"new: B105 README.md:9\"],
        sast: [{rule: \"B602\", file: \"README.md\", line: 7}, {rule: \"B105\", file: \"README.md\", line: 9}]})
      and .[0].details == [\"new: B602 README.md:7\", \"new: B105 README.md:9\"]" "$1" || return 1
  h14 "0003's ci.gates: risk, secrets, test-weakening, deps, sast GO, then test NO-GO; rework[0].gates ends on the sast NO-GO, each line verbatim" \
    "$t03 | (.ci.gates | map(.gate + \":\" + .verdict)) == [\"risk:GO\", \"secrets:GO\", \"test-weakening:GO\", \"deps:GO\", \"sast:GO\", \"test:NO-GO\"]
      and (.ci.gates | map(.line)) == \$s3[0].ci.lines
      and (.rework[0].gates | map(.gate + \":\" + .verdict)) == [\"risk:GO\", \"secrets:GO\", \"test-weakening:GO\", \"deps:GO\", \"sast:NO-GO\"]
      and (.rework[0].gates | map(.line)) == \$s3[0].rework[0].lines" "$1" || return 1
  h14 "0003's timeline: gates rows for attempts 1 and 2, a rework row for attempt 2, and one gate row per shown detail" \
    "$t03 | ([.timeline[] | select(.phase == \"gates\") | .attempt]) == [1, 2]
      and ([.timeline[] | select(.phase == \"rework\") | .attempt]) == [2]
      and ([.timeline[] | select(.phase == \"gate\") | .line]) == [\"FAILED tests/a.py::one\", \"FAILED tests/a.py::two\"]" "$1" || return 1
  h14 "0002's rework[0].why is withheld with the exact error for attempt 1, its 1.3 details are null, and the text is nowhere in the entry" \
    "$t02 | .rework | length == 1 and (.[0].why == {attempt: 1, judge: \"gates\", gate: null, line: \$s2[0].rework[0].line,
        details: {ok: false, error: \"withheld: the secrets gate did not say GO on attempt 1\"}, sast: null})
      and .[0].details == null and (.[0] | tojson | contains(\"test_join\") | not)" "$1" || return 1
  h14 "0010's rework[0].why is shown: the mutation gate, the survivor, sast null" \
    "$t10 | .rework[0].why == {attempt: 1, judge: \"gates\", gate: \"mutation\", line: \$s10[0].rework[0].line,
        details: \$s10[0].rework[0].details, sast: null} and .rework[0].details == \$s10[0].rework[0].details" "$1" || return 1
  h14 "every ci that said GO has a ci.why that is null (the key is there), and a NO-GO has one that is not" \
    "[.tickets[] | .ci | select(type == \"object\")] | length > 0
      and all(if .verdict == \"GO\" then (has(\"why\") and .why == null) else (.why | type) == \"object\" end)" "$1" || return 1
  h14 "every rework entry of every ticket has a why of its own attempt, judge and line" \
    "[.tickets[] | (.rework | select(type == \"array\") | .[])] | length > 0 and all(.why.attempt == .attempt and .why.judge == .judge and .why.line == .line)" "$1" || return 1
  # docs/adr/0079 §1: an answer carries reason, brief_sha256 and brief_author
  # beside by and via. Carol's 0.4.0-c NO-GO is the one answer line of the stub
  # that holds them, and the check reads its three values from that line; the
  # other answers say none, as null, the keys there.
  h14 "every answer of releases[] has the keys reason, brief_sha256 and brief_author" \
    "[.releases[] | .answer | select(. != null)] | length == 3 and all(has(\"reason\") and has(\"brief_sha256\") and has(\"brief_author\"))" "$1" || return 1
  h14 "carol's 0.4.0-c NO-GO carries the reason, brief_sha256 and brief_author of the stub's answer line, by and via with them" \
    "([\$rel[] | select(.kind == \"answer\" and .version == \"0.4.0-c\")][0]) as \$l
      | (\$l.reason == \"the error budget is spent for the week\" and (\$l.brief_sha256 | type) == \"string\"
        and (.releases[] | select(.version == \"0.4.0-c\") | .answer)
          == {answer: \"NO-GO\", at: \$l.at, by: \"carol\", via: \"cli\", reason: \"the error budget is spent for the week\",
              brief_sha256: \$l.brief_sha256, brief_author: \"the release role, stub-harness\"})" "$1" || return 1
  h14 "the answers that say no reason, no brief sha and no author say null for each" \
    "[.releases[] | select(.version == \"0.2.5-n\" or .version == \"0.3.0-b\") | .answer]
      | length == 2 and all(.reason == null and .brief_sha256 == null and .brief_author == null)" "$1" || return 1
  h14 "no release without an answer has one made up" \
    "[.releases[] | select(.version == \"0.2.0-a\" or .version == \"0.5.0-d\") | .answer] == [null, null]" "$1" || return 1
  h14 "each sign-off row's reason is its release's answer's reason (a key, null here)" \
    ". as \$b | [.tickets[] | . as \$t | .timeline[] | select(.phase == \"sign-off\")
      | has(\"reason\") and .reason == (\$b.releases[] | select(.version == \$t.release.version) | .answer.reason)]
      | length == 2 and all" "$1" || return 1
  h14 "a ticket's sign-off rows are exactly the two the stub's answers give it" \
    "[.tickets[] | select(any(.timeline[]; .phase == \"sign-off\")) | .id] == [\"0002\", \"0010\"]" "$1" || return 1
  # docs/adr/0080 §1, §4: stage times and numbers as structures, a rolled-back
  # release's reason, the canary rows' judged_at and slo. The values are the stub's
  # first log line (0.2.0-a, rolled back at 5%) and release.json (0.5.0-d, held),
  # written out by hand here; the stages of the older log lines have none.
  h14 "0.2.0-a's reason is the stub's, and its stage's started_at, judged_at and slo are the stub's, by hand" \
    "(\$rel[0].record) as \$r | (.releases[] | select(.version == \"0.2.0-a\"))
      | (.reason == \"5%: error_rate 1.000\" and .reason == \$r.reason
        and .stages[0].started_at == \"2026-11-01T09:27:30Z\" and .stages[0].judged_at == \"2026-11-01T09:28:31Z\"
        and .stages[0].started_at == \$r.stages[0].started_at and .stages[0].judged_at == \$r.stages[0].judged_at
        and .stages[0].slo == [{query: \"error_rate\", canary: 1.0, stable: 0.0, delta: 1.0, max_delta: 0.01}]
        and .stages[0].slo == \$r.stages[0].slo)" "$1" || return 1
  h14 "no other release has a reason (null, or no key where the source has none)" \
    "[.releases[] | select(.version != \"0.2.0-a\") | .reason] | length == 4 and all(. == null)" "$1" || return 1
  h14 "the releases whose log lines predate the fields have no stage times and no slo" \
    "[.releases[] | select(.version == \"0.2.5-n\" or .version == \"0.3.0-b\" or .version == \"0.4.0-c\") | .stages[]]
      | length == 5 and all(.started_at == null and .judged_at == null and .slo == null)" "$1" || return 1
  h14 "0.5.0-d, from release.json, has its two stages' times and slo as the stub's, by hand" \
    "(.releases[] | select(.version == \"0.5.0-d\") | .stages) as \$s | \$cur[0].stages as \$c
      | (\$s | length) == 2 and \$s[0].started_at == \"2026-11-04T10:00:00Z\" and \$s[0].judged_at == \"2026-11-04T10:01:02Z\"
        and \$s[1].started_at == \"2026-11-04T10:01:05Z\" and \$s[1].judged_at == \"2026-11-04T10:02:07Z\"
        and \$s[1].slo == [{query: \"error_rate\", canary: 0.001, stable: 0.0, delta: 0.001, max_delta: 0.01}]
        and all(range(0; 2); \$s[.].slo == \$c[.].slo and \$s[.].judged_at == \$c[.].judged_at)" "$1" || return 1
  h14 "launch.record is the stub's record as written: its stages' times and slo, reason null, no running" \
    ".launch.record as \$l | \$cur[0] as \$c
      | (\$l | has(\"reason\") and .reason == null and (has(\"running\") | not))
        and (\$l.stages | length) == 2
        and all(range(0; 2); \$l.stages[.].started_at == \$c.stages[.].started_at and \$l.stages[.].judged_at == \$c.stages[.].judged_at
          and \$l.stages[.].slo == \$c.stages[.].slo and \$l.stages[.].slo[0].max_delta == 0.01)" "$1" || return 1
  h14 "ticket 0001's canary row is at the stage's judged_at and carries the stage's slo" \
    "(.tickets[] | select(.id == \"0001\") | [.timeline[] | select(.phase == \"canary\")]) as \$c
      | (\$c | length) == 1 and \$c[0].at == \"2026-11-01T09:28:31Z\" and \$c[0].at == \$rel[0].record.stages[0].judged_at
        and \$c[0].slo == [{query: \"error_rate\", canary: 1.0, stable: 0.0, delta: 1.0, max_delta: 0.01}]
        and \$c[0].slo == \$rel[0].record.stages[0].slo" "$1" || return 1
  h14 "the canary rows of releases with no judged_at keep the release's at and have no slo" \
    "[.tickets[] | select(.id != \"0001\") | . as \$t | .timeline[] | select(.phase == \"canary\")
      | (.slo == null) and .at == (\$rel[] | select(.kind == \"release\" and .ticket == \$t.id) | .at)]
      | length == 4 and all" "$1" || return 1
}
bad=$(hand_14 "$root/out1.json") || nogo "the 1.4 fields over the stub sources: $bad" \
  "$(jq -c "{t3: ($t03 | {attempt, ci_why: .ci.why, rework_why: [.rework[].why]}), t2: ($t02 | [.rework[].why])}" "$root/out1.json" 2>&1)"
plant14() {
  jq "$2" "$root/out1.json" > "$tmp/plant.json"
  if hand_14 "$tmp/plant.json" >/dev/null; then
    nogo "the hand checks of the 1.4 fields accept a logbook with $1; a gate that cannot fail is not a gate"
  fi
}
plant14 "the schema 1.3" '.schema = "lights-out/logbook/1.3"'
plant14 "0003's attempt null" "$(tix 0003 '.attempt = null')"
plant14 "0003's ci.why gone" "$(tix 0003 '.ci.why = null')"
plant14 "0003's ci.why gate changed" "$(tix 0003 '.ci.why.gate = "tests"')"
plant14 "0003's ci.why attempt changed" "$(tix 0003 '.ci.why.attempt = 1')"
plant14 "0003's ci.why judge changed" "$(tix 0003 '.ci.why.judge = "review"')"
plant14 "0003's ci.why line trimmed" "$(tix 0003 '.ci.why.line = "NO-GO: test — 2 failed"')"
plant14 "0003's ci.why details cut to one" "$(tix 0003 '.ci.why.details |= .[0:1]')"
plant14 "0003's ci.why details withheld" "$(tix 0003 '.ci.why.details = {ok: false, error: "withheld: the secrets gate did not say GO on attempt 2"}')"
plant14 "0003's ci.why with a sast list" "$(tix 0003 '.ci.why.sast = []')"
plant14 "0003's rework[0].why gone" "$(tix 0003 '.rework[0].why = null')"
plant14 "0003's rework[0].why gate changed" "$(tix 0003 '.rework[0].why.gate = "test"')"
plant14 "0003's rework[0].why sast finding's line changed" "$(tix 0003 '.rework[0].why.sast[1].line = 10')"
plant14 "0003's rework[0].why sast finding dropped" "$(tix 0003 '.rework[0].why.sast |= .[0:1]')"
plant14 "0003's rework[0].why sast null" "$(tix 0003 '.rework[0].why.sast = null')"
plant14 "0003's rework[0].why sast withheld" "$(tix 0003 '.rework[0].why.sast = {ok: false, error: "withheld: the secrets gate did not say GO on attempt 1"}')"
plant14 "0003's rework[0].why details withheld" "$(tix 0003 '.rework[0].why.details = {ok: false, error: "withheld: the secrets gate did not say GO on attempt 1"}')"
plant14 "0003's rework[0] 1.3 details null" "$(tix 0003 '.rework[0].details = null')"
plant14 "0003's timeline with no gate rows" "$(tix 0003 '.timeline |= map(select(.phase != "gate"))')"
plant14 "0002's details shown" "$(tix 0002 '.rework[0].why.details = ["FAILED tests/test_w.py::test_join"]')"
plant14 "0002's withheld error for another attempt" "$(tix 0002 '.rework[0].why.details.error = "withheld: the secrets gate did not say GO on attempt 2"')"
plant14 "0002's withheld error reworded" "$(tix 0002 '.rework[0].why.details.error = "withheld"')"
plant14 "0002's 1.3 details still shown" "$(tix 0002 '.rework[0].details = ["FAILED tests/test_w.py::test_join"]')"
plant14 "0002's why gone" "$(tix 0002 '.rework[0].why = null')"
plant14 "0010's rework why withheld" "$(tix 0010 '.rework[0].why.details = {ok: false, error: "withheld: the secrets gate did not say GO on attempt 1"}')"
plant14 "0010's rework why gate changed" "$(tix 0010 '.rework[0].why.gate = "test"')"
plant14 "0010's rework why gone" "$(tix 0010 'del(.rework[0].why)')"
plant14 "a GO ci with a why" "$(tix 0010 '.ci.why = {attempt: 2, judge: "gates", gate: null, line: "GO: gates", details: [], sast: null}')"
plant14 "a GO ci with no why key" "$(tix 0010 'del(.ci.why)')"
plant14 "a rework why of another attempt" "$(tix 0010 '.rework[0].why.attempt = 2')"
rel14() { printf '.releases |= map(if .version == "%s" then %s else . end)' "$1" "$2"; }
plant14 "carol's reason changed" "$(rel14 0.4.0-c '.answer.reason = "the budget is fine"')"
plant14 "carol's reason null" "$(rel14 0.4.0-c '.answer.reason = null')"
plant14 "carol's brief_sha256 null" "$(rel14 0.4.0-c '.answer.brief_sha256 = null')"
plant14 "carol's brief_sha256 another" "$(rel14 0.4.0-c '.answer.brief_sha256 = "0000"')"
plant14 "carol's brief_author null" "$(rel14 0.4.0-c '.answer.brief_author = null')"
plant14 "carol's brief_author dropped" "$(rel14 0.4.0-c 'del(.answer.brief_author)')"
plant14 "carol's reason key dropped" "$(rel14 0.4.0-c 'del(.answer.reason)')"
plant14 "a reason made up for bob's GO" "$(rel14 0.2.5-n '.answer.reason = "fine"')"
plant14 "a brief sha made up for alice's GO" "$(rel14 0.3.0-b '.answer.brief_sha256 = "ab"')"
plant14 "a brief author made up for bob's GO" "$(rel14 0.2.5-n '.answer.brief_author = "bob"')"
plant14 "an answer made up for an unanswered release" "$(rel14 0.5.0-d '.answer = {answer: "GO", at: "2026-11-04T00:00:00Z", by: "x", via: "cli", reason: null, brief_sha256: null, brief_author: null}')"
plant14 "0002's sign-off row with a reason" "$(tix 0002 '(.timeline[] | select(.phase == "sign-off") | .reason) = "fine"')"
plant14 "0010's sign-off row with no reason key" "$(tix 0010 '.timeline |= map(if .phase == "sign-off" then del(.reason) else . end)')"
plant14 "0002's sign-off row dropped" "$(tix 0002 '.timeline |= map(select(.phase != "sign-off"))')"
# docs/adr/0080 §1, §4: the stage times, the numbers and the reason, each one planted wrong.
plant14 "0.2.0-a's reason changed" "$(rel14 0.2.0-a '.reason = "5%: latency"')"
plant14 "0.2.0-a's reason null" "$(rel14 0.2.0-a '.reason = null')"
plant14 "0.2.0-a's reason dropped" "$(rel14 0.2.0-a 'del(.reason)')"
plant14 "a reason made up for 0.3.0-b" "$(rel14 0.3.0-b '.reason = "5%: error_rate 1.000"')"
plant14 "0.2.0-a's stage started_at changed" "$(rel14 0.2.0-a '.stages[0].started_at = "2026-11-01T09:27:31Z"')"
plant14 "0.2.0-a's stage judged_at null" "$(rel14 0.2.0-a '.stages[0].judged_at = null')"
plant14 "0.2.0-a's stage slo delta changed" "$(rel14 0.2.0-a '.stages[0].slo[0].delta = 0.5')"
plant14 "0.2.0-a's stage slo max_delta changed" "$(rel14 0.2.0-a '.stages[0].slo[0].max_delta = 0.05')"
plant14 "0.2.0-a's stage slo gone" "$(rel14 0.2.0-a 'del(.stages[0].slo)')"
plant14 "a slo made up for 0.3.0-b's stage" "$(rel14 0.3.0-b '.stages[0].slo = [{query: "error_rate", canary: 0.0, stable: 0.0, delta: 0.0, max_delta: 0.01}]')"
plant14 "a judged_at made up for 0.4.0-c's stage" "$(rel14 0.4.0-c '.stages[0].judged_at = "2026-11-03T12:00:00Z"')"
plant14 "0.5.0-d's second stage judged_at changed" "$(rel14 0.5.0-d '.stages[1].judged_at = "2026-11-04T10:02:08Z"')"
plant14 "0.5.0-d's first stage slo gone" "$(rel14 0.5.0-d '.stages[0].slo = []')"
plant14 "launch.record's stage slo gone" '.launch.record.stages[1].slo = null'
plant14 "launch.record's stage judged_at changed" '.launch.record.stages[0].judged_at = "2026-11-04T10:01:03Z"'
plant14 "launch.record's started_at dropped" 'del(.launch.record.stages[0].started_at)'
plant14 "launch.record with a reason" '.launch.record.reason = "5%: error_rate 1.000"'
plant14 "launch.record with no reason key" 'del(.launch.record.reason)'
plant14 "launch.record with a running stage" '.launch.record.running = {pct: 25, started_at: "2026-11-04T10:01:05Z", judge_after_s: 60}'
plant14 "0001's canary row with its release's at" "$(tix 0001 '(.timeline[] | select(.phase == "canary") | .at) = "2026-11-01T09:29:00Z"')"
plant14 "0001's canary row with no slo" "$(tix 0001 '(.timeline[] | select(.phase == "canary") | .slo) = null')"
plant14 "0001's canary row with another delta" "$(tix 0001 '(.timeline[] | select(.phase == "canary") | .slo[0].delta) = 0.0')"
plant14 "0002's canary row with a slo made up" "$(tix 0002 '(.timeline[] | select(.phase == "canary") | .slo) = [{query: "error_rate", canary: 0.0, stable: 0.0, delta: 0.0, max_delta: 0.01}]')"
plant14 "0002's canary row at another time" "$(tix 0002 '(.timeline[] | select(.phase == "canary") | .at) = "2026-11-02T10:00:00Z"')"

# The 1.5 fields (docs/adr/0083 and 0082, amendments of 2026-11-13), worked out
# from the stub's tower documents and release log and written down by hand; the
# reader's code is not read. The ledger: 0003's assign ($0.825, 300 s,
# 2026-11-03) and rework ($0.3375, 120 s, 2026-11-03), 0010's replayed assign
# ($0, 48 s, not counted as agents' time) and its review at a model with no price
# (unknown, 30 s, 2026-11-02). The stub's policy/rework.env sets
# SPEND_BUDGET_TICKET_USD=1.00 and no day limit. The window: 30 days ending at
# 0009's last history entry, 2026-11-07T08:10:07Z, the record's last event.
h15() {
  jq -e --slurpfile s7 "$stub/state/tickets.0007.json" "$2" "$3" >/dev/null 2>&1 || { printf '%s\n' "$1"; return 1; }
}
# shellcheck disable=SC2016 # $t, $s7 and the dollar amounts are jq's and the record's, not the shell's
hand_15() {
  h15 "the schema is 1.8 (1.5's fields are kept)" '.schema == "lights-out/logbook/1.8"' "$1" || return 1
  h15 "spend per ticket: 0003 \$1.1625 over 2 runs, 420 s of agents, 1,200 s elapsed; 0010 \$0 over 2, 1 unknown, 30 s, 5,400 s; the others 0 runs with their elapsed time" \
    '.spend.tickets as $t | ($t | keys) == ["0001","0002","0003","0004","0005","0007","0008","0009","0010"]
      and ($t["0003"] | .runs == 2 and .unknown == 0 and .agent_s == 420 and .elapsed_s == 1200 and ((.cost_usd - 1.1625) | fabs) < 0.000001)
      and ($t["0010"] | .runs == 2 and .unknown == 1 and .agent_s == 30 and .elapsed_s == 5400 and .cost_usd == 0)
      and ([$t["0001","0002","0004","0005","0007","0008","0009"] | .elapsed_s] == [1800, 7200, 300, 60, 3000, 2400, 607])
      and ([$t["0001","0002","0004","0005","0007","0008","0009"] | .runs + .unknown + .agent_s + .cost_usd] | all(. == 0))' "$1" || return 1
  h15 "spend per role: dev-worker \$1.1625, 3 runs, 420 s; test-reviewer \$0, 1 run, 1 unknown, 30 s" \
    '.spend.roles | keys == ["dev-worker", "test-reviewer"]
      and (.["dev-worker"] | .runs == 3 and .unknown == 0 and .agent_s == 420 and ((.cost_usd - 1.1625) | fabs) < 0.000001)
      and (.["test-reviewer"] | .runs == 1 and .unknown == 1 and .agent_s == 30 and .cost_usd == 0)' "$1" || return 1
  h15 "spend per day and in all: 2026-11-02 \$0, 2 runs, 1 unknown; 2026-11-03 \$1.1625, 2 runs; \$1.1625, 4 runs, 1 unknown" \
    '.spend | (.days | keys) == ["2026-11-02", "2026-11-03"]
      and (.days["2026-11-02"] | .runs == 2 and .unknown == 1 and .cost_usd == 0)
      and (.days["2026-11-03"] | .runs == 2 and .unknown == 0 and ((.cost_usd - 1.1625) | fabs) < 0.000001)
      and (.total | .runs == 4 and .unknown == 1 and ((.cost_usd - 1.1625) | fabs) < 0.000001)' "$1" || return 1
  h15 "spend's summary, limits and budget line, as ./go spend words them" \
    '.spend | .summary == "9 tickets, 4 runs, $1.16 known, 1 of cost unknown"
      and .limits == {SPEND_BUDGET_TICKET_USD: 1, SPEND_BUDGET_DAY_USD: null}
      and .lines[-1] == "budget: SPEND_BUDGET_TICKET_USD=$1.00, SPEND_BUDGET_DAY_USD no limit"
      and (.lines | index("ticket 0003: $1.16 over 2 run(s); agents 0h07m00s, elapsed 0h20m00s")) != null
      and (.lines | index("ticket 0010: $0.00 over 2 run(s), 1 of cost unknown; agents 0h00m30s, elapsed 1h30m00s")) != null
      and (.lines | index("role dev-worker: $1.16 over 3 run(s); 0h07m00s")) != null
      and (.lines | index("day 2026-11-02: $0.00 over 2 run(s), 1 of cost unknown")) != null
      and ([.lines[] | split(" ")[0]] == ["ticket","ticket","ticket","ticket","ticket","ticket","ticket","ticket","ticket","role","role","day","day","burn:","projection:","budget:"])' "$1" || return 1
  h15 "0007 stopped by the cap: stop cap, its last rework line and judge; no other ticket has a stop" \
    '(.tickets[] | select(.id == "0007") | .outcome) == {verdict: "needs-human", judge: $s7[0].rework[-1].judge, line: $s7[0].rework[-1].line, stop: "cap"}
      and ([.tickets[] | select(.state != "needs-human") | .outcome | has("stop")] | any | not)' "$1" || return 1
  h15 "dora's window: 30 days ending at the record's last event" \
    '.dora | .schema == "dora/1" and .window_days == 30 and .from == "2026-10-08T08:10:07Z" and .to == "2026-11-07T08:10:07Z"' "$1" || return 1
  h15 "dora's measures: 2 deployments; lead time 7,650 s over 2; 2 failed of 4 decided; recovery 90 s over 1, 1 unmeasured" \
    '.dora.measures | (.deployment_frequency | .measured and .count == 2 and ((.per_day - (2 / 30)) | fabs) < 0.000001)
      and .lead_time == {measured: true, median_s: 7650, n: 2, without_ticket: 0}
      and .change_fail_rate == {measured: true, rate: 0.5, failed: 2, decided: 4, pending: 0}
      and .recovery_time == {measured: true, median_s: 90, n: 1, unmeasured: 1}' "$1" || return 1
  h15 "dora's lines: the window line, then one per measure in order, as ./go dora words them" \
    '.dora.lines == ["window: 30 days, 2026-10-08T08:10:07Z to 2026-11-07T08:10:07Z",
      "deployment frequency: 2 promoted to 100%, 0.067 per day",
      "lead time: median 2.1 h from request to 100% over 2 release(s), 0 without a ticket",
      "change fail rate: 50%: 2 of 4 decided release(s) rolled back, 0 pending",
      "recovery time: median 2 min from the failing stage'"'"'s start to the rollback over 1 release(s), 1 unmeasured"]' "$1" || return 1
}
bad=$(hand_15 "$root/out1.json") || nogo "the 1.5 fields over the stub sources: $bad" \
  "$(jq -c '{spend: (.spend | del(.tickets)), dora}' "$root/out1.json" 2>&1)"
plant15() {
  jq "$2" "$root/out1.json" > "$tmp/plant.json"
  if hand_15 "$tmp/plant.json" >/dev/null; then
    nogo "the hand checks of the 1.5 fields accept a logbook with $1; a gate that cannot fail is not a gate"
  fi
}
plant15 "the schema 1.4" '.schema = "lights-out/logbook/1.4"'
plant15 "0003's cost changed" '.spend.tickets["0003"].cost_usd = 1.16'
plant15 "0010's replayed run counted as agents' time" '.spend.tickets["0010"].agent_s = 78'
plant15 "0010's unknown run counted as known" '.spend.tickets["0010"].unknown = 0'
plant15 "a ticket dropped from spend" '.spend.tickets |= del(.["0005"])'
plant15 "0009's elapsed changed" '.spend.tickets["0009"].elapsed_s = 600'
plant15 "a role renamed" '.spend.roles |= with_entries(if .key == "test-reviewer" then .key = "reviewer" else . end)'
plant15 "a day's runs changed" '.spend.days["2026-11-02"].runs = 1'
plant15 "the total's unknown dropped" '.spend.total.unknown = 0'
# shellcheck disable=SC2016 # $t, $s7 and the dollar amounts are jq's and the record's, not the shell's
plant15 "the summary reworded" '.spend.summary = "9 tickets, 4 runs, $1.16 known"'
plant15 "a day limit made up" '.spend.limits.SPEND_BUDGET_DAY_USD = 5'
plant15 "the budget line not last" '.spend.lines |= ([.[-1]] + .[0:-1])'
# shellcheck disable=SC2016 # $t, $s7 and the dollar amounts are jq's and the record's, not the shell's
plant15 "a ticket line reworded" '.spend.lines |= map(if startswith("ticket 0003") then "ticket 0003: $1.16" else . end)'
plant15 "0007 with no stop" "$(tix 0007 'del(.outcome.stop)')"
plant15 "0007 stopped by the budget" "$(tix 0007 '.outcome.stop = "budget"')"
plant15 "0007's line not its last rework's" "$(tix 0007 '.outcome.line = "spend budget: x"')"
plant15 "a stop on a ticket that is not at needs-human" "$(tix 0003 '.outcome.stop = "cap"')"
plant15 "dora's window ending at a clock" '.dora.to = "2026-11-13T00:00:00Z"'
plant15 "dora's window of 7 days" '.dora.window_days = 7'
plant15 "a third deployment" '.dora.measures.deployment_frequency.count = 3'
plant15 "the mean lead time" '.dora.measures.lead_time.median_s = 7651'
plant15 "0.4.0-c counted as a success" '.dora.measures.change_fail_rate |= (.failed = 1 | .rate = 0.25)'
plant15 "0.4.0-c's recovery estimated" '.dora.measures.recovery_time |= (.n = 2 | .unmeasured = 0)'
plant15 "dora's lines without the window" '.dora.lines |= .[1:]'
plant15 "dora's lines reformatted" '.dora.lines[2] = "lead time: median 7650 s"'

# The budget's reason on the card (docs/adr/0083, amendment §4): 0007's document
# with a budget line, read again.
cp "$root/state/tickets.0007.json" "$tmp/0007.keep"
bline="spend budget: this ticket spent \$4.20, at or over SPEND_BUDGET_TICKET_USD=\$4.00; the next attempt is not launched"
jq --arg l "$bline" '.budget = {line: $l}' "$tmp/0007.keep" > "$root/state/tickets.0007.json"
run_reader LOGBOOK_FILE=out7.json
jq -e --arg l "$bline" --slurpfile s7 "$tmp/0007.keep" '(.tickets[] | select(.id == "0007") | .outcome)
    == {verdict: "needs-human", judge: $s7[0].rework[-1].judge, line: $l, stop: "budget"}' "$root/out7.json" >/dev/null \
  || nogo "a needs-human ticket whose document carries budget.line does not say the budget stopped it, with that line" \
    "$(jq -c '.tickets[] | select(.id == "0007") | .outcome' "$root/out7.json" 2>&1)"
cp "$tmp/0007.keep" "$root/state/tickets.0007.json"

# The kanban (docs/adr/0055 §7): every card's column is its ticket's tower
# state, and every ticket is on exactly one card. Then the tower moves a
# ticket, and its card moves with it, and nothing else does.
cards_agree() {
  jq -e '([.kanban[] | .state as $s | .tickets[] | {id: ., col: $s}]) as $cards
    | ($cards | length) == (.tickets | length)
    and ([.tickets[] | .id as $i | .state as $st | ([$cards[] | select(.id == $i and .col == $st)] | length) == 1] | all)' "$1" >/dev/null
}
cards_agree "$root/out1.json" || nogo "a kanban card is not in its ticket's state's column, or a ticket is not on exactly one card" \
  "$(jq -c '{kanban, states: [.tickets[] | {id, state}]}' "$root/out1.json")"
cp "$root/state/tickets.0005.json" "$tmp/0005.keep"
jq '.state = "merged"' "$tmp/0005.keep" > "$root/state/tickets.0005.json"
run_reader LOGBOOK_FILE=out4.json
cards_agree "$root/out4.json" || nogo "after the tower moved ticket 0005, a card is not in its state's column"
[ "$(jq -c '[.kanban[] | select(.state == "merged" or .state == "opened") | {(.state): .tickets}] | add' "$root/out4.json")" = '{"opened":[],"merged":["0005"]}' ] \
  || nogo "moving ticket 0005 to merged did not move its card from opened to merged" "$(jq -c .kanban "$root/out4.json")"
cp "$tmp/0005.keep" "$root/state/tickets.0005.json"
jq '.kanban[0].tickets += ["0001"]' "$root/out1.json" > "$tmp/kanban-bad.json"
! cards_agree "$tmp/kanban-bad.json" || nogo "the kanban check accepts a ticket on two cards"
jq '(.tickets[] | select(.id == "0009") | .state) = "opened"' "$root/out1.json" > "$tmp/kanban-bad.json"
! cards_agree "$tmp/kanban-bad.json" || nogo "the kanban check accepts a card outside its ticket's state"

# The golden depends on its sources: change one fact, the bytes change.
cp "$root/state/tickets.0001.json" "$tmp/0001.keep"
jq '.release.verdict = "NO-GO: launchpad — 25%: latency"' "$tmp/0001.keep" > "$root/state/tickets.0001.json"
run_reader LOGBOOK_FILE=out3.json
if cmp -s "$root/out3.json" "$golden"; then
  nogo "changing a ticket's release verdict in the store left the logbook equal to the golden"
fi
cp "$tmp/0001.keep" "$root/state/tickets.0001.json"

# var_case WHAT JQ_EXPR [VAR=val...]: one variant of the sources, one fact.
var_case() {
  what=$1
  expr=$2
  shift 2
  run_reader LOGBOOK_FILE=var.json "$@"
  case "$out" in "GO: logbook"*) : ;; *) nogo "variant $what: core/logbook.sh printed '$out' (exit $rc)" "$(cat "$root/reader.err")" ;; esac
  jq -e "$expr" "$root/var.json" >/dev/null 2>"$root/jq.err" \
    || nogo "variant $what: the logbook does not satisfy $expr" "$(cat "$root/jq.err")" "$(jq -c '{launch: .launch, releases: (.releases | if type == "array" then map({version, from, answer}) else . end), tickets: (.tickets | if type == "array" then length else . end)}' "$root/var.json")"
}

# A record the console's press already answered: GO, then NO-GO.
jq -c '. + {promoted: {weight: 100, at: "2026-11-04T10:00:00Z", by: "bob", via: "cli"}}' "$root/release.json" > "$root/release.promoted.json"
jq -c '. + {rejected: {at: "2026-11-04T10:00:00Z", by: "bob", via: "cli"}}' "$root/release.json" > "$root/release.rejected.json"
var_case "a promoted record" '
  .launch.pressable == false and .launch.preparable == false
  and .launch.disabled_reason == "release 0.5.0-d was already answered GO by bob via cli"
  and (.releases[-1] | .from == "release.json" and .answer == {answer: "GO", by: "bob", via: "cli", at: "2026-11-04T10:00:00Z", reason: null, brief_sha256: null, brief_author: null})' \
  RELEASE_RECORD=release.promoted.json
# docs/adr/0079 §1: a record that holds them, and the logbook says so in the
# release's answer and in launch.record.
jq -c '. + {promoted: {weight: 100, at: "2026-11-04T10:00:00Z", by: "bob", via: "console", reason: "numbers hold", brief_sha256: "ab12", brief_author: "the release role"}}' "$root/release.json" > "$root/release.promoted-why.json"
var_case "a promoted record with its reason" '
  .launch.record.promoted.reason == "numbers hold" and .launch.record.promoted.brief_sha256 == "ab12"
  and .launch.record.promoted.brief_author == "the release role"
  and (.releases[-1].answer == {answer: "GO", by: "bob", via: "console", at: "2026-11-04T10:00:00Z", reason: "numbers hold", brief_sha256: "ab12", brief_author: "the release role"})' \
  RELEASE_RECORD=release.promoted-why.json
var_case "a rejected record" '
  .launch.pressable == false
  and .launch.disabled_reason == "release 0.5.0-d was already answered NO-GO by bob via cli"
  and .releases[-1].answer == {answer: "NO-GO", by: "bob", via: "cli", at: "2026-11-04T10:00:00Z", reason: null, brief_sha256: null, brief_author: null}' \
  RELEASE_RECORD=release.rejected.json

# The plane runs another canary: promote's own sentence.
printf '{"stable":"0.3.0-b","canary":"0.9.9-x","weight":25}\n' > "$root/world/status.json"
var_case "a plane with another canary" '
  .launch.pressable == false and .launch.preparable == false
  and .launch.disabled_reason == "the record holds 0.5.0-d at 25%, the plane has canary 0.9.9-x at 25%"
  and .launch.plane.canary == "0.9.9-x"'
printf '{"stable":"0.3.0-b","canary":"0.5.0-d","weight":25}\n' > "$root/world/status.json"

# A plane that cannot be read.
mkdir -p "$root/adapters/stub-delivery-down"
printf '#!/bin/sh\nprintf "first line\\nthe plane is down\\n" >&2\nexit 1\n' > "$root/adapters/stub-delivery-down/status.sh"
chmod +x "$root/adapters/stub-delivery-down/status.sh"
var_case "a plane that fails" '
  .launch.plane == {ok: false, error: "the plane is down"}
  and .launch.pressable == false
  and .launch.disabled_reason == "the plane cannot be read: the plane is down"' DELIVERY=stub-delivery-down

# No brief for the held version: prepare it.
mkdir -p "$root/nobrief"
var_case "no brief" '
  .launch.brief == null and .launch.pressable == false and .launch.preparable == true
  and .launch.disabled_reason == "no brief for 0.5.0-d yet; prepare it"' PROMOTE_OUT=nobrief
# A brief that does not name the version.
mkdir -p "$root/otherbrief"
printf '# Promotion brief\n\nnothing about the version\n' > "$root/otherbrief/brief.md"
var_case "a brief for another version" '
  .launch.brief.sha256 != null and .launch.brief.author == null
  and .launch.preparable == true and .launch.pressable == false' PROMOTE_OUT=otherbrief

# No record at all: no fallback entry either.
var_case "no release record" '
  .launch.record == null and .launch.pressable == false
  and .launch.disabled_reason == "no release record; run ./go release"
  and (.releases | length) == 4 and all(.releases[]; .from == "releases.jsonl")' RELEASE_RECORD=absent.json
# A record that is not JSON.
printf 'not json\n' > "$root/release.bad.json"
var_case "a record that is not JSON" '
  (.launch.record | .ok == false and (.error | length) > 0) and .launch.pressable == false
  and (.launch.disabled_reason | length) > 0' RELEASE_RECORD=release.bad.json
# A rolled-back record.
jq -c '.verdict = "rolled-back" | .held_at = null' "$root/release.json" > "$root/release.rb.json"
var_case "a rolled-back record" '
  (.launch.pressable == false
  and (.launch.disabled_reason | startswith("release 0.5.0-d is rolled-back")))
  and (.releases[-1] | .from == "release.json" and .verdict == "rolled-back")' RELEASE_RECORD=release.rb.json
# docs/adr/0080 §2, §4: a record in progress that names its running stage is
# in launch.record as written, `running` verbatim.
jq -c '.verdict = "in-progress" | .held_at = null | del(.reason) | .stages |= .[0:1]
  | .running = {pct: 25, started_at: "2026-11-04T10:01:05Z", judge_after_s: 60}' "$root/release.json" > "$root/release.running.json"
running_expr='.launch.record.running == {pct: 25, started_at: "2026-11-04T10:01:05Z", judge_after_s: 60}
  and .launch.record.verdict == "in-progress" and (.launch.record.stages | length) == 1
  and .launch.record.stages[0].slo[0].max_delta == 0.01 and .launch.pressable == false'
var_case "an in-progress record with its running stage" "$running_expr" RELEASE_RECORD=release.running.json
# The check refuses a logbook that dropped or changed the running stage.
for bad in 'del(.launch.record.running)' '.launch.record.running.pct = 5' '.launch.record.running.started_at = "2026-11-04T10:00:00Z"' \
  '.launch.record.running.judge_after_s = 30' '.launch.record.running.judge_after_s = "60"' '.launch.record.running = null'; do
  jq "$bad" "$root/var.json" > "$tmp/var-bad.json"
  if jq -e "$running_expr" "$tmp/var-bad.json" >/dev/null 2>&1; then
    nogo "the in-progress hand check accepts a logbook with '$bad'; a gate that cannot fail is not a gate"
  fi
done
# A rolled-back record keeps its reason in launch.record and in releases[-1].
jq -c '.verdict = "rolled-back" | .held_at = null | .reason = "25%: error_rate 0.050"' "$root/release.json" > "$root/release.rbwhy.json"
var_case "a rolled-back record with its reason" '
  .launch.record.reason == "25%: error_rate 0.050" and (.launch.record | has("running") | not)
  and (.releases[-1] | .from == "release.json" and .reason == "25%: error_rate 0.050")' RELEASE_RECORD=release.rbwhy.json

# A record the last log entry already is: no second entry.
jq -c 'select(.kind == "release" and .record.version == "0.4.0-c") | .record' "$root/releases.jsonl" > "$root/release.same.json"
var_case "a record equal to the last log entry" '(.releases | length) == 4 and all(.releases[]; .from == "releases.jsonl")' \
  RELEASE_RECORD=release.same.json

# An absent log: no entries, then the record's own.
var_case "an absent release log" '(.releases | length) == 1 and .releases[0].from == "release.json" and .releases[0].at == null' \
  RELEASES_LOG=absent.jsonl
# A line that is not JSON: the whole field says so.
{ head -n 1 "$root/releases.jsonl"; printf 'this is not json\n'; tail -n 1 "$root/releases.jsonl"; } > "$root/releases.bad.jsonl"
var_case "a release log with a bad line" '.releases == {ok: false, error: "releases.bad.jsonl line 2 is not JSON"}' RELEASES_LOG=releases.bad.jsonl

# A store that is down (not "absent"): the whole field, with its last stderr line.
mkdir -p "$root/adapters/stub-state-down"
printf '#!/bin/sh\nprintf "noise\\nthe store is down\\n" >&2\nexit 2\n' > "$root/adapters/stub-state-down/get.sh"
chmod +x "$root/adapters/stub-state-down/get.sh"
var_case "a store that is down" '.tickets == {ok: false, error: "the store is down"}' STATE_STORE=stub-state-down

# Only the perimeters held unread (docs/adr/0071 §1): the files' held_by is null,
# their tests and ADRs, which the map answers, are not.
mkdir -p "$root/adapters/stub-state-noheld"
cat > "$root/adapters/stub-state-noheld/get.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
[ "$1" != perimeters ] || { printf 'noise\nthe held file is down\n' >&2; exit 2; }
f="$d/state/$1.$2.json"
[ -f "$f" ] || exit 1
cat "$f"
STUB
chmod +x "$root/adapters/stub-state-noheld/get.sh"
var_case "perimeters held unread: a file's holders" '.perimeters_held == {ok: false, error: "the held file is down"}
  and ((.tickets[] | select(.id == "0010")) | .files | length == 2 and all(.held_by == null)
       and .[0].tests == ["tests/test_notify.py"] and .[0].adrs == ["0002"])' STATE_STORE=stub-state-noheld

# A policy that does not parse: the whole field.
mkdir -p "$root/badcontract"
cp "$root/contract/workload.env" "$root/badcontract/workload.env"
printf '# Provenance, decision: x\nERROR_RATE_MAX_DELTA=banana\n' > "$root/badcontract/policy.env"
var_case "a policy that does not parse" '.thresholds | type == "object" and .ok == false and (.error | length) > 0' WORKLOAD_CONTRACT=badcontract

# A post mortem that is not JSON.
mkdir -p "$root/pm-bad/broken"
printf 'not json\n' > "$root/pm-bad/broken/postmortem.json"
var_case "a post mortem that is not JSON" '.postmortems | type == "object" and .ok == false and (.error | length) > 0' POSTMORTEM_DIR=pm-bad
# No post mortem at all.
mkdir -p "$root/pm-none"
var_case "no post mortem" '.postmortems == []' POSTMORTEM_DIR=pm-none

# The map is one more source of the reader's (docs/adr/0059 §3): its absence
# or refusal is {ok: false, error} where `adrs` would be, and no ticket then
# has an ADR; a map at another commit than HEAD is still read; the reader
# opens it read-only; a ticket's glob crosses `/`.
# commit_all DIR: a git repository of whatever DIR holds, one commit.
commit_all() {
  git -C "$1" init -q . >/dev/null 2>&1
  git -C "$1" add -A >/dev/null 2>&1
  GIT_AUTHOR_NAME=stub GIT_AUTHOR_EMAIL=stub@example.invalid GIT_COMMITTER_NAME=stub \
    GIT_COMMITTER_EMAIL=stub@example.invalid GIT_AUTHOR_DATE="1793491200 +0000" \
    GIT_COMMITTER_DATE="1793491200 +0000" \
    git -C "$1" -c commit.gpgsign=false commit -q -m stub >/dev/null 2>&1
}
# map_of DIR FILE: the map core/map.py builds from DIR's one commit.
map_of() {
  env -u GIT_DIR -u GIT_WORK_TREE python3 "$repo/core/map.py" build --repo "$1" --rev HEAD --tickets tickets \
    --targets "$tmp/targets.txt" --out "$2" >/dev/null 2>"$tmp/map.err" \
    || nogo "core/map.py could not build the map of $1" "$(cat "$tmp/map.err")"
}
var_case "no map" '.adrs | type == "object"' MAP_FILE=absent.sqlite
chk unread "$root/var.json" "./go map" >"$tmp/chk.out" || nogo "with no map, adrs is not {ok: false, error} naming ./go map" "$(cat "$tmp/chk.out")"
# docs/adr/0071 §1: the files the map cannot speak for say null, not [].
var_case "no map: a file's tests and adrs" '(.tickets[] | select(.id == "0010")) | .files | length == 2 and all(.tests == null and .adrs == null and (.held_by | type == "array"))' MAP_FILE=absent.sqlite
cp "$root/.crew/map.sqlite" "$root/map-1.sqlite"
chk mapset "$root/map-1.sqlite" schema 1 >"$tmp/chk.out" || nogo "could not make a schema 1 map" "$(cat "$tmp/chk.out")"
var_case "a map of schema 1" '.adrs | type == "object"' MAP_FILE=map-1.sqlite
chk unread "$root/var.json" >"$tmp/chk.out" || nogo "a map of schema 1 was read: adrs is not {ok: false, error}" "$(cat "$tmp/chk.out")"
printf 'not a database\n' > "$root/map-text.sqlite"
var_case "a map that is not a database" '.adrs | type == "object"' MAP_FILE=map-text.sqlite
chk unread "$root/var.json" >"$tmp/chk.out" || nogo "a file that is not a database was read: adrs is not {ok: false, error}" "$(cat "$tmp/chk.out")"
: > "$root/map-empty.sqlite"
var_case "an empty map file" '.adrs | type == "object"' MAP_FILE=map-empty.sqlite
chk unread "$root/var.json" >"$tmp/chk.out" || nogo "an empty file was read: adrs is not {ok: false, error}" "$(cat "$tmp/chk.out")"

other="$tmp/other"
mkdir -p "$other/docs/adr" "$other/app/backend"
# shellcheck disable=SC2016 # the backticks are the ADR's own
printf '%s\n' '# ADR 0042 — Another world' '' '- Status: accepted' '' 'It cites `app/backend/router.py`.' > "$other/docs/adr/0042-another-world.md"
printf 'x = 1\n' > "$other/app/backend/router.py"
commit_all "$other"
other_sha=$(git -C "$other" rev-parse HEAD)
map_of "$other" "$root/other.sqlite"
var_case "a map at another commit than HEAD" ".adrs.map_sha == \"$other_sha\" and .adrs.map_sha != .source.commit
  and (.adrs.items | map(.number) == [\"0042\"]) and .adrs.items[0].files == [\"app/backend/router.py\"]
  and .adrs.items[0].tickets == [\"0008\"] and .adrs.items[0].status == \"accepted\"
  and ([.tickets[] | {(.id): .adrs}] | add) == {\"0001\": [], \"0002\": [], \"0003\": [], \"0004\": [], \"0005\": [], \"0007\": [], \"0008\": [\"0042\"], \"0009\": [], \"0010\": []}" \
  MAP_FILE=other.sqlite
chk adrs "$root/var.json" "$root/other.sqlite" >"$tmp/chk.out" || nogo "the ADRs of a map at another commit break the rules" "$(cat "$tmp/chk.out")"

noadr="$tmp/noadr"
mkdir -p "$noadr"
printf '# Readme\n' > "$noadr/README.md"
commit_all "$noadr"
map_of "$noadr" "$root/noadr.sqlite"
var_case "a map with no ADR" '.adrs.items == [] and (.adrs.map_sha | test("^[0-9a-f]{40}$")) and (.adrs | keys == ["items", "map_sha"])
  and all(.tickets[]; .adrs == [])' MAP_FILE=noadr.sqlite
chk adrs "$root/var.json" "$root/noadr.sqlite" >"$tmp/chk.out" || nogo "a map with no ADR gives the wrong adrs" "$(cat "$tmp/chk.out")"

mkdir -p "$root/ro"
cp "$root/.crew/map.sqlite" "$root/ro/map.sqlite"
ro_sum=$(sha256sum "$root/ro/map.sqlite" | cut -d' ' -f1)
var_case "a map in its own directory" '(.adrs.items | length) == 3' MAP_FILE=ro/map.sqlite
[ "$(find "$root/ro" -type f | wc -l | tr -d ' ')" = 1 ] || nogo "reading the map left a file beside it (a journal?): $(find "$root/ro" -type f)"
[ "$(sha256sum "$root/ro/map.sqlite" | cut -d' ' -f1)" = "$ro_sum" ] || nogo "reading the map changed it: the reader must open it read-only"

mkdir -p "$root/tickets-wide"
cp "$root"/tickets/*.md "$root/tickets-wide/"
printf '# 0008 — Split the router\n\n## Perimeter\n\n- app/*\n' > "$root/tickets-wide/0008.md"
var_case "a glob that crosses /" '(.tickets[] | select(.id == "0008") | .adrs) == ["0001", "0002"]' TICKETS_DIR=tickets-wide
chk adrs "$root/var.json" "$root/.crew/map.sqlite" >"$tmp/chk.out" || nogo "a wide glob gives the wrong adrs" "$(cat "$tmp/chk.out")"

# Who requested and through what (docs/adr/0059 §4): the tower's history entry
# reaches the timeline's request row.
# The 1.6 flow (docs/adr/0085 §1-§3), worked out from the stub's tower
# documents and release log and written down by hand; the reader's code is not
# read. 0010 was opened directly at 06:00, its PR #10 opened at 06:05, its
# second attempt passed the eight gates (the stub's ci.seconds), the ci said
# review at 06:55 and the merge m10 came at 07:06 after the test-reviewer
# approved; main went green at 07:08, the release held at 25% at 07:30 after
# two stages with no times, and bob promoted it via cli at 07:45. 0003's
# second attempt said NO-GO at test after five GO gates.
h16() {
  jq -e --slurpfile s3 "$stub/state/tickets.0003.json" --slurpfile s10 "$stub/state/tickets.0010.json" \
    --argjson order "$gate_order" "$2" "$3" >/dev/null 2>&1 || { printf '%s\n' "$1"; return 1; }
}
# shellcheck disable=SC2016 # $s3, $s10 and $order are jq's own variables
hand_16() {
  h16 "every ticket's flow: ticket, perimeter, the Makefile's eight gates in order, pr, canary, human, full" \
    '[.tickets[] | [.flow[] | if .node == "gate" then .gate else .node end]] | unique
      == [["ticket", "perimeter"] + $order + ["pr", "canary", "human", "full"]]' "$1" || return 1
  h16 "0010's ticket node: GO, opened directly at 06:00, no seconds" \
    "$t10"' | .flow[0] == {node: "ticket", gate: null, verdict: "GO", started_at: "2026-11-02T06:00:00Z",
      ended_at: "2026-11-02T06:00:00Z", seconds: null, line: "opened directly: no request, no spec gate"}' "$1" || return 1
  h16 "0010's perimeter: GO from 06:00 to 06:05, 300 s, PR #10 inside its two globs" \
    "$t10"' | .flow[1] == {node: "perimeter", gate: null, verdict: "GO", started_at: "2026-11-02T06:00:00Z",
      ended_at: "2026-11-02T06:05:00Z", seconds: 300,
      line: "PR #10 opened, every file inside app/backend/notify/*, tests/test_notify.py"}' "$1" || return 1
  h16 "0010's eight gates: GO, each line verbatim from the stub's ci.lines, each with the stub's ci.seconds" \
    "$t10"' | [.flow[2:10][] | [.gate, .verdict, .line, .seconds, .started_at, .ended_at]]
      == [$order[] as $g | [$g, "GO", ($s10[0].ci.lines[] | select(. == "GO: \($g)" or startswith("GO: \($g) — "))), $s10[0].ci.seconds[$g], null, null]]' "$1" || return 1
  h16 "0010's pr: GO from 06:55 to 07:06, 660 s, merged m10 and approved by the test-reviewer" \
    "$t10"' | .flow[10] == {node: "pr", gate: null, verdict: "GO", started_at: "2026-11-02T06:55:00Z",
      ended_at: "2026-11-02T07:06:00Z", seconds: 660, line: "merged m10, approved by the test-reviewer"}' "$1" || return 1
  h16 "0010's canary: GO from main-green at 07:08 to the release at 07:30, held at 25% after two stages" \
    "$t10"' | .flow[11] == {node: "canary", gate: null, verdict: "GO", started_at: "2026-11-02T07:08:00Z",
      ended_at: "2026-11-02T07:30:00Z", seconds: 1320, line: "held at 25%: 2 stage(s) GO"}' "$1" || return 1
  h16 "0010's human gate and 100%: promoted by bob via cli at 07:45" \
    "$t10"' | .flow[12:] == [{node: "human", gate: null, verdict: "GO", started_at: "2026-11-02T07:30:00Z",
      ended_at: "2026-11-02T07:45:00Z", seconds: 900, line: "promoted by bob via cli"},
      {node: "full", gate: null, verdict: "GO", started_at: "2026-11-02T07:45:00Z", ended_at: "2026-11-02T07:45:00Z",
       seconds: null, line: "100% of the traffic on 0.2.5-n"}]' "$1" || return 1
  h16 "0003's gates: five GO, test NO-GO with its line and 41 s, mutation and test-proof not reached; nothing after" \
    "$t03"' | [.flow[2:][] | [.verdict, .line, .seconds]]
      == [["GO", "GO: risk — low, auto-merge", 1], ["GO", "GO: secrets", 3], ["GO", "GO: test-weakening", 1], ["GO", "GO: deps", 2],
          ["GO", "GO: sast — in the workbench, none new (0 inherited)", 6], ["NO-GO", "NO-GO: test — 2 failed", 41],
          ["not-reached", "not run: test said NO-GO first", null], ["not-reached", "not run: test said NO-GO first", null],
          ["not-reached", null, null], ["not-reached", null, null], ["not-reached", null, null], ["not-reached", null, null]]' "$1" || return 1
  h16 "0003 and 0010 keep each gate's seconds on ci and on the rework entry" \
    "($t03 | .ci.seconds == \$s3[0].ci.seconds and .rework[0].seconds == \$s3[0].rework[0].seconds)
      and ($t10 | .ci.seconds == \$s10[0].ci.seconds)" "$1" || return 1
  # docs/adr/0086 §1: the stub's six roles under the profile's gemini, the release
  # role's harness the stub's own; 0003's attempt in flight (2,210,000 counted).
  h16 "roles: six rows in the network table's order, configured from the profile and role.env, with their budgets" \
    '(.roles | map(.role)) == ["architect", "dev-worker", "ops-triage", "product", "release", "test-reviewer"]
      and ([.roles[] | [.configured.provider, .configured.model, .configured.harness, .configured.thinking, .budget]]
        == [["gemini", "gemini-3.8-flash", "gemini-cli", "high", 600000], ["gemini", "gemini-3.8-flash", "gemini-cli", "high", 2600000],
            ["gemini", "gemini-3.8-flash", "adk", "low", null], ["gemini", "gemini-3.8-flash", "adk", "medium", null],
            ["gemini", "gemini-3.8-flash", "stub-harness", "low", null], ["gemini", "gemini-3.8-flash", "gemini-cli", "high", 1500000]])' "$1" || return 1
  h16 "the dev-worker runs on 0003 (last: 0003's rework at attempt 2), the test-reviewer last ran on 0010, the four others idle" \
    '(.roles | map({(.role): [.state, .used, .line, (.last.ticket // null), (.last.step // null)]}) | add)
      == {"architect": ["idle", null, "has not run in this record", null, null],
          "dev-worker": ["running", 2210000, "running on 0003: 2,210,000 of 2,600,000 tokens — near the budget", "0003", "rework"],
          "ops-triage": ["idle", null, "has not run in this record", null, null],
          "product": ["idle", null, "has not run in this record", null, null],
          "release": ["idle", null, "has not run in this record", null, null],
          "test-reviewer": ["ran", 30900, "last ran on 0010 (review, attempt 1) at 2026-11-02T06:40:00Z: 30,900 tokens of 1,500,000", "0010", "review"]}
      and (.roles[1].running == {ticket: "0003", tokens: 2210000, budget: 2600000, warn: true})' "$1" || return 1
  h16 "the roles summary" \
    '.roles_summary == "6 roles on 1 provider: gemini; last runs on gemini/gemini-3.8-flash, prov/model-x"' "$1" || return 1
  # docs/adr/0087 §4: 0003 at ci-failed after attempt 2, no error last, 2,210,000 counted, no run.json.
  h16 "0003's attempt 3 in flight, near TOKEN_BUDGET; no other ticket in flight" \
    "$t03"' | .live == {role: "dev-worker", attempt: 3, tokens: 2210000, budget: 2600000, warn: true,
      line: "dev-worker, attempt 3: 2,210,000 of 2,600,000 tokens (85%) — near TOKEN_BUDGET, the run stops at 2,600,000"}' "$1" || return 1
  h16 "no ticket but 0003 has a run in flight" '[.tickets[] | select(.id != "0003") | .live] | all(. == null)' "$1" || return 1
  # docs/adr/0087 §3: the last day with a run is 2026-11-03, 0003's two runs at 08:05 and 08:15.
  h16 "the burn of 2026-11-03: \$1.1625 over an hour, 15.75 h left, \$19.471875 projected, no day limit" \
    '.spend.burn | .day == "2026-11-03" and .first == "2026-11-03T08:05:00Z" and .end == "2026-11-03T08:15:00Z"
      and .runs == 2 and .unknown == 0 and ((.spent_usd - 1.1625) | fabs) < 0.000001 and .hours == 1
      and ((.per_hour_usd - 1.1625) | fabs) < 0.000001 and .left_h == 15.75 and ((.projected_usd - 19.471875) | fabs) < 0.000001
      and .limit_usd == null and .over == null
      and ([.hourly | to_entries[] | select(.value != 0) | [.key, .value]] == [[8, 1.1625]])' "$1" || return 1
  h16 "the burn and the projection lines, before the budget's" \
    '.spend.lines[-3:] == ["burn: $1.16 per hour on 2026-11-03 ($1.16 known in 2 run(s) from 08:05 to 08:15 UTC, over 1.0 h)",
      "projection: $19.47 by the end of 2026-11-03 UTC at that rate, as of 08:15 UTC; SPEND_BUDGET_DAY_USD no limit",
      "budget: SPEND_BUDGET_TICKET_USD=$1.00, SPEND_BUDGET_DAY_USD no limit"]' "$1" || return 1
}
bad=$(hand_16 "$root/out1.json") || nogo "the 1.6 fields over the stub sources: $bad" \
  "$(jq -c '{flow: [.tickets[] | select(.id == "0010" or .id == "0003") | {id, flow, live}], roles, roles_summary, burn: .spend.burn}' "$root/out1.json" 2>&1)"
plant16() {
  jq "$2" "$root/out1.json" > "$tmp/plant.json"
  if hand_16 "$tmp/plant.json" >/dev/null; then
    nogo "the hand checks of the 1.6 flow accept a logbook with $1; a gate that cannot fail is not a gate"
  fi
}
plant16 "two gates swapped" "$(tix 0010 '.flow |= (.[0:2] + [.[3], .[2]] + .[4:])')"
plant16 "0010's perimeter timed from the clock" "$(tix 0010 '.flow[1].seconds = 299')"
plant16 "0010's pr line reworded" "$(tix 0010 '.flow[10].line = "merged m10"')"
plant16 "0010's canary running" "$(tix 0010 '.flow[11].verdict = "running"')"
plant16 "0010's human answer by someone else" "$(tix 0010 '.flow[12].line = "promoted by eve via cli"')"
plant16 "0003's mutation reached" "$(tix 0003 '.flow[8].verdict = "GO"')"
plant16 "0003's test seconds lost" "$(tix 0003 '.flow[7].seconds = null')"
plant16 "0003's rework seconds dropped" "$(tix 0003 'del(.rework[0].seconds)')"
plant16 "two roles swapped" '.roles |= ([.[1], .[0]] + .[2:])'
plant16 "the dev-worker's model from another profile" '.roles[1].configured.model = "other"'
plant16 "the dev-worker idle" '.roles[1] |= (.state = "ran" | .running = null)'
plant16 "the test-reviewer's tokens summed over its ticket" '.roles[5].used = 72000'
plant16 "the summary counting two providers" '.roles_summary = "6 roles on 2 providers: gemini, prov"'
plant16 "0003's run in flight under the warning" "$(tix 0003 '.live.warn = false')"
plant16 "a run in flight on 0005" "$(tix 0005 '.live = {role: "dev-worker", attempt: 1, tokens: 1, budget: 2600000, warn: false, line: "x"}')"
plant16 "the burn over ten minutes" '.spend.burn.hours = 0.166667'
plant16 "the projection from a clock" '.spend.burn.left_h = 12'
plant16 "the projection line after the budget" '.spend.lines |= (.[0:-3] + [.[-2], .[-1], .[-3]])'

# The 1.7 intakes (docs/adr/0090 §7), written down by hand from the stub's
# tower documents and its two *.intake.md files; the reader's code is not
# read. Intake 0011, by client@example.com via the console on 2026-11-06,
# one question answered, two proposed tickets with the architect not
# consulted, the first confirmed as 0009 and the second dropped by
# owner@example.com via cli; intake 0013 waits on its open question. Neither
# is a ticket, a card or a line of the spend; ticket 0009 names its intake.
h17() {
  jq -e --rawfile w11 "$stub/tickets/0011.intake.md" --rawfile w13 "$stub/tickets/0013.intake.md" \
    "$2" "$3" >/dev/null 2>&1 || { printf '%s\n' "$1"; return 1; }
}
# shellcheck disable=SC2016 # $w11 and $w13 are jq's own variables
hand_17() {
  h17 "the intakes are 0011 and 0013, in that order, with the client's words verbatim" \
    '[.intakes[] | .id] == ["0011", "0013"] and .intakes[0].text == $w11 and .intakes[1].text == $w13' "$1" || return 1
  h17 "0011: confirmed, by client@example.com via console at 09:00, confirmed by owner@example.com via cli" \
    '.intakes[0] | .state == "confirmed" and .by == "client@example.com" and .via == "console" and .at == "2026-11-06T09:00:00Z"
      and .confirmation == {by: "owner@example.com", via: "cli", at: "2026-11-06T09:10:00Z"} and .stranded == null' "$1" || return 1
  h17 "0011: one question, answered via the console" \
    '.intakes[0].questions | length == 1 and .[0].from == "product" and .[0].answer == "The first one waiting gets it." and .[0].answered_via == "console"' "$1" || return 1
  h17 "0011: two proposed tickets, 1 confirmed as 0009, 2 dropped; the tickets are [0009]" \
    '.intakes[0] | [.proposal[] | [.n, .decision, .ticket]] == [[1, "confirm", "0009"], [2, "drop", null]] and .tickets == ["0009"]' "$1" || return 1
  h17 "0011: each proposed ticket checked by the gate, perimeter empty, the architect not consulted" \
    '.intakes[0] | ([.proposal[] | .check] == ["NO-GO: spec — ## Perimeter declares no path", "NO-GO: spec — ## Perimeter declares no path"])
      and ([.proposal[] | .perimeter] == [[], []]) and .architect.consulted == false and (.architect.why | length > 0) and .product.consulted == true' "$1" || return 1
  h17 "0013: at intake-questions, its one question open, nothing proposed or confirmed" \
    '.intakes[1] | .state == "intake-questions" and (.questions | length == 1 and .[0].answer == null) and .proposal == [] and .confirmation == null and .tickets == []' "$1" || return 1
  h17 "no intake is a ticket or a card, and only 0009 names an intake" \
    '([.tickets[] | .id] | index("0011") == null and index("0013") == null) and ([.kanban[].tickets[]] | index("0011") == null and index("0013") == null)
      and ([.tickets[] | select(.intake != null) | [.id, .intake]] == [["0009", "0011"]])' "$1" || return 1
  h17 "the intakes, which ran no recorded spend here, are no line of the spend" \
    '(.spend.tickets | has("0011") | not) and (.spend.tickets | has("0013") | not)' "$1" || return 1
}
bad=$(hand_17 "$root/out1.json") || nogo "the 1.7 intakes over the stub sources: $bad" "$(jq -c '{intakes}' "$root/out1.json" 2>&1)"
plant17() {
  jq "$2" "$root/out1.json" > "$tmp/plant.json"
  if hand_17 "$tmp/plant.json" >/dev/null; then
    nogo "the hand checks of the 1.7 intakes accept a logbook with $1; a gate that cannot fail is not a gate"
  fi
}
plant17 "the intakes swapped" '.intakes |= [.[1], .[0]]'
plant17 "the client's words trimmed" '.intakes[0].text |= .[0:20]'
plant17 "the confirmation by someone else" '.intakes[0].confirmation.by = "eve"'
plant17 "the dropped ticket kept" '.intakes[0].proposal[1].decision = "confirm"'
plant17 "a check line reworded" '.intakes[0].proposal[0].check = "GO: spec"'
plant17 "the open question answered" '.intakes[1].questions[0].answer = "yes"'
plant17 "an intake as a card" '.kanban[0].tickets += ["0013"]'
plant17 "0009 without its intake" '(.tickets[] | select(.id == "0009") | .intake) = null'
plant17 "an intake as a line of the spend" '.spend.tickets["0011"] = {runs: 0}'

# The 1.8 additions (docs/adr/0091 §9), written down by hand from the stub's
# tower documents: ticket 0001's body passed the screen, ticket 0010's was
# flagged, intake 0013's words were flagged; every other ticket and intake 0011
# were never screened. No proposal was asked file by file: `uncertain` and the
# architect's `by` are null. The sha256 is of each text file, worked out here.
h18() {
  jq -e --arg s1 "$(sha256sum "$stub/tickets/0001.md" | cut -d' ' -f1)" \
    --arg s10 "$(sha256sum "$stub/tickets/0010.md" | cut -d' ' -f1)" \
    --arg s13 "$(sha256sum "$stub/tickets/0013.intake.md" | cut -d' ' -f1)" \
    "$2" "$3" >/dev/null 2>&1 || { printf '%s\n' "$1"; return 1; }
}
# shellcheck disable=SC2016 # $s1, $s10 and $s13 are jq's own variables
hand_18() {
  h18 "0001 passed, 0010 flagged, every other ticket never screened" \
    '[.tickets[] | select(.screen != null) | [.id, .screen.verdict]] == [["0001", "pass"], ["0010", "flag"]]' "$1" || return 1
  h18 "0001 and 0010: the screen's numbers, the replay and the sha256 of each body" \
    '[.tickets[] | select(.id == "0001" or .id == "0010") | .screen | [.p_yes, .confidence, .adapter, .replayed, .sha256]]
      == [[0.01, 0.99, "replay", true, $s1], [0.64, 0.64, "replay", true, $s10]]' "$1" || return 1
  h18 "intake 0013 flagged with the sha256 of its words, intake 0011 never screened" \
    '(.intakes[1].screen | .verdict == "flag" and .p_yes == 0.62 and .sha256 == $s13) and .intakes[0].screen == null' "$1" || return 1
  h18 "no proposal asked file by file: uncertain and the architect's by are null" \
    '([.intakes[] | .proposal[] | .uncertain] | all(. == null)) and .intakes[0].architect.by == null' "$1" || return 1
}
bad=$(hand_18 "$root/out1.json") || nogo "the 1.8 screens over the stub sources: $bad" "$(jq -c '[.tickets[] | {id, screen}], [.intakes[] | {id, screen}]' "$root/out1.json" 2>&1)"
plant18() {
  jq "$2" "$root/out1.json" > "$tmp/plant.json"
  if hand_18 "$tmp/plant.json" >/dev/null; then
    nogo "the hand checks of the 1.8 screens accept a logbook with $1; a gate that cannot fail is not a gate"
  fi
}
plant18 "the flag read as a pass" '(.tickets[] | select(.id == "0010") | .screen.verdict) = "pass"'
plant18 "a screen on a ticket never screened" '(.tickets[] | select(.id == "0002") | .screen) = {verdict: "pass"}'
plant18 "the intake's screen dropped" '.intakes[1].screen = null'
plant18 "another text's sha256" '(.tickets[] | select(.id == "0001") | .screen.sha256) = "0"'
# A proposal asked file by file: the architect's by and questions, each
# ticket's uncertain paths, read from the document as they are.
cp "$root/state/tickets.0011.json" "$tmp/0011.keep"
jq '.architect += {by: "decide", questions: 150, consulted: true} | .proposal[0].uncertain = ["app/frontend/src/api.ts", "app/backend/meetups/auth.py"] | .proposal[1].uncertain = []' \
  "$tmp/0011.keep" > "$root/state/tickets.0011.json"
var_case "a proposal asked file by file" '.intakes[0] | .architect.by == "decide" and .architect.questions == 150
  and .proposal[0].uncertain == ["app/frontend/src/api.ts", "app/backend/meetups/auth.py"] and .proposal[1].uncertain == []'
cp "$tmp/0011.keep" "$root/state/tickets.0011.json"

cp "$root/state/tickets.0009.json" "$tmp/0009.keep"
jq '.history[0] += {by: "ada@example.org", via: "console"}' "$tmp/0009.keep" > "$root/state/tickets.0009.json"
var_case "a request by ada via the console" '[.tickets[] | select(.id == "0009") | .timeline[] | select(.phase == "request")]
  | length == 1 and .[0].by == "ada@example.org" and .[0].via == "console"'
cp "$tmp/0009.keep" "$root/state/tickets.0009.json"

# --- b. the golden against every committed schema of its major ---------------
for n in 0 1 2 3 4 5 6 7 8; do
  [ -f "core/logbook/1.$n.schema.json" ] || nogo "core/logbook/1.$n.schema.json not found: the golden validates against 1.0 to 1.8"
done
chk valid "$golden" >"$tmp/chk.out" || nogo "the golden does not validate" "$(cat "$tmp/chk.out")"
# The old-logbook fixtures stay valid against their own major's schemas, untouched.
chk valid "$golden17" >"$tmp/chk.out" || nogo "golden-1.7.json (the old-logbook fixture) does not validate" "$(cat "$tmp/chk.out")"
[ "$(jq -r .schema "$golden17")" = "lights-out/logbook/1.7" ] || nogo "golden-1.7.json is not a 1.7 logbook: the old fixture was changed"
chk valid "$golden16" >"$tmp/chk.out" || nogo "golden-1.6.json (the old-logbook fixture) does not validate" "$(cat "$tmp/chk.out")"
[ "$(jq -r .schema "$golden16")" = "lights-out/logbook/1.6" ] || nogo "golden-1.6.json is not a 1.6 logbook: the old fixture was changed"
chk valid "$golden15" >"$tmp/chk.out" || nogo "golden-1.5.json (the old-logbook fixture) does not validate" "$(cat "$tmp/chk.out")"
[ "$(jq -r .schema "$golden15")" = "lights-out/logbook/1.5" ] || nogo "golden-1.5.json is not a 1.5 logbook: the old fixture was changed"
chk valid "$golden14" >"$tmp/chk.out" || nogo "golden-1.4.json (the old-logbook fixture) does not validate" "$(cat "$tmp/chk.out")"
[ "$(jq -r .schema "$golden14")" = "lights-out/logbook/1.4" ] || nogo "golden-1.4.json is not a 1.4 logbook: the old fixture was changed"
chk valid "$golden13" >"$tmp/chk.out" || nogo "golden-1.3.json (the old-logbook fixture) does not validate" "$(cat "$tmp/chk.out")"
chk valid "$golden12" >"$tmp/chk.out" || nogo "golden-1.2.json (the old-logbook fixture) does not validate" "$(cat "$tmp/chk.out")"
[ "$(jq -r .schema "$golden13")" = "lights-out/logbook/1.3" ] || nogo "golden-1.3.json is not a 1.3 logbook: the old fixture was changed"
[ "$(jq -r .schema "$golden12")" = "lights-out/logbook/1.2" ] || nogo "golden-1.2.json is not a 1.2 logbook: the old fixture was changed"

# --- c. the committed snapshot -----------------------------------------------
if [ -f console/snapshot/logbook.json ]; then
  chk snapshot console/snapshot/logbook.json tickets >"$tmp/chk.out" \
    || nogo "the committed snapshot is not valid or has drifted" "$(cat "$tmp/chk.out")"
elif [ -f console/snapshot/README.md ]; then
  nogo "console/snapshot/README.md exists but console/snapshot/logbook.json does not; a README without the file is drift"
else
  printf 'ci-logbook: skipped the snapshot check: console/snapshot/ has no logbook.json and no README.md\n' >&2
fi

# --- d. the console's perimeter ----------------------------------------------
[ -f console/serve.py ] || nogo "console/serve.py not found"
[ -f console/build.py ] || nogo "console/build.py not found"
[ -d console/src ] || nogo "console/src not found"
[ -f console/index.html ] || nogo "console/index.html not found"
printf 'drifted\n' > "$st/drifted-index.html"
if python3 -B console/build.py --src console/src --out "$st/drifted-index.html" --check >/dev/null 2>&1; then
  nogo "console/build.py --check accepted a drifted output; a gate that cannot fail is not a gate"
fi
python3 -B console/build.py --check >"$tmp/chk.out" 2>"$tmp/chk.err" \
  || nogo "console/index.html has drifted from console/src/ (run python3 console/build.py --write)" "$(cat "$tmp/chk.out")" "$(cat "$tmp/chk.err")"
chk names console >"$tmp/chk.out" || nogo "console/ names what it must not" "$(cat "$tmp/chk.out")"
chk serve console/serve.py >"$tmp/chk.out" || nogo "console/serve.py is outside its perimeter" "$(cat "$tmp/chk.out")"
chk index console/index.html >"$tmp/chk.out" || nogo "console/index.html is outside its perimeter" "$(cat "$tmp/chk.out")"
chk machine console/index.html >"$tmp/chk.out" || nogo "console/index.html's tower table disagrees with core/tower.py or core/logbook.py" "$(cat "$tmp/chk.out")"
chk words console/index.html "$repo" >"$tmp/chk.out" || nogo "console/index.html's network-words disagree with core/network.py --table, roles/ or the Makefile's GATES" "$(cat "$tmp/chk.out")"

# `./go network` (docs/adr/0071 §4): the recipe over a given logbook runs no
# reader and prints on stdout what core/network.py prints, the view and nothing
# else; TICKET and ATTEMPT reach the view. The check can fail: the view of
# another ticket is not the default's.
# Without the TICKET a calling make exports (its `TICKET ?= 0001`): the recipe
# passes on only a ticket its caller gave, and the default is the view's own.
net_make() { env -u TICKET -u ATTEMPT make --no-print-directory network NETWORK_LOGBOOK="$golden" "$@" 2>"$tmp/net-make.err"; }
net_make > "$tmp/net-make.out" || nogo "make network NETWORK_LOGBOOK=<golden> failed" "$(cat "$tmp/net-make.err")"
python3 core/network.py --logbook "$golden" > "$tmp/net-py.out" 2>"$tmp/net-py.err" || nogo "core/network.py --logbook <golden> failed" "$(cat "$tmp/net-py.err")"
[ -s "$tmp/net-py.out" ] || nogo "core/network.py printed nothing over the golden"
cmp -s "$tmp/net-make.out" "$tmp/net-py.out" \
  || nogo "make network NETWORK_LOGBOOK=<golden> does not print what core/network.py prints" "$(diff "$tmp/net-py.out" "$tmp/net-make.out" | head -20)"
net_make TICKET=0001 ATTEMPT=1 > "$tmp/net-make-1.out" || nogo "make network TICKET=0001 ATTEMPT=1 failed" "$(cat "$tmp/net-make.err")"
python3 core/network.py --logbook "$golden" --ticket 0001 --attempt 1 > "$tmp/net-py-1.out" 2>"$tmp/net-py.err" \
  || nogo "core/network.py --ticket 0001 --attempt 1 failed" "$(cat "$tmp/net-py.err")"
cmp -s "$tmp/net-make-1.out" "$tmp/net-py-1.out" \
  || nogo "make network TICKET=0001 ATTEMPT=1 does not print what core/network.py --ticket 0001 --attempt 1 prints" "$(diff "$tmp/net-py-1.out" "$tmp/net-make-1.out" | head -20)"
! cmp -s "$tmp/net-py.out" "$tmp/net-py-1.out" || nogo "the view of ticket 0001 is the default view; TICKET changes nothing, so the check above proves nothing"

# --- e. the console at runtime -----------------------------------------------
free_port() {
  python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'
}

# start_console DIR LOG VAR=val...: console/serve.py from DIR on a free port
# ($port, $pid). Waits for any answer, not for a good one.
start_console() {
  sc_dir=$1
  sc_log=$2
  shift 2
  port=$(free_port)
  (cd "$sc_dir" && exec env CONSOLE_LISTEN="$port" CONSOLE_PORT="$port" "$@" python3 console/serve.py 2>"$sc_log" </dev/null) &
  pid=$!
  pids="$pids $pid"
  sc_i=0
  while :; do
    sc_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$port/" 2>/dev/null) || sc_code=000
    [ "$sc_code" != 000 ] && break
    kill -0 "$pid" 2>/dev/null || nogo "console/serve.py exited before it answered" "$(cat "$sc_log")"
    sc_i=$((sc_i + 1))
    [ "$sc_i" -lt 75 ] || nogo "console/serve.py did not answer on :$port" "$(cat "$sc_log")"
    sleep 0.2
  done
}
stop_console() {
  kill "$pid" 2>/dev/null || :
  wait "$pid" 2>/dev/null || :
}

# req [curl args...]: sets $code and $body (a file; empty when the answer has
# no body, a 304 for one, which curl does not write at all).
req() {
  : > "$tmp/body"
  code=$(curl -s -o "$tmp/body" -w '%{http_code}' --max-time 60 "$@") || code=000
  body="$tmp/body"
}
base() { printf 'http://127.0.0.1:%s' "$port"; }

# press ANSWER VERSION HELD_AT SHA: the POST, every header right unless the
# caller sets PO (the Origin) or PH (one more header, which may be Host or an
# identity) in front of the call.
press() {
  p_body=$(printf '{"answer":"%s","version":"%s","held_at":%s,"brief_sha256":"%s"}' "$1" "$2" "$3" "$4")
  req -X POST -H 'Content-Type: application/json' -H "Origin: ${PO:-http://127.0.0.1:$port}" \
    -H "${PH:-X-Noop: 1}" -d "$p_body" "$(base)/v1/go-for-launch"
}

# press_why ANSWER VERSION HELD_AT SHA EXTRA: the same POST, with EXTRA (raw
# JSON members such as ',"reason":"x"', or nothing) added to the body.
press_why() {
  p_body=$(printf '{"answer":"%s","version":"%s","held_at":%s,"brief_sha256":"%s"%s}' "$1" "$2" "$3" "$4" "$5")
  req -X POST -H 'Content-Type: application/json' -H "Origin: ${PO:-http://127.0.0.1:$port}" \
    -H "${PH:-X-Noop: 1}" -d "$p_body" "$(base)/v1/go-for-launch"
}

# reply TICKET QUESTION TEXT: the answer POST, every header right unless PO
# or PH is set, as for press.
reply() {
  r_body=$(jq -cn --arg t "$1" --argjson q "$2" --arg a "$3" '{ticket: $t, question: $q, answer: $a}')
  req -X POST -H 'Content-Type: application/json' -H "Origin: ${PO:-http://127.0.0.1:$port}" \
    -H "${PH:-X-Noop: 1}" -d "$r_body" "$(base)/v1/answer"
}

# post_json PATH BODY: the POST, every header right unless PO (the Origin) or
# PH (one more header: a Host, an identity) is set, as for press.
post_json() {
  printf '%s' "$2" > "$tmp/post.json"
  req -X POST -H 'Content-Type: application/json' -H "Origin: ${PO:-http://127.0.0.1:$port}" \
    -H "${PH:-X-Noop: 1}" --data-binary "@$tmp/post.json" "$(base)$1"
}

# The editor's request (docs/adr/0059 §4): one draft the spec gate passes, and
# edit EDIT, a jq filter, to break one rule of ADR 0055 §4 in it.
draft_go='{"title":"A waiting list when a meetup is full","context":"A full meetup refuses every further signup.","criteria":[{"given":"a meetup at capacity","when":"a new member signs up","then":"they join the waiting list and see their position"}],"perimeter":["app/backend/waitlist/*"],"feature":"waiting-list","priority":"P1"}'
draft() { printf '%s' "$draft_go" | jq -c "$1"; }

# parity NAME WANT EDIT: /v1/spec-check on the draft EDIT makes answers
# {ticket, markdown, line}, and line is the very line gates/spec.sh prints on
# that Markdown, run in $gate_root; WANT is its verdict, GO or NO-GO, or `-`
# when the draft leaves the form of an empty field to the draft (a bare
# `Given` or none): parity only. The id is the next in $ticket_dir.
parity() {
  pa_name=$1
  pa_want=$2
  pa_req=$(draft "$3")
  post_json /v1/spec-check "$pa_req"
  [ "$code" = 200 ] || nogo "spec check ($pa_name): answered $code, want 200" "$(cat "$body")" "$pa_req"
  jq -e '(.ticket | type == "string") and (.markdown | type == "string") and (.line | type == "string")' "$body" >/dev/null \
    || nogo "spec check ($pa_name): the answer is not {ticket, markdown, line}" "$(cat "$body")"
  jq -j '.markdown' "$body" > "$tmp/draft.md"
  pa_have=$(jq -r '.line' "$body")
  pa_gate=$(gate_line "$gate_root" "$tmp/draft.md")
  [ "$pa_have" = "$pa_gate" ] \
    || nogo "spec check ($pa_name): the line is '$pa_have', gates/spec.sh says '$pa_gate' on the Markdown it was given" "$(cat "$tmp/draft.md")"
  case "$pa_have" in
    "GO: spec") pa_is=GO ;;
    "NO-GO: spec — "?*) pa_is=NO-GO ;;
    *) nogo "spec check ($pa_name): '$pa_have' is not a gate line" ;;
  esac
  [ "$pa_want" = - ] || [ "$pa_is" = "$pa_want" ] \
    || nogo "spec check ($pa_name): the verdict is $pa_is ($pa_have), want $pa_want" "$(cat "$tmp/draft.md")"
  pa_id=$(next_id "$ticket_dir")
  jq -e --arg id "$pa_id" '.ticket == $id' "$body" >/dev/null \
    || nogo "spec check ($pa_name): the ticket is $(jq -r .ticket "$body"), want the next id, $pa_id"
  [ "$(head -n 1 "$tmp/draft.md")" = "# $pa_id — $(printf '%s' "$pa_req" | jq -r .title)" ] \
    || nogo "spec check ($pa_name): the Markdown does not start with '# $pa_id — <title>'" "$(head -n 3 "$tmp/draft.md")"
}
# The drafts: a GO one and a NO-GO one per rule of ADR 0055 §4 (a spec with no
# `## Acceptance criteria` section at all cannot come from the editor).
spec_batch() {
  parity "a draft the gate passes" GO '.'
  parity "no context, feature or priority" GO 'del(.context, .feature, .priority)'
  parity "a title that is not ASCII" GO '.title = "Più posti — non sempre"'
  parity "two criteria" GO '.criteria += [{"given": "a waiting list with one member", "when": "an attendee cancels", "then": "the first member on the list becomes an attendee"}]'
  parity "no criterion" NO-GO '.criteria = []'
  parity "a Then with no outcome" NO-GO '.criteria[0].then = ""'
  parity "a vague outcome" NO-GO '.criteria[0].then = "it works"'
  parity "a vague outcome in capitals" NO-GO '.criteria[0].then = "The page loads FAST"'
  parity "a second criterion that is vague" NO-GO '.criteria += [{"given": "x", "when": "y", "then": "it is handled correctly"}]'
  parity "no perimeter path" NO-GO '.perimeter = []'
  parity "a Feature that is not a slug" NO-GO '.feature = "Waiting List"'
  parity "a Priority that is not P0 to P3" NO-GO '.priority = "urgent"'
  parity "an empty Given" - '.criteria[0].given = ""'
  parity "an empty When" - '.criteria[0].when = ""'
  parity "a Given that ends its paragraph" - '.criteria[0].given = "a meetup\n\nWhen it is full"'
}
# body_refusals PATH: bodies the console turns away with 400 before it runs
# anything, on the two routes that take the editor's fields.
body_refusals() {
  for br_body in '{"context":"no title"}' '{"title":5}' '{"title":null}' '[]' '"title"' 'null' 'not json' ''; do
    post_json "$1" "$br_body"
    [ "$code" = 400 ] || nogo "$1: the body '$br_body' answered $code, want 400" "$(cat "$body")"
  done
  br_big=$(head -c 20000 /dev/zero | tr '\0' x)
  post_json "$1" "{\"title\":\"too big\",\"context\":\"$br_big\"}"
  [ "$code" = 400 ] || nogo "$1: a body over 16384 bytes answered $code, want 400" "$(head -c 300 "$body")"
}
# route_refusals PATH: the checks of every POST, before the body is read.
route_refusals() {
  req -X POST -H 'Content-Type: text/plain' -H "Origin: http://127.0.0.1:$port" -d "$draft_go" "$(base)$1"
  [ "$code" = 415 ] || nogo "$1: a POST with Content-Type text/plain answered $code, want 415"
  req -X POST -H "Origin: http://127.0.0.1:$port" --data-binary "$draft_go" -H 'Content-Type:' "$(base)$1"
  [ "$code" = 415 ] || nogo "$1: a POST with no Content-Type answered $code, want 415"
  PO=http://evil.invalid post_json "$1" "$draft_go"
  [ "$code" = 403 ] || nogo "$1: a POST with a foreign Origin answered $code, want 403"
  PH='Host: evil.invalid' post_json "$1" "$draft_go"
  [ "$code" = 403 ] || nogo "$1: a POST with a foreign Host answered $code, want 403"
}
# etag_checks WHAT: /v1/logbook.json carries the sha256 of its bytes as its
# ETag, answers 304 and no body to the same tag, and 200 to another.
etag_checks() {
  curl -s -D "$tmp/hdr" -o "$tmp/body" --max-time 10 "$(base)/v1/logbook.json" || nogo "$1: GET /v1/logbook.json failed"
  et_tag=$(sed -n 's/^[Ee][Tt][Aa][Gg]: *//p' "$tmp/hdr" | tr -d '\r')
  et_want="\"$(sha256sum "$tmp/body" | cut -d' ' -f1)\""
  [ "$et_tag" = "$et_want" ] || nogo "$1: the ETag is '$et_tag', want $et_want, the sha256 of the bytes between quotes" "$(cat "$tmp/hdr")"
  req -H "If-None-Match: $et_tag" "$(base)/v1/logbook.json"
  [ "$code" = 304 ] || nogo "$1: a matching If-None-Match answered $code, want 304"
  [ ! -s "$body" ] || nogo "$1: the 304 carries a body"
  req -H 'If-None-Match: "0000"' "$(base)/v1/logbook.json"
  [ "$code" = 200 ] || nogo "$1: another If-None-Match answered $code, want 200"
  [ -s "$body" ] || nogo "$1: the 200 to another If-None-Match has no body"
}

# --- e1. snapshot mode, with a tripwire where the scripts would be ----------
# The console's temporary files go to $tmp/ctmp, to see the spec check delete
# its own.
mkdir -p "$tmp/ctmp"
trip="$tmp/trip"
mkdir -p "$trip/core" "$trip/fixtures/logbook" "$trip/gates" "$trip/ci"
cp -R "$repo/console" "$trip/console"
cp "$golden" "$trip/fixtures/logbook/golden-1.8.json"
for s in logbook promote; do
  # shellcheck disable=SC2016 # the tripwire script expands its own $d
  printf '#!/bin/sh\nd=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\necho "$0" >> "$d/ran"\nexit 1\n' > "$trip/core/$s.sh"
done
# The spec check runs the gate and the draft, and nothing else of core/.
for f in "$repo"/core/*.py; do ln -s "$f" "$trip/core/$(basename "$f")"; done
cp "$repo/gates/spec.sh" "$trip/gates/spec.sh"
cp "$repo/ci/lib.sh" "$trip/ci/lib.sh"
cp -R "$stub/tickets" "$trip/tickets"
start_console "$trip" "$tmp/snap.log" CONSOLE_SNAPSHOT=fixtures/logbook/golden-1.8.json TMPDIR="$tmp/ctmp"
req "$(base)/"
[ "$code" = 200 ] || nogo "snapshot mode: GET / answered $code, want 200" "$(cat "$tmp/snap.log")"
cmp -s "$body" "$repo/console/index.html" || nogo "snapshot mode: GET / is not console/index.html"
req "$(base)/v1/logbook.json"
[ "$code" = 200 ] || nogo "snapshot mode: GET /v1/logbook.json answered $code, want 200"
cmp -s "$body" "$golden" || nogo "snapshot mode: GET /v1/logbook.json is not the snapshot file, byte for byte"
req "$(base)/v1/console.json"
[ "$code" = 200 ] || nogo "snapshot mode: GET /v1/console.json answered $code, want 200"
jq -e '.mode == "snapshot" and .can_launch == false and (.reason | type == "string" and length > 0)' "$body" >/dev/null \
  || nogo "snapshot mode: /v1/console.json is not {mode: snapshot, can_launch: false, reason}" "$(cat "$body")"
curl -s -D "$tmp/sse.hdr" -o "$tmp/sse.body" --max-time 5 "$(base)/v1/events" || nogo "snapshot mode: GET /v1/events failed or blocked"
grep -qi '^Content-Type: text/event-stream' "$tmp/sse.hdr" || nogo "snapshot mode: GET /v1/events is not text/event-stream" "$(cat "$tmp/sse.hdr")"
grep -qi '^Cache-Control: no-store' "$tmp/sse.hdr" || nogo "snapshot mode: GET /v1/events lacks Cache-Control: no-store" "$(cat "$tmp/sse.hdr")"
grep -qi '^X-Content-Type-Options: nosniff' "$tmp/sse.hdr" || nogo "snapshot mode: GET /v1/events lacks X-Content-Type-Options: nosniff" "$(cat "$tmp/sse.hdr")"
grep -q '^event: logbook$' "$tmp/sse.body" || nogo "snapshot mode: GET /v1/events did not emit event: logbook" "$(cat "$tmp/sse.body")"
sse_want="\"$(sha256sum "$golden" | cut -d' ' -f1)\""
sed -n 's/^data: *//p' "$tmp/sse.body" | jq -e --arg tag "$sse_want" '.mode == "snapshot" and .etag == $tag and .read_at == null' >/dev/null \
  || nogo "snapshot mode: /v1/events data is not {etag, mode: snapshot, read_at: null}" "$(cat "$tmp/sse.body")"
want_sha=$(jq -r '.launch.brief.sha256' "$golden")
press GO 0.5.0-d 25 "$want_sha"
[ "$code" = 403 ] || nogo "snapshot mode: a correct POST answered $code, want 403" "$(cat "$body")"
press PREPARE 0.5.0-d 25 "$want_sha"
[ "$code" = 403 ] || nogo "snapshot mode: a PREPARE POST answered $code, want 403" "$(cat "$body")"
reply 0009 1 "a perimeter"
[ "$code" = 403 ] || nogo "snapshot mode: an answer POST answered $code, want 403" "$(cat "$body")"
for path in /v1/logbook.json /v1/console.json /v1/events /; do
  req -X POST -H 'Content-Type: application/json' -H "Origin: http://127.0.0.1:$port" -d '{}' "$(base)$path"
  case "$code" in 404 | 405) : ;; *) nogo "POST $path answered $code, want 404 or 405: the six POST routes are the writes" ;; esac
done
for verb in PUT DELETE PATCH; do
  req -X "$verb" -H 'Content-Type: application/json' -H "Origin: http://127.0.0.1:$port" -d '{}' "$(base)/v1/go-for-launch"
  case "$code" in 2*) nogo "$verb /v1/go-for-launch answered $code; POST is the only write" ;; esac
done
etag_checks "snapshot mode"

# docs/adr/0059 §4 in snapshot mode: the spec check runs (it runs no plane and
# writes nothing), on every draft the gate judges, with the gate's own line;
# a request is refused.
gate_root=$trip
ticket_dir=$trip/tickets
snap_digest=$(tree_digest "$trip")
snap_ctmp=$(ls "$tmp/ctmp")
spec_batch
body_refusals /v1/spec-check
route_refusals /v1/spec-check
for sc_body in '{"title":"x","criteria":"Given a, when b, then c"}' '{"title":"x","criteria":["Given a, when b, then c"]}' \
  '{"title":"x","perimeter":"app/*"}' '{"title":"x","perimeter":[3]}'; do
  post_json /v1/spec-check "$sc_body"
  [ "$code" = 400 ] || nogo "spec check: the fields $sc_body answered $code, want 400 (the draft refuses them)" "$(cat "$body")"
  jq -e '.error | type == "string" and length > 0' "$body" >/dev/null || nogo "spec check: a refused draft's answer is not {error}" "$(cat "$body")"
done
sc_pad=$(head -c 16000 /dev/zero | tr '\0' x)
post_json /v1/spec-check "{\"title\":\"long\",\"context\":\"$sc_pad\"}"
[ "$code" = 200 ] || nogo "spec check: a body of 16000 bytes, under the limit of 16384, answered $code, want 200" "$(head -c 300 "$body")"
post_json /v1/request "$draft_go"
[ "$code" = 403 ] || nogo "snapshot mode: a request answered $code, want 403" "$(cat "$body")"
[ "$(tree_digest "$trip")" = "$snap_digest" ] || nogo "snapshot mode: the spec check or a request changed the files under the root"
[ "$(ls "$tmp/ctmp")" = "$snap_ctmp" ] || nogo "the spec check left its temporary files behind: $(ls "$tmp/ctmp")"
[ ! -e "$trip/ran" ] || nogo "snapshot mode started $(cat "$trip/ran"); it must run nothing"
stop_console

# Public: the page and the logbook, nothing that runs a process per request.
mkdir -p "$trip/console/snapshot"
cp "$golden" "$trip/console/snapshot/logbook.json"
public_digest=$(tree_digest "$trip")
start_console "$trip" "$tmp/public.log" CONSOLE_AUTH=public CONSOLE_SNAPSHOT=console/snapshot/logbook.json TMPDIR="$tmp/ctmp"
req "$(base)/v1/logbook.json"
[ "$code" = 200 ] || nogo "public mode: GET /v1/logbook.json answered $code, want 200" "$(cat "$tmp/public.log")"
post_json /v1/spec-check "$draft_go"
[ "$code" = 403 ] || nogo "public mode: a spec check answered $code, want 403" "$(cat "$body")"
post_json /v1/request "$draft_go"
[ "$code" = 403 ] || nogo "public mode: a request answered $code, want 403" "$(cat "$body")"
[ "$(tree_digest "$trip")" = "$public_digest" ] || nogo "public mode: a POST changed the files under the root"
[ ! -e "$trip/ran" ] || nogo "public mode started $(cat "$trip/ran"); it must run nothing"
stop_console

# --- e2. the start-up refusals: one stderr line naming the key ---------------
# refuse KEY VAR=val...: exit non-zero within a few seconds, stderr one line
# that names KEY.
refuse() {
  rf_key=$1
  shift
  rf_port=$(free_port)
  rf_rc=0
  env CONSOLE_LISTEN="$rf_port" CONSOLE_PORT="$rf_port" "$@" timeout 8 python3 console/serve.py >/dev/null 2>"$tmp/refuse.err" </dev/null || rf_rc=$?
  [ "$rf_rc" -ne 0 ] || nogo "console/serve.py started with $* ; want a refusal naming $rf_key"
  [ "$rf_rc" -ne 124 ] || nogo "console/serve.py kept running with $* ; want a refusal naming $rf_key"
  [ "$(grep -c . "$tmp/refuse.err")" -eq 1 ] \
    || nogo "console/serve.py with $* wrote $(grep -c . "$tmp/refuse.err") stderr lines, want exactly one naming $rf_key" "$(cat "$tmp/refuse.err")"
  grep -q "$rf_key" "$tmp/refuse.err" \
    || nogo "the refusal with $* does not name $rf_key" "$(cat "$tmp/refuse.err")"
}
snap="CONSOLE_SNAPSHOT=$golden"
refuse CONSOLE_AUTH "$snap" CONSOLE_AUTH=nonsense
refuse CONSOLE_PUBLISH "$snap" CONSOLE_AUTH=loopback CONSOLE_PUBLISH=0.0.0.0
refuse CONSOLE_IDENTITY_HEADER "$snap" CONSOLE_AUTH=proxy
refuse CONSOLE_SNAPSHOT "$snap" CONSOLE_AUTH=public
printf 'not a logbook\n' > "$tmp/garbage.json"
refuse CONSOLE_SNAPSHOT "CONSOLE_SNAPSHOT=$tmp/garbage.json"
refuse CONSOLE_SNAPSHOT "CONSOLE_SNAPSHOT=$tmp/absent.json"

# --- e3. LIVE mode in the stub root -------------------------------------------
live="$tmp/live"
mkroot "$live"
cp "$live/release.json" "$tmp/release.held.json"
cp "$live/releases.jsonl" "$tmp/releases.held.jsonl"
want_sha=$(sha256sum "$live/promote/brief.md" | cut -d' ' -f1)

# live_env: the sources, absolute, for the console and for promote under it.
live_env() {
  printf '%s\n' LEVEL=l0 WORKLOAD=app CODE_HOST=gitea STATE_STORE=stub-state DELIVERY=stub-delivery \
    TICKETS_DIR=tickets RELEASE_RECORD=release.json RELEASES_LOG=releases.jsonl PROMOTE_OUT=promote \
    POSTMORTEM_DIR=postmortem WORKLOAD_CONTRACT=contract AGENT_HARNESS=replay PLANE_HOST=nowhere.invalid
}
# first_read HEADER...: polls /v1/console.json until the console has read once.
first_read() {
  fr_i=0
  while :; do
    req "$@" "$(base)/v1/console.json"
    if [ "$code" = 200 ] && jq -e '.read_at != null' "$body" >/dev/null 2>&1; then break; fi
    fr_i=$((fr_i + 1))
    [ "$fr_i" -lt 60 ] || nogo "the live console never completed its first read" "$(cat "$body")" "$(cat "$sc_log")"
    sleep 0.5
  done
  jq -e '.mode == "live" and .read_error == null' "$body" >/dev/null \
    || nogo "the live console's first read failed: $(jq -c . "$body")" "$(cat "$sc_log")"
}
# pressable_again: waits for a logbook that says the press is possible.
pressable_again() {
  pa_i=0
  while :; do
    req "$(base)/v1/logbook.json"
    if [ "$code" = 200 ] && jq -e '.launch.pressable == true' "$body" >/dev/null 2>&1; then break; fi
    pa_i=$((pa_i + 1))
    [ "$pa_i" -lt 60 ] || nogo "the live console's logbook never said launch.pressable: $(jq -c .launch.disabled_reason "$body" 2>/dev/null)"
    sleep 0.5
  done
}
untouched() {  # WHAT: nothing moved, nothing recorded
  [ ! -s "$live/effects.log" ] || nogo "$1: the delivery stub was called: $(cat "$live/effects.log")"
  cmp -s "$live/release.json" "$tmp/release.held.json" || nogo "$1: the release record changed"
  cmp -s "$live/releases.jsonl" "$tmp/releases.held.jsonl" || nogo "$1: the release log changed"
}

# shellcheck disable=SC2046 # one VAR=val per line, no spaces in a value
start_console "$live" "$tmp/live.log" $(live_env)
first_read
req "$(base)/v1/logbook.json"
jq -e --arg sha "$want_sha" '.launch.pressable == true and .launch.brief.sha256 == $sha' "$body" >/dev/null \
  || nogo "the live console's logbook does not offer the press the stub sources allow" "$(jq -c .launch "$body" | head -c 600)"
live_tag="\"$(sha256sum "$body" | cut -d' ' -f1)\""
curl -s -D "$tmp/live-sse.hdr" -o "$tmp/live-sse.body" --max-time 5 "$(base)/v1/events?once=1" \
  || nogo "live mode: GET /v1/events?once=1 failed or blocked"
grep -qi '^Content-Type: text/event-stream' "$tmp/live-sse.hdr" || nogo "live mode: GET /v1/events?once=1 is not text/event-stream" "$(cat "$tmp/live-sse.hdr")"
grep -q '^event: logbook$' "$tmp/live-sse.body" || nogo "live mode: GET /v1/events?once=1 did not emit event: logbook" "$(cat "$tmp/live-sse.body")"
sed -n 's/^data: *//p' "$tmp/live-sse.body" | jq -e --arg tag "$live_tag" '.mode == "live" and .etag == $tag and (.read_at | type == "string" and length > 0) and .read_error == null' >/dev/null \
  || nogo "live mode: /v1/events?once=1 data does not match the live logbook" "$(cat "$tmp/live-sse.body")"

req -X POST -H 'Content-Type: text/plain' -H "Origin: http://127.0.0.1:$port" -d '{}' "$(base)/v1/go-for-launch"
[ "$code" = 415 ] || nogo "a POST with Content-Type text/plain answered $code, want 415"
untouched "415 case"
req -X POST -H "Origin: http://127.0.0.1:$port" --data-binary '{}' -H 'Content-Type:' "$(base)/v1/go-for-launch"
[ "$code" = 415 ] || nogo "a POST with no Content-Type answered $code, want 415"
PO=http://evil.invalid press GO 0.5.0-d 25 "$want_sha"
[ "$code" = 403 ] || nogo "a POST with a foreign Origin answered $code, want 403"
untouched "foreign Origin"
PH='Host: evil.invalid' press GO 0.5.0-d 25 "$want_sha"
[ "$code" = 403 ] || nogo "a POST with a foreign Host answered $code, want 403"
untouched "foreign Host"
press GO 0.5.0-d 25 0000000000000000000000000000000000000000000000000000000000000000
[ "$code" = 409 ] || nogo "a POST whose brief_sha256 differs answered $code, want 409"
untouched "another brief"
press GO 0.9.9-x 25 "$want_sha"
[ "$code" = 409 ] || nogo "a POST for another version answered $code, want 409"
untouched "another version"
press GO 0.5.0-d 5 "$want_sha"
[ "$code" = 409 ] || nogo "a POST with another held_at answered $code, want 409"
untouched "another held_at"
press PREPARE 0.5.0-d 25 "$want_sha"
[ "$code" = 409 ] || nogo "PREPARE with a brief already on disk answered $code, want 409 (launch.preparable is false)"
untouched "PREPARE with a brief"
req -X POST -H 'Content-Type: application/json' -H "Origin: http://127.0.0.1:$port" -d 'this is not json' "$(base)/v1/go-for-launch"
[ "$code" = 400 ] || nogo "a POST whose body is not JSON answered $code, want 400"
press MAYBE 0.5.0-d 25 "$want_sha"
[ "$code" = 400 ] || nogo "a POST with answer MAYBE answered $code, want 400"
req -X POST -H 'Content-Type: application/json' -H "Origin: http://127.0.0.1:$port" -d '{"answer":"GO"}' "$(base)/v1/go-for-launch"
[ "$code" = 400 ] || nogo "a POST without version, held_at and brief_sha256 answered $code, want 400"
untouched "malformed bodies"
# docs/adr/0079 §2: a NO-GO needs a reason (missing, empty, blank, null: 400,
# nothing runs); a reason is a string, on a GO too.
press_why NO-GO 0.5.0-d 25 "$want_sha" ''
[ "$code" = 400 ] || nogo "a NO-GO with no reason answered $code, want 400" "$(cat "$body")"
untouched "NO-GO with no reason"
press_why NO-GO 0.5.0-d 25 "$want_sha" ',"reason":""'
[ "$code" = 400 ] || nogo "a NO-GO with an empty reason answered $code, want 400" "$(cat "$body")"
untouched "NO-GO with an empty reason"
press_why NO-GO 0.5.0-d 25 "$want_sha" ',"reason":"   \t "'
[ "$code" = 400 ] || nogo "a NO-GO with a blank reason answered $code, want 400" "$(cat "$body")"
untouched "NO-GO with a blank reason"
press_why NO-GO 0.5.0-d 25 "$want_sha" ',"reason":null'
[ "$code" = 400 ] || nogo "a NO-GO with a null reason answered $code, want 400" "$(cat "$body")"
untouched "NO-GO with a null reason"
for r in 7 true '["too slow"]' '{"text":"x"}'; do
  press_why NO-GO 0.5.0-d 25 "$want_sha" ",\"reason\":$r"
  [ "$code" = 400 ] || nogo "a NO-GO whose reason is $r answered $code, want 400" "$(cat "$body")"
  untouched "NO-GO with a reason that is $r"
  press_why GO 0.5.0-d 25 "$want_sha" ",\"reason\":$r"
  [ "$code" = 400 ] || nogo "a GO whose reason is $r answered $code, want 400" "$(cat "$body")"
  untouched "GO with a reason that is $r"
done
stop_console

# --- e4. LIVE behind a proxy: the identity header ------------------------------
# shellcheck disable=SC2046
start_console "$live" "$tmp/proxy.log" $(live_env) CONSOLE_AUTH=proxy CONSOLE_IDENTITY_HEADER=X-Test-User CONSOLE_LAUNCHERS=alice
for path in / /v1/logbook.json /v1/console.json /v1/events; do
  req "$(base)$path"
  [ "$code" = 401 ] || nogo "proxy mode: GET $path without the identity header answered $code, want 401"
done
first_read -H 'X-Test-User: alice'
req -H 'X-Test-User: mallory' "$(base)/v1/events?once=1"
[ "$code" = 200 ] || nogo "proxy mode: GET /v1/events?once=1 with identity header answered $code, want 200"
press GO 0.5.0-d 25 "$want_sha"
[ "$code" = 401 ] || nogo "proxy mode: a POST without the identity header answered $code, want 401"
untouched "no identity"
PH='X-Test-User: mallory' press GO 0.5.0-d 25 "$want_sha"
[ "$code" = 403 ] || nogo "proxy mode: a POST by a non-launcher answered $code, want 403"
untouched "non-launcher"
req -H 'X-Test-User: mallory' "$(base)/v1/console.json"
jq -e '.identity == "mallory" and .can_launch == false' "$body" >/dev/null \
  || nogo "proxy mode: /v1/console.json for a non-launcher is not {identity: mallory, can_launch: false}" "$(cat "$body")"
req -H 'X-Test-User: alice' "$(base)/v1/console.json"
jq -e '.identity == "alice" and .can_launch == true' "$body" >/dev/null \
  || nogo "proxy mode: /v1/console.json for a launcher is not {identity: alice, can_launch: true}" "$(cat "$body")"
# The identity is what reaches `by` (ADR 0047 §5).
PH='X-Test-User: alice' press GO 0.5.0-d 25 "$want_sha"
[ "$code" = 200 ] || nogo "proxy mode: a correct POST by a launcher answered $code, want 200" "$(cat "$body")" "$(cat "$tmp/proxy.log")"
[ "$(cat "$live/effects.log")" = "shift 100" ] || nogo "proxy mode: the delivery stub saw '$(cat "$live/effects.log")', want exactly 'shift 100'"
jq -e '.promoted.by == "alice" and .promoted.via == "console"' "$live/release.json" >/dev/null \
  || nogo "proxy mode: the record's promoted is not by alice via console" "$(cat "$live/release.json")"
# docs/adr/0059 §4: a request without the identity header is 401, and any
# identity the proxy sets may request, launcher or not; its name is `by`.
gate_root=$live
ticket_dir=$live/tickets
want_id=$(next_id "$live/tickets")
post_json /v1/request "$draft_go"
[ "$code" = 401 ] || nogo "proxy mode: a request without the identity header answered $code, want 401" "$(cat "$body")"
[ "$(next_id "$live/tickets")" = "$want_id" ] || nogo "proxy mode: a request without the identity header wrote a ticket"
: > "$live/effects.log"
PH='X-Test-User: mallory' post_json /v1/request "$draft_go"
[ "$code" = 200 ] || nogo "proxy mode: a request by an identity that is no launcher answered $code, want 200" "$(cat "$body")" "$(cat "$tmp/proxy.log")"
jq -e --arg id "$want_id" '.ticket == $id and .exit == 0' "$body" >/dev/null \
  || nogo "proxy mode: the answer to a request is not {ticket: $want_id, exit: 0, ...}" "$(cat "$body")"
[ -f "$live/tickets/$want_id.md" ] || nogo "proxy mode: a request wrote no tickets/$want_id.md"
[ "$(cat "$live/effects.log")" = "request $want_id mallory console" ] \
  || nogo "proxy mode: make request saw '$(cat "$live/effects.log")', want the ticket, mallory and console"
stop_console

# Back to the held record, as launchpad left it, for the loopback press.
cp "$tmp/release.held.json" "$live/release.json"
cp "$tmp/releases.held.jsonl" "$live/releases.jsonl"
: > "$live/effects.log"

# --- e5. LIVE on loopback: the good press ---------------------------------------
# shellcheck disable=SC2046
start_console "$live" "$tmp/live2.log" $(live_env) TMPDIR="$tmp/ctmp"
first_read
pressable_again
want_author=$(head -n 1 "$live/promote/brief.author" 2>/dev/null || true)
# docs/adr/0079 §1, §2: a NO-GO with a reason runs promote; the record's
# `rejected` and the log's answer line carry the reason, the brief's sha256
# (and its author, null when the brief has none), via console.
press_why NO-GO 0.5.0-d 25 "$want_sha" ',"reason":"  p99 is too close to the limit  "'
[ "$code" = 200 ] || nogo "a NO-GO POST with a reason answered $code, want 200" "$(cat "$body")" "$(cat "$tmp/live2.log")"
jq -e '.line == "NO-GO: go-for-launch — human said NO-GO" and .exit == 1' "$body" >/dev/null \
  || nogo "the answer to a NO-GO POST is not {line: 'NO-GO: go-for-launch — human said NO-GO', exit: 1}" "$(cat "$body")"
[ "$(cat "$live/effects.log")" = "rollback" ] || nogo "the delivery stub saw '$(cat "$live/effects.log")' after the NO-GO, want exactly 'rollback'"
jq -e --arg s "$want_sha" --arg a "$want_author" '.rejected.reason == "p99 is too close to the limit" and .rejected.via == "console"
  and .rejected.brief_sha256 == $s and .rejected.brief_author == (if $a == "" then null else $a end) and (has("promoted") | not)' "$live/release.json" >/dev/null \
  || nogo "the record after the NO-GO press does not hold its reason, the brief's sha256 and author, via console" "$(cat "$live/release.json")"
tail -n 1 "$live/releases.jsonl" | jq -e --arg s "$want_sha" '.kind == "answer" and .answer == "NO-GO" and .via == "console"
  and .reason == "p99 is too close to the limit" and .brief_sha256 == $s' >/dev/null \
  || nogo "the release log's new line is not the console's NO-GO answer with its reason and brief sha" "$(tail -n 1 "$live/releases.jsonl")"
# The held record again, for the GO.
cp "$tmp/release.held.json" "$live/release.json"
cp "$tmp/releases.held.jsonl" "$live/releases.jsonl"
: > "$live/effects.log"
sleep 3
pressable_again
press_why GO 0.5.0-d 25 "$want_sha" ',"reason":" numbers hold at 25% "'
[ "$code" = 200 ] || nogo "a correct GO POST answered $code, want 200" "$(cat "$body")" "$(cat "$tmp/live2.log")"
jq -e '.line == "GO: go-for-launch" and .exit == 0' "$body" >/dev/null \
  || nogo "the answer to a correct GO POST is not {line: 'GO: go-for-launch', exit: 0}" "$(cat "$body")"
[ "$(cat "$live/effects.log")" = "shift 100" ] || nogo "the delivery stub saw '$(cat "$live/effects.log")', want exactly 'shift 100'"
jq -e '.promoted.weight == 100 and .promoted.by == "unauthenticated" and .promoted.via == "console" and (.promoted.at | type == "string") and (has("rejected") | not)' "$live/release.json" >/dev/null \
  || nogo "the record after the press is not promoted by unauthenticated via console" "$(cat "$live/release.json")"
jq -e --arg s "$want_sha" --arg a "$want_author" '.promoted.reason == "numbers hold at 25%" and .promoted.brief_sha256 == $s
  and .promoted.brief_author == (if $a == "" then null else $a end)' "$live/release.json" >/dev/null \
  || nogo "the record after the GO press does not hold its reason and the brief's sha256 and author" "$(cat "$live/release.json")"
tail -n 1 "$live/releases.jsonl" | jq -e '.reason == "numbers hold at 25%"' >/dev/null \
  || nogo "the release log's GO answer does not hold the reason" "$(tail -n 1 "$live/releases.jsonl")"
[ "$(wc -l < "$live/releases.jsonl")" -eq "$(($(wc -l < "$tmp/releases.held.jsonl") + 1))" ] \
  || nogo "the release log did not gain exactly one line from the press"
tail -n 1 "$live/releases.jsonl" | jq -e '.kind == "answer" and .version == "0.5.0-d" and .answer == "GO" and .by == "unauthenticated" and .via == "console"' >/dev/null \
  || nogo "the release log's new line is not the console's GO answer" "$(tail -n 1 "$live/releases.jsonl")"
# A second press on the answered record is refused: the logbook has moved on.
sleep 3
press GO 0.5.0-d 25 "$want_sha"
[ "$code" = 409 ] || nogo "a second GO on an answered record answered $code, want 409"
[ "$(cat "$live/effects.log")" = "shift 100" ] || nogo "a second GO moved the plane again: $(cat "$live/effects.log")"

# The second write (docs/adr/0055 §2): ticket 0009 waits on question 1.
# Anything but that question runs nothing; the good answer runs the stub
# root's `make answer` with the text, who answered and via console.
: > "$live/effects.log"
reply 0009 0 "an old question"
[ "$code" = 409 ] || nogo "an answer to an answered question answered $code, want 409" "$(cat "$body")"
reply 0001 0 "a ticket with no question"
[ "$code" = 409 ] || nogo "an answer on a ticket with no open question answered $code, want 409" "$(cat "$body")"
reply 0009 1 "   "
[ "$code" = 400 ] || nogo "an empty answer answered $code, want 400" "$(cat "$body")"
PO=http://evil.invalid reply 0009 1 "a perimeter"
[ "$code" = 403 ] || nogo "an answer with a foreign Origin answered $code, want 403"
[ ! -s "$live/effects.log" ] || nogo "a refused answer ran something: $(cat "$live/effects.log")"
reply 0009 1 "app/backend/meetups/*.py"
[ "$code" = 200 ] || nogo "a correct answer POST answered $code, want 200" "$(cat "$body")" "$(cat "$tmp/live2.log")"
jq -e '.line == "GO: answer" and .exit == 0' "$body" >/dev/null || nogo "the answer POST is not {line: 'GO: answer', exit: 0}" "$(cat "$body")"
[ "$(cat "$live/effects.log")" = "answer 0009 app/backend/meetups/*.py unauthenticated console" ] \
  || nogo "make answer saw '$(cat "$live/effects.log")', want the ticket, the text, unauthenticated and console"

# The ETag, on the logbook this console reads now (docs/adr/0059 §1).
etag_checks "live mode"

# The spec check, live (docs/adr/0059 §4): the gate's line on every draft, and
# the root, the repository's status, the tickets, the log of what ran and the
# console's temporary files as they were.
: > "$live/effects.log"
gate_root=$live
ticket_dir=$live/tickets
live_digest=$(tree_digest "$live")
live_status=$(git -C "$live" status --porcelain)
live_tickets=$(ls "$live/tickets")
live_ctmp=$(ls "$tmp/ctmp")
spec_batch
body_refusals /v1/spec-check
route_refusals /v1/spec-check
[ "$(tree_digest "$live")" = "$live_digest" ] || nogo "the spec check changed the files under the root"
[ "$(git -C "$live" status --porcelain)" = "$live_status" ] || nogo "the spec check changed the repository's status" "$(git -C "$live" status --porcelain)"
[ "$(ls "$live/tickets")" = "$live_tickets" ] || nogo "the spec check changed tickets/: $(ls "$live/tickets")"
[ ! -s "$live/effects.log" ] || nogo "the spec check ran something: $(cat "$live/effects.log")"
[ "$(ls "$tmp/ctmp")" = "$live_ctmp" ] || nogo "the spec check left its temporary files behind: $(ls "$tmp/ctmp")"

# A request (docs/adr/0059 §4). Refused with nothing written and nothing run:
# a body that is no request (400), a draft the gate refuses (409, with the
# gate's line), a POST that is not the console's own.
body_refusals /v1/request
route_refusals /v1/request
for edit in '.criteria = []' '.criteria[0].then = "it works"' '.perimeter = []' '.feature = "Waiting List"' '.priority = "urgent"'; do
  post_json /v1/request "$(draft "$edit")"
  [ "$code" = 409 ] || nogo "a request whose draft the gate refuses ($edit) answered $code, want 409" "$(cat "$body")"
  jq -e '(.error | type == "string" and length > 0) and (.line | startswith("NO-GO: spec — "))' "$body" >/dev/null \
    || nogo "the 409 to a refused draft ($edit) is not {error, line: <the gate's NO-GO line>}" "$(cat "$body")"
done
[ "$(tree_digest "$live")" = "$live_digest" ] || nogo "a refused request changed the files under the root"
[ ! -s "$live/effects.log" ] || nogo "a refused request ran something: $(cat "$live/effects.log")"
# A good draft: the next id, the very Markdown the spec check showed, and
# `make request` with who pressed and via console.
want_id=$(next_id "$live/tickets")
post_json /v1/spec-check "$draft_go"
jq -e --arg id "$want_id" '.ticket == $id and .line == "GO: spec"' "$body" >/dev/null || nogo "the spec check of the good draft is not GO for ticket $want_id" "$(cat "$body")"
jq -j '.markdown' "$body" > "$tmp/shown.md"
post_json /v1/request "$draft_go"
[ "$code" = 200 ] || nogo "a request of a good draft answered $code, want 200" "$(cat "$body")" "$(cat "$tmp/live2.log")"
jq -e --arg id "$want_id" '.ticket == $id and .line == "GO: request" and .exit == 0' "$body" >/dev/null \
  || nogo "the answer to a request is not {ticket: $want_id, line: 'GO: request', exit: 0}" "$(cat "$body")"
cmp -s "$live/tickets/$want_id.md" "$tmp/shown.md" || nogo "tickets/$want_id.md is not the Markdown the spec check showed" "$(diff "$tmp/shown.md" "$live/tickets/$want_id.md" | head -20)"
[ "$(gate_line "$live" "$live/tickets/$want_id.md")" = "GO: spec" ] || nogo "the gate does not pass tickets/$want_id.md"
[ "$(cat "$live/effects.log")" = "request $want_id unauthenticated console" ] \
  || nogo "make request saw '$(cat "$live/effects.log")', want the ticket, unauthenticated and console"
# The next one takes the next id, and never overwrites the first.
: > "$live/effects.log"
second_id=$(next_id "$live/tickets")
[ "$second_id" != "$want_id" ] || nogo "the next id after a request is the request's own"
post_json /v1/request "$(draft '.title = "A second request"')"
[ "$code" = 200 ] || nogo "a second request answered $code, want 200" "$(cat "$body")"
jq -e --arg id "$second_id" '.ticket == $id' "$body" >/dev/null || nogo "the second request's ticket is not $second_id" "$(cat "$body")"
cmp -s "$live/tickets/$want_id.md" "$tmp/shown.md" || nogo "the second request changed tickets/$want_id.md"
[ "$(head -n 1 "$live/tickets/$second_id.md")" = "# $second_id — A second request" ] || nogo "tickets/$second_id.md does not start with its id and title" "$(head -n 3 "$live/tickets/$second_id.md")"
[ "$(cat "$live/effects.log")" = "request $second_id unauthenticated console" ] || nogo "the second make request saw '$(cat "$live/effects.log")'"
stop_console

# --- f. every numeric policy key has its provenance --------------------------
files_all="policy/merge.env policy/rework.env"
for d in app workloads/*/ workloads/*/.lights-out; do
  if [ -f "$d/policy.env" ]; then files_all="$files_all $d/policy.env"; fi
done
# shellcheck disable=SC2086 # file names without spaces
chk prov all $files_all >"$tmp/chk.out" || nogo "a numeric policy key has no provenance" "$(cat "$tmp/chk.out")"
files_base=""
for d in app workloads/*/ workloads/*/.lights-out; do
  if [ -f "$d/workload.env" ]; then files_base="$files_base $d/workload.env"; fi
done
if [ -n "$files_base" ]; then
  # shellcheck disable=SC2086
  chk prov baseline $files_base >"$tmp/chk.out" || nogo "a MUTATION_BASELINE has no provenance" "$(cat "$tmp/chk.out")"
fi

go
