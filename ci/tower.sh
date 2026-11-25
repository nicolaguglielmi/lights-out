#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-tower: proves core/tower.sh, adapters/inproc/run.sh and core/audit.sh
# (docs/adr/0019 §1, §2, §6, §9; docs/adr/0034; docs/adr/0038) on stub
# adapters, cold: no plane, no engine, milliseconds. The ci/harness.sh
# shape. A temp root holds file-backed stubs for the StateStore, EventBus
# and CodeHost ports plus a TOWER_STEPS directory of stub steps — except
# `merge`, which is the real core/steps/merge.sh over the stub CodeHost,
# because the crash case (§9 d) is about that seam, and `rework`, the real
# core/steps/rework.sh with the real policy/rework.env, because the rework
# loop and its cap (docs/adr/0038 §2, §3) are what cases (i) to (l) prove.
# The root's core/ is linked file by file so that core/crew.sh, the one
# thing rework.sh calls that needs a harness, can be a stub. Case (h) runs
# the real core/steps/ci.sh on a real git range: a PR is judged by the
# base's gates, policy and perimeter, not by its own (docs/adr/0033).
# The 1.3 sources (docs/adr/0071 §1, §8): the stub ci and crew leave what the
# real ones leave, the lines of each gate and the patch of an attempt, and the
# real rework.sh must copy the lines of the rejected attempt into its entry,
# whichever judge rejected it (gates (i), review (j), main (m)), and leave
# `lines` null where ci had none; case (h) runs the real ci.sh on a real range
# with two planted gates, one that always says GO and one that always says
# NO-GO, and `ci.lines` must be the gates' lines file, in order, stopping at the
# first NO-GO, and a perimeter NO-GO its one line. assign.sh and rework.sh ask
# tower.py for the patch, review.sh does not (static check, with its own
# planted miss). Each gate's seconds (docs/adr/0085 §4): (h) `ci.seconds` holds
# the gates that ran and only those, each a whole number, null on a perimeter
# refusal; (i) the real rework.sh copies it into the rejected attempt's entry.
# Each new check is able to fail: a premise names what would make it pass for
# the wrong reason.
# Written from the ADRs, not from the scripts.
set -eu
name=tower
. ci/lib.sh

for f in core/tower.sh core/audit.sh core/steps/merge.sh core/steps/ci.sh core/steps/rework.sh \
  policy/rework.env adapters/inproc/run.sh; do
  [ -f "$f" ] || nogo "$f not found"
done

# The cap the loop is held to (docs/adr/0038 §3), read from the policy and
# never written down here.
# shellcheck disable=SC1090,SC1091 # the repo's own policy file
max=$(. ./policy/rework.env && printf '%s' "${REWORK_MAX_ATTEMPTS:-}")
case "$max" in
  ''|*[!0-9]*|0|1) nogo "policy/rework.env: REWORK_MAX_ATTEMPTS is '$max'; the loop cases need an integer of at least 2" ;;
esac

repo=$(pwd)
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/adapters/stub-state" "$root/adapters/stub-bus" "$root/adapters/stub-codehost" \
  "$root/steps" "$root/state" "$root/bus" "$root/world" "$root/core"
# core/ file by file: everything is the repo's own except crew.sh and
# mainline.py (below).
for f in "$repo"/core/*; do
  case "$(basename "$f")" in crew.sh|mainline.py) ;; *) ln -s "$f" "$root/core/$(basename "$f")" ;; esac
done
ln -s "$repo/policy" "$root/policy"
ln -s "$repo/ci" "$root/ci"
ln -s "$repo/adapters/inproc" "$root/adapters/inproc"

# --- stub StateStore: one file per <collection>/<id> ---
cat > "$root/adapters/stub-state/get.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
f="$d/state/$1.$2.json"
[ -f "$f" ] || exit 1
cat "$f"
STUB
cat > "$root/adapters/stub-state/put.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
jq -c . > "$d/state/$1.$2.json"
STUB
cat > "$root/adapters/stub-state/delete.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
rm -f "$d/state/$1.$2.json"
STUB

# --- stub EventBus: a FIFO of files, ackId = file name ---
cat > "$root/adapters/stub-bus/publish.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
n=$(cat "$d/bus/.seq" 2>/dev/null || printf 0); n=$((n + 1)); printf '%s' "$n" > "$d/bus/.seq"
printf '%s\n' "$1" > "$d/bus/$(printf '%06d' "$n").msg"
STUB
cat > "$root/adapters/stub-bus/pull.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
for f in "$d"/bus/*.msg; do
  [ -e "$f" ] || exit 0
  printf '%s %s\n' "$(basename "$f")" "$(cat "$f")"
  exit 0
done
STUB
cat > "$root/adapters/stub-bus/ack.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
rm -f "$d/bus/$1"
STUB

# --- stub CodeHost: a world file per PR, the gitea merge/pr contracts ---
cat > "$root/adapters/stub-codehost/pr.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
f="$d/world/pr-$1.json"
[ -f "$f" ] || { printf 'stub-codehost: no PR #%s\n' "$1" >&2; exit 1; }
cat "$f"
STUB
cat > "$root/adapters/stub-codehost/merge.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
f="$d/world/pr-$1.json"
[ -f "$f" ] || { printf 'stub-codehost: no PR #%s\n' "$1" >&2; exit 1; }
if [ "$(jq -r .merged "$f")" != true ]; then
  printf 'merge performed %s\n' "$1" >> "$d/effects.log"
  new=$(jq -c '.merged = true | .state = "closed" | .merge_sha = "merge-of-" + .head_sha' "$f")
  printf '%s\n' "$new" > "$f"
fi
jq -r '"\(.merge_sha) \(.head_sha)"' "$f"
STUB

# --- stub steps; `merge` and `rework` are the real ones ---
# assign opens the PR at head1 and sets attempt 1 (docs/adr/0038 §9).
cat > "$root/steps/assign.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
printf 'assign\n' >> "$d/effects.log"
printf '%s\n' '{"state":"pr-opened","attempt":1,"pr":{"number":1,"url":"stub://pr/1","base_sha":"base","head_sha":"head1"},"task":{"role":"dev-worker","harness":"stub","model":null},"spec":{"sha256":"abc","title":"0001 — stub ticket"}}'
STUB
# ci judges the head the tower hands it (HEAD_SHA, from the document), never
# a fixed one. STUB_CI: pass | review | fail (every attempt) | fail-once
# (NO-GO on attempt 1, GO after) | crash. A NO-GO carries the line and the
# details array the real step records (docs/adr/0038 §5), and every mode the
# lines of the gates that ran (docs/adr/0071 §1): the NO-GO's last line is its
# own, so a lines array copied from another attempt would show. A GO carries
# no line and no details, so a field left over from an earlier attempt would show.
cat > "$root/steps/ci.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
printf 'ci\n' >> "$d/effects.log"
mode=${STUB_CI:-pass}
[ "$mode" != crash ] || exit 1
if [ "$mode" = fail-once ]; then
  if [ "$(jq -r '.attempt // 1' "$DOC_FILE")" -eq 1 ]; then mode=fail; else mode=pass; fi
fi
case "$mode" in
  pass) jq -cn --arg h "$HEAD_SHA" '{state: "ci-passed", ci: {verdict: "GO", risk_level: "low", decision: "auto-merge", head_sha: $h,
    lines: ["GO: risk — low, auto-merge", "GO: secrets"]}}' ;;
  review) jq -cn --arg h "$HEAD_SHA" '{state: "review", ci: {verdict: "GO", risk_level: "high", decision: "review", head_sha: $h,
    lines: ["GO: risk — high, review", "GO: secrets"]}}' ;;
  fail) jq -cn --arg h "$HEAD_SHA" '{state: "ci-failed", ci: {verdict: "NO-GO", failed: "mutation", risk_level: "low", decision: "auto-merge", head_sha: $h,
    line: "NO-GO: mutation — 2 mutants survived in \($h)",
    lines: ["GO: risk — low, auto-merge", "NO-GO: mutation — 2 mutants survived in \($h)"], seconds: {risk: 1, mutation: 3},
    details: ["app/meetups.py:27 swap-comparison", "app/meetups.py:31 drop-return"]}}' ;;
esac
STUB
# review: STUB_REVIEW: approve | reject (every attempt) | reject-once
# (attempt 1) | crash. The reason names the head it judged and runs to
# several lines. Like the real reviewer it leaves a transcript behind, with
# a marker nothing downstream may repeat (docs/adr/0038 §4).
cat > "$root/steps/review.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
printf 'review\n' >> "$d/effects.log"
mode=${STUB_REVIEW:-approve}
[ "$mode" != crash ] || exit 1
if [ "$mode" = reject-once ]; then
  if [ "$(jq -r '.attempt // 1' "$DOC_FILE")" -eq 1 ]; then mode=reject; else mode=approve; fi
fi
mkdir -p "$d/.crew/$TICKET/review/out"
printf '{"role":"assistant","text":"TRANSCRIPT-MARKER-5c1e reasoning about %s"}\n' "$HEAD_SHA" \
  > "$d/.crew/$TICKET/review/out/transcript.jsonl"
case "$mode" in
  approve) jq -cn --arg h "$HEAD_SHA" '{state: "ci-passed", review: {role: "test-reviewer", verdict: "approved", reason: "stub approved \($h)"}}' ;;
  reject) jq -cn --arg h "$HEAD_SHA" '{state: "ci-failed", review: {role: "test-reviewer", verdict: "changes-requested",
    reason: "stub rejected \($h)\nreviewer point one: the empty-venue branch has no test\nreviewer point two: the loop in meetups.py never ends"}}' ;;
esac
STUB
cat > "$root/steps/release.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
printf 'release\n' >> "$d/effects.log"
printf '%s\n' '{"state":"rolled-back","release":{"version":"0.2.0-waitlist","verdict":"NO-GO: launchpad — 5%: stub","numbers":["slo: stub"],"image":"stub"}}'
STUB
# mainline: main's head judged green (docs/adr/0056 §3); the check itself
# is ci/mainline.sh's subject.
cat > "$root/steps/mainline.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
printf 'mainline\n' >> "$d/effects.log"
jq -cn --arg m "$(jq -r .merge.sha "$DOC_FILE")" '{state: "main-green", mainline: {verdict: "GO", green: $m}}'
STUB
ln -s "$repo/core/steps/merge.sh" "$root/steps/merge.sh"
ln -s "$repo/core/steps/rework.sh" "$root/steps/rework.sh"

# --- stub crew: what rework.sh calls as `sh core/crew.sh`, cwd = the root,
# with TICKET, ROLE, ATTEMPT, REWORK_FILE and REWORK_HEAD. It stands in for
# the harness and the code host's push, and keeps the real crew's contract
# (docs/adr/0038 §4, §7): it refuses unless REWORK_HEAD is the head the code
# host holds (the judged one), it "pushes" a new head, head<ATTEMPT>, into
# the same PR, and it writes pr-opened.env and out/run.json where rework.sh
# reads them. It copies the worker's input aside, since rework.sh deletes
# it. STUB_CREW=fail makes it fail, as a harness that changes nothing does.
cat > "$root/core/crew.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
: "${TICKET:?}" "${ATTEMPT:?}" "${REWORK_FILE:?}" "${REWORK_HEAD:?}"
printf 'crew %s\n' "$ATTEMPT" >> "$d/effects.log"
cp "$REWORK_FILE" "$d/rework-input-$ATTEMPT.md"
[ "${STUB_CREW:-ok}" != fail ] || { printf 'stub crew: told to fail\n' >&2; exit 1; }
host=$(jq -r .head_sha "$d/world/pr-1.json")
[ "$host" = "$REWORK_HEAD" ] || {
  printf 'stub crew: ticket/%s on the code host is %s, the judged head was %s\n' "$TICKET" "$host" "$REWORK_HEAD" >&2
  exit 1
}
head="head$ATTEMPT"
jq -c --arg h "$head" '.head_sha = $h' "$d/world/pr-1.json" > "$d/world/pr-1.json.new"
mv "$d/world/pr-1.json.new" "$d/world/pr-1.json"
rm -rf "${d:?}/.crew/$TICKET"
mkdir -p "$d/.crew/$TICKET/out"
printf 'PR_NUMBER=1\nPR_URL=stub://pr/1\nBASE_SHA=base\nHEAD_SHA=%s\n' "$head" > "$d/.crew/$TICKET/pr-opened.env"
printf '%s\n' '{"harness":"stub","model":null}' > "$d/.crew/$TICKET/out/run.json"
# The patch of this attempt, as format-patch writes it, `--` signature included.
printf 'From %s Mon Sep 17 00:00:00 2001\nSubject: [PATCH] attempt %s\n\n---\ndiff --git a/app/meetups.py b/app/meetups.py\n--- a/app/meetups.py\n+++ b/app/meetups.py\n@@ -1 +1,2 @@\n x\n+attempt %s\n--\n2.43.0\n\n' \
  "$head" "$ATTEMPT" "$ATTEMPT" > "$d/.crew/$TICKET/out/patch"
printf 'GO: crew\n'
STUB

# The re-judge rule merge.sh runs first (docs/adr/0056 §1) is ci/mainline.sh's
# subject; here main never moved, so it has nothing to say.
cat > "$root/core/mainline.py" <<'STUB'
import sys
sys.exit(0 if sys.argv[1:] == ["rejudge"] else 2)
STUB

export STATE_STORE=stub-state EVENT_BUS=stub-bus CODE_HOST=stub-codehost TOWER_STEPS="$root/steps"
# The stub code host has no repository to clone: the merge step's map
# rebuild is off here (docs/adr/0054 §4); ci/map.sh proves it.
export MAP_FILE=
cd "$root"

reset() {
  rm -rf state/* bus/* bus/.seq world/* effects.log .crew rework-input-*.md
  printf '%s\n' '{"number":1,"state":"open","merged":false,"head_sha":"head1","merge_sha":null}' > world/pr-1.json
}
effects() { cat effects.log 2>/dev/null | tr '\n' ',' ; }
doc() { cat "state/tickets.0001.json"; }
queue() { find bus -name '*.msg' | wc -l | tr -d ' '; }
# count REGEX: how many effects match REGEX (anchored by the caller).
count() { awk -v p="$1" '$0 ~ p { n++ } END { print n + 0 }' effects.log; }
# trail: the history as step[!]>state@attempt, "!" marking a failed attempt
# (docs/adr/0034 §1) and "-" an entry written before the document had an
# attempt (docs/adr/0038 §9).
trail() { doc | jq -r '[.history[] | "\(.step)\(if .error then "!" else "" end)>\(.state)@\(.attempt // "-")"] | join(",")'; }
log="$root/run.log"
mismatch=""
fail_case() {
  [ -z "$mismatch" ] || return 0
  mismatch="$1"; printf 'ci-tower: %s: %s\n' "$1" "$2" >&2; cat "$log" >&2
}

# (a) the transition table, driven by the inproc loop.
reset
sh core/tower.sh open 0001 2>"$log" || fail_case a-open "open exited $?"
[ "$(doc | jq -r .state)" = opened ] || fail_case a-open "open did not write state opened"
[ "$(queue)" = 1 ] || fail_case a-open "open published $(queue) events, want 1 (assign)"
if [ -z "$mismatch" ]; then
  sh adapters/inproc/run.sh 2>"$log" || fail_case a-run "inproc run exited $?"
  [ "$(doc | jq -r .state)" = rolled-back ] || fail_case a-final "final state $(doc | jq -r .state), want rolled-back"
  [ "$(effects)" = "assign,ci,merge performed 1,mainline,release," ] || fail_case a-effects "effects: $(effects)"
  [ "$(queue)" = 0 ] || fail_case a-queue "queue not empty after the run"
  [ "$(doc | jq '.history | length')" = 6 ] || fail_case a-history "history has $(doc | jq '.history | length') entries, want 6"
  [ "$(doc | jq -r .merge.sha)" = merge-of-head1 ] || fail_case a-merge "merge.sha not recorded from the code host"
  # docs/adr/0038 golden-rule check: a ticket the gates pass first time
  # never takes the rework lines; every entry after assign carries attempt 1.
  [ "$(trail)" = "open>opened@-,assign>pr-opened@1,ci>ci-passed@1,merge>merged@1,mainline>main-green@1,release>rolled-back@1" ] \
    || fail_case a-trail "history: $(trail)"
  [ "$(doc | jq '(.rework // []) | length')" = 0 ] || fail_case a-trail "a first-time GO left a rework record"
fi
# (a') used to prove ci-failed terminal. It no longer is (docs/adr/0038 §1):
# a gate's NO-GO requests rework, and what stops a ticket is the cap, cases
# (i) to (l) below.

# (a'') review (docs/adr/0021 §1): a GO+review PR reaches the review step,
# whose own verdict decides ci-passed or ci-failed — never review itself,
# which is no longer terminal. approve continues on to merge/release
# exactly like the golden path (a); a rejection is the rework loop's, cases
# (j) and (k') below (docs/adr/0038 §1: ci-failed requests rework).
if [ -z "$mismatch" ]; then
  reset
  if ! sh core/tower.sh open 0001 2>"$log" || ! STUB_CI=review STUB_REVIEW=approve sh adapters/inproc/run.sh 2>"$log"; then
    fail_case a-review-approve "run failed"
  fi
  [ "$(doc | jq -r .state)" = rolled-back ] || fail_case a-review-approve "final state $(doc | jq -r .state), want rolled-back"
  [ "$(effects)" = "assign,ci,review,merge performed 1,mainline,release," ] || fail_case a-review-approve "effects: $(effects)"
  [ "$(queue)" = 0 ] || fail_case a-review-approve "queue not empty after the run"
  [ "$(doc | jq -r .review.verdict)" = approved ] || fail_case a-review-approve "review.verdict not recorded"
fi

# (b) the same event twice: applied once.
if [ -z "$mismatch" ]; then
  reset
  if ! sh core/tower.sh open 0001 2>"$log" || ! sh adapters/inproc/run.sh 2>"$log"; then fail_case b "run failed"; fi
  before=$(doc) || fail_case b "no document after the run"
  sh adapters/stub-bus/publish.sh '{"ticket":"0001","step":"ci"}' || fail_case b "could not publish the duplicate"
  sh adapters/inproc/run.sh 2>"$log" || fail_case b "second run exited $?"
  grep -q 'ci already applied' "$log" || fail_case b "no 'already applied' line on a duplicate event"
  [ "$(effects)" = "assign,ci,merge performed 1,mainline,release," ] || fail_case b "duplicate event had an effect: $(effects)"
  [ "$(doc)" = "$before" ] || fail_case b "duplicate event changed the document"
fi

# (c) out of order: merge on an opened document is dropped.
if [ -z "$mismatch" ]; then
  reset
  sh core/tower.sh open 0001 2>"$log" || fail_case c "open exited $?"
  sh core/tower.sh step 0001 merge 2>"$log" || fail_case c "out-of-order step exited $?"
  [ -z "$(effects)" ] || fail_case c "out-of-order merge had an effect"
  [ "$(doc | jq -r .state)" = opened ] || fail_case c "state moved"
fi

# (d) the crash case: the merge happened, the state was not written.
if [ -z "$mismatch" ]; then
  reset
  printf '%s\n' '{"number":1,"state":"closed","merged":true,"head_sha":"head1","merge_sha":"X"}' > world/pr-1.json
  printf '%s\n' '{"id":"0001","state":"ci-passed","history":[],"pr":{"number":1,"head_sha":"head1"},"ci":{"head_sha":"head1"}}' \
    | sh adapters/stub-state/put.sh tickets 0001 || fail_case d "could not seed the document"
  sh core/tower.sh step 0001 merge 2>"$log" || fail_case d "merge retry exited $?"
  [ -z "$(effects)" ] || fail_case d "a second merge was performed: $(effects)"
  [ "$(doc | jq -r .state)" = merged ] || fail_case d "state $(doc | jq -r .state), want merged"
  [ "$(doc | jq -r .merge.sha)" = X ] || fail_case d "merge.sha $(doc | jq -r .merge.sha), want the host's X"
  [ "$(doc | jq -r .merge.head_sha)" = head1 ] || fail_case d "merge.head_sha not the host's head"
  [ "$(queue)" = 1 ] && [ "$(sh adapters/stub-bus/pull.sh | jq -r .step 2>/dev/null || cat bus/*.msg | jq -r .step)" = mainline ] \
    || fail_case d "mainline was not requested after the recovered merge (docs/adr/0056 §2)"
fi

# (e) a failing step: exit 1, nothing published, state and fields
# untouched; the history gains exactly one entry, the failed attempt
# (docs/adr/0034 §1).
if [ -z "$mismatch" ]; then
  reset
  sh core/tower.sh open 0001 2>"$log" || fail_case e "open exited $?"
  rm -f bus/*.msg
  sh core/tower.sh step 0001 assign 2>"$log" >/dev/null || fail_case e "assign exited $?"
  rm -f bus/*.msg
  before=$(doc) || fail_case e "no document after assign"
  if STUB_CI=crash sh core/tower.sh step 0001 ci 2>"$log"; then fail_case e "a failing step exited 0"; fi
  [ "$(doc | jq -c 'del(.history)')" = "$(printf '%s' "$before" | jq -c 'del(.history)')" ] \
    || fail_case e "a failing step changed the document beyond its history"
  [ "$(doc | jq -c '.history[:-1]')" = "$(printf '%s' "$before" | jq -c '.history')" ] \
    || fail_case e "a failing step rewrote earlier history entries"
  [ "$(doc | jq -r '.history[-1] | "\(.step) \(.state) \(.error != null)"')" = "ci pr-opened true" ] \
    || fail_case e "last history entry is $(doc | jq -c '.history[-1]'), want a failed ci at pr-opened"
  [ "$(queue)" = 0 ] || fail_case e "a failing step published an event"
fi

# (f) the audit: GO on the complete chain, NO-GO on each broken link.
audit() { TICKET=0001 sh core/audit.sh 2>"$log"; }
if [ -z "$mismatch" ]; then
  reset
  if ! sh core/tower.sh open 0001 2>"$log" || ! sh adapters/inproc/run.sh 2>"$log"; then fail_case f "run failed"; fi
  complete=$(doc) || fail_case f "no document after the run"
  out=$(audit) || true
  [ "$out" = "GO: audit" ] || fail_case f-go "audit on a complete chain: $out"
  expect_nogo() {  # NAME WORD: the audit must NO-GO and name WORD
    out=$(audit) && fail_case "f-$1" "audit passed: $out" || true
    case "$out" in "NO-GO: audit — "*"$2"*) : ;; *) fail_case "f-$1" "audit said: $out (want a NO-GO naming '$2')" ;; esac
  }
  printf '%s' "$complete" | jq 'del(.merge.sha)' | sh adapters/stub-state/put.sh tickets 0001 \
    || fail_case f-missing-link "could not write the document"
  expect_nogo missing-link missing
  printf '%s' "$complete" | jq '.ci.head_sha = "other"' | sh adapters/stub-state/put.sh tickets 0001 \
    || fail_case f-sha-mismatch "could not write the document"
  expect_nogo sha-mismatch other
  # docs/adr/0021 §9: ci.decision=review past the review step must carry
  # review.verdict/review.reason — a reviewed merge with no review on the
  # record is exactly what an audit exists to catch.
  printf '%s' "$complete" | jq '.ci.decision = "review"' | sh adapters/stub-state/put.sh tickets 0001 \
    || fail_case f-review-missing "could not write the document"
  expect_nogo review-missing review.verdict
  printf '%s' "$complete" | sh adapters/stub-state/put.sh tickets 0001 || fail_case f-host-open "could not write the document"
  printf '%s\n' '{"number":1,"state":"open","merged":false,"head_sha":"head1","merge_sha":null}' > world/pr-1.json
  expect_nogo host-open open
  printf '%s\n' '{"number":1,"state":"closed","merged":true,"head_sha":"head1","merge_sha":"other-merge"}' > world/pr-1.json
  expect_nogo host-merge-sha other-merge
  rm -f world/pr-1.json
  expect_nogo host-down unreachable
fi

# (g) resume (docs/adr/0034): a ticket whose ci step failed is stranded
# at pr-opened with nothing queued; resume requests ci again, the run
# reaches the next states to the end, the history keeps the failed
# attempt, the resume and the new attempt, and the audit still passes.
if [ -z "$mismatch" ]; then
  reset
  sh core/tower.sh open 0001 2>"$log" || fail_case g-resume "open exited $?"
  if STUB_CI=crash sh adapters/inproc/run.sh 2>"$log"; then fail_case g-resume "a run with a failing step exited 0"; fi
  [ "$(doc | jq -r .state)" = pr-opened ] || fail_case g-resume "stranded at $(doc | jq -r .state), want pr-opened"
  [ "$(queue)" = 0 ] || fail_case g-resume "something was queued after the failed step"
  sh core/tower.sh resume 0001 2>"$log" || fail_case g-resume "resume of a stranded ticket exited $?"
  [ "$(queue)" = 1 ] || fail_case g-resume "resume queued $(queue) events, want 1"
  sh adapters/inproc/run.sh 2>"$log" || fail_case g-resume "the resumed run exited $?"
  [ "$(doc | jq -r .state)" = rolled-back ] || fail_case g-resume "final state $(doc | jq -r .state), want rolled-back"
  [ "$(effects)" = "assign,ci,ci,merge performed 1,mainline,release," ] || fail_case g-resume "effects: $(effects)"
  steps=$(doc | jq -r '[.history[] | .step + (if .error then "!" else "" end)] | join(",")')
  [ "$steps" = "open,assign,ci!,resume,ci,merge,mainline,release" ] || fail_case g-resume "history steps: $steps"
  out=$(audit) || true
  [ "$out" = "GO: audit" ] || fail_case g-resume-audit "audit after a resumed run: $out"
fi
# (g') resume refuses a ticket that is not stranded: one whose last
# history entry did not fail, mid-flight (assign queued) or terminal.
expect_refused() {  # NAME: resume must exit non-zero, say why, change and publish nothing
  before=$(doc) || fail_case "$1" "no document"
  queued=$(queue)
  if sh core/tower.sh resume 0001 2>"$log"; then fail_case "$1" "resume of a ticket that is not stranded exited 0"; fi
  grep -q 'not stranded' "$log" || fail_case "$1" "no 'not stranded' line on the refusal"
  [ "$(doc)" = "$before" ] || fail_case "$1" "a refused resume changed the document"
  [ "$(queue)" = "$queued" ] || fail_case "$1" "a refused resume published an event"
}
if [ -z "$mismatch" ]; then
  reset
  sh core/tower.sh open 0001 2>"$log" || fail_case g-refuse-open "open exited $?"
  expect_refused g-refuse-in-flight
  sh adapters/inproc/run.sh 2>"$log" || fail_case g-refuse-terminal "run exited $?"
  expect_refused g-refuse-terminal
fi

# The rework loop (docs/adr/0038). `ci-failed` — the gates or the reviewer
# rejected this head — requests `rework`; the real core/steps/rework.sh runs
# the stub crew for the next attempt; `needs-human` is where the cap stops
# a ticket. These cases come after (g') because they use its helpers; (h)
# needs the repo as cwd and so stays last.
line1='NO-GO: mutation — 2 mutants survived in head1'
detail1='app/meetups.py:27 swap-comparison'
detail2='app/meetups.py:31 drop-return'
# The lines of the gates that ran, as the stub ci records them (docs/adr/0071 §1).
lines_pass='["GO: risk — low, auto-merge","GO: secrets"]'
lines_review='["GO: risk — high, review","GO: secrets"]'
lines_fail() { printf '["GO: risk — low, auto-merge","NO-GO: mutation — 2 mutants survived in head%s"]' "$1"; }
# stub_patch N: the patch the stub crew leaves for attempt N, byte for byte.
stub_patch() {
  printf 'From head%s Mon Sep 17 00:00:00 2001\nSubject: [PATCH] attempt %s\n\n---\ndiff --git a/app/meetups.py b/app/meetups.py\n--- a/app/meetups.py\n+++ b/app/meetups.py\n@@ -1 +1,2 @@\n x\n+attempt %s\n--\n2.43.0\n\n' "$1" "$1" "$1"
}
has() { grep -qF -- "$2" "$1"; }  # FILE TEXT: does FILE contain TEXT
audit_go() {  # NAME: the audit must accept the record as it stands
  out=$(audit) || true
  [ "$out" = "GO: audit" ] || fail_case "$1" "audit: $out"
}
reworked() {  # ENV...: open the ticket and run the loop to the end, with ENV set
  sh core/tower.sh open 0001 2>"$log" && env "$@" sh adapters/inproc/run.sh 2>"$log"
}

# (i) the condition loop (§1, §2, §4, §9): the gates NO-GO the first head,
# the tower sends it back to the worker with the verdict, the second head
# passes, and the ticket goes on to merge and release as in (a).
if [ -z "$mismatch" ]; then
  reset
  reworked STUB_CI=fail-once || fail_case i "run failed"
  [ "$(doc | jq -r .state)" = rolled-back ] || fail_case i-final "final state $(doc | jq -r .state), want rolled-back"
  [ "$(effects)" = "assign,ci,crew 2,ci,merge performed 1,mainline,release," ] || fail_case i-effects "effects: $(effects)"
  [ "$(queue)" = 0 ] || fail_case i-queue "queue not empty after the run"
  [ "$(trail)" = "open>opened@-,assign>pr-opened@1,ci>ci-failed@1,rework>pr-opened@2,ci>ci-passed@2,merge>merged@2,mainline>main-green@2,release>rolled-back@2" ] \
    || fail_case i-trail "history: $(trail)"
  # What survives of the rejected attempt: one entry, with the gate's own words.
  [ "$(doc | jq '.rework | length')" = 1 ] || fail_case i-rework "rework has $(doc | jq '.rework | length') entries, want 1"
  [ "$(doc | jq -r '.rework[0] | "\(.attempt) \(.judge) \(.head_sha) \(.line)"')" = "1 gates head1 $line1" ] \
    || fail_case i-rework "rework[0]: $(doc | jq -c '.rework[0]')"
  [ "$(doc | jq -c '.rework[0].details')" = "[\"$detail1\",\"$detail2\"]" ] \
    || fail_case i-rework "rework[0].details: $(doc | jq -c '.rework[0].details')"
  [ -n "$(doc | jq -r '.rework[0].at // empty')" ] || fail_case i-rework "rework[0] has no time"
  # docs/adr/0071 §1: the entry keeps the lines of the gates that rejected the
  # attempt, which are not the lines of the attempt that passed.
  [ "$(doc | jq -c '.rework[0].lines')" = "$(lines_fail 1)" ] || fail_case i-lines "rework[0].lines: $(doc | jq -c '.rework[0].lines')"
  # docs/adr/0085 §4: the entry keeps each gate's seconds beside its lines.
  [ "$(doc | jq -cS '.rework[0].seconds')" = '{"mutation":3,"risk":1}' ] || fail_case i-seconds "rework[0].seconds: $(doc | jq -cS '.rework[0].seconds')"
  [ "$(doc | jq -c '.ci.lines')" = "$lines_pass" ] || fail_case i-lines "ci.lines after the passing attempt: $(doc | jq -c '.ci.lines')"
  [ "$(doc | jq -c '.rework[0].lines')" != "$(doc | jq -c '.ci.lines')" ] || fail_case i-lines "the rejected attempt's lines are the passing attempt's"
  # docs/adr/0071 §1: the patch and its files of the attempt rework.sh ran, from
  # the crew's out/patch, on the attempt's task; the first attempt's stub task has none.
  [ "$(doc | jq -r '.tasks[1].attempt')" = 2 ] || fail_case i-patch "tasks[1] is $(doc | jq -c '.tasks[1] | del(.patch)')"
  [ "$(doc | jq -r '.tasks[1].patch.text')" = "$(stub_patch 2)" ] || fail_case i-patch "tasks[1].patch.text is not the crew's patch of attempt 2"
  [ "$(doc | jq -r '.tasks[1].patch.bytes')" = "$(stub_patch 2 | wc -c | tr -d ' ')" ] || fail_case i-patch "tasks[1].patch.bytes is $(doc | jq -r '.tasks[1].patch.bytes')"
  [ "$(doc | jq -cS '[.tasks[1].patch.truncated, .tasks[1].files]')" = '[false,[{"added":1,"member":null,"path":"app/meetups.py","removed":0}]]' ] \
    || fail_case i-patch "tasks[1].files: $(doc | jq -cS '[.tasks[1].patch.truncated, .tasks[1].files]')"
  [ "$(doc | jq -r '.tasks[0] | has("patch") or has("files")')" = false ] || fail_case i-patch "the first attempt's stub task grew a patch"
  # What the worker was given for attempt 2: the verdict, in the gate's words.
  for want in "$line1" "$detail1" "$detail2"; do
    has rework-input-2.md "$want" || fail_case i-input "the worker's attempt-2 input lacks: $want"
  done
  # ci and review describe attempt 2 only: nothing of the NO-GO is left on them.
  [ "$(doc | jq -r '"\(.attempt) \(.pr.head_sha) \(.ci.verdict) \(.ci.head_sha)"')" = "2 head2 GO head2" ] \
    || fail_case i-attempt "attempt, head and verdict: $(doc | jq -c '[.attempt, .pr.head_sha, .ci]')"
  [ "$(doc | jq -r '"\(.ci.failed) \(.ci.line) \(.ci.details) \(.review)"')" = "null null null null" ] \
    || fail_case i-attempt "ci or review still carry attempt 1: $(doc | jq -c '[.ci, .review]')"
  [ "$(doc | jq -r '"\(.merge.sha) \(.merge.head_sha)"')" = "merge-of-head2 head2" ] \
    || fail_case i-merge "merged $(doc | jq -c .merge), want the head attempt 2 produced"
  audit_go i-audit
fi

# (j) the review loop (§2, §4): the reviewer rejects the first head, the
# worker gets the reviewer's reason — and never the reviewer's transcript —
# the second head is approved, and the ticket goes on.
if [ -z "$mismatch" ]; then
  reset
  reworked STUB_CI=review STUB_REVIEW=reject-once || fail_case j "run failed"
  [ "$(doc | jq -r .state)" = rolled-back ] || fail_case j-final "final state $(doc | jq -r .state), want rolled-back"
  [ "$(effects)" = "assign,ci,review,crew 2,ci,review,merge performed 1,mainline,release," ] || fail_case j-effects "effects: $(effects)"
  [ "$(trail)" = "open>opened@-,assign>pr-opened@1,ci>review@1,review>ci-failed@1,rework>pr-opened@2,ci>review@2,review>ci-passed@2,merge>merged@2,mainline>main-green@2,release>rolled-back@2" ] \
    || fail_case j-trail "history: $(trail)"
  [ "$(doc | jq '.rework | length')" = 1 ] || fail_case j-rework "rework has $(doc | jq '.rework | length') entries, want 1"
  [ "$(doc | jq -r '.rework[0] | "\(.attempt) \(.judge) \(.head_sha)"')" = "1 review head1" ] \
    || fail_case j-rework "rework[0]: $(doc | jq -c '.rework[0]')"
  # docs/adr/0071 §1: the reviewer rejected a head the gates passed; the entry
  # keeps the gates' GO lines of that head (a NO-GO of the gates never reached it).
  [ "$(doc | jq -c '.rework[0].lines')" = "$lines_review" ] || fail_case j-lines "rework[0].lines: $(doc | jq -c '.rework[0].lines')"
  [ "$(doc | jq -c '.rework[0].lines | map(startswith("GO: ")) | all')" = true ] || fail_case j-lines "a line that is not a GO in a review's entry"
  for want in 'reviewer point one: the empty-venue branch has no test' 'reviewer point two: the loop in meetups.py never ends'; do
    has rework-input-2.md "$want" || fail_case j-input "the worker's attempt-2 input lacks the reviewer's reason: $want"
  done
  grep -qi 'changes-requested' rework-input-2.md || fail_case j-input "the worker's input does not say the reviewer asked for changes"
  # The premise of the next line: the reviewer did leave a transcript.
  has .crew/0001/review/out/transcript.jsonl TRANSCRIPT-MARKER || fail_case j-premise "the stub reviewer left no transcript marker"
  ! has rework-input-2.md TRANSCRIPT-MARKER || fail_case j-input "the reviewer's transcript reached the worker"
  [ "$(doc | jq -r '"\(.attempt) \(.ci.head_sha) \(.review.verdict)"')" = "2 head2 approved" ] \
    || fail_case j-attempt "attempt, head, verdict: $(doc | jq -c '[.attempt, .ci.head_sha, .review.verdict]')"
  [ "$(doc | jq -r .review.reason)" = "stub approved head2" ] \
    || fail_case j-attempt "review still carries attempt 1: $(doc | jq -r .review.reason)"
  audit_go j-audit
fi

# (k) the cap (§2, §3): gates that never pass stop the ticket at needs-human
# after REWORK_MAX_ATTEMPTS attempts — the number read from the policy — and
# nothing is merged, queued or resumable; the audit reads it as a stopped PR.
if [ -z "$mismatch" ]; then
  reset
  reworked STUB_CI=fail || fail_case k "run failed"
  [ "$(doc | jq -r .state)" = needs-human ] || fail_case k-final "final state $(doc | jq -r .state), want needs-human"
  [ "$(count '^ci$')" = "$max" ] || fail_case k-ci "ci ran $(count '^ci$') times, want $max"
  [ "$(count '^crew ')" = "$((max - 1))" ] || fail_case k-crew "crew ran $(count '^crew ') times, want $((max - 1))"
  [ "$(doc | jq -r .attempt)" = "$max" ] || fail_case k-attempt "attempt $(doc | jq -r .attempt), want $max"
  [ "$(doc | jq '.rework | length')" = "$max" ] || fail_case k-rework "rework has $(doc | jq '.rework | length') entries, want $max"
  [ "$(doc | jq -c '[.rework[].attempt]')" = "$(jq -cn --argjson m "$max" '[range(1; $m + 1)]')" ] \
    || fail_case k-rework "rework attempts: $(doc | jq -c '[.rework[].attempt]')"
  [ "$(doc | jq -c '[.rework[].judge] | unique')" = '["gates"]' ] || fail_case k-rework "judges: $(doc | jq -c '[.rework[].judge]')"
  [ "$(doc | jq -r '.rework[-1].head_sha')" = "head$max" ] || fail_case k-rework "the last rejected head is $(doc | jq -r '.rework[-1].head_sha')"
  # docs/adr/0071 §1: each entry, the one that stops the ticket too, has the
  # lines of its own attempt (its head is named in the NO-GO line).
  n=1
  while [ "$n" -le "$max" ]; do
    [ "$(doc | jq -c ".rework[$((n - 1))].lines")" = "$(lines_fail "$n")" ] \
      || fail_case k-lines "rework[$((n - 1))].lines: $(doc | jq -c ".rework[$((n - 1))].lines"), want attempt $n's"
    n=$((n + 1))
  done
  [ "$(queue)" = 0 ] || fail_case k-queue "an event was published after needs-human"
  [ "$(count '^merge|^release')" = 0 ] || fail_case k-merge "a capped ticket was merged or released: $(effects)"
  [ "$(jq -r .merged world/pr-1.json)" = false ] || fail_case k-merge "the code host says the PR is merged"
  [ "$(doc | jq -r '.merge // "none"')" = none ] || fail_case k-merge "the document records a merge"
  # The worker is given the previous verdict, not the earlier ones (§4).
  n=2
  while [ "$n" -le "$max" ]; do
    has "rework-input-$n.md" "survived in head$((n - 1))" || fail_case k-input "attempt $n's input lacks the previous verdict"
    if [ "$n" -ge 3 ]; then
      ! has "rework-input-$n.md" "survived in head$((n - 2))" || fail_case k-input "attempt $n's input carries an earlier verdict"
    fi
    n=$((n + 1))
  done
  [ ! -e "rework-input-$((max + 1)).md" ] || fail_case k-crew "a worker input for attempt $((max + 1)) exists"
  expect_refused k-resume
  audit_go k-audit
fi

# (k') the same cap through a reviewer that always rejects: every judge is
# the reviewer, and the record still audits.
if [ -z "$mismatch" ]; then
  reset
  reworked STUB_CI=review STUB_REVIEW=reject || fail_case "k'" "run failed"
  [ "$(doc | jq -r .state)" = needs-human ] || fail_case "k'-final" "final state $(doc | jq -r .state), want needs-human"
  [ "$(count '^ci$')" = "$max" ] || fail_case "k'-ci" "ci ran $(count '^ci$') times, want $max"
  [ "$(count '^review$')" = "$max" ] || fail_case "k'-review" "review ran $(count '^review$') times, want $max"
  [ "$(count '^crew ')" = "$((max - 1))" ] || fail_case "k'-crew" "crew ran $(count '^crew ') times, want $((max - 1))"
  [ "$(doc | jq '.rework | length')" = "$max" ] || fail_case "k'-rework" "rework has $(doc | jq '.rework | length') entries, want $max"
  [ "$(doc | jq -c '[.rework[].judge] | unique')" = '["review"]' ] || fail_case "k'-rework" "judges: $(doc | jq -c '[.rework[].judge]')"
  [ "$(doc | jq -c '[.rework[].lines] | unique')" = "[$lines_review]" ] || fail_case "k'-lines" "rework lines: $(doc | jq -c '[.rework[].lines]')"
  [ "$(queue)" = 0 ] || fail_case "k'-queue" "an event was published after needs-human"
  [ "$(count '^merge|^release')" = 0 ] || fail_case "k'-merge" "a capped ticket was merged or released: $(effects)"
  n=2
  while [ "$n" -le "$max" ]; do
    has "rework-input-$n.md" "stub rejected head$((n - 1))" || fail_case "k'-input" "attempt $n's input lacks the reviewer's reason"
    ! has "rework-input-$n.md" TRANSCRIPT-MARKER || fail_case "k'-input" "the reviewer's transcript reached the worker at attempt $n"
    n=$((n + 1))
  done
  expect_refused "k'-resume"
  audit_go "k'-audit"
fi

# (l) a rework step that fails is not an attempt (§7, docs/adr/0034): the
# crew fails, the ticket waits at ci-failed with one `error` entry and the
# same attempt number, nothing queued; resume requests rework again, and
# only the verdict that follows advances the count.
if [ -z "$mismatch" ]; then
  reset
  if reworked STUB_CI=fail-once STUB_CREW=fail; then fail_case l "a run whose rework step failed exited 0"; fi
  [ "$(doc | jq -r .state)" = ci-failed ] || fail_case l-stranded "state $(doc | jq -r .state), want ci-failed"
  [ "$(doc | jq -r .attempt)" = 1 ] || fail_case l-stranded "attempt $(doc | jq -r .attempt), want 1"
  [ "$(doc | jq '[.history[] | select(.error)] | length')" = 1 ] \
    || fail_case l-stranded "$(doc | jq '[.history[] | select(.error)] | length') error entries, want 1"
  [ "$(trail)" = "open>opened@-,assign>pr-opened@1,ci>ci-failed@1,rework!>ci-failed@1" ] || fail_case l-stranded "history: $(trail)"
  [ "$(doc | jq '(.rework // []) | length')" = 0 ] || fail_case l-stranded "a failed rework step left a verdict on the record"
  [ "$(doc | jq -r .pr.head_sha)" = head1 ] || fail_case l-stranded "pr.head_sha moved to $(doc | jq -r .pr.head_sha)"
  [ "$(queue)" = 0 ] || fail_case l-stranded "a failed rework step published an event"
  [ "$(effects)" = "assign,ci,crew 2," ] || fail_case l-stranded "effects: $(effects)"
  if [ -z "$mismatch" ]; then
    sh core/tower.sh resume 0001 2>"$log" || fail_case l-resume "resume of a stranded rework exited $?"
    [ "$(queue)" = 1 ] || fail_case l-resume "resume queued $(queue) events, want 1"
    [ "$(cat bus/*.msg | jq -r .step)" = rework ] || fail_case l-resume "resume requested $(cat bus/*.msg | jq -r .step), want rework"
    STUB_CI=fail-once sh adapters/inproc/run.sh 2>"$log" || fail_case l-resume "the resumed run exited $?"
    [ "$(doc | jq -r .state)" = rolled-back ] || fail_case l-resume "final state $(doc | jq -r .state), want rolled-back"
    [ "$(effects)" = "assign,ci,crew 2,crew 2,ci,merge performed 1,mainline,release," ] || fail_case l-resume "effects: $(effects)"
    [ "$(trail)" = "open>opened@-,assign>pr-opened@1,ci>ci-failed@1,rework!>ci-failed@1,resume>ci-failed@1,rework>pr-opened@2,ci>ci-passed@2,merge>merged@2,mainline>main-green@2,release>rolled-back@2" ] \
      || fail_case l-resume "history: $(trail)"
    [ "$(doc | jq -r '"\(.rework | length) \(.rework[0].attempt)"')" = "1 1" ] \
      || fail_case l-resume "rework after the resume: $(doc | jq -c .rework)"
    audit_go l-audit
  fi
fi
# (l') the stub crew's refusal is live, so the cases above mean something: a
# head that moved on the code host since the verdict is refused by the crew
# (§4), and that is a failed step like any other.
if [ -z "$mismatch" ]; then
  reset
  sh core/tower.sh open 0001 2>"$log" || fail_case "l'" "open exited $?"
  sh core/tower.sh step 0001 assign 2>"$log" || fail_case "l'" "assign exited $?"
  STUB_CI=fail sh core/tower.sh step 0001 ci 2>"$log" || fail_case "l'" "ci exited $?"
  [ "$(doc | jq -r .state)" = ci-failed ] || fail_case "l'" "state $(doc | jq -r .state), want ci-failed"
  rm -f bus/*.msg
  jq '.head_sha = "moved"' world/pr-1.json > world/pr-1.json.new && mv world/pr-1.json.new world/pr-1.json
  if sh core/tower.sh step 0001 rework 2>"$log"; then fail_case "l'" "a rework of a head that is no longer the host's exited 0"; fi
  [ "$(trail)" = "open>opened@-,assign>pr-opened@1,ci>ci-failed@1,rework!>ci-failed@1" ] || fail_case "l'" "history: $(trail)"
  [ "$(jq -r .head_sha world/pr-1.json)" = moved ] || fail_case "l'" "the refused rework pushed a head"
fi

# (m) the third judge and a record with no lines (docs/adr/0071 §1): the entry
# copies `ci.lines` whoever rejected, and says null where ci had none. The
# document is written by hand at ci-failed, as the tower leaves it after the
# check of main (docs/adr/0056 §6) or after gates that left no lines file.
seed_failed() {  # CI_JSON MAINLINE_JSON: a ticket at ci-failed on attempt 1, head1
  jq -cn --argjson ci "$1" --argjson main "$2" '{id: "0001", state: "ci-failed", attempt: 1, history: [],
    pr: {number: 1, url: "stub://pr/1", base_sha: "base", head_sha: "head1"}, ci: $ci, mainline: $main}' \
    | sh adapters/stub-state/put.sh tickets 0001
}
if [ -z "$mismatch" ]; then
  reset
  seed_failed '{"verdict":"GO","decision":"auto-merge","head_sha":"head1","lines":["GO: risk — low, auto-merge","GO: secrets"]}' \
    '{"verdict":"NO-GO","judge":"main","line":"NO-GO: gates — main is red after the merge","details":["main broke"]}' \
    || fail_case m-seed "could not seed the document"
  sh core/tower.sh step 0001 rework 2>"$log" || fail_case m-main "rework of a main NO-GO exited $?"
  [ "$(doc | jq -r '.rework[0] | "\(.attempt) \(.judge) \(.line)"')" = "1 main NO-GO: gates — main is red after the merge" ] \
    || fail_case m-main "rework[0]: $(doc | jq -c '.rework[0]')"
  [ "$(doc | jq -c '.rework[0].lines')" = "$lines_pass" ] || fail_case m-lines "rework[0].lines for the main judge: $(doc | jq -c '.rework[0].lines')"
  [ "$(doc | jq -r '.state')" = pr-opened ] || fail_case m-main "state $(doc | jq -r .state), want pr-opened"
fi
if [ -z "$mismatch" ]; then
  reset
  seed_failed '{"verdict":"NO-GO","failed":"perimeter","decision":"unknown","head_sha":"head1","line":"NO-GO: perimeter — app/x.py is outside","details":[]}' null \
    || fail_case m-seed "could not seed the document"
  sh core/tower.sh step 0001 rework 2>"$log" || fail_case m-null "rework of a ci with no lines exited $?"
  [ "$(doc | jq -r '.rework[0] | "\(.judge) \(has("lines")) \(.lines)"')" = "gates true null" ] \
    || fail_case m-null "rework[0] of a ci with no lines: $(doc | jq -c '.rework[0]')"
fi
if [ -z "$mismatch" ]; then
  reset
  seed_failed '{"verdict":"NO-GO","failed":"perimeter","decision":"unknown","head_sha":"head1","line":"NO-GO: perimeter — app/x.py is outside","lines":["NO-GO: perimeter — app/x.py is outside"],"details":[]}' null \
    || fail_case m-seed "could not seed the document"
  sh core/tower.sh step 0001 rework 2>"$log" || fail_case m-perimeter "rework of a perimeter NO-GO exited $?"
  [ "$(doc | jq -c '.rework[0].lines')" = '["NO-GO: perimeter — app/x.py is outside"]' ] \
    || fail_case m-perimeter "rework[0].lines of a perimeter NO-GO: $(doc | jq -c '.rework[0].lines')"
fi

cd "$repo"

# The patch is stored for a worker's attempt, never for a reviewer's run
# (docs/adr/0071 §1): assign.sh and step_rework.py call `tower.py task --patch`,
# step_review.py calls `tower.py task` without it. Read with awk; the matcher is
# tried first on a planted line each way, so it can say no and can say yes.
asks_for_patch() { awk '/tower\.py.*task/ && /--patch/ { found = 1 } END { exit found ? 0 : 1 }'; }
calls_task() { awk '/tower\.py.*task/ { found = 1 } END { exit found ? 0 : 1 }'; }
# shellcheck disable=SC2016 # the planted lines are text, not expansions
printf 'task=$(python3 core/tower.py task --patch "$d/run.json")\n' | asks_for_patch || fail_case patch-matcher "the matcher misses a call with --patch"
# shellcheck disable=SC2016 # the planted lines are text, not expansions
! printf 'task=$(python3 core/tower.py task "$d/run.json")\n' | asks_for_patch || fail_case patch-matcher "the matcher sees --patch in a call without it"
! printf '# --patch is for workers\n' | asks_for_patch || fail_case patch-matcher "the matcher sees --patch away from a tower.py task call"
calls_task < "$repo/core/steps/assign.sh" || fail_case "patch-assign" "core/steps/assign.sh does not call tower.py task at all; this check proves nothing"
asks_for_patch < "$repo/core/steps/assign.sh" || fail_case "patch-assign" "core/steps/assign.sh does not ask tower.py task for the patch (--patch)"
calls_task < "$repo/core/step_rework.py" || fail_case "patch-rework" "core/step_rework.py does not call tower.py task at all; this check proves nothing"
asks_for_patch < "$repo/core/step_rework.py" || fail_case "patch-rework" "core/step_rework.py does not ask tower.py task for the patch (--patch)"
calls_task < "$repo/core/step_review.py" || fail_case patch-review "core/step_review.py does not call tower.py task at all; this check proves nothing"
! asks_for_patch < "$repo/core/step_review.py" || fail_case patch-review "core/step_review.py asks tower.py task for a patch: a reviewer's run must store none"

# (h) docs/adr/0033, ticket 0007: the real core/steps/ci.sh on a real git
# range, a stub CodeHost and a stub Tracker. The base is this checkout's
# own judges, with GATES narrowed to risk (the one gate that routes; the
# other five need an engine or the network). The PR only loosens
# policy/merge.env so that every level auto-merges.
#   h1: the ticket's perimeter holds the diff. The PR's own policy would
#       auto-merge it; the CI step must classify it high (platform) with the
#       base's policy and send it to review.
#   h2: the same diff under a ticket whose perimeter does not hold it:
#       ci-failed, NO-GO on the perimeter, no gate run, whoever wrote it.
if [ -z "$mismatch" ]; then
  g="$root/g"
  mkdir -p "$g/host/gates" "$g/host/ci" "$g/host/core/steps" "$g/host/policy" "$g/host/app" "$g/tickets"
  # EXTRA_GATES (the environment, empty unless a case sets it) adds the planted gates below.
  # shellcheck disable=SC2016 # $(EXTRA_GATES) is make's, not the shell's
  sed 's/^GATES := .*/GATES := risk $(EXTRA_GATES)/' Makefile > "$g/host/Makefile"
  cp -R profiles "$g/host/profiles"
  cp app/workload.env "$g/host/app/workload.env"
  cp gates/risk.sh "$g/host/gates/"
  # Two planted gates, one that always says GO with a detail and one that always
  # says NO-GO with a detail line (h3, h4): what `ci.lines` must hold is known.
  printf '#!/bin/sh\nname=go-demo\n. ci/lib.sh\ngo_with "planted, always GO"\n' > "$g/host/gates/go-demo.sh"
  printf '#!/bin/sh\nname=nogo-demo\n. ci/lib.sh\nnogo "planted, always NO-GO" "a detail line"\n' > "$g/host/gates/nogo-demo.sh"
  cp ci/lib.sh "$g/host/ci/"
  cp core/__init__.py core/cmd_util.py core/risk.py core/policy.py core/perimeter.sh core/step_ci.py "$g/host/core/"
  # gates/risk.sh judges through core/gates/ (docs/adr/0084).
  cp -R core/gates "$g/host/core/"
  rm -rf "$g/host/core/gates/__pycache__"
  cp core/steps/ci.sh "$g/host/core/steps/"
  cp policy/merge.env "$g/host/policy/"
  (
    export GIT_AUTHOR_NAME=ci GIT_AUTHOR_EMAIL=ci@localhost GIT_COMMITTER_NAME=ci GIT_COMMITTER_EMAIL=ci@localhost
    cd "$g/host"
    git init -q -b main . && git add -A && git -c commit.gpgsign=false commit -q -m base
    sed 's/^RISK_AUTO_MERGE_LEVELS=.*/RISK_AUTO_MERGE_LEVELS=low:medium:high/' policy/merge.env > policy/merge.env.new
    mv policy/merge.env.new policy/merge.env
    git checkout -q -b ticket/0001 && git -c commit.gpgsign=false commit -q -am 'every level auto-merges'
    git branch ticket/0002
  ) >"$log" 2>&1 || fail_case g-setup "could not build the host repo"
fi
if [ -z "$mismatch" ]; then
  base_sha=$(git -C "$g/host" rev-parse main)
  head_sha=$(git -C "$g/host" rev-parse ticket/0001)
  # The PR would route itself if its own policy judged it: without this
  # the case could pass for the wrong reason.
  git -C "$g/host" show "$head_sha:policy/merge.env" > "$g/pr-merge.env"
  own=$(git -C "$g/host" diff --no-renames --numstat "$base_sha" "$head_sha" \
    | python3 core/risk.py --policy "$g/pr-merge.env" | jq -r .decision)
  [ "$own" = auto-merge ] || fail_case g-premise "the PR's own policy says $own, want auto-merge"
  git clone -q "$g/host" "$g/tower" >"$log" 2>&1 || fail_case g-setup "could not clone the host repo"
fi
if [ -z "$mismatch" ]; then
  mkdir -p "$g/tower/adapters/stub-codehost" "$g/tower/adapters/stub-tracker"
  cat > "$g/tower/adapters/stub-codehost/clone-url.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
printf '%s\n' "$d/host"
STUB
  cat > "$g/tower/adapters/stub-codehost/status.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
printf '%s %s\n' "$1" "$2" >> "$d/status.log"
STUB
  cat > "$g/tower/adapters/stub-tracker/get.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cat "$d/tickets/$1.md"
STUB
  cat > "$g/tickets/0001.md" <<'TICKET'
# 0001 - loosen the merge policy

## Perimeter

- `policy/merge.env`
TICKET
  cat > "$g/tickets/0002.md" <<'TICKET'
# 0002 - docs only

## Perimeter

- `docs/*`
TICKET
  step_ci() {  # TICKET: runs the real CI step from the tower clone, JSON on stdout
    (cd "$g/tower" && TICKET=$1 PR_NUMBER=1 BASE_SHA=$base_sha HEAD_SHA=$head_sha \
      CODE_HOST=stub-codehost TRACKER=stub-tracker WORKLOAD=app WORKLOAD_CONTRACT=app WORKLOAD_ATTACHED='' GATES_OUT=.gates-out \
      sh core/steps/ci.sh) 2>"$log"
  }
  got=$(step_ci 0001) || fail_case h1 "the CI step exited $?"
  [ "$(printf '%s' "$got" | jq -r '.state + " " + .ci.verdict + " " + .ci.risk_level + " " + .ci.decision')" = "review GO high review" ] \
    || fail_case h1 "a PR loosening policy/merge.env: $got (want review GO high review)"
  [ "$(cat "$g/status.log")" = "$head_sha success" ] || fail_case h1 "status posted: $(cat "$g/status.log")"
  # docs/adr/0071 §1: ci.lines is the gates' own lines file, and the one gate
  # that ran says plain `GO: risk`; the summary line is another thing.
  [ "$(printf '%s' "$got" | jq -c '.ci.lines')" = '["GO: risk"]' ] || fail_case h1-lines "ci.lines: $(printf '%s' "$got" | jq -c '.ci.lines')"
  [ "$(printf '%s' "$got" | jq -r '.ci.line')" != "$(printf '%s' "$got" | jq -r '.ci.lines[0]')" ] \
    || fail_case h1-lines "ci.lines[0] is the summary line, so it proves nothing about the lines file"
  # docs/adr/0085 §4: each gate that ran has its whole seconds, and only those.
  [ "$(printf '%s' "$got" | jq -c '.ci.seconds | [keys, (map(select(type == "number" and . >= 0 and . == floor)) | length)]')" = '[["risk"],1]' ] \
    || fail_case h1-seconds "ci.seconds: $(printf '%s' "$got" | jq -c '.ci.seconds')"
fi
if [ -z "$mismatch" ]; then
  rm -f "$g/status.log"
  got=$(step_ci 0002) || fail_case h2 "the CI step exited $?"
  [ "$(printf '%s' "$got" | jq -r '.state + " " + .ci.verdict + " " + .ci.failed')" = "ci-failed NO-GO perimeter" ] \
    || fail_case h2 "a PR outside its ticket's perimeter: $got (want ci-failed NO-GO perimeter)"
  case "$(printf '%s' "$got" | jq -r .ci.line)" in
    "NO-GO: perimeter — policy/merge.env is outside"*) : ;;
    *) fail_case h2 "line does not name the file: $(printf '%s' "$got" | jq -r .ci.line)" ;;
  esac
  ! grep -q '^risk: ' "$log" || fail_case h2 "a gate ran on a diff outside the perimeter"
  [ "$(cat "$g/status.log")" = "$head_sha failure" ] || fail_case h2 "status posted: $(cat "$g/status.log")"
  # docs/adr/0071 §1: the step's own re-check of the perimeter records its one line.
  [ "$(printf '%s' "$got" | jq -c '.ci.lines | length')" = 1 ] || fail_case h2-lines "ci.lines: $(printf '%s' "$got" | jq -c '.ci.lines')"
  [ "$(printf '%s' "$got" | jq -r '.ci.lines[0]')" = "$(printf '%s' "$got" | jq -r '.ci.line')" ] \
    || fail_case h2-lines "ci.lines[0] is '$(printf '%s' "$got" | jq -r '.ci.lines[0]')', not the one NO-GO line the step printed"
  case "$(printf '%s' "$got" | jq -r '.ci.lines[0]')" in "NO-GO: perimeter — "*) : ;; *) fail_case h2-lines "the recorded line is not a perimeter NO-GO" ;; esac
  # docs/adr/0085 §4: no gate ran, so no gate has seconds.
  [ "$(printf '%s' "$got" | jq -c '.ci.seconds')" = null ] || fail_case h2-seconds "ci.seconds of a perimeter refusal: $(printf '%s' "$got" | jq -c '.ci.seconds')"
fi
# h3 and h4 (docs/adr/0071 §1): the gates run from the base's Makefile with the
# planted gates added through EXTRA_GATES. h3, all GO: every gate's line, in the
# order run. h4, a NO-GO in the middle: the lines up to it and none after (`make
# gates` stops at the first NO-GO), the failed gate named, state ci-failed.
if [ -z "$mismatch" ]; then
  export EXTRA_GATES=go-demo
  got=$(step_ci 0001) || fail_case h3 "the CI step exited $?"
  unset EXTRA_GATES
  [ "$(printf '%s' "$got" | jq -c '.ci.lines')" = '["GO: risk","GO: go-demo — planted, always GO"]' ] \
    || fail_case h3 "ci.lines with two gates that said GO: $(printf '%s' "$got" | jq -c '.ci.lines') (the document: $got)"
  [ "$(printf '%s' "$got" | jq -r '.ci.verdict')" = GO ] || fail_case h3 "verdict $(printf '%s' "$got" | jq -r '.ci.verdict'), want GO"
  [ "$(printf '%s' "$got" | jq -c '.ci.seconds | [keys, (map(select(type == "number" and . >= 0 and . == floor)) | length)]')" = '[["go-demo","risk"],2]' ] \
    || fail_case h3-seconds "ci.seconds with two gates that ran: $(printf '%s' "$got" | jq -c '.ci.seconds')"
fi
if [ -z "$mismatch" ]; then
  export EXTRA_GATES="nogo-demo go-demo"
  got=$(step_ci 0001) || fail_case h4 "the CI step exited $?"
  unset EXTRA_GATES
  [ "$(printf '%s' "$got" | jq -r '.state + " " + .ci.verdict + " " + .ci.failed')" = "ci-failed NO-GO nogo-demo" ] \
    || fail_case h4 "a planted NO-GO: $got (want ci-failed NO-GO nogo-demo; if the gate did not run, the premise of h3 and h4 is gone)"
  [ "$(printf '%s' "$got" | jq -c '.ci.lines')" = '["GO: risk","NO-GO: nogo-demo — planted, always NO-GO"]' ] \
    || fail_case h4 "ci.lines of a NO-GO: $(printf '%s' "$got" | jq -c '.ci.lines'); want the gates up to the NO-GO and none after"
  [ "$(printf '%s' "$got" | jq -c '.ci.details')" = '["a detail line"]' ] || fail_case h4 "ci.details: $(printf '%s' "$got" | jq -c '.ci.details')"
  [ "$(printf '%s' "$got" | jq -c '.ci.seconds | [keys, (map(select(type == "number" and . >= 0 and . == floor)) | length)]')" = '[["nogo-demo","risk"],2]' ] \
    || fail_case h4-seconds "ci.seconds of a NO-GO: $(printf '%s' "$got" | jq -c '.ci.seconds'); want the gates up to the NO-GO and none after"
fi

[ -z "$mismatch" ] || nogo "case $mismatch disagrees with docs/adr/0019, 0033, 0034 or 0038"
go
