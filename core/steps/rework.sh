#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# step-rework (docs/adr/0038): a head the gates or the reviewer rejected
# goes back to dev-worker with the verdict. Reads the document only: the
# attempt number, and who rejected — ci.verdict NO-GO is the gates,
# otherwise review.verdict changes-requested is the reviewer; anything
# else is no verdict to rework, a step failure. Appends the verdict to
# `rework` (one entry per rejected attempt, so the next attempt's ci and
# review fields cannot erase it). At policy/rework.env's cap, read from
# this checkout and never from the PR, prints needs-human and runs
# nothing. Otherwise writes the verdict document — the NO-GO line and the
# gate's own details, or the reviewer's verdict and reason; never a
# transcript — and runs core/crew.sh for the next attempt from the head
# that was judged: same ticket, same perimeter, same PR, new head. Prints
# pr-opened with ci and review cleared, so merge and audit can only read
# the new attempt's verdicts. A crew failure exits 1: the tower strands
# the ticket at ci-failed for ./go resume (docs/adr/0034), not an attempt.
set -eu
: "${TICKET:?TICKET not set}"
: "${DOC_FILE:?DOC_FILE not set; run this via core/tower.sh, not directly}"
: "${ROLE:=dev-worker}"

# shellcheck disable=SC1091 # policy/rework.env is this checkout's own
. policy/rework.env
: "${REWORK_MAX_ATTEMPTS:?policy/rework.env sets no REWORK_MAX_ATTEMPTS}"

attempt=$(jq -r '.attempt // 1' "$DOC_FILE")
judged=$(jq -r '.pr.head_sha // empty' "$DOC_FILE")
[ -n "$judged" ] || { printf 'step-rework: %s has no pr.head_sha\n' "$DOC_FILE" >&2; exit 1; }

entry=$(jq -c --argjson n "$attempt" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
  if .ci.verdict == "NO-GO" then
    {attempt: $n, judge: "gates", head_sha: .pr.head_sha, line: .ci.line, details: (.ci.details // []), at: $at}
  elif .review.verdict == "changes-requested" then
    {attempt: $n, judge: "review", head_sha: .pr.head_sha,
     line: "CHANGES-REQUESTED — \(.review.role)", details: (.review.reason // "" | split("\n")), at: $at}
  else empty end' "$DOC_FILE")
[ -n "$entry" ] || {
  printf 'step-rework: ticket %s is at ci-failed with neither a gates NO-GO nor a changes-requested review\n' "$TICKET" >&2
  exit 1
}
rework=$(jq -c --argjson e "$entry" '(.rework // []) + [$e]' "$DOC_FILE")

if [ "$attempt" -ge "$REWORK_MAX_ATTEMPTS" ]; then
  printf '[rework] ticket %s: attempt %s of %s rejected (%s); a human is needed\n' \
    "$TICKET" "$attempt" "$REWORK_MAX_ATTEMPTS" "$(printf '%s' "$entry" | jq -r .line)" >&2
  jq -cn --argjson rework "$rework" '{state: "needs-human", rework: $rework}'
  exit 0
fi

next=$((attempt + 1))
verdict=$(mktemp)
trap 'rm -f "$verdict"' EXIT
printf '%s' "$entry" | jq -r --argjson next "$next" --argjson max "$REWORK_MAX_ATTEMPTS" '
  "## Rework: attempt \($next) of \($max)\n",
  "Your previous attempt (head \(.head_sha)) was not accepted. The verdict",
  "below is everything the \(if .judge == "gates" then "gates" else "reviewer" end) said about it. Your workspace already",
  "holds that attempt: change it to address every point the verdict raises,",
  "within the ticket above, and nothing else.\n",
  "Verdict:\n",
  "    \(.line)",
  (if (.details | length) > 0 then "\nDetails:\n", (.details[] | "    \(.)") else empty end)' > "$verdict"

printf '[rework] ticket %s: attempt %s rejected by the %s; attempt %s of %s\n' \
  "$TICKET" "$attempt" "$(printf '%s' "$entry" | jq -r .judge)" "$next" "$REWORK_MAX_ATTEMPTS" >&2
crew_dir=".crew/$TICKET"
TICKET="$TICKET" ROLE="$ROLE" ATTEMPT="$next" REWORK_FILE="$verdict" REWORK_HEAD="$judged" \
  sh core/crew.sh >&2

# shellcheck disable=SC1090,SC1091 # written by crew.sh just now
. "$crew_dir/pr-opened.env"
harness=$(jq -r '.harness // "unknown"' "$crew_dir/out/run.json")
model=$(jq -r '.model // .recorded.model // null' "$crew_dir/out/run.json")
# The cost of the run (docs/adr/0047 §2, P1): what the harness reported,
# or under replay what the recorded run reported, the rule model already
# follows. A key the harness does not report is null, never a guess.
cost=$(jq -c '{provider: (.provider // .recorded.provider // null),
  thinking: (.thinking // .recorded.thinking // null), turns: (.turns // .recorded.turns // null),
  tokens: (.tokens // .recorded.tokens // null), duration_s: (.duration_s // .recorded.duration_s // null),
  input_sha256: (.input_sha256 // .recorded.input_sha256 // null), replayed: (.harness == "replay")}' \
  "$crew_dir/out/run.json")

jq -cn --argjson next "$next" --argjson rework "$rework" --arg n "$PR_NUMBER" --arg url "$PR_URL" \
  --arg base "$BASE_SHA" --arg head "$HEAD_SHA" --arg harness "$harness" --arg model "$model" --arg role "$ROLE" --argjson cost "$cost" \
  '{state: "pr-opened", attempt: $next, rework: $rework, ci: null, review: null,
    pr: {number: ($n | tonumber), url: $url, base_sha: $base, head_sha: $head},
    task: ({role: $role, harness: $harness, model: (if $model == "null" then null else $model end)} + $cost)}'
