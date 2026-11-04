#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-logbook: the logbook and the console tell the truth and stay inside
# their perimeter (docs/adr/0047 §2-§5, Consequences; field by field in
# docs/plans/console.md, "Decided at build"), cold: no plane, no socket.
# Needs python3, jq, curl and the `jsonschema` package, all in the
# workbench. Written from that ADR and that plan, not from core/logbook.py
# or console/.
#   a. With stub sources in a throwaway root, core/logbook.sh runs twice:
#      one GO line each, the two files byte-equal, and byte-equal to the
#      golden fixtures/logbook/golden-1.0.json, which is written by hand
#      from the plan. Then variants of the sources, each checked on the
#      one field the plan's rule decides.
#   b. The golden validates against every committed schema of its major.
#   c. The committed snapshot, when there is one, validates, and its
#      spec.sha256 values are the committed tickets' (§4). A README with no
#      snapshot is drift; with neither, this point is skipped.
#   d. console/ names no adapter, no .crew/ and no store; serve.py starts
#      two scripts and has one POST route; index.html fetches /v1/ only.
#   e. The console at runtime: snapshot mode refuses to write; the start-up
#      refusals (§5); LIVE mode in the stub root refuses every bad press
#      before anything runs, and one good press moves the plane and records
#      who pressed.
#   f. Every numeric policy key has a "# Provenance, <kind>:" line above it
#      (Consequences).
# Before any of that, every checker runs on a bad input and must reject it:
# a gate that cannot fail is not a gate.
set -eu
name=logbook
. ci/lib.sh

repo=$(pwd)
stub="$repo/fixtures/logbook/stub"
golden="$repo/fixtures/logbook/golden-1.0.json"
tmp=$(mktemp -d)
pids=""
trap 'for p in $pids; do kill "$p" 2>/dev/null || :; done; rm -rf "$tmp"' EXIT

for tool in python3 jq curl sha256sum timeout; do
  command -v "$tool" >/dev/null 2>&1 || nogo "$tool is not installed; this gate runs in the workbench"
done
python3 -c 'import jsonschema' 2>/dev/null || nogo "python3 cannot import jsonschema; it is in the workbench lock (docs/adr/0047 §2)"
[ -f "$golden" ] || nogo "fixtures/logbook/golden-1.0.json not found"
[ -d "$stub" ] || nogo "fixtures/logbook/stub not found"

# --- the checkers, one python file, one subcommand each ---------------------
cat > "$tmp/checks.py" <<'PY'
import ast, glob, hashlib, json, os, re, sys

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


SCRIPTS = {"logbook.sh", "promote.sh"}


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
        for token in re.findall(r"[\w./-]+\.sh\b", c):
            d, base = os.path.split(token)
            if d not in ("", "core"):
                die(f"{path} names the script {token}; only core/logbook.sh and core/promote.sh")
            found.add(base)
    if found != SCRIPTS:
        die(f"{path} names the scripts {sorted(found)}; want exactly {sorted(SCRIPTS)}")
    for c in consts:
        if c in ("git", "curl", "jq", "docker", "podman", "docker-compose", "bash"):
            die(f"{path} names the program {c}; it starts core/logbook.sh and core/promote.sh only")
    for n in ast.walk(tree):
        if isinstance(n, ast.keyword) and n.arg == "shell" and not (isinstance(n.value, ast.Constant) and n.value.value is False):
            die(f"{path} passes shell={ast.dump(n.value)}; argv only")
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "os" \
                and (n.attr in ("system", "popen") or n.attr.startswith(("exec", "spawn"))):
            die(f"{path} calls os.{n.attr}; the two scripts go through subprocess only")
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in ("do_PUT", "do_DELETE", "do_PATCH"):
            die(f"{path} defines {n.name}; go-for-launch is the one write")
    v1 = {c for c in consts if re.fullmatch(r"/v1/[\w.\-]+", c)}
    want = {"/v1/logbook.json", "/v1/console.json", "/v1/go-for-launch"}
    if v1 != want:
        die(f"{path} names the routes {sorted(v1)}; want exactly {sorted(want)}")
    posts = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "do_POST"]
    if len(posts) != 1:
        die(f"{path} has {len(posts)} do_POST; want 1")
    in_post = {n.value for n in ast.walk(posts[0]) if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and n.value.startswith("/")}
    if in_post - {"/v1/go-for-launch"}:
        die(f"{path}: do_POST names {sorted(in_post - {'/v1/go-for-launch'})}; /v1/go-for-launch is the one POST route")


def cmd_index(path):
    src = open(path, encoding="utf-8").read()
    for banned in ("XMLHttpRequest", "sendBeacon", "<form"):
        if banned in src:
            die(f"{path} uses {banned}; the page reads with GET fetch and writes with the one POST")
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
            if goes != "/v1/go-for-launch":
                die(f"{path}: a POST to {goes or args[:30]}; /v1/go-for-launch is the one POST route")
        elif goes == "/v1/go-for-launch":
            die(f"{path} fetches /v1/go-for-launch without method POST")
    for m in re.finditer(r"\bmethod\s*:\s*(\S+)", src):
        if m.group(1).strip("'\",`") != "POST":
            die(f"{path} has method {m.group(1)}; POST is the only write")
    if "/v1/logbook.json" not in src:
        die(f"{path} never names /v1/logbook.json")


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
(
  cd "$st"
  must_reject "schema validation (an enum value)" valid bad-enum.json
  must_reject "schema validation (a missing top-level key)" valid bad-missing.json
  must_reject "schema validation (a major with no schema)" valid bad-major.json
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

# serve.py: two scripts, one POST route.
cat > "$st/serve-good.py" <<'PY'
"""Docstring naming core/anything-else.sh is not an argv."""
import subprocess


class H:
    def do_GET(self):
        if self.path in ("/", "/v1/logbook.json", "/v1/console.json"):
            return
    def do_POST(self):
        if self.path != "/v1/go-for-launch":
            return
        subprocess.run(["sh", "core/promote.sh"], check=False)
        subprocess.run(["sh", "core/logbook.sh"], check=False)
PY
{ cat "$st/serve-good.py"; printf 'subprocess.run(["sh", "adapters/x/get.sh"])\n'; } > "$st/serve-adapter.py"
sed 's|self.path != "/v1/go-for-launch"|self.path not in ("/v1/go-for-launch", "/other")|' "$st/serve-good.py" > "$st/serve-two-posts.py"
sed 's|check=False)|check=False, shell=True)|' "$st/serve-good.py" > "$st/serve-shell.py"
sed 's|"/v1/console.json"|"/v1/console.json", "/v1/extra"|' "$st/serve-good.py" > "$st/serve-route.py"
sed 's|\["sh", "core/promote.sh"\]|["git", "core/promote.sh"]|' "$st/serve-good.py" > "$st/serve-git.py"
{ cat "$st/serve-good.py"; printf '\n\nclass X:\n    def do_DELETE(self):\n        pass\n'; } > "$st/serve-delete.py"
must_accept "serve.py" serve "$st/serve-good.py"
for v in adapter two-posts shell route git delete; do
  must_reject "serve.py ($v)" serve "$st/serve-$v.py"
done

# index.html: GET fetch on /v1/, one POST.
cat > "$st/index-good.html" <<'HTML'
<script>
fetch('/v1/logbook.json').then(r => r.json());
fetch("/v1/go-for-launch", {method: 'POST', body: '{}'});
</script>
HTML
sed "s|fetch('/v1/logbook.json')|fetch('https://example.invalid/l.json')|" "$st/index-good.html" > "$st/index-external.html"
sed "s|fetch('/v1/logbook.json')|fetch('/state.json')|" "$st/index-good.html" > "$st/index-other.html"
sed "s|fetch('/v1/logbook.json')|fetch('/v1/logbook.json', {method: 'PUT'})|" "$st/index-good.html" > "$st/index-put.html"
sed "s|fetch('/v1/logbook.json')|fetch('/v1/console.json', {method: 'POST'})|" "$st/index-good.html" > "$st/index-post-elsewhere.html"
sed "s|{method: 'POST', body|{body|" "$st/index-good.html" > "$st/index-get-go.html"
sed 's|<script>|<script>var x = new XMLHttpRequest();|' "$st/index-good.html" > "$st/index-xhr.html"
must_accept "index.html" index "$st/index-good.html"
for v in external other put post-elsewhere get-go xhr; do
  must_reject "index.html ($v)" index "$st/index-$v.html"
done

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
# mkroot DIR: core/, ci/ symlinked from this repository, a copy of console/,
# stub policy files, a stub contract, tickets, tower documents (one file per
# <collection>.<id>), a release log, a record, a post mortem, a brief, a stub
# StateStore and a stub Delivery, and a git repository whose one commit is
# the same in every run.
mkroot() {
  r=$1
  mkdir -p "$r/adapters/stub-state" "$r/adapters/stub-delivery" "$r/world" "$r/roles/release"
  printf 'THINKING=low\nTIMEOUT_S=900\nHARNESS=stub-harness\n' > "$r/roles/release/role.env"
  ln -s "$repo/core" "$r/core"
  ln -s "$repo/ci" "$r/ci"
  [ ! -d "$repo/console" ] || cp -R "$repo/console" "$r/console"
  cp -R "$stub/policy" "$stub/contract" "$stub/tickets" "$stub/state" "$stub/postmortem" "$stub/promote" "$r/"
  cp "$stub/releases.jsonl" "$stub/release.json" "$r/"
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
  chmod +x "$r"/adapters/stub-state/get.sh "$r"/adapters/stub-delivery/*.sh
  printf '{"stable":"0.3.0-b","canary":"0.5.0-d","weight":25}\n' > "$r/world/status.json"
  : > "$r/effects.log"
  # An empty commit with fixed names and dates: the same hash in every run,
  # whatever the root holds (the golden names it).
  git -C "$r" init -q . >/dev/null 2>&1
  GIT_AUTHOR_NAME=stub GIT_AUTHOR_EMAIL=stub@example.invalid GIT_COMMITTER_NAME=stub \
    GIT_COMMITTER_EMAIL=stub@example.invalid GIT_AUTHOR_DATE="1793491200 +0000" \
    GIT_COMMITTER_DATE="1793491200 +0000" \
    git -C "$r" -c commit.gpgsign=false commit -q --allow-empty -m stub >/dev/null 2>&1
  [ "$(git -C "$r" rev-parse HEAD 2>/dev/null)" = bc8b42b2ce93f7048f60e5a1aade7f58b05e6d3b ] \
    || nogo "the stub root's commit is not the one the golden names; this git writes another commit object"
}

# run_reader [VAR=val...]: core/logbook.sh in $root, the stub sources by
# relative path; sets $out (stdout) and $rc, stderr in $root/reader.err.
run_reader() {
  out=$(cd "$root" && env -u GIT_DIR -u GIT_WORK_TREE LEVEL=l0 WORKLOAD=app CODE_HOST=gitea \
    STATE_STORE=stub-state DELIVERY=stub-delivery TICKETS_DIR=tickets RELEASE_RECORD=release.json \
    RELEASES_LOG=releases.jsonl PROMOTE_OUT=promote POSTMORTEM_DIR=postmortem WORKLOAD_CONTRACT=contract \
    "$@" sh core/logbook.sh 2>reader.err </dev/null) && rc=0 || rc=$?
}

root="$tmp/root"
mkroot "$root"
[ -f "$repo/core/logbook.sh" ] || nogo "core/logbook.sh not found"

# --- a. the reader, twice, against the golden --------------------------------
run_reader LOGBOOK_FILE=out1.json
[ "$out" = "GO: logbook — out1.json, 7 tickets" ] \
  || nogo "core/logbook.sh printed '$out' (exit $rc), want 'GO: logbook — out1.json, 7 tickets'" "$(cat "$root/reader.err")"
run_reader LOGBOOK_FILE=out2.json
[ "$out" = "GO: logbook — out2.json, 7 tickets" ] \
  || nogo "the second run printed '$out' (exit $rc), want 'GO: logbook — out2.json, 7 tickets'" "$(cat "$root/reader.err")"
cmp -s "$root/out1.json" "$root/out2.json" \
  || nogo "two runs over the same sources wrote different bytes (ADR 0047 §2: no clock)" "$(diff "$root/out1.json" "$root/out2.json" | head -20)"
cmp -s "$root/out1.json" "$golden" \
  || nogo "the logbook over the stub sources is not fixtures/logbook/golden-1.0.json" "$(diff "$golden" "$root/out1.json" | head -40)"

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
  and (.releases[-1] | .from == "release.json" and .answer == {answer: "GO", by: "bob", via: "cli", at: "2026-11-04T10:00:00Z"})' \
  RELEASE_RECORD=release.promoted.json
var_case "a rejected record" '
  .launch.pressable == false
  and .launch.disabled_reason == "release 0.5.0-d was already answered NO-GO by bob via cli"
  and .releases[-1].answer == {answer: "NO-GO", by: "bob", via: "cli", at: "2026-11-04T10:00:00Z"}' \
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
  and (.releases | length) == 3 and all(.releases[]; .from == "releases.jsonl")' RELEASE_RECORD=absent.json
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
# A record the last log entry already is: no second entry.
jq -c 'select(.kind == "release" and .record.version == "0.4.0-c") | .record' "$root/releases.jsonl" > "$root/release.same.json"
var_case "a record equal to the last log entry" '(.releases | length) == 3 and all(.releases[]; .from == "releases.jsonl")' \
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

# --- b. the golden against every committed schema of its major ---------------
chk valid "$golden" >"$tmp/chk.out" || nogo "the golden does not validate" "$(cat "$tmp/chk.out")"

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
[ -f console/index.html ] || nogo "console/index.html not found"
chk names console >"$tmp/chk.out" || nogo "console/ names what it must not" "$(cat "$tmp/chk.out")"
chk serve console/serve.py >"$tmp/chk.out" || nogo "console/serve.py is outside its perimeter" "$(cat "$tmp/chk.out")"
chk index console/index.html >"$tmp/chk.out" || nogo "console/index.html is outside its perimeter" "$(cat "$tmp/chk.out")"

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

# req [curl args...]: sets $code and $body (a file).
req() {
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

# --- e1. snapshot mode, with a tripwire where the scripts would be ----------
trip="$tmp/trip"
mkdir -p "$trip/core" "$trip/fixtures/logbook"
cp -R "$repo/console" "$trip/console"
cp "$golden" "$trip/fixtures/logbook/golden-1.0.json"
for s in logbook promote; do
  # shellcheck disable=SC2016 # the tripwire script expands its own $d
  printf '#!/bin/sh\nd=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\necho "$0" >> "$d/ran"\nexit 1\n' > "$trip/core/$s.sh"
done
start_console "$trip" "$tmp/snap.log" CONSOLE_SNAPSHOT=fixtures/logbook/golden-1.0.json
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
want_sha=$(jq -r '.launch.brief.sha256' "$golden")
press GO 0.5.0-d 25 "$want_sha"
[ "$code" = 403 ] || nogo "snapshot mode: a correct POST answered $code, want 403" "$(cat "$body")"
press PREPARE 0.5.0-d 25 "$want_sha"
[ "$code" = 403 ] || nogo "snapshot mode: a PREPARE POST answered $code, want 403" "$(cat "$body")"
for path in /v1/logbook.json /v1/console.json /; do
  req -X POST -H 'Content-Type: application/json' -H "Origin: http://127.0.0.1:$port" -d '{}' "$(base)$path"
  case "$code" in 404 | 405) : ;; *) nogo "POST $path answered $code, want 404 or 405: go-for-launch is the one write" ;; esac
done
for verb in PUT DELETE PATCH; do
  req -X "$verb" -H 'Content-Type: application/json' -H "Origin: http://127.0.0.1:$port" -d '{}' "$(base)/v1/go-for-launch"
  case "$code" in 2*) nogo "$verb /v1/go-for-launch answered $code; go-for-launch is the one POST" ;; esac
done
[ ! -e "$trip/ran" ] || nogo "snapshot mode started $(cat "$trip/ran"); it must run nothing"
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
# pressable_again HEADER...: waits for a logbook that says the press is possible.
pressable_again() {
  pa_i=0
  while :; do
    req "$@" "$(base)/v1/logbook.json"
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
stop_console

# --- e4. LIVE behind a proxy: the identity header ------------------------------
# shellcheck disable=SC2046
start_console "$live" "$tmp/proxy.log" $(live_env) CONSOLE_AUTH=proxy CONSOLE_IDENTITY_HEADER=X-Test-User CONSOLE_LAUNCHERS=alice
for path in / /v1/logbook.json /v1/console.json; do
  req "$(base)$path"
  [ "$code" = 401 ] || nogo "proxy mode: GET $path without the identity header answered $code, want 401"
done
first_read -H 'X-Test-User: alice'
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
stop_console

# Back to the held record, as launchpad left it, for the loopback press.
cp "$tmp/release.held.json" "$live/release.json"
cp "$tmp/releases.held.jsonl" "$live/releases.jsonl"
: > "$live/effects.log"

# --- e5. LIVE on loopback: the good press ---------------------------------------
# shellcheck disable=SC2046
start_console "$live" "$tmp/live2.log" $(live_env)
first_read
pressable_again
press GO 0.5.0-d 25 "$want_sha"
[ "$code" = 200 ] || nogo "a correct GO POST answered $code, want 200" "$(cat "$body")" "$(cat "$tmp/live2.log")"
jq -e '.line == "GO: go-for-launch" and .exit == 0' "$body" >/dev/null \
  || nogo "the answer to a correct GO POST is not {line: 'GO: go-for-launch', exit: 0}" "$(cat "$body")"
[ "$(cat "$live/effects.log")" = "shift 100" ] || nogo "the delivery stub saw '$(cat "$live/effects.log")', want exactly 'shift 100'"
jq -e '.promoted.weight == 100 and .promoted.by == "unauthenticated" and .promoted.via == "console" and (.promoted.at | type == "string") and (has("rejected") | not)' "$live/release.json" >/dev/null \
  || nogo "the record after the press is not promoted by unauthenticated via console" "$(cat "$live/release.json")"
[ "$(wc -l < "$live/releases.jsonl")" -eq "$(($(wc -l < "$tmp/releases.held.jsonl") + 1))" ] \
  || nogo "the release log did not gain exactly one line from the press"
tail -n 1 "$live/releases.jsonl" | jq -e '.kind == "answer" and .version == "0.5.0-d" and .answer == "GO" and .by == "unauthenticated" and .via == "console"' >/dev/null \
  || nogo "the release log's new line is not the console's GO answer" "$(tail -n 1 "$live/releases.jsonl")"
# A second press on the answered record is refused: the logbook has moved on.
sleep 3
press GO 0.5.0-d 25 "$want_sha"
[ "$code" = 409 ] || nogo "a second GO on an answered record answered $code, want 409"
[ "$(cat "$live/effects.log")" = "shift 100" ] || nogo "a second GO moved the plane again: $(cat "$live/effects.log")"
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
