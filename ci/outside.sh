#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-outside: docs/adr/0058 & docs/adr/0104 proved cold, no engine, no network.
#   (a) adapters outside the repository, fixtures/outside-adapters/
#       json-files, file-delivery and file-codehost copied out of the checkout:
#       make refuses any goal in one line before anything runs; passing the
#       port's suite / trial+register admits it; linked at adapters/outside/*;
#       one changed byte (even mid-trial) is refused again; a broken copy fails
#       the suite, is not registered and stays refused; a symlink inside the tree
#       is refused; outside/ on a port with no suite (TRACKER) is refused;
#       AGENT_HARNESS, wired to no trial, is refused naming its suite; a suite
#       beside another goal on one command line tries nothing; no trial record
#       outlives a suite.
#   (b) other CIs are surfaces, never judges (§6): the Jenkins, GitLab CI
#       and CircleCI recipes (docs/adr/0095 §10) each run the lights-out
#       wrapper, post its line under their own status context, and that
#       context is none the tower posts or awaits; success only on a GO line.
# Everything runs in a scratch root that links this checkout's files, with
# a real adapters/ of its own, so no link lands in this checkout.
# shellcheck disable=SC2016 # the patterns grep for the Jenkinsfile's own $-expressions
set -eu
name=outside
. ci/lib.sh

for tool in make python3 jq git; do
  command -v "$tool" >/dev/null 2>&1 || nogo "$tool is not on PATH; run this via ./go ci"
done
repo=$(pwd -P)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# --- the scratch root: every top-level entry linked, adapters/ copied as links ---
root=$tmp/root
mkdir -p "$root/adapters"
for f in "$repo"/* "$repo"/.env.example; do
  b=$(basename "$f")
  case "$b" in adapters | .crew | .attached) continue ;; esac
  ln -s "$f" "$root/$b"
done
for d in "$repo"/adapters/*/; do
  b=$(basename "$d")
  [ "$b" != outside ] || continue
  ln -s "$repo/adapters/$b" "$root/adapters/$b"
done
ext=$tmp/ext
mkdir -p "$ext"
cp -R fixtures/outside-adapters/json-files "$ext/json-files"
export JSON_FILES_DIR="$tmp/store"
reg=$tmp/register

# m GOAL VAR=VALUE...: make in the scratch root; stdout in $out, stderr in
# $err, exit code in $rc.
m() {
  goal=$1
  shift
  if (cd "$root" && make -s --no-print-directory "$goal" WORKLOAD=app WORKLOAD_CONTRACT=app WORKLOAD_ATTACHED= \
      ADAPTERS_PATH="$ext" ADAPTERS_REGISTER="$reg" "$@") \
      >"$tmp/out" 2>"$tmp/err" </dev/null; then rc=0; else rc=$?; fi
  out=$(cat "$tmp/out")
  err=$(cat "$tmp/err")
}
# refused LABEL PATTERN...: make stopped before any goal, one stderr line
# holding every PATTERN, nothing on stdout.
refused() {
  label=$1
  shift
  [ "$rc" -ne 0 ] || nogo "a: $label: make ran the goal (exit 0)"
  [ -z "$out" ] || nogo "a: $label: make printed on stdout before refusing: [$out]"
  [ "$(printf '%s\n' "$err" | awk 'END { print NR }')" -eq 1 ] || nogo "a: $label: want one refusal line, got: [$err]"
  for p do
    case "$err" in *"$p"*) ;; *) nogo "a: $label: the refusal does not say [$p]: [$err]" ;; esac
  done
}
admitted() {
  [ "$rc" -eq 0 ] || nogo "a: $1: refused: [$err]"
}

ss="STATE_STORE=outside/json-files"
m help "$ss"
refused "no proof" "$ss is refused" "statestore-conformance GO" "./go statestore-conformance $ss"
[ ! -e "$root/adapters/outside" ] || nogo "a: no proof: the refusal linked the adapter anyway"

m statestore-conformance "$ss"
[ "$rc" -eq 0 ] || nogo "a: statestore-conformance on the outside adapter: exit $rc, [$out]" "$err"
case "$out" in "GO: statestore-conformance"*) ;; *) nogo "a: statestore-conformance printed [$out]" ;; esac
[ "$(printf '%s\n' "$out" | awk 'END { print NR }')" -eq 1 ] || nogo "a: statestore-conformance printed more than one line: [$out]"
digest=$(python3 core/adapters.py hash "$ext/json-files")
awk -F'\t' -v d="$digest" '$1 == "STATE_STORE" && $2 == "json-files" && $3 == d && index($4, "statestore-conformance@") == 1 { f = 1 } END { exit !f }' "$reg" 2>/dev/null \
  || nogo "a: the GO registered no line for sha256 $digest" "$(cat "$reg" 2>/dev/null)"

m help "$ss"
admitted "after its suite"
[ -L "$root/adapters/outside/json-files" ] || nogo "a: admitted but not linked at adapters/outside/json-files"
[ "$(readlink "$root/adapters/outside/json-files")" = "$ext/json-files" ] || nogo "a: the link points at $(readlink "$root/adapters/outside/json-files")"

printf '\n' >> "$ext/json-files/delete.sh"
m help "$ss"
refused "one changed byte" "$ss is refused"

# A broken copy: put accepts a JSON array (case 5 of the suite).
cp -R fixtures/outside-adapters/json-files "$ext/json-broken"
awk '{ sub(/type == "object"/, "type == \"object\" or type == \"array\""); print }' \
  fixtures/outside-adapters/json-files/put.sh > "$ext/json-broken/put.sh"
cmp -s "$ext/json-broken/put.sh" fixtures/outside-adapters/json-files/put.sh && nogo "a: the broken copy is not broken"
before=$(cat "$reg")
m statestore-conformance STATE_STORE=outside/json-broken
[ "$rc" -ne 0 ] || nogo "a: a put that accepts an array passed statestore-conformance: [$out]"
case "$out" in "NO-GO: statestore-conformance — "*) ;; *) nogo "a: the broken copy's suite printed [$out]" ;; esac
[ "$(cat "$reg")" = "$before" ] || nogo "a: a NO-GO registered the broken copy"
m help STATE_STORE=outside/json-broken
refused "a broken copy" "STATE_STORE=outside/json-broken is refused"

cp -R fixtures/outside-adapters/json-files "$ext/json-linked"
ln -s /etc/hostname "$ext/json-linked/extra.sh"
m help STATE_STORE=outside/json-linked
refused "a symlink in the tree" "STATE_STORE=outside/json-linked is refused" "symlink"

m help TRACKER=outside/json-files
refused "a port with no suite" "TRACKER=outside/json-files is refused" "no conformance suite"

m help AGENT_HARNESS=outside/json-files
refused "an outside harness, left (§1)" "AGENT_HARNESS=outside/json-files is refused" "harness-conformance"

# A goal beside a suite is not a trial: it is admitted, or refused, first.
m statestore-conformance STATE_STORE=outside/json-broken help
refused "a suite with another goal" "STATE_STORE=outside/json-broken is refused"
[ ! -e "$tmp/trial-STATE_STORE" ] || nogo "a: a suite that ended left its trial record behind"

# --- a2: DELIVERY=outside/file-delivery & CODE_HOST=outside/file-codehost (ADR 0104) ---
cp -R fixtures/outside-adapters/file-delivery "$ext/file-delivery"
export FILE_DELIVERY_DIR="$tmp/file-delivery-state"
export FILE_DELIVERY_CANARY_URL="http://127.0.0.1:8080"
dlv="DELIVERY=outside/file-delivery"

m help "$dlv"
refused "delivery no proof" "$dlv is refused" "delivery-conformance GO" "./go delivery-conformance $dlv"
[ ! -e "$root/adapters/outside/file-delivery" ] || nogo "a2: no proof linked file-delivery anyway"

(cd "$root" && DELIVERY=outside/file-delivery ADAPTERS_PATH="$ext" ADAPTERS_REGISTER="$reg" python3 core/adapters.py trial DELIVERY) \
  || nogo "a2: trial DELIVERY failed"
[ -e "$tmp/trial-DELIVERY" ] || nogo "a2: trial-DELIVERY record missing"
m help "$dlv"
refused "delivery on trial without ADAPTERS_TRIAL" "$dlv is refused"
m help "$dlv" ADAPTERS_TRIAL=DELIVERY
admitted "file-delivery during trial with ADAPTERS_TRIAL=DELIVERY"

dlv_dir="$root/adapters/outside/file-delivery"
sh "$dlv_dir/ready.sh" >/dev/null || nogo "a2: file-delivery ready.sh failed"
sh "$dlv_dir/deploy.sh" 0.2.0-canary "app:v2" || nogo "a2: file-delivery deploy.sh failed"
st0=$(sh "$dlv_dir/status.sh")
printf '%s' "$st0" | jq -e 'keys | sort == ["canary","stable","weight"]' >/dev/null || nogo "a2: bad delivery status schema: $st0"
[ "$(printf '%s' "$st0" | jq -r '.canary')" = "0.2.0-canary" ] || nogo "a2: expected canary 0.2.0-canary: $st0"
[ "$(printf '%s' "$st0" | jq -r '.weight')" = "0" ] || nogo "a2: expected weight 0: $st0"
curl_url=$(sh "$dlv_dir/canary-url.sh")
[ "$curl_url" = "http://127.0.0.1:8080" ] || nogo "a2: bad canary-url.sh: $curl_url"
shift_rc=0
sh "$dlv_dir/shift.sh" 50 >/dev/null 2>&1 || shift_rc=$?
[ "$shift_rc" -ne 0 ] || nogo "a2: shift.sh 50 accepted invalid percentage"
for pct in 5 25 100; do
  sh "$dlv_dir/shift.sh" "$pct" || nogo "a2: shift.sh $pct failed"
  w=$(sh "$dlv_dir/status.sh" | jq -r '.weight')
  [ "$w" = "$pct" ] || nogo "a2: shift.sh $pct left weight=$w"
done
sh "$dlv_dir/rollback.sh" || nogo "a2: rollback.sh #1 failed"
sh "$dlv_dir/rollback.sh" || nogo "a2: rollback.sh #2 (idempotent) failed"
st_rb=$(sh "$dlv_dir/status.sh")
[ "$(printf '%s' "$st_rb" | jq -r '.weight')" = "0" ] || nogo "a2: rollback left weight != 0: $st_rb"
[ "$(printf '%s' "$st_rb" | jq -r '.canary')" = "null" ] || nogo "a2: rollback left canary != null: $st_rb"
grep -q '^# name: traffic' "$dlv_dir/slo.promql" || nogo "a2: slo.promql missing traffic selector"

dlv_digest=$(python3 core/adapters.py hash "$ext/file-delivery")
(cd "$root" && DELIVERY=outside/file-delivery ADAPTERS_PATH="$ext" ADAPTERS_REGISTER="$reg" python3 core/adapters.py register DELIVERY delivery-conformance) \
  || nogo "a2: register DELIVERY failed"
[ ! -e "$tmp/trial-DELIVERY" ] || nogo "a2: register DELIVERY left trial-DELIVERY behind"
awk -F'\t' -v d="$dlv_digest" '$1 == "DELIVERY" && $2 == "file-delivery" && $3 == d && index($4, "delivery-conformance@") == 1 { f = 1 } END { exit !f }' "$reg" 2>/dev/null \
  || nogo "a2: register DELIVERY wrote no line for sha256 $dlv_digest"

rm -f "$root/adapters/outside/file-delivery"
m help "$dlv"
admitted "file-delivery after register"
[ -L "$root/adapters/outside/file-delivery" ] || nogo "a2: admitted DELIVERY not linked"
[ "$(readlink "$root/adapters/outside/file-delivery")" = "$ext/file-delivery" ] || nogo "a2: DELIVERY link mismatch"

printf '\n' >> "$ext/file-delivery/rollback.sh"
m help "$dlv"
refused "delivery one changed byte" "$dlv is refused"

cp -R fixtures/outside-adapters/file-delivery "$ext/file-delivery-linked"
ln -s /etc/hostname "$ext/file-delivery-linked/extra.sh"
m help DELIVERY=outside/file-delivery-linked
refused "delivery symlink in tree" "DELIVERY=outside/file-delivery-linked is refused" "symlink"

cp -R fixtures/outside-adapters/file-codehost "$ext/file-codehost"
export FILE_CODEHOST_DIR="$tmp/file-codehost-state"
wl_ch="$tmp/wl-ch"
mkdir -p "$wl_ch"
git -C "$wl_ch" init -q -b main 2>/dev/null || { git -C "$wl_ch" init -q && git -C "$wl_ch" symbolic-ref HEAD refs/heads/main; }
printf 'hello\n' > "$wl_ch/README.md"
git -C "$wl_ch" add README.md
GIT_AUTHOR_NAME=ci GIT_AUTHOR_EMAIL=ci@localhost GIT_COMMITTER_NAME=ci GIT_COMMITTER_EMAIL=ci@localhost \
  git -C "$wl_ch" commit -q -m "initial"

ch="CODE_HOST=outside/file-codehost"
m help "$ch"
refused "codehost no proof" "$ch is refused" "codehost-conformance GO" "./go codehost-conformance $ch"
[ ! -e "$root/adapters/outside/file-codehost" ] || nogo "a2: no proof linked file-codehost anyway"

cp -R fixtures/outside-adapters/file-codehost "$ext/file-codehost-tamper"
before_reg=$(cat "$reg")
(cd "$root" && CODE_HOST=outside/file-codehost-tamper ADAPTERS_PATH="$ext" ADAPTERS_REGISTER="$reg" python3 core/adapters.py trial CODE_HOST) \
  || nogo "a2: trial file-codehost-tamper failed"
printf '\n' >> "$ext/file-codehost-tamper/ready.sh"
tamper_rc=0
(cd "$root" && CODE_HOST=outside/file-codehost-tamper ADAPTERS_PATH="$ext" ADAPTERS_REGISTER="$reg" python3 core/adapters.py register CODE_HOST codehost-conformance >/dev/null 2>&1) || tamper_rc=$?
[ "$tamper_rc" -ne 0 ] || nogo "a2: register accepted tree mutated during trial"
(cd "$root" && ADAPTERS_REGISTER="$reg" python3 core/adapters.py untrial CODE_HOST) >/dev/null 2>&1 || true
[ "$(cat "$reg")" = "$before_reg" ] || nogo "a2: mutated-during-trial wrote to register"
[ ! -e "$tmp/trial-CODE_HOST" ] || nogo "a2: untrial left trial-CODE_HOST behind"

(cd "$root" && CODE_HOST=outside/file-codehost ADAPTERS_PATH="$ext" ADAPTERS_REGISTER="$reg" python3 core/adapters.py trial CODE_HOST) \
  || nogo "a2: trial CODE_HOST failed"
m help "$ch"
refused "codehost on trial without ADAPTERS_TRIAL" "$ch is refused"
m help "$ch" ADAPTERS_TRIAL=CODE_HOST
admitted "file-codehost during trial with ADAPTERS_TRIAL=CODE_HOST"
ch_dir="$root/adapters/outside/file-codehost"
sh "$ch_dir/ready.sh" >/dev/null || nogo "a2: file-codehost ready.sh failed"
WORKLOAD="$wl_ch" sh "$ch_dir/bootstrap.sh" || nogo "a2: file-codehost bootstrap.sh failed"
curl_repo=$(sh "$ch_dir/clone-url.sh")
ws_ch="$tmp/ws-ch"
git clone -q "$curl_repo" "$ws_ch"
git -C "$ws_ch" checkout -q -b feat/outside-proof
printf 'change\n' > "$ws_ch/change.txt"
git -C "$ws_ch" add change.txt
GIT_AUTHOR_NAME=ci GIT_AUTHOR_EMAIL=ci@localhost GIT_COMMITTER_NAME=ci GIT_COMMITTER_EMAIL=ci@localhost \
  git -C "$ws_ch" commit -q -m "feat: outside proof"
head_sha=$(git -C "$ws_ch" rev-parse HEAD)
sh "$ch_dir/push.sh" "$ws_ch" feat/outside-proof || nogo "a2: file-codehost push.sh failed"
printf '{"title":"Outside CodeHost proof","body":"ADR 0104 cold smoke"}\n' > "$tmp/pr.json"
pr_out=$(sh "$ch_dir/open-pr.sh" feat/outside-proof "$tmp/pr.json")
pr_num=${pr_out%% *}
[ -n "$pr_num" ] || nogo "a2: open-pr.sh gave no PR number: $pr_out"
pr_out2=$(sh "$ch_dir/open-pr.sh" feat/outside-proof "$tmp/pr.json")
[ "${pr_out2%% *}" = "$pr_num" ] || nogo "a2: open-pr.sh not idempotent: $pr_out vs $pr_out2"
sh "$ch_dir/status.sh" "$head_sha" pending gates "pending" || nogo "a2: status.sh pending failed"
sh "$ch_dir/status.sh" "$head_sha" success gates "GO: gates" || nogo "a2: status.sh success failed"
await_state=$(AWAIT_TIMEOUT=2 sh "$ch_dir/await.sh" "$head_sha" gates)
[ "$await_state" = "success" ] || nogo "a2: await.sh returned $await_state"
pr_json=$(sh "$ch_dir/pr.sh" "$pr_num")
printf '%s' "$pr_json" | jq -e 'keys | sort == ["head_sha","merge_sha","merged","number","state"]' >/dev/null \
  || nogo "a2: pr.sh schema mismatch: $pr_json"
[ "$(printf '%s' "$pr_json" | jq -r '.merged')" = "false" ] || nogo "a2: pr.sh reported merged before merge"
stale_rc=0
stale_out=$(sh "$ch_dir/merge.sh" "$pr_num" "0000000000000000000000000000000000000000" 2>/dev/null) || stale_rc=$?
[ "$stale_rc" -ne 0 ] || nogo "a2: stale merge.sh succeeded"
[ -z "$stale_out" ] || nogo "a2: stale merge.sh wrote to stdout: $stale_out"
merge_out=$(sh "$ch_dir/merge.sh" "$pr_num" "$head_sha") || nogo "a2: merge.sh failed"
merge_out2=$(sh "$ch_dir/merge.sh" "$pr_num" "$head_sha") || nogo "a2: merge.sh idempotent retry failed"
[ "$merge_out" = "$merge_out2" ] || nogo "a2: merge.sh idempotency mismatch: $merge_out vs $merge_out2"
pr_after=$(sh "$ch_dir/pr.sh" "$pr_num")
[ "$(printf '%s' "$pr_after" | jq -r '.merged')" = "true" ] || nogo "a2: pr.sh not merged after merge.sh"

ch_digest=$(python3 core/adapters.py hash "$ext/file-codehost")
(cd "$root" && CODE_HOST=outside/file-codehost ADAPTERS_PATH="$ext" ADAPTERS_REGISTER="$reg" python3 core/adapters.py register CODE_HOST codehost-conformance) \
  || nogo "a2: register CODE_HOST failed"
awk -F'\t' -v d="$ch_digest" '$1 == "CODE_HOST" && $2 == "file-codehost" && $3 == d && index($4, "codehost-conformance@") == 1 { f = 1 } END { exit !f }' "$reg" 2>/dev/null \
  || nogo "a2: register CODE_HOST wrote no line for sha256 $ch_digest"

rm -f "$root/adapters/outside/file-codehost"
m help "$ch"
admitted "file-codehost after register"
[ -L "$root/adapters/outside/file-codehost" ] || nogo "a2: admitted CODE_HOST not linked"
[ "$(readlink "$root/adapters/outside/file-codehost")" = "$ext/file-codehost" ] || nogo "a2: CODE_HOST link mismatch"

printf '\n' >> "$ext/file-codehost/merge.sh"
m help "$ch"
refused "codehost one changed byte" "$ch is refused"

cp -R fixtures/outside-adapters/file-codehost "$ext/file-codehost-linked"
ln -s /etc/hostname "$ext/file-codehost-linked/extra.sh"
m help CODE_HOST=outside/file-codehost-linked
refused "codehost symlink in tree" "CODE_HOST=outside/file-codehost-linked is refused" "symlink"

# --- b: a surface is never a judge (ADR 0058 §6, ADR 0095 §10) -------------------
# The contexts the tower posts: the third argument of every status.sh call in core/.
tower=$(awk '!/^[[:space:]]*#/ && /adapters\/\$CODE_HOST\/status\.sh"/ {
    for (i = 1; i <= NF; i++) if ($i == "\"$status\"") { print $(i + 1); break }
  }' core/*.sh core/steps/*.sh | sort -u)
[ -n "$tower" ] || nogo "b: found no context the tower posts in core/; the check reads nothing"
# The tower reads no commit status at all: no core file awaits one. The
# conformance suites in core/gates/ drive await.sh to prove it, and are not
# the tower (docs/adr/0084); nor are the tests.
readers=$(grep -rlE 'await\.sh|/statuses' core --include='*.sh' --include='*.py' \
  --exclude-dir=gates --exclude-dir=tests --exclude-dir=__pycache__ 2>/dev/null || :)
[ -z "$readers" ] || nogo "b: core/ reads commit statuses, where a surface's verdict could become the tower's: $readers"
# Every recipe: <file> <its context> <the state it posts on anything but GO>.
# GitLab's API says `failed`; GitHub-shaped statuses (GitHub, Gitea) say
# `failure`. The context is either a literal "context":"..." in the body or
# one `context=<value>` line the body is built from.
contexts=""
while read -r jf want bad; do
  [ -f "$jf" ] || nogo "b: $jf not found"
  ctx=$({ sed -n 's/.*"context":"\([^"]*\)".*/\1/p' "$jf"
          sed -n 's/^[[:space:]]*context=\([^[:space:]]*\)[[:space:]]*$/\1/p' "$jf"; } | sort -u)
  [ "$ctx" = "$want" ] || nogo "b: $jf posts under [$(printf '%s' "$ctx" | tr '\n' ' ')], want $want only"
  for t in $tower; do
    [ "$t" != "$ctx" ] || nogo "b: $jf posts under $t, a context the tower posts"
  done
  grep -qE '^[[:space:]]*line=\$\(lights-out gates "\$@"\)' "$jf" \
    || nogo "b: $jf does not take its line from the lights-out wrapper's gates"
  grep -qE "^[[:space:]]*case \"\\\$line\" in GO:\\*\) state=success ;; \\*\) state=$bad ;; esac\$" "$jf" \
    || nogo "b: $jf does not post success on a GO line only"
  contexts="$contexts $ctx"
done <<'EOF'
surfaces/jenkins/Jenkinsfile lights-out/jenkins failure
surfaces/gitlab-ci/.gitlab-ci.yml lights-out/gitlab-ci failed
surfaces/circleci/config.yml lights-out/circleci failure
EOF

go_with "outside StateStore, Delivery and CodeHost refused, proven, admitted, refused again on one byte; the surfaces post under${contexts}, the tower under $(printf '%s' "$tower" | tr '\n' ' ' | sed 's/ $//')"
