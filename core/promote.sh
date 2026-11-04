#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-promote: go-for-launch, the one human decision before 100% (docs/
# adr/0020 §4). Reads the release record launchpad wrote, refuses anything
# but a held canary that the plane still runs at the held weight, has the
# release role compose the promotion brief
# from that record, prints the brief, and asks: GO_FOR_LAUNCH=GO|NO-GO
# when set, else one line from the terminal, else NO-GO (nobody answered,
# nothing moves). GO shifts the canary to 100% of traffic; NO-GO rolls it
# back. Vendor-free: the DELIVERY adapter and the role's harness adapter
# are the only scripts it calls. At L0 "100%" is the canary cluster taking
# every request; the canary does not become the new stable image (M8).
#
# Who answered, and through what (docs/adr/0047 §3): GO_FOR_LAUNCH_BY
# (default `unauthenticated`: a container's user name would look like an
# identity it is not) and GO_FOR_LAUNCH_VIA (default `cli`; the console
# passes `console`) go into the record, `promoted` on GO and `rejected` on
# NO-GO, and into one line of RELEASES_LOG per answer. With
# GO_FOR_LAUNCH_BRIEF_SHA256 set, promote answers on the brief already in
# PROMOTE_OUT, the one the human read, if its sha256 matches, and writes
# no new one; otherwise nothing moves. With no answer it writes the brief
# and moves nothing: that is how a console prepares the brief it shows.
#
# Reuses ci/lib.sh's go/nogo for the one-line verdict, as launchpad does.
set -eu
name=go-for-launch
. ci/lib.sh

: "${RELEASE_RECORD:=.crew/release.json}"
: "${DELIVERY:?DELIVERY not set; run this via ./go, not directly}"
: "${AGENT_HARNESS:?AGENT_HARNESS not set; run this via ./go, not directly}"
: "${PROMOTE_OUT:=.crew/promote}"
: "${RELEASES_LOG:=.crew/releases.jsonl}"
by=${GO_FOR_LAUNCH_BY:-unauthenticated}
via=${GO_FOR_LAUNCH_VIA:-cli}
answered_sha=${GO_FOR_LAUNCH_BRIEF_SHA256:-}

[ -f "$RELEASE_RECORD" ] || nogo "no release record; run ./go release" "looked at $RELEASE_RECORD"
record=$(jq -c . "$RELEASE_RECORD" 2>/dev/null) || nogo "$RELEASE_RECORD is not JSON"
verdict=$(printf '%s' "$record" | jq -r '.verdict // "none"')
version=$(printf '%s' "$record" | jq -r '.version // empty')
[ -n "$version" ] || nogo "$RELEASE_RECORD names no version"
if [ "$verdict" != held ]; then
  stage=$(printf '%s' "$record" | jq -r '.stages[-1].pct // "no"')
  nogo "release $version is $verdict at ${stage}%, nothing to promote"
fi

# The record is what launchpad decided, not what the plane runs now: a
# later rollback (demo-hold's cleanup, another launchpad's opening one)
# leaves it at `held`. The Delivery port's status.sh reads the plane
# itself (docs/adr/0026 §2); promote only a canary that is still there at
# the weight it was held at (ticket 0008).
held_at=$(printf '%s' "$record" | jq -r '.held_at // empty')
[ -n "$held_at" ] || nogo "$RELEASE_RECORD is held but names no held_at"
status=$(sh "adapters/$DELIVERY/status.sh") \
  || nogo "adapters/$DELIVERY/status.sh could not read the plane; nothing moved"
plane_canary=$(printf '%s' "$status" | jq -r '.canary // "none"' 2>/dev/null) \
  || nogo "adapters/$DELIVERY/status.sh did not print JSON" "$status"
plane_weight=$(printf '%s' "$status" | jq -r '.weight' 2>/dev/null)
if [ "$plane_canary" != "$version" ] || [ "$plane_weight" != "$held_at" ]; then
  nogo "the record holds $version at ${held_at}%, the plane has canary $plane_canary at ${plane_weight}%; nothing moved"
fi

# The brief: the release role over the record, through its harness. At L0
# the roles have no model — they replay responses recorded at L1, keyed
# on their input — and a live record carries live numbers no recording
# can match (found at M7 close, docs/adr/0020 §4 note). So with
# AGENT_HARNESS=replay the brief is rendered from the record by this
# script, labelled as such; from L1 up the release role writes it. Not a
# hidden mock: the numbers are the record's, the label says who wrote it,
# and the README declares it.
[ -f roles/release/role.env ] || nogo "roles/release/role.env not found"
# shellcheck disable=SC1091 # the role's budgets
. roles/release/role.env
harness=${HARNESS:-$AGENT_HARNESS}
if [ -n "$answered_sha" ]; then
  # The answer is to the brief already shown: no second brief is written,
  # because a release role at L1 would not write the same one twice.
  on_disk=$(sha256sum "$PROMOTE_OUT/brief.md" 2>/dev/null | cut -d' ' -f1)
  [ "$on_disk" = "$answered_sha" ] || nogo "the brief on disk is not the one answered; nothing moved"
  brief=$(cat "$PROMOTE_OUT/brief.md")
  author=$(head -n1 "$PROMOTE_OUT/brief.author" 2>/dev/null) || author="unknown"
elif [ "$AGENT_HARNESS" = replay ]; then
  rm -rf "$PROMOTE_OUT"
  mkdir -p "$PROMOTE_OUT/out"
  author="core/promote.sh from the record — no model at L0, the release role writes it from L1"
  brief=$(printf '%s' "$record" | jq -r '
    "# Promotion brief: \(.version)\n\nPromote `\(.version)` (`\(.image)`), replacing stable `\(.stable)`.\n\n## Stages\n"
    + ([.stages[] | "- \(.pct)%: \(.verdict)\n" + ([.numbers[] | "  - \(.)\n"] | join(""))] | join(""))
    + "\n## Not verified\n- 100% of traffic: not observed yet; that is what this decision starts.\n"
    + "\nRecommendation: GO — every stage passed verify; the numbers above are the evidence."')
else
  rm -rf "$PROMOTE_OUT"
  mkdir -p "$PROMOTE_OUT/out"
  author="the release role, $harness"
  ROLE=release INPUT="$RELEASE_RECORD" OUT="$PROMOTE_OUT/out" \
    THINKING="${THINKING:-low}" TIMEOUT_S="${TIMEOUT_S:-900}" \
    sh "adapters/$harness/run.sh" >&2 || nogo "the release role did not produce a brief ($harness)"
  calls="$PROMOTE_OUT/out/calls.json"
  [ -f "$calls" ] || nogo "the release role left no calls.json"
  declined=$(jq -r '[.[] | select(.name == "decline_brief")] | first | .args.reason // empty' "$calls")
  [ -z "$declined" ] || nogo "the release role declined: $declined"
  brief=$(jq -r '[.[] | select(.name == "submit_brief")] | first | .args.brief // empty' "$calls")
  [ -n "$brief" ] || nogo "the release role submitted no brief"
  brief_version=$(jq -r '[.[] | select(.name == "submit_brief")] | first | .args.version // empty' "$calls")
  [ "$brief_version" = "$version" ] || nogo "the brief is for $brief_version, the record is $version"
fi
case "$brief" in
  *"$version"*) ;;
  *) nogo "the brief does not name $version" ;;
esac
for pct in $(printf '%s' "$record" | jq -r '.stages[].pct'); do
  case "$brief" in
    *"$pct%"*) ;;
    *) nogo "the brief does not name the $pct% stage" ;;
  esac
done
if [ -z "$answered_sha" ]; then
  printf '%s\n' "$brief" > "$PROMOTE_OUT/brief.md"
  printf '%s\n' "$author" > "$PROMOTE_OUT/brief.author"
fi
{
  printf '[go-for-launch] promotion brief for %s (by %s):\n\n' "$version" "$author"
  printf '%s\n\n' "$brief"
} >&2

# answered ANSWER: who answered goes into the record (`promoted` or
# `rejected`) and one line of the release log.
answered() {
  at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  key=promoted
  [ "$1" = GO ] || key=rejected
  printf '%s' "$record" | jq -c --arg k "$key" --arg at "$at" --arg by "$by" --arg via "$via" \
    '. + {($k): ((if $k == "promoted" then {weight: 100} else {} end) + {at: $at, by: $by, via: $via})}' \
    > "$RELEASE_RECORD"
  mkdir -p "$(dirname "$RELEASES_LOG")"
  jq -cn --arg at "$at" --arg v "$version" --arg a "$1" --arg by "$by" --arg via "$via" \
    '{kind: "answer", at: $at, version: $v, answer: $a, by: $by, via: $via}' >> "$RELEASES_LOG"
}

# The human.
answer=${GO_FOR_LAUNCH:-}
if [ -z "$answer" ]; then
  if [ -t 0 ]; then
    printf 'go-for-launch %s to 100%%? [GO/NO-GO] ' "$version" >&2
    read -r answer || answer=""
  fi
fi
case "$answer" in
  GO) ;;
  NO-GO)
    sh "adapters/$DELIVERY/rollback.sh" >&2 || nogo "human said NO-GO, and the rollback failed"
    answered NO-GO
    nogo "human said NO-GO" "$version rolled back" ;;
  "") nogo "no human answer (set GO_FOR_LAUNCH=GO or answer on the terminal); nothing moved" ;;
  *) nogo "answer \"$answer\" is neither GO nor NO-GO; nothing moved" ;;
esac

sh "adapters/$DELIVERY/shift.sh" 100 >&2 || nogo "shift to 100% did not take"
answered GO
printf '[go-for-launch] %s at 100%% of traffic (%s)\n' "$version" "$DELIVERY" >&2
go
