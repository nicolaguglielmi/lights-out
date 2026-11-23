#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-workloads: replaces ci/compose.sh (docs/adr/0030 §5). Every workload
# directory in the repo -- today's app/, tomorrow's workloads/minimal/,
# found by the two globs docs/adr/0030 §5 names, */workload.env and
# workloads/*/workload.env -- is checked against the Workload port
# (docs/adr/0030 §2-3), independent of whichever WORKLOAD the enclosing
# `make` invocation itself selected:
#   (a) workload.env defines exactly the twelve keys, no more, no fewer --
#       an extra one could silently override DELIVERY or a `?=` default
#       (docs/adr/0030 §4's trap); WORKLOAD_TEST_PATHS, the globs naming
#       the tests (docs/adr/0053 §1), is non-empty; the four runner keys (docs/adr/0042
#       §1-§2) are non-empty commands with no `$` or `#` (make's include
#       would expand the one and cut at the other) and a TOOLCHAIN_IMAGE
#       of a known form: workbench, compose:<service>, an image reference;
#   (b) when the contract has a compose.yaml -- the plane half, required
#       only to run the plane (docs/adr/0042 §7) -- `docker-compose
#       config`, with that compose.yaml merged in explicitly (-f, not the
#       ambient COMPOSE_FILE, which reflects only the enclosing make's own
#       WORKLOAD) and WORKLOAD set for interpolation, shows the three fixed
#       services with their profiles and images (docs/adr/0030 §3), and a
#       compose: toolchain names one of them with profile test; a contract
#       without compose.yaml may not use the compose form;
#   (c) with the plane half, that workload's flags/flags.json defines
#       release-freeze and its own WORKLOAD_KILL_SWITCH;
#   (d) its worker.md, the dev-worker's brief, has `## Scope` and
#       `## Tests` (docs/adr/0037 §6);
#   (e) its policy.env, if any, passes core/policy.py: known keys, valid
#       values, a provenance line over each (docs/adr/0037 §5);
#   (f) with the plane half, the harnesses are confined (docs/adr/0046): on
#       the merged project rendered with the `crew` profile, network `crew`
#       is internal; every CLI harness service is on exactly [crew], with
#       the four proxy variables (HTTPS_PROXY, HTTP_PROXY and their lower-case
#       twins) at http://egress:3128, dns [192.0.2.1] and no published port;
#       every service a harness depends on (a workload's crew-postgres) is on
#       exactly [crew] with dns [192.0.2.1] and no published port (§7: the
#       engine's resolver forwards no name out); `egress` is on exactly crew
#       and default with
#       none. The first project rendered is also judged with one planted
#       defect at a time, and each must be named: a check that cannot fail
#       is not one;
#   (g) its crew-egress.txt, if it has one (the package indexes the worker may
#       reach, docs/adr/0046 §5), lists name:port entries in the shape an
#       adapter's EGRESS has (egress_flaw, ci/lib.sh), comment lines aside;
#       the shape is planted-defect checked here too;
#   (h) what it gives the agents passes core/agents.py check: its roles/
#       files for the two reviewers and its mcp.json for the dev-worker,
#       each remote host on its own crew-egress.txt (docs/adr/0073 §10).
# The contract is the workload's directory for one inside this checkout
# (app/), and its .lights-out/ for a repository laid out to be attached:
# workloads/*/ here, or the attached clone itself, named as the one
# argument by `make attached`, which exports its WORKLOAD_CONTRACT
# (docs/adr/0037 §2, §7).
# `docker-compose config` only renders the merged project; it needs no
# engine socket (ci/compose.sh's own rationale, docs/adr/0012), only the
# WORKBENCH_IMAGE precondition every other compose call in this repo has
# (the root compose.yaml's harness and egress services require it, :?).
set -eu
name=workloads
. ci/lib.sh

[ -n "${WORKBENCH_IMAGE:-}" ] || nogo "WORKBENCH_IMAGE is not set; run this via ./go, not directly"

err_log=$(mktemp)
policy_out=$(mktemp)
egress_files=$(mktemp -d)
trap 'rm -rf "$err_log" "$policy_out" "$egress_files"' EXIT


expected_keys='CANARY_VERSION MUTATION_BASELINE WORKLOAD_KILL_SWITCH WORKLOAD_SAMPLE_PATH WORKLOAD_LOCKFILES WORKLOAD_MUTATION_SCOPE WORKLOAD_TEST_ROOT WORKLOAD_TEST_PATHS TOOLCHAIN_IMAGE TEST_CMD SAST_CMD MUTATION_CMD'

# The CLI harnesses, discovered as ci/harness-shape.sh discovers them: an
# adapters/<name>/ with run.sh, Containerfile and a pin, package.json or
# package-lock.json, or release.env (docs/adr/0050 §2). Their compose
# services carry the adapter's name.
harnesses=""
for d in adapters/*/; do
  h=$(basename "$d")
  if [ -f "adapters/$h/run.sh" ] && [ -f "adapters/$h/Containerfile" ] && { [ -f "adapters/$h/package.json" ] || [ -f "adapters/$h/package-lock.json" ] || [ -f "adapters/$h/release.env" ]; }; then
    harnesses="$harnesses $h"
  fi
done
[ -n "$harnesses" ] || nogo "no CLI harness under adapters/ (a directory with run.sh, Containerfile and package.json or release.env)"

# (f), as a Python program: argv are the rendered project (JSON), the harnesses,
# and "check" or "selfcheck". `check` prints the first flaw and exits 1;
# `selfcheck` judges the project good, then the project with one planted defect
# at a time, each of which must be refused with the words given here.
crew_check=$(cat <<'PY'
import copy
import json
import sys

PROXY = "http://egress:3128"
PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
NO_RESOLVER = ["192.0.2.1"]


def nets(service):
    return sorted((service.get("networks") or {}).keys())


def flaw(cfg, harnesses):
    services = cfg.get("services") or {}
    crew = (cfg.get("networks") or {}).get("crew")
    if crew is None:
        return "the project has no network crew (docs/adr/0046 §1)"
    if crew.get("internal") is not True:
        return "network crew is not internal, so it has a route out that does not go through egress (docs/adr/0046 §1)"
    for h in harnesses:
        svc = services.get(h)
        if svc is None:
            return "harness service %s is not in the project rendered with --profile crew" % h
        if nets(svc) != ["crew"]:
            return "harness service %s is on networks %s, want exactly [crew] (docs/adr/0046 §1)" % (h, nets(svc))
        env = svc.get("environment") or {}
        for var in PROXY_VARS:
            if env.get(var) != PROXY:
                return "harness service %s has %s=%r, want %s (docs/adr/0046 §3)" % (h, var, env.get(var), PROXY)
        if svc.get("ports"):
            return "harness service %s publishes a port; it must publish none (docs/adr/0046 §1)" % h
        if svc.get("dns") != NO_RESOLVER:
            return "harness service %s has dns %s, want %s, so the engine forwards no name out (docs/adr/0046 §7)" % (h, svc.get("dns"), NO_RESOLVER)
    for dep in sorted({d for h in harnesses for d in (services[h].get("depends_on") or {})}):
        svc = services.get(dep)
        if svc is None:
            return "a harness depends on %s, which is not in the project" % dep
        if nets(svc) != ["crew"]:
            return "%s, a service a harness depends on, is on networks %s, want exactly [crew] (docs/adr/0046 §6)" % (dep, nets(svc))
        if svc.get("ports"):
            return "%s, a service a harness depends on, publishes a port; it must publish none (docs/adr/0046 §6)" % dep
        if svc.get("dns") != NO_RESOLVER:
            return "%s, a service a harness depends on, has dns %s, want %s (docs/adr/0046 §7)" % (dep, svc.get("dns"), NO_RESOLVER)
    egress = services.get("egress")
    if egress is None:
        return "the project has no service egress (docs/adr/0046 §2)"
    if nets(egress) != ["crew", "default"]:
        return "service egress is on networks %s, want exactly [crew, default] (docs/adr/0046 §2)" % nets(egress)
    if egress.get("ports"):
        return "service egress publishes a port; it must publish none (docs/adr/0046 §2)"
    return None


def planted(h):
    """(name, words the refusal must contain, a function that plants the defect in a project)."""
    def service(c, name, **kw):
        c["services"][name] = kw

    def depend(c, **kw):
        service(c, "planted-db", **kw)
        c["services"][h]["depends_on"] = dict(c["services"][h].get("depends_on") or {}, **{"planted-db": {"condition": "service_started"}})

    def drop_var(var):
        return lambda c: c["services"][h]["environment"].pop(var)

    def set_var(var, value):
        return lambda c: c["services"][h]["environment"].__setitem__(var, value)

    cases = [
        ("network crew not internal", "is not internal", lambda c: c["networks"]["crew"].pop("internal")),
        ("no network crew", "no network crew", lambda c: c["networks"].pop("crew")),
        ("a harness also on default", "want exactly [crew]",
         lambda c: c["services"][h].__setitem__("networks", {"crew": None, "default": None})),
        ("a harness on default only", "want exactly [crew]", lambda c: c["services"][h].pop("networks")),
        ("a harness with a published port", "publishes a port",
         lambda c: c["services"][h].__setitem__("ports", [{"target": 80, "published": "8080"}])),
        ("a harness with the wrong proxy", "HTTPS_PROXY", set_var("HTTPS_PROXY", "http://proxy.example:3128")),
        ("a harness with the engine's resolver", "has dns None", lambda c: c["services"][h].pop("dns")),
        ("a harness with a public resolver", "has dns ['1.1.1.1']", lambda c: c["services"][h].__setitem__("dns", ["1.1.1.1"])),
        ("a dependency on the default network", "planted-db, a service a harness depends on, is on networks",
         lambda c: depend(c, networks={"default": None}, dns=NO_RESOLVER)),
        ("a dependency on crew and default", "planted-db, a service a harness depends on, is on networks",
         lambda c: depend(c, networks={"crew": None, "default": None}, dns=NO_RESOLVER)),
        ("a dependency that publishes a port", "planted-db, a service a harness depends on, publishes a port",
         lambda c: depend(c, networks={"crew": None}, dns=NO_RESOLVER, ports=[{"target": 5432, "published": "5432"}])),
        ("a dependency with the engine's resolver", "planted-db, a service a harness depends on, has dns",
         lambda c: depend(c, networks={"crew": None})),
        ("egress without crew", "service egress is on networks", lambda c: c["services"]["egress"].__setitem__("networks", {"default": None})),
        ("egress without default", "service egress is on networks", lambda c: c["services"]["egress"].__setitem__("networks", {"crew": None})),
        ("egress with a published port", "service egress publishes a port",
         lambda c: c["services"]["egress"].__setitem__("ports", [{"target": 3128, "published": "3128"}])),
        ("no egress service", "no service egress", lambda c: c["services"].pop("egress")),
    ]
    for var in PROXY_VARS:
        cases.append(("a harness without " + var, var, drop_var(var)))
    return cases


def selfcheck(cfg, harnesses):
    first = flaw(cfg, harnesses)
    if first:
        return "the self-check's good project is not judged good: " + first
    h = harnesses[0]
    # A dependency that is right must be accepted: the planted ones are judged against it.
    good = copy.deepcopy(cfg)
    good["services"]["planted-db"] = {"networks": {"crew": None}, "dns": NO_RESOLVER}
    good["services"][h]["depends_on"] = dict(good["services"][h].get("depends_on") or {}, **{"planted-db": {}})
    if flaw(good, harnesses):
        return "the self-check's project with a good dependency on crew is refused: " + flaw(good, harnesses)
    for name, words, plant in planted(h):
        bad = copy.deepcopy(cfg)
        plant(bad)
        got = flaw(bad, harnesses)
        if not got:
            return "ci/workloads.sh cannot fail: it accepted the defect '%s'" % name
        if words not in got:
            return "ci/workloads.sh named the wrong problem for '%s': wanted a message with %r, got: %s" % (name, words, got)
    return None


cfg, harnesses, mode = json.loads(sys.argv[1]), sys.argv[2].split(), sys.argv[3]
problem = (flaw if mode == "check" else selfcheck)(cfg, harnesses)
if problem:
    print(problem)
    sys.exit(1)
PY
)

# (g): a contract's crew-egress.txt, comment lines aside, in the shape of an
# adapter's EGRESS (egress_flaw, ci/lib.sh); nothing at all is fine.
crew_egress_flaw() {  # FILE
  egress_flaw "$(grep -v '^[[:space:]]*#' "$1" | tr -s ' \t\n' ' ')"
}

# (g)'s self-check: a list of good entries with comments is accepted, and a
# wildcard, an IP literal and an entry with no port are each refused, by name.
printf '# the package index\npypi.org:443\nfiles.pythonhosted.org:443\n' > "$egress_files/good.txt"
printf '# comments only\n' > "$egress_files/empty.txt"
printf 'pypi.org:443\n*.pythonhosted.org:443\n' > "$egress_files/wildcard.txt"
printf 'pypi.org:443\n151.101.0.223:443\n' > "$egress_files/ip.txt"
printf 'pypi.org\n' > "$egress_files/port.txt"
for f in good empty; do
  [ -z "$(crew_egress_flaw "$egress_files/$f.txt")" ] || nogo "the self-check's good crew-egress.txt ($f) is refused: $(crew_egress_flaw "$egress_files/$f.txt")"
done
for pair in wildcard:wildcard ip:"IP literal" port:"no port"; do
  got=$(crew_egress_flaw "$egress_files/${pair%%:*}.txt")
  case "$got" in
    *"${pair#*:}"*) ;;
    "") nogo "ci/workloads.sh cannot fail: it accepted a crew-egress.txt with a ${pair%%:*} entry" ;;
    *) nogo "ci/workloads.sh named the wrong problem for a crew-egress.txt with a ${pair%%:*} entry" "wanted a message with: ${pair#*:}" "got: $got" ;;
  esac
done
crew_selfchecked=0

# (h) app/load.sh fails loudly (docs/adr/0066 §4, the Workload contract's
# load.sh): against a fake app on 127.0.0.1 (APP_URL), cold. When a setup
# call (signup, login, create meetup) answers 500, or the meetup answer
# carries no id, load.sh exits non-zero within a few seconds, prints exactly
# one stderr line, `app-load: setup failed at <METHOD> <path>: <cause>;
# generating no load`, and never requests a path with `//`. When setup
# succeeds it loops POST /api/meetups/<id>/registrations with the id the app
# returned. Not run for an attached repository (one argument): that
# contract's load.sh is its own. Assumptions beyond the ADR: the fake app
# answers 201 {} to signup, {"token":"tok-1"} to login, {"id":"meetup-42"}
# to a meetup, 201 to a registration; `exits within a few seconds` is 6 s.
if [ $# -eq 0 ] && [ -f app/load.sh ]; then
  for tool in python3 curl jq; do
    command -v "$tool" >/dev/null 2>&1 || nogo "app/load.sh needs python3, curl and jq to be proved cold"
  done
  lw=$(mktemp -d)
  load_pid=""
  trap 'rm -rf "$err_log" "$policy_out" "$egress_files"; [ -z "$load_pid" ] || kill "$load_pid" 2>/dev/null || true; [ -z "${fake_pid:-}" ] || kill "$fake_pid" 2>/dev/null || true; rm -rf "$lw"' EXIT
  cat > "$lw/fake.py" <<'PY'
import http.server
import json
import os
import sys

log_path, port_path, fail_path = sys.argv[1:4]


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        with open(log_path, "a") as f:
            f.write("POST %s auth=%s\n" % (self.path, self.headers.get("Authorization", "")))
        with open(fail_path) as f:
            fail = f.read().strip()
        route = {"/api/signup": "signup", "/api/login": "login", "/api/meetups": "meetup"}.get(self.path)
        status, body = 201, {}
        if route == fail:
            status, body = 500, {"error": "boom"}
        elif route == "login":
            status, body = 200, {"token": "tok-1"}
        elif route == "meetup":
            status, body = 201, ({} if fail == "noid" else {"id": "meetup-42"})
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
with open(port_path + ".tmp", "w") as f:
    f.write(str(s.server_address[1]))
os.replace(port_path + ".tmp", port_path)
s.serve_forever()
PY
  : > "$lw/log"
  echo none > "$lw/fail"
  (python3 "$lw/fake.py" "$lw/log" "$lw/port" "$lw/fail" >/dev/null 2>"$lw/fake.err" & echo $! > "$lw/pid")
  fake_pid=$(cat "$lw/pid")
  tries=0
  while [ ! -s "$lw/port" ]; do
    tries=$((tries + 1))
    [ "$tries" -le 100 ] || nogo "the fake app for app/load.sh did not start" "$(cat "$lw/fake.err")"
    sleep 0.1
  done
  load_url="http://127.0.0.1:$(cat "$lw/port")"

  # run_load FAILING_STEP: sets load_rc, 124 when it was still running at 6 s.
  run_load() {
    echo "$1" > "$lw/fail"
    : > "$lw/log"
    env -i PATH="$PATH" HOME="$lw" APP_URL="$load_url" sh app/load.sh >"$lw/out" 2>"$lw/err" </dev/null &
    load_pid=$!
    t=0
    while kill -0 "$load_pid" 2>/dev/null && [ "$t" -lt 60 ]; do
      sleep 0.1
      t=$((t + 1))
    done
    if kill -0 "$load_pid" 2>/dev/null; then
      kill "$load_pid" 2>/dev/null || true
      load_rc=124
    else
      wait "$load_pid" && load_rc=0 || load_rc=$?
    fi
    pkill -P "$load_pid" 2>/dev/null || true
    load_pid=""
  }

  for step in "signup:POST /api/signup" "login:POST /api/login" "meetup:POST /api/meetups" "noid:POST /api/meetups"; do
    failing=${step%%:*}
    at=${step#*:}
    run_load "$failing"
    [ "$load_rc" -ne 0 ] && [ "$load_rc" -ne 124 ] \
      || nogo "app/load.sh did not exit non-zero within 6 s when $failing fails (exit $load_rc); docs/adr/0066 §4" "$(cat "$lw/err")"
    [ "$(awk 'END{print NR}' "$lw/err")" -eq 1 ] \
      || nogo "app/load.sh printed $(awk 'END{print NR}' "$lw/err") stderr lines when $failing fails, want exactly one (docs/adr/0066 §4)" "$(cat "$lw/err")"
    case "$(cat "$lw/err")" in
      "app-load: setup failed at $at: "*"; generating no load") ;;
      *) nogo "app/load.sh stderr when $failing fails is not 'app-load: setup failed at $at: ...; generating no load' (docs/adr/0066 §4)" "$(cat "$lw/err")" ;;
    esac
    [ ! -s "$lw/out" ] || nogo "app/load.sh wrote to stdout when $failing fails" "$(cat "$lw/out")"
    ! grep -q '//' "$lw/log" || nogo "app/load.sh requested a path with // when $failing fails (docs/adr/0066 §4)" "$(cat "$lw/log")"
    ! grep -q 'registrations' "$lw/log" || nogo "app/load.sh generated load after $failing failed (docs/adr/0066 §4)" "$(cat "$lw/log")"
  done

  # Setup succeeds: the loop uses the id the app returned.
  echo none > "$lw/fail"
  : > "$lw/log"
  env -i PATH="$PATH" HOME="$lw" APP_URL="$load_url" sh app/load.sh >"$lw/out" 2>"$lw/err" </dev/null &
  load_pid=$!
  t=0
  while [ "$(grep -c 'registrations' "$lw/log")" -lt 2 ] && [ "$t" -lt 60 ]; do
    sleep 0.1
    t=$((t + 1))
  done
  kill "$load_pid" 2>/dev/null || true
  pkill -P "$load_pid" 2>/dev/null || true
  { wait "$load_pid"; } 2>/dev/null || true
  load_pid=""
  [ "$(grep -c 'registrations' "$lw/log")" -ge 2 ] \
    || nogo "app/load.sh did not loop registrations within 6 s after a good setup" "$(cat "$lw/err")" "$(cat "$lw/log")"
  grep 'registrations' "$lw/log" | grep -qv '^POST /api/meetups/meetup-42/registrations auth=Bearer tok-1$' \
    && nogo "app/load.sh requested a registration path other than /api/meetups/meetup-42/registrations with the login token" "$(cat "$lw/log")"
  ! grep -q '//' "$lw/log" || nogo "app/load.sh requested a path with // after a good setup" "$(cat "$lw/log")"
fi

# "<workload dir>:<contract dir>" pairs; no path here holds a colon.
pairs=""
if [ $# -gt 0 ]; then
  [ -n "${WORKLOAD_CONTRACT:-}" ] || nogo "WORKLOAD_CONTRACT is not set; run this as make attached"
  pairs="$1:$WORKLOAD_CONTRACT"
else
  for f in */workload.env; do
    [ -e "$f" ] || continue
    pairs="$pairs $(dirname "$f"):$(dirname "$f")"
  done
  for f in workloads/*/.lights-out/workload.env; do
    [ -e "$f" ] || continue
    pairs="$pairs ${f%/.lights-out/workload.env}:$(dirname "$f")"
  done
  # lights-out's own contract, the one its self-judgement reads
  # (docs/adr/0049): the repository root is the workload.
  [ ! -e .lights-out/workload.env ] || pairs="$pairs .:.lights-out"
fi
[ -n "$pairs" ] || nogo "no */workload.env or workloads/*/.lights-out/workload.env found"

for pair in $pairs; do
  wd=${pair%%:*}
  cd_=${pair#*:}
  f="$cd_/workload.env"
  [ -f "$f" ] || nogo "$f does not exist"

  # (a) exactly the twelve keys. workload.env is "profile syntax" (its own
  # header): KEY=VALUE, one per line, no inline comments -- a comment line
  # is dropped whole, never a suffix, so a naive substring search on the
  # raw text would be wrong here. A command may hold spaces and quotes
  # (make keeps them, core/policy.py matches KEY=VALUE), never `$` or `#`.
  reason=$(python3 - "$f" "$expected_keys" <<'PY'
import re
import sys

path, expected_s = sys.argv[1], sys.argv[2]
expected = set(expected_s.split())

with open(path) as fh:
    raw_lines = [ln.strip() for ln in fh]

keys = []
values = {}
for ln in raw_lines:
    if not ln or ln.startswith("#"):
        continue
    key, sep, value = ln.partition("=")
    if not sep:
        print(f"{path}: not KEY=VALUE: {ln!r}")
        sys.exit(1)
    keys.append(key)
    values[key] = value

if "WORKLOAD_TEST_PATHS" in values and not values["WORKLOAD_TEST_PATHS"].strip(":"):
    print(f"{path}: WORKLOAD_TEST_PATHS is empty; test-proof and test-weakening need the tests named (docs/adr/0053 §1)")
    sys.exit(1)

for key in ("TEST_CMD", "SAST_CMD", "MUTATION_CMD"):
    value = values.get(key, "")
    if key in values and not value:
        print(f"{path}: {key} is empty; every toolchain gate needs its command (docs/adr/0042 §1)")
        sys.exit(1)
    if "$" in value or "#" in value:
        print(f"{path}: {key} holds a `$` or `#`, which make's include would expand or cut (docs/adr/0042 §1)")
        sys.exit(1)

image = values.get("TOOLCHAIN_IMAGE", "")
if "TOOLCHAIN_IMAGE" in values:
    image_re = re.compile(r"^[a-z0-9]([a-z0-9._/-]*[a-z0-9])?(:[A-Za-z0-9._-]+)?(@sha256:[0-9a-f]{64})?$")
    if image == "workbench":
        pass
    elif image.startswith("compose:"):
        if not re.match(r"^compose:[A-Za-z0-9_.-]+$", image):
            print(f"{path}: TOOLCHAIN_IMAGE={image!r} is not compose:<service> (docs/adr/0042 §2)")
            sys.exit(1)
    elif not image_re.match(image):
        print(f"{path}: TOOLCHAIN_IMAGE={image!r} is none of workbench, compose:<service> or an image reference (docs/adr/0042 §2)")
        sys.exit(1)

if "WORKLOAD_HEALTH_PROBE" in values:
    probe = values["WORKLOAD_HEALTH_PROBE"]
    ok_probe = probe == "" or bool(re.fullmatch(r"http:/\S*", probe)) or probe == "tcp"
    if not ok_probe:
        m = re.fullmatch(r"tcp:([0-9]+)", probe)
        ok_probe = bool(m and 1 <= int(m.group(1)) <= 65535)
    if not ok_probe:
        print(f"{path}: WORKLOAD_HEALTH_PROBE={probe!r} must be empty, http:/<path>, tcp or tcp:<port> (docs/adr/0098)")
        sys.exit(1)

seen = set()
dupes = sorted({k for k in keys if k in seen or seen.add(k)})
if dupes:
    print(f"{path} defines a key more than once: {', '.join(dupes)}")
    sys.exit(1)

optional_keys = {"WORKLOAD_HEALTH_PROBE"}
actual = set(keys)
extra = sorted(actual - expected - optional_keys)
missing = sorted(expected - actual)
if extra or missing:
    bits = []
    if extra:
        bits.append("extra: " + ", ".join(extra))
    if missing:
        bits.append("missing: " + ", ".join(missing))
    print(f"{path} must define the twelve docs/adr/0030 §2, 0042 §1 and 0053 §1 keys plus optional WORKLOAD_HEALTH_PROBE ({'; '.join(bits)})")
    sys.exit(1)
PY
  ) && ok=1 || ok=0
  [ "$ok" -eq 1 ] || nogo "$reason"

  toolchain=$(grep -v '^[[:space:]]*#' "$f" | sed -n 's/^TOOLCHAIN_IMAGE=//p')
  if [ ! -f "$cd_/compose.yaml" ]; then
    # No plane half (docs/adr/0042 §7): the gates half is all there is,
    # and a compose: toolchain would name a service that does not exist.
    case "$toolchain" in
      compose:*) nogo "$f: TOOLCHAIN_IMAGE=$toolchain needs $cd_/compose.yaml, which does not exist (docs/adr/0042 §7)" ;;
    esac
  elif [ "${LIGHTS_OUT_FLAVOUR:-}" = gates ] && ! command -v docker-compose >/dev/null 2>&1; then
    # The gates image (docs/adr/0057 §3) has no docker-compose and no plane:
    # the plane half is not rendered here, and the gates never use it (the
    # image refuses a compose: toolchain). ./go attach checks it from a clone.
    printf 'workloads: %s has a plane half, not rendered in the gates image; ./go attach checks it from a clone\n' "$cd_" >&2
  else

  # (b) the merged project, with this workload's compose.yaml explicit,
  # shows the three fixed services (docs/adr/0030 §3). --profile canary
  # --profile test --profile crew: `config` renders only the default-profile
  # services unless a profile is explicitly enabled (confirmed empirically,
  # docker-compose v5.0.1), so backend-canary, backend-test and the harnesses'
  # own services (and what they depend on) would otherwise never appear in the
  # output at all. stderr goes to a log, not
  # into $config_json: any warning on stderr on an otherwise-successful run
  # would land inside the string the next step parses as JSON.
  set +e
  config_json=$(WORKLOAD="$wd" WORKLOAD_CONTRACT="$cd_" docker-compose --profile canary --profile test --profile crew \
    -f compose.yaml -f "$cd_/compose.yaml" config --format json 2>"$err_log")
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    cat "$err_log" >&2
    nogo "docker-compose config failed for WORKLOAD=$wd; see stderr"
  fi

  reason=$(python3 - "$wd" "$config_json" "$toolchain" <<'PY'
import json
import sys

wd, config_json, toolchain = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    data = json.loads(config_json)
except ValueError as exc:
    print(f"{wd}: docker-compose config did not print valid JSON: {exc}")
    sys.exit(1)

services = data.get("services", {})
want_profiles = {
    "backend": [],
    "backend-canary": ["canary"],
    "backend-test": ["test"],
}
missing = [s for s in want_profiles if s not in services]
if missing:
    print(f"{wd}: docker-compose config is missing service(s): {', '.join(missing)}")
    sys.exit(1)

for svc_name, want in want_profiles.items():
    profiles = services[svc_name].get("profiles") or []
    if profiles != want:
        print(f"{wd}: service {svc_name} has profiles {profiles}, want {want}")
        sys.exit(1)

backend_image = services["backend"].get("image", "")
if not backend_image.startswith("localhost:5050/lights-out-backend:"):
    print(f"{wd}: service backend image is {backend_image!r}, want the localhost:5050/lights-out-backend: prefix (docs/adr/0030 §3)")
    sys.exit(1)

if "build" in services["backend-canary"]:
    print(f"{wd}: service backend-canary has a build: section; docs/adr/0030 §3 says only the verified digest runs, never a build")
    sys.exit(1)
if not services["backend-canary"].get("image"):
    print(f"{wd}: service backend-canary has no image")
    sys.exit(1)

if toolchain.startswith("compose:"):
    svc = toolchain[len("compose:"):]
    if svc not in services:
        print(f"{wd}: TOOLCHAIN_IMAGE={toolchain} names a service the merged project does not define (docs/adr/0042 §2)")
        sys.exit(1)
    if (services[svc].get("profiles") or []) != ["test"]:
        print(f"{wd}: TOOLCHAIN_IMAGE={toolchain}: service {svc} must have exactly the profile test (docs/adr/0042 §2)")
        sys.exit(1)
PY
  ) && ok=1 || ok=0
  [ "$ok" -eq 1 ] || nogo "$reason"

  # (f) the harnesses are confined on this project (docs/adr/0046), and the
  # first project judged is also judged with each defect planted.
  reason=$(python3 -c "$crew_check" "$config_json" "$harnesses" check) && ok=1 || ok=0
  [ "$ok" -eq 1 ] || nogo "$wd: $reason"
  if [ "$crew_selfchecked" -eq 0 ]; then
    reason=$(python3 -c "$crew_check" "$config_json" "$harnesses" selfcheck) && ok=1 || ok=0
    [ "$ok" -eq 1 ] || nogo "$reason"
    crew_selfchecked=1
  fi

  # (c) the flag file defines release-freeze and, when non-empty, this
  # workload's own kill switch -- read from this workload's own file, never
  # the ambient $WORKLOAD_KILL_SWITCH, which only reflects the enclosing make's WORKLOAD.
  kill_switch=$(grep -v '^[[:space:]]*#' "$f" | sed -n 's/^WORKLOAD_KILL_SWITCH=//p')

  flags_file="$cd_/flags/flags.json"
  [ -f "$flags_file" ] || nogo "$flags_file does not exist"

  reason=$(python3 - "$flags_file" "$kill_switch" <<'PY'
import json
import sys

path, kill_switch = sys.argv[1], sys.argv[2]
with open(path) as f:
    try:
        data = json.load(f)
    except ValueError as exc:
        print(f"{path} is not valid JSON: {exc}")
        sys.exit(1)

flags = data.get("flags", {})
required = ["release-freeze"] + ([kill_switch] if kill_switch else [])
missing = [n for n in required if n not in flags]
if missing:
    print(f"{path} does not define flag(s): {', '.join(missing)}")
    sys.exit(1)
PY
  ) && ok=1 || ok=0
  [ "$ok" -eq 1 ] || nogo "$reason"

  fi

  # (d) the brief, with the two sections the dev-worker's prompt points at.
  brief="$cd_/worker.md"
  [ -f "$brief" ] || nogo "$brief does not exist"
  for heading in '## Scope' '## Tests'; do
    grep -qx "$heading" "$brief" || nogo "$brief has no '$heading' section"
  done

  # (g) the hosts the worker may reach besides its provider, if the contract
  # names any (docs/adr/0046 §5).
  if [ -f "$cd_/crew-egress.txt" ]; then
    reason=$(crew_egress_flaw "$cd_/crew-egress.txt")
    [ -z "$reason" ] || nogo "$cd_/crew-egress.txt: $reason (docs/adr/0046 §5)"
  fi

  # (h) the reviewers' role files and the dev-worker's MCP servers, through
  # the one reader crew.sh and review.sh use (docs/adr/0073 §10).
  reason=$(python3 core/agents.py check --contract "$cd_") \
    || nogo "$cd_: ${reason#NO-GO: agents — }"

  # (e) the thresholds, through the one reader the gates use; a looser
  # override is reported on stderr, not refused.
  reason=$(python3 core/policy.py --platform policy/merge.env --workload-env "$f" \
    --overrides "$cd_/policy.env" --out "$policy_out") || nogo "$reason"
done

go
