#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# step-assign (docs/adr/0019 §1): ticket → PR through core/crew.sh, then
# the record: the PR, the range, which harness and model produced it, and
# the spec as the harness read it (sha256 and first line of the ticket
# text). Re-runnable: crew.sh wipes .crew/<ticket> and reclones. Prints
# the fields to merge into the document; next state pr-opened, attempt 1
# (core/steps/rework.sh counts the next ones, docs/adr/0038).
set -eu
: "${TICKET:?TICKET not set}"
: "${ROLE:=dev-worker}"

crew_dir=".crew/$TICKET"
TICKET="$TICKET" ROLE="$ROLE" sh core/crew.sh >&2

# shellcheck disable=SC1090,SC1091 # written by crew.sh just now
. "$crew_dir/pr-opened.env"
harness=$(jq -r '.harness // "unknown"' "$crew_dir/out/run.json")
# replay records the model of the run it replays, never one of its own.
model=$(jq -r '.model // .recorded.model // null' "$crew_dir/out/run.json")
# The cost of the run (docs/adr/0047 §2, P1): what the harness reported,
# or under replay what the recorded run reported, the rule model already
# follows. A key the harness does not report is null, never a guess.
cost=$(jq -c '{provider: (.provider // .recorded.provider // null),
  thinking: (.thinking // .recorded.thinking // null), turns: (.turns // .recorded.turns // null),
  tokens: (.tokens // .recorded.tokens // null), duration_s: (.duration_s // .recorded.duration_s // null),
  input_sha256: (.input_sha256 // .recorded.input_sha256 // null), replayed: (.harness == "replay")}' \
  "$crew_dir/out/run.json")
spec_sha=$(sha256sum "$crew_dir/ticket.md" | cut -d' ' -f1)
spec_title=$(head -n1 "$crew_dir/ticket.md" | sed 's/^# *//')

jq -cn --arg n "$PR_NUMBER" --arg url "$PR_URL" --arg base "$BASE_SHA" --arg head "$HEAD_SHA" \
  --arg harness "$harness" --arg model "$model" --arg role "$ROLE" \
  --arg sha "$spec_sha" --arg title "$spec_title" --argjson cost "$cost" \
  '{state: "pr-opened", attempt: 1,
    pr: {number: ($n | tonumber), url: $url, base_sha: $base, head_sha: $head},
    task: ({role: $role, harness: $harness, model: (if $model == "null" then null else $model end)} + $cost),
    spec: {sha256: $sha, title: $title}}'
