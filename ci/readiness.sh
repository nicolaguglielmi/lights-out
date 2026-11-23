#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-readiness: gates/production-readiness.sh and the conformance record's
# hash (docs/adr/0057 §9, §10), cold: no plane, no engine, no real endpoint.
# Written from the ADR, not from the gate. The gate runs from the repository
# root in an empty environment but PATH and HOME, plus the variables a case
# sets, against a .env file and a conformance directory the case builds
# (READINESS_ENV_FILE, CONFORMANCE_DIR) and, for `promql`, a stub endpoint
# on 127.0.0.1 (python3's http.server) that answers the PromQL instant
# queries derived from the active slo.promql (`count(target_info)` for
# argo-rollouts, `count(envoy_cluster_upstream_rq_completed)` by default)
# with one sample, none, a refusal without the
# auth header, or not at all (a port nobody listens on).
#   hash       adapter_hash (ci/lib.sh): one sha256 hex digest; the same twice,
#              for a copy at the same relative path elsewhere and after a
#              touch; different when any one byte of any file changes (first
#              byte, appended byte), a file is added (empty; in a
#              subdirectory), renamed or removed.
#   contract   every case: exactly one stdout line, no ANSI, exit 0 for GO
#              (the line starts `GO: production-readiness — `) and 1 for
#              NO-GO (`NO-GO: production-readiness — `, details on stderr);
#              no secret value on stdout or stderr.
#   GO         DELIVERY=argo-rollouts with a record of today's hash, promql on
#              the stub, a clean .env, CONSOLE_AUTH=proxy, an identity header
#              and a launcher; also with no .env file at all, and with
#              replace-me or empty values for every secret suffix.
#   L0         the profile's defaults (envoy-canary, prometheus, no record, no
#              CONSOLE_*): NO-GO naming delivery, metrics and human gate.
#   one defect each, from the GO case, the NO-GO line naming only that item:
#     delivery     no record; a record with another hash; a record of another
#                  adapter only; a record with no HASH; an adapter without
#                  rollback.sh; DELIVERY unset or naming no adapter; a record
#                  gone stale because the adapter changed after it was written
#     metrics      METRICS=prometheus; METRICS_URL empty or unset; an empty
#                  result; nothing listening; an endpoint that wants a header
#                  METRICS_AUTH_HEADER (in the environment) supplies
#     secrets      a value in .env for a key ending _KEY, _TOKEN, _SECRET,
#                  _PASSWORD, or METRICS_AUTH_HEADER
#     human gate   GO_FOR_LAUNCH=GO in the environment or in .env;
#                  CONSOLE_AUTH loopback or unset; no CONSOLE_IDENTITY_HEADER
#                  (unset, empty); CONSOLE_LAUNCHERS unset, empty, or only
#                  commas and spaces
#   several    two items fail: both named; all four fail: all four named.
#   defaults   with READINESS_ENV_FILE and CONFORMANCE_DIR unset, `.env` and
#              `.crew/conformance` of the working directory are the ones read.
# The checks must be able to fail: the hash judge is run on stand-in hash
# functions that each get one thing wrong, and the gate judge on stand-in
# gates that each break one clause of the contract; each must be found
# wanting, and a right stand-in must pass.
# shellcheck disable=SC2086,SC2329 # $tmo is a command prefix or nothing; the stand-in hash functions and cleanup are called by name
set -eu
name=readiness
. ci/lib.sh

for t in python3 curl jq sha256sum; do
  command -v "$t" >/dev/null 2>&1 || nogo "$t is not on PATH (./go ci runs in the workbench)"
done
[ -f gates/production-readiness.sh ] || nogo "gates/production-readiness.sh not found"
command -v adapter_hash >/dev/null 2>&1 || nogo "ci/lib.sh has no adapter_hash (docs/adr/0057 §9: one function for the record's writer and reader)"
adapter=argo-rollouts
[ -f "adapters/$adapter/rollback.sh" ] || nogo "adapters/$adapter/rollback.sh not found: the constructed GO case needs a delivery adapter with one (docs/adr/0051)"
bare=""
for d in adapters/*/; do
  b=$(basename "$d")
  [ -f "adapters/$b/rollback.sh" ] || { bare=$b; break; }
done
[ -n "$bare" ] || nogo "every adapter has a rollback.sh: the case 'an adapter without rollback.sh' needs one that has none"
l0_delivery=$(sed -n 's/^DELIVERY=//p' profiles/l0.env)
l0_metrics=$(sed -n 's/^METRICS=//p' profiles/l0.env)
{ [ "$l0_delivery" = envoy-canary ] && [ "$l0_metrics" = prometheus ]; } \
  || nogo "profiles/l0.env names DELIVERY=$l0_delivery METRICS=$l0_metrics: the L0 case expects envoy-canary and prometheus (docs/adr/0057 §10); update this check with the profile"

repo=$(pwd -P)
w=$(mktemp -d)
pids=""
cleanup() {
  for p in $pids; do kill "$p" 2>/dev/null || :; done
  rm -rf "$w"
}
trap cleanup EXIT
esc=$(printf '\033')
gate=gates/production-readiness.sh
tmo=""
command -v timeout >/dev/null 2>&1 && tmo="timeout 120"
mkdir "$w/c"

lines() { awk 'END{print NR}' "$1"; }

# --- the hash ---------------------------------------------------------------------
# hash_flaws FN: what is wrong with the hash function FN, called as `FN adapters/<name>`
# from a directory, one line each.
hash_flaws() {
  fn=$1
  d=adapters/$adapter
  rm -rf "$w/h"
  mkdir -p "$w/h/adapters"
  cp -R "$d" "$w/h/adapters/"
  hh() { (cd "$w/h" && "$fn" "$d"); }
  h0=$(hh) || { echo "$fn failed on $d"; return 0; }
  printf '%s\n' "$h0" | grep -Eq '^[0-9a-f]{64}$' || { echo "$fn printed [$h0], want one sha256 digest in hex"; return 0; }
  [ "$(hh)" = "$h0" ] || echo "two calls on an unchanged directory give two hashes"
  [ "$("$fn" "$d")" = "$h0" ] || echo "a copy of the directory, at the same relative path elsewhere, hashes differently from the original"
  find "$w/h/adapters" -type f -exec touch -t 200001010000 {} +
  [ "$(hh)" = "$h0" ] || echo "the files' modification times change the hash"
  find "$w/h/adapters" -type f | LC_ALL=C sort > "$w/h.files"
  while IFS= read -r f; do
    rel=${f#"$w/h/"}
    cp "$f" "$w/h.bak"
    if [ -s "$f" ]; then
      first=$(head -c 1 "$f")
      if [ "$first" = Z ]; then sub=Y; else sub=Z; fi
      { printf '%s' "$sub"; tail -c +2 "$f"; } > "$w/h.new"
      cat "$w/h.new" > "$f"
      [ "$(hh)" != "$h0" ] || echo "changing the first byte of $rel does not change the hash"
      cat "$w/h.bak" > "$f"
    fi
    printf 'Z' >> "$f"
    [ "$(hh)" != "$h0" ] || echo "appending a byte to $rel does not change the hash"
    cat "$w/h.bak" > "$f"
    mv "$f" "$f.renamed"
    [ "$(hh)" != "$h0" ] || echo "renaming $rel does not change the hash"
    mv "$f.renamed" "$f"
    rm "$f"
    [ "$(hh)" != "$h0" ] || echo "removing $rel does not change the hash"
    cp "$w/h.bak" "$f"
  done < "$w/h.files"
  [ "$(hh)" = "$h0" ] || echo "the hash is not back after the bytes are restored"
  : > "$w/h/$d/zz-empty"
  [ "$(hh)" != "$h0" ] || echo "adding an empty file does not change the hash"
  rm "$w/h/$d/zz-empty"
  mkdir -p "$w/h/$d/zz/sub"
  printf a > "$w/h/$d/zz/sub/n.txt"
  h1=$(hh)
  [ "$h1" != "$h0" ] || echo "adding a file in a subdirectory does not change the hash"
  printf b > "$w/h/$d/zz/sub/n.txt"
  [ "$(hh)" != "$h1" ] || echo "changing a file in a subdirectory does not change the hash"
  rm -r "$w/h/$d/zz"
  [ "$(hh)" = "$h0" ] || echo "the hash is not back after the files are restored"
  :
}

# Stand-ins: one right, each of the others gets one thing wrong.
hash_ok() { find "$1" -type f | LC_ALL=C sort | while IFS= read -r f; do printf '%s\n' "$f"; cat "$f"; done | sha256sum | cut -d' ' -f1; }
hash_const() { printf '%064d\n' 0; }
hash_names() { find "$1" -type f | LC_ALL=C sort | sha256sum | cut -d' ' -f1; }
hash_contents() { find "$1" -type f | LC_ALL=C sort | while IFS= read -r f; do cat "$f"; done | sha256sum | cut -d' ' -f1; }
hash_toplevel() { find "$1" -maxdepth 1 -type f | LC_ALL=C sort | while IFS= read -r f; do printf '%s\n' "$f"; cat "$f"; done | sha256sum | cut -d' ' -f1; }
hash_nomd() { find "$1" -type f ! -name '*.md' | LC_ALL=C sort | while IFS= read -r f; do printf '%s\n' "$f"; cat "$f"; done | sha256sum | cut -d' ' -f1; }
hash_counter() { n=$(cat "$w/ctr" 2>/dev/null || echo 0); echo $((n + 1)) > "$w/ctr"; printf '%s' "$n" | sha256sum | cut -d' ' -f1; }
hash_abs() { find "$PWD/$1" -type f | LC_ALL=C sort | while IFS= read -r f; do printf '%s\n' "$f"; cat "$f"; done | sha256sum | cut -d' ' -f1; }
hash_mtime() { find "$1" -type f | LC_ALL=C sort | while IFS= read -r f; do ls -l "$f"; cat "$f"; done | sha256sum | cut -d' ' -f1; }

found=$(hash_flaws adapter_hash)
[ -z "$found" ] || nogo "adapter_hash (ci/lib.sh): $(printf '%s\n' "$found" | head -n 1)" "$found"

# must_flag FN WANT: the hash judge, run on the stand-in FN, names WANT.
must_flag() {
  got=$(hash_flaws "$1")
  case "$got" in
    *"$2"*) ;;
    "") nogo "ci/readiness.sh cannot fail: the hash judge accepted $1" ;;
    *) nogo "ci/readiness.sh named the wrong problem for $1" "wanted: $2" "got: $got" ;;
  esac
}
got=$(hash_flaws hash_ok)
[ -z "$got" ] || nogo "ci/readiness.sh refuses a right hash function (hash_ok)" "$got"
must_flag hash_const "changing the first byte"
must_flag hash_names "changing the first byte"
must_flag hash_contents "renaming"
must_flag hash_contents "adding an empty file"
must_flag hash_toplevel "in a subdirectory"
must_flag hash_nomd "removing"
must_flag hash_counter "two calls"
must_flag hash_abs "same relative path"
must_flag hash_mtime "modification times"

# --- the stub endpoint --------------------------------------------------------------
cat > "$w/stub.py" <<'PY'
import http.server, json, os, sys, urllib.parse

mode, portfile = sys.argv[1:3]
SAMPLE = {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [0, "1"]}]}}
EMPTY = {"status": "success", "data": {"resultType": "vector", "result": []}}
VALID_QUERIES = ("count(target_info)", "count(envoy_cluster_upstream_rq_completed)")


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query).get("query", [""])[0]
        if u.path != "/api/v1/query":
            self.send_error(404)
            return
        if mode == "auth" and self.headers.get("Authorization") != "Bearer CANARY-2":
            self.send_error(401)
            return
        body = SAMPLE if mode in ("good", "auth") and q in VALID_QUERIES else EMPTY
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
with open(portfile + ".tmp", "w") as f:
    f.write(str(srv.server_address[1]))
os.rename(portfile + ".tmp", portfile)
srv.serve_forever()
PY
serve() {
  python3 -I "$w/stub.py" "$1" "$w/$1.port" >/dev/null 2>&1 &
  pids="$pids $!"
  i=0
  while [ ! -s "$w/$1.port" ] && [ "$i" -lt 100 ]; do
    sleep 0.1 2>/dev/null || sleep 1
    i=$((i + 1))
  done
  [ -s "$w/$1.port" ] || nogo "the test's own stub ($1) did not start: python3 http.server on 127.0.0.1"
}
url() { printf 'http://127.0.0.1:%s' "$(cat "$w/$1.port")"; }
serve good
serve empty
serve auth
dead=$(python3 -I -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
# The stubs answer what they claim, through the adapter's own query.sh.
# samples URL [HEADER]: how many samples query.sh gets for count(target_info) from URL.
samples() { METRICS_URL=$1 METRICS_AUTH_HEADER=${2:-} sh adapters/promql/query.sh 'count(target_info)' 2>/dev/null | jq '.data.result | length' 2>/dev/null; }
[ "$(samples "$(url good)")" = 1 ] || nogo "the test's own stub (good) does not answer one sample to count(target_info)"
[ "$(samples "$(url empty)")" = 0 ] || nogo "the test's own stub (empty) does not answer an empty result"
[ "$(samples "$(url auth)")" = "" ] || nogo "the test's own stub (auth) answers without the header"
[ "$(samples "$(url auth)" 'Authorization: Bearer CANARY-2')" = 1 ] || nogo "the test's own stub (auth) refuses the right header"
[ "$(samples "http://127.0.0.1:$dead")" = "" ] || nogo "something listens on 127.0.0.1:$dead, which the 'nothing listening' case needs free"

# --- the cases' building blocks ------------------------------------------------------
# A .env with the values .env.example declares: placeholders and empties for every
# secret suffix, names that only look like secrets, and a comment holding a value.
cat > "$w/env.clean" <<'ENV'
# A comment.
APP_PORT=9000
GEMINI_API_KEY=replace-me
ANTHROPIC_API_KEY=
OPENAI_API_KEY=replace-me
ZAI_API_KEY=replace-me
GITHUB_REPO=owner/name
GITHUB_TOKEN=
WEBHOOK_SECRET=replace-me
DB_PASSWORD=
METRICS_AUTH_HEADER=
KEY_ID=not-a-secret-name
TOKEN_PATH=/run/secrets/token
# OLD_API_KEY=CANARY-1
ENV
adapter_hash_now=$(adapter_hash "adapters/$adapter")
bare_hash_now=$(adapter_hash "adapters/$bare")

# record DIR ADAPTER HASH: what gates/delivery-conformance.sh writes on GO (§9).
record() { printf 'ADAPTER=%s\nHASH=%s\nAT=2026-11-08T12:00:00Z\n' "$2" "$3" > "$1/delivery-$2.env"; }
# newcase ID: case ID as the constructed GO: its .env, its records, the variables it sets.
newcase() {
  c=$w/c/$1
  rm -rf "$c"
  mkdir -p "$c/conf"
  cp "$w/env.clean" "$c/env"
  record "$c/conf" "$adapter" "$adapter_hash_now"
  {
    printf 'DELIVERY=%s\n' "$adapter"
    printf 'METRICS=promql\n'
    printf 'METRICS_URL=%s\n' "$(url good)"
    printf 'CONSOLE_AUTH=proxy\n'
    printf 'CONSOLE_IDENTITY_HEADER=X-Auth-User\n'
    printf 'CONSOLE_LAUNCHERS=alice\n'
  } > "$c/vars"
}
unsetv() { grep -v "^$2=" "$w/c/$1/vars" > "$w/c/$1/vars.new" || :; mv "$w/c/$1/vars.new" "$w/c/$1/vars"; }
setv() { unsetv "$1" "$2"; printf '%s=%s\n' "$2" "$3" >> "$w/c/$1/vars"; }
# setenvfile ID KEY=VALUE: the .env file of case ID has that line instead of KEY's.
setenvfile() { k=${2%%=*}; grep -v "^$k=" "$w/c/$1/env" > "$w/c/$1/env.new" || :; printf '%s\n' "$2" >> "$w/c/$1/env.new"; mv "$w/c/$1/env.new" "$w/c/$1/env"; }

# gate_run ID: $gate in a clean environment with case ID's variables; stdout in
# $c/out, stderr in $c/err, the status in $c/rc. A case with a `cwd` file runs
# there; one with a `defaults` file leaves READINESS_ENV_FILE and CONFORMANCE_DIR
# unset.
gate_run() {
  c=$w/c/$1
  dir=$repo
  [ ! -f "$c/cwd" ] || dir=$(cat "$c/cwd")
  if [ -f "$c/defaults" ]; then set --; else set -- "READINESS_ENV_FILE=$c/env" "CONFORMANCE_DIR=$c/conf"; fi
  while IFS= read -r line; do set -- "$@" "$line"; done < "$c/vars"
  (cd "$dir" && env -i PATH="$PATH" HOME="$w" "$@" $tmo sh "$gate") > "$c/out" 2> "$c/err" && echo 0 > "$c/rc" || echo $? > "$c/rc"
}

# flaws ID WORD...: what is wrong with the gate's answer on case ID, one line each. No
# WORD: it must be GO. WORDs: it must be NO-GO naming those items and no other.
flaws() {
  c=$w/c/$1
  shift
  rc=$(cat "$c/rc")
  n=$(lines "$c/out")
  [ "$n" = 1 ] || { echo "stdout has $n lines, want exactly one"; return 0; }
  line=$(cat "$c/out")
  case "$line" in *"$esc"*) echo "an ANSI escape sequence on stdout"; return 0 ;; esac
  if grep -q 'CANARY-' "$c/out" "$c/err"; then echo "a secret's value is on stdout or stderr"; fi
  if [ $# -eq 0 ]; then
    [ "$rc" = 0 ] || echo "want exit 0, got $rc"
    case "$line" in "GO: production-readiness — "?*) ;; *) echo "want 'GO: production-readiness — <detail>', got: $line" ;; esac
    return 0
  fi
  [ "$rc" = 1 ] || echo "want exit 1, got $rc"
  case "$line" in "NO-GO: production-readiness — "?*) ;; *) echo "want 'NO-GO: production-readiness — <items>', got: $line"; return 0 ;; esac
  [ -s "$c/err" ] || echo "a NO-GO with nothing on stderr: the details belong there"
  reason=${line#"NO-GO: production-readiness — "}
  for item in delivery metrics secrets "human gate"; do
    want=""
    for x; do [ "$x" != "$item" ] || want=1; done
    case "$reason" in *"$item"*) has=1 ;; *) has="" ;; esac
    [ "$want" = "$has" ] && continue
    if [ -n "$want" ]; then echo "the line does not name '$item', which fails"; else echo "the line names '$item', which holds"; fi
  done
  :
}

# expect ID DESC WORD...: the gate, on case ID, is as flaws says.
expect() {
  id=$1
  desc=$2
  shift 2
  gate_run "$id"
  found=$(flaws "$id" "$@")
  [ -z "$found" ] || nogo "$desc: $(printf '%s\n' "$found" | head -n 1)" "case: $desc" "$found" "stdout:" "$(cat "$w/c/$id/out")" "stderr:" "$(cat "$w/c/$id/err")"
}

# --- the cases ----------------------------------------------------------------------
newcase go
expect go "the constructed GO"

newcase l0
rm -f "$w/c/l0/conf"/*
printf 'DELIVERY=%s\nMETRICS=%s\n' "$l0_delivery" "$l0_metrics" > "$w/c/l0/vars"
expect l0 "the L0 defaults" delivery metrics "human gate"

# delivery
newcase d-norecord
rm -f "$w/c/d-norecord/conf"/*
expect d-norecord "no conformance record" delivery
newcase d-otherhash
record "$w/c/d-otherhash/conf" "$adapter" "$(printf 'another adapter' | sha256sum | cut -d' ' -f1)"
expect d-otherhash "a record with another hash" delivery
newcase d-otheradapter
rm -f "$w/c/d-otheradapter/conf"/*
record "$w/c/d-otheradapter/conf" envoy-canary "$(adapter_hash adapters/envoy-canary)"
expect d-otheradapter "a record of another adapter only" delivery
newcase d-nohash
printf 'ADAPTER=%s\nAT=2026-11-08T12:00:00Z\n' "$adapter" > "$w/c/d-nohash/conf/delivery-$adapter.env"
expect d-nohash "a record with no HASH" delivery
newcase d-emptyhash
printf 'ADAPTER=%s\nHASH=\nAT=2026-11-08T12:00:00Z\n' "$adapter" > "$w/c/d-emptyhash/conf/delivery-$adapter.env"
expect d-emptyhash "a record with an empty HASH" delivery
newcase d-norollback
rm -f "$w/c/d-norollback/conf"/*
record "$w/c/d-norollback/conf" "$bare" "$bare_hash_now"
setv d-norollback DELIVERY "$bare"
expect d-norollback "adapters/$bare has no rollback.sh, and a right record" delivery
newcase d-unset
unsetv d-unset DELIVERY
expect d-unset "DELIVERY unset" delivery
newcase d-nosuch
setv d-nosuch DELIVERY no-such-adapter
record "$w/c/d-nosuch/conf" no-such-adapter "$adapter_hash_now"
expect d-nosuch "DELIVERY names no adapter" delivery

# A record that was right, then the adapter changed: the gate runs in a copy of
# ci/, gates/ and adapters/ (core/ and profiles/ linked), once as it was and once
# with a line added to adapters/<adapter>/deploy.sh.
newroot() {
  rm -rf "$1"
  mkdir -p "$1"
  cp -R ci gates adapters "$1/"
  ln -s "$repo/core" "$1/core"
  ln -s "$repo/profiles" "$1/profiles"
  [ ! -d verify ] || ln -s "$repo/verify" "$1/verify"
}
newroot "$w/root1"
newroot "$w/root2"
printf '# changed after the record was written\n' >> "$w/root2/adapters/$adapter/deploy.sh"
newcase d-fresh
echo "$w/root1" > "$w/c/d-fresh/cwd"
expect d-fresh "the record of an adapter unchanged since, in a copy of the tree"
newcase d-stale
echo "$w/root2" > "$w/c/d-stale/cwd"
expect d-stale "the record of an adapter changed since" delivery

# metrics
newcase m-prometheus
setv m-prometheus METRICS prometheus
expect m-prometheus "METRICS=prometheus on an endpoint that answers" metrics
newcase m-urlempty
setv m-urlempty METRICS_URL ""
expect m-urlempty "METRICS_URL empty" metrics
newcase m-urlunset
unsetv m-urlunset METRICS_URL
expect m-urlunset "METRICS_URL unset" metrics
newcase m-empty
setv m-empty METRICS_URL "$(url empty)"
expect m-empty "the endpoint answers an empty result" metrics
newcase m-dead
setv m-dead METRICS_URL "http://127.0.0.1:$dead"
expect m-dead "nothing listens at METRICS_URL" metrics
newcase m-noauth
setv m-noauth METRICS_URL "$(url auth)"
expect m-noauth "the endpoint wants a header and METRICS_AUTH_HEADER is not set" metrics
newcase m-auth
setv m-auth METRICS_URL "$(url auth)"
setv m-auth METRICS_AUTH_HEADER "Authorization: Bearer CANARY-2"
expect m-auth "the endpoint wants a header and METRICS_AUTH_HEADER, in the environment, is it"

# secrets: the five suffixes, then what must pass
for kv in 'GEMINI_API_KEY=CANARY-1' 'GITHUB_TOKEN=CANARY-1' 'WEBHOOK_SECRET=CANARY-1' \
  'DB_PASSWORD=CANARY-1' 'METRICS_AUTH_HEADER=Authorization: Bearer CANARY-1'; do
  newcase "s-${kv%%=*}"
  setenvfile "s-${kv%%=*}" "$kv"
  expect "s-${kv%%=*}" ".env holds a value for ${kv%%=*}" secrets
done
newcase s-placeholders
for k in GEMINI_API_KEY GITHUB_TOKEN WEBHOOK_SECRET DB_PASSWORD METRICS_AUTH_HEADER; do setenvfile s-placeholders "$k=replace-me"; done
expect s-placeholders ".env holds replace-me for every suffix"
newcase s-empties
for k in GEMINI_API_KEY GITHUB_TOKEN WEBHOOK_SECRET DB_PASSWORD METRICS_AUTH_HEADER; do setenvfile s-empties "$k="; done
expect s-empties ".env holds an empty value for every suffix"
newcase s-nofile
rm "$w/c/s-nofile/env"
expect s-nofile "no .env file at all"

# human gate
newcase h-gfl-env
setv h-gfl-env GO_FOR_LAUNCH GO
expect h-gfl-env "GO_FOR_LAUNCH=GO in the environment" "human gate"
newcase h-gfl-file
printf 'GO_FOR_LAUNCH=GO\n' >> "$w/c/h-gfl-file/env"
expect h-gfl-file "GO_FOR_LAUNCH=GO in the .env file" "human gate"
newcase h-loopback
setv h-loopback CONSOLE_AUTH loopback
expect h-loopback "CONSOLE_AUTH=loopback" "human gate"
newcase h-authunset
unsetv h-authunset CONSOLE_AUTH
expect h-authunset "CONSOLE_AUTH unset" "human gate"
newcase h-noheader
unsetv h-noheader CONSOLE_IDENTITY_HEADER
expect h-noheader "no CONSOLE_IDENTITY_HEADER" "human gate"
newcase h-emptyheader
setv h-emptyheader CONSOLE_IDENTITY_HEADER ""
expect h-emptyheader "CONSOLE_IDENTITY_HEADER empty" "human gate"
newcase h-nolaunchers
unsetv h-nolaunchers CONSOLE_LAUNCHERS
expect h-nolaunchers "CONSOLE_LAUNCHERS unset" "human gate"
newcase h-emptylaunchers
setv h-emptylaunchers CONSOLE_LAUNCHERS ""
expect h-emptylaunchers "CONSOLE_LAUNCHERS empty" "human gate"
newcase h-blanklaunchers
setv h-blanklaunchers CONSOLE_LAUNCHERS " , ,  ,"
expect h-blanklaunchers "CONSOLE_LAUNCHERS only commas and spaces" "human gate"

# several at once
newcase x-two
rm -f "$w/c/x-two/conf"/*
setenvfile x-two "DB_PASSWORD=CANARY-1"
expect x-two "no record and a secret" delivery secrets
newcase x-four
rm -f "$w/c/x-four/conf"/*
setv x-four METRICS prometheus
setv x-four GO_FOR_LAUNCH GO
setenvfile x-four "GITHUB_TOKEN=CANARY-1"
expect x-four "all four items fail" delivery metrics secrets "human gate"

# The defaults: READINESS_ENV_FILE and CONFORMANCE_DIR unset, the working directory's
# .env and .crew/conformance are read (root1 is the unchanged copy above).
mkdir -p "$w/root1/.crew/conformance"
cp "$w/c/go/conf/delivery-$adapter.env" "$w/root1/.crew/conformance/"
newcase f-secret
echo "$w/root1" > "$w/c/f-secret/cwd"
: > "$w/c/f-secret/defaults"
cp "$w/env.clean" "$w/root1/.env"
printf 'GITHUB_TOKEN=CANARY-1\n' >> "$w/root1/.env"
expect f-secret "the default .env holds a token" secrets
cp "$w/env.clean" "$w/root1/.env"
expect f-secret "the default .env is clean, the default record is right"
rm "$w/root1/.crew/conformance/delivery-$adapter.env"
expect f-secret "no record in the default conformance directory" delivery

# --- the checks must be able to fail ---------------------------------------------------
# A stand-in gate that breaks one clause of the contract, and the case and expectation
# it must be found wanting on; then right ones, that must pass.
mkgate() { printf '%s\n' "$2" > "$w/g-$1.sh"; }
mkgate allgo 'printf "GO: production-readiness — fine\n"'
mkgate nodetail 'printf "GO: production-readiness\n"'
mkgate allnogo 'printf "NO-GO: production-readiness — delivery, metrics, secrets, human gate\n"; echo details >&2; exit 1'
mkgate fewer 'printf "NO-GO: production-readiness — delivery\n"; echo details >&2; exit 1'
mkgate twolines 'printf "GO: production-readiness — fine\nextra\n"'
mkgate ansi 'printf "\033[32mGO: production-readiness — fine\033[0m\n"'
mkgate nogo0 'printf "NO-GO: production-readiness — delivery, metrics, human gate\n"; echo details >&2'
mkgate go1 'printf "GO: production-readiness — fine\n"; exit 1'
mkgate silent 'printf "NO-GO: production-readiness — delivery, metrics, human gate\n"; exit 1'
mkgate leaks 'printf "NO-GO: production-readiness — delivery, metrics, human gate\n"; echo CANARY-leak >&2; exit 1'
mkgate wrongname 'printf "NO-GO: readiness — delivery, metrics, human gate\n"; echo details >&2; exit 1'
mkgate ok_l0 'printf "NO-GO: production-readiness — delivery: no record; metrics: prometheus; human gate: no launchers\n"; echo details >&2; exit 1'
mkgate ok_go 'printf "GO: production-readiness — all four hold\n"'
# reject FILE ID DESC WORD...: FILE as the gate on case ID is found wanting.
reject() {
  gate=$1
  id=$2
  desc=$3
  shift 3
  gate_run "$id"
  got=$(flaws "$id" "$@")
  gate=gates/production-readiness.sh
  [ -n "$got" ] || nogo "ci/readiness.sh cannot fail: it accepted $desc, on case $id"
}
accept() {
  gate=$1
  id=$2
  shift 2
  gate_run "$id"
  got=$(flaws "$id" "$@")
  gate=gates/production-readiness.sh
  [ -z "$got" ] || nogo "ci/readiness.sh refuses a right stand-in gate ($(basename "$1")), on case $id" "$got"
}
accept "$w/g-ok_go.sh" go
accept "$w/g-ok_l0.sh" l0 delivery metrics "human gate"
reject "$w/g-allnogo.sh" go "a gate that says NO-GO on the constructed GO"
reject "$w/g-nodetail.sh" go "a GO line with no detail"
reject "$w/g-twolines.sh" go "two stdout lines"
reject "$w/g-ansi.sh" go "an ANSI sequence"
reject "$w/g-go1.sh" go "GO with exit 1"
reject "$w/g-allgo.sh" l0 "a gate that says GO on the L0 defaults" delivery metrics "human gate"
reject "$w/g-nogo0.sh" l0 "NO-GO with exit 0" delivery metrics "human gate"
reject "$w/g-silent.sh" l0 "NO-GO with nothing on stderr" delivery metrics "human gate"
reject "$w/g-leaks.sh" l0 "a secret's value on stderr" delivery metrics "human gate"
reject "$w/g-wrongname.sh" l0 "a line named readiness, not production-readiness" delivery metrics "human gate"
reject "$w/g-fewer.sh" l0 "a NO-GO that leaves out items that fail" delivery metrics "human gate"
reject "$w/g-allnogo.sh" d-norecord "a NO-GO that names items that hold" delivery
reject "$w/g-allnogo.sh" s-GITHUB_TOKEN "a NO-GO that names items that hold" secrets

go
