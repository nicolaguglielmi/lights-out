#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# step-review (docs/adr/0021 §2, §3): a medium- or high-risk PR's second
# judge. Fetches ticket/<id> from the code host (ci.sh's own verification
# lines: the branch exists there, and it is the head the PR was opened
# with), clones the head into its own workspace under .crew/ (a worktree's
# .git file would not resolve inside the harness container), builds the
# input document — the ticket text plus the diff, nothing else, so the
# review is of the code, not of the risk score that routed it here — and
# runs the role's harness on it. `test-reviewer` is the default role;
# `architect` runs through the same script (docs/adr/0021 §4) but nothing
# in the tower calls it. The harness's final message decides the state:
# APPROVED reaches ci-passed (merge's own precondition, unchanged), and
# CHANGES-REQUESTED reaches ci-failed, from which the tower sends the
# reason back to the worker (core/steps/rework.sh, docs/adr/0038) —
# nothing else is a valid verdict, and there is no default. Called by the tower
# (PR_NUMBER, BASE_SHA, HEAD_SHA from the document) and by the Makefile's
# `review` target (from .crew/<id>/pr-opened.env): one code path for both,
# ci.sh's own precedent.
set -eu
: "${TICKET:?TICKET not set}"
: "${PR_NUMBER:?PR_NUMBER not set}"
: "${BASE_SHA:?BASE_SHA not set}"
: "${HEAD_SHA:?HEAD_SHA not set}"
: "${CODE_HOST:?CODE_HOST not set; run this via ./go, not directly}"
: "${TRACKER:?TRACKER not set; run this via ./go, not directly}"
: "${AGENT_HARNESS:?AGENT_HARNESS not set; run this via ./go, not directly}"
: "${ROLE:=test-reviewer}"

if [ ! -f "roles/$ROLE/prompt.md" ]; then
  printf 'step-review: role %s has no roles/%s/prompt.md yet\n' "$ROLE" "$ROLE" >&2
  exit 1
fi
# shellcheck disable=SC1090,SC1091 # role.env's path is built from $ROLE
. "roles/$ROLE/role.env"
harness=${HARNESS:-$AGENT_HARNESS}

branch="ticket/$TICKET"
clone_url=$(sh "adapters/$CODE_HOST/clone-url.sh")
git -C "${WORKLOAD:-.}" fetch -q "$clone_url" "$branch" \
  || { printf 'step-review: could not fetch %s from %s\n' "$branch" "$CODE_HOST" >&2; exit 1; }
fetched=$(git -C "${WORKLOAD:-.}" rev-parse FETCH_HEAD)
[ "$fetched" = "$HEAD_SHA" ] \
  || { printf 'step-review: %s on %s is %s, the PR was opened at %s\n' "$branch" "$CODE_HOST" "$fetched" "$HEAD_SHA" >&2; exit 1; }

# Absolute, like core/crew.sh's own crew_dir: adapters/gemini-cli/run.sh
# derives the in-container path from $(pwd)/.crew/..., so a relative path
# here would silently mismatch it.
review_dir="$(pwd)/.crew/$TICKET/review"
rm -rf "$review_dir"
mkdir -p "$review_dir"

git clone -q "$clone_url" "$review_dir/workspace" >&2 \
  || { printf 'step-review: clone from %s failed\n' "$CODE_HOST" >&2; exit 1; }
git -C "$review_dir/workspace" checkout -q "$HEAD_SHA" >&2 \
  || { printf 'step-review: checkout of %s failed\n' "$HEAD_SHA" >&2; exit 1; }

sh "adapters/$TRACKER/get.sh" "$TICKET" > "$review_dir/ticket.md" \
  || { printf 'step-review: no such ticket: %s\n' "$TICKET" >&2; exit 1; }

# The input document (docs/adr/0021 §2, §3): ticket text, then the diff.
# Not the risk verdict — that would invite a review of the size number
# instead of the code, and the risk verdict has already done its job by
# routing the PR here — nor an earlier attempt's verdict: the reviewer
# reads the ticket and the diff, every attempt (docs/adr/0038 §4). No
# commit sha in this text, on purpose: a replay reapplies the recorded
# patch with `git am`, which mints a fresh commit (new hash, new
# timestamp) for byte-identical content every time, so a sha embedded
# here would make input_matches_recorded false on every
# ordinary replay — a constant false positive, not a drift signal.
{
  cat "$review_dir/ticket.md"
  printf '\n---\n\nDiff:\n\n```diff\n'
  git -C "$review_dir/workspace" diff "$BASE_SHA".."$HEAD_SHA"
  printf '```\n'
} > "$review_dir/input.md"
input_sha256=$(sha256sum "$review_dir/input.md" | cut -d' ' -f1)

# BASE is the workspace's own HEAD, not the ticket's BASE_SHA: the
# workspace is already checked out at HEAD_SHA (the diff to review is the
# input document, not something to reproduce from a range), so a
# reviewer that makes no edits gets an empty patch — never dev-worker's
# own diff replayed a second time under the reviewer's name. Found live:
# BASE_SHA here made `adapters/gemini-cli/run.sh`'s generic
# `format-patch` reproduce the PR's own commit, and replay then tried to
# `git am` it onto a workspace that already had it, refusing every time.
mkdir -p "$review_dir/out"
review_head=$(git -C "$review_dir/workspace" rev-parse HEAD)
# The attempt under review (docs/adr/0038 §8): replay keys its verdict on it.
attempt=1
[ -z "${DOC_FILE:-}" ] || attempt=$(jq -r '.attempt // 1' "$DOC_FILE")
ROLE="$ROLE" TICKET="$TICKET" ATTEMPT="$attempt" WORKSPACE="$review_dir/workspace" BASE="$review_head" \
OUT="$review_dir/out" TICKET_FILE="$review_dir/input.md" \
THINKING="$THINKING" MAX_TURNS="$MAX_TURNS" TIMEOUT_S="$TIMEOUT_S" TOKEN_BUDGET="$TOKEN_BUDGET" \
  sh "adapters/$harness/run.sh" >&2 \
  || { printf 'step-review: %s run failed for ticket %s\n' "$harness" "$TICKET" >&2; exit 1; }

[ -f "$review_dir/out/final.md" ] \
  || { printf 'step-review: %s produced no final.md\n' "$harness" >&2; exit 1; }
first_line=$(head -n1 "$review_dir/out/final.md")
reason=$(tail -n +2 "$review_dir/out/final.md" | sed '/./,$!d')

case "$first_line" in
  APPROVED) verdict=approved; state=ci-passed; status=success ;;
  CHANGES-REQUESTED) verdict=changes-requested; state=ci-failed; status=failure ;;
  *)
    printf 'step-review: final.md'"'"'s first line is %s, want APPROVED or CHANGES-REQUESTED\n' \
      "${first_line:-empty}" >&2
    exit 1 ;;
esac
[ -n "$reason" ] || { printf 'step-review: %s verdict has an empty reason\n' "$verdict" >&2; exit 1; }

# A replayed run's reported input_sha256, if it recorded one, against what
# this run actually built: a drifted pair is visible on the document, not
# a NO-GO — a prose edit to the ticket must not block the tower by itself
# (docs/adr/0021 §9's rejected alternative).
recorded_sha256=$(jq -r '.recorded.input_sha256 // empty' "$review_dir/out/run.json" 2>/dev/null) || recorded_sha256=""
if [ "$harness" = replay ] && [ -n "$recorded_sha256" ]; then
  [ "$recorded_sha256" = "$input_sha256" ] && input_matches=true || input_matches=false
else
  input_matches=true
fi

harness_reported=$(jq -r '.harness // "unknown"' "$review_dir/out/run.json")
model=$(jq -r '.model // .recorded.model // null' "$review_dir/out/run.json")
# The cost of the review run (docs/adr/0047 §2, P1), the rule of assign;
# input_sha256 is this step's own, above.
cost=$(jq -c '{provider: (.provider // .recorded.provider // null),
  thinking: (.thinking // .recorded.thinking // null), turns: (.turns // .recorded.turns // null),
  tokens: (.tokens // .recorded.tokens // null), duration_s: (.duration_s // .recorded.duration_s // null),
  replayed: (.harness == "replay")}' "$review_dir/out/run.json")

sh "adapters/$CODE_HOST/status.sh" "$HEAD_SHA" "$status" review "$verdict: $(printf '%s\n' "$reason" | head -n1)" \
  || { printf 'step-review: could not post the status\n' >&2; exit 1; }

jq -cn --arg state "$state" --arg role "$ROLE" --arg verdict "$verdict" --arg reason "$reason" \
  --arg harness "$harness_reported" --arg model "$model" --arg insha "$input_sha256" --argjson matches "$input_matches" --argjson cost "$cost" \
  '{state: $state, review: ($cost + {role: $role, verdict: $verdict, reason: $reason, harness: $harness,
    model: (if $model == "null" then null else $model end),
    input_sha256: $insha, input_matches_recorded: $matches})}'
