#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-launchpad: the release controller (docs/adr/0013 §4, amended by
# docs/adr/0014 §1, 0025 §6-§7). Vendor-free: it only calls the DELIVERY,
# METRICS, REGISTRY and FLAGS adapter scripts the profile names and the
# verify/slo.sh gate — no vendor name appears below (ci/vendor-free.sh).
# Stages: rollback to a known state, read the stable version, check the
# freeze flag, verify the signature, staging, then 5% and 25%, each gated
# by verify/slo.sh. A NO-GO at any stage rolls back and exits 1. A full
# pass holds the canary at 25%, waiting for go-for-launch (core/
# promote.sh, docs/adr/0020 §4), and exits 0. Either way the decision and
# the numbers that produced it are written to RELEASE_RECORD, the file
# promote reads; from the deploy on, the record says `in-progress` until a
# verdict replaces it, so a run killed mid-flight never leaves the prior
# run's `held` behind (ticket 0008). Generates no traffic itself:
# production has real traffic, the Makefile `release` target starts the
# workload's `load.sh` beside this at L0 (docs/adr/0030 §3).
#
# Not scanned by ci/contract.sh (core/, not ci/ gates/ verify/), but reuses
# ci/lib.sh's go/nogo for the same GO:/NO-GO: line shape.
set -eu
name=launchpad
. ci/lib.sh

: "${CANARY_VERSION:?CANARY_VERSION not set}"
: "${CANARY_TAG:?CANARY_TAG not set}"
: "${STAGE_SECONDS:=60}"
: "${PLANE_HOST:=host.containers.internal}"
: "${APP_URL:=http://$PLANE_HOST:8080}"
: "${DELIVERY:?DELIVERY not set; run this via ./go, not directly}"
: "${METRICS:?METRICS not set; run this via ./go, not directly}"
: "${REGISTRY:?REGISTRY not set; run this via ./go, not directly}"
: "${FLAGS:?FLAGS not set; run this via ./go, not directly}"

: "${RELEASE_RECORD:=.crew/release.json}"

delivery="adapters/$DELIVERY"
metrics_query="adapters/$METRICS/query.sh"
export METRICS_QUERY="$metrics_query"

rollback() {
  sh "$delivery/rollback.sh"
}

# The release record (docs/adr/0020 §4, amended 0025 §7): version, stable,
# the verified image (empty until the signature check below passes), one
# entry per verified stage with its verdict and the `slo:` lines behind
# it, and the outcome: `held`, `rolled-back`, or `in-progress` — written
# just before the deploy and replaced by one of the other two on every
# exit path after it, so `in-progress` on disk means a run died before
# its verdict (ticket 0008). A pre-staging refusal (freeze, signature)
# leaves the prior run's record on disk untouched (docs/adr/0026 §6 case
# 3); core/promote.sh checks any `held` record against the plane.
#
# The release log (docs/adr/0047 §2, P2): every final record, `held` or
# `rolled-back`, is also appended to RELEASES_LOG with the time and the
# tower's ticket (RELEASE_TICKET, set by core/steps/release.sh; null for a
# release run directly). RELEASE_RECORD keeps only the latest; the log
# keeps them all, for the logbook. Never rewritten, only appended.
: "${RELEASES_LOG:=.crew/releases.jsonl}"
stages='[]'
image=""
record() {
  # record VERDICT [HELD_AT]
  mkdir -p "$(dirname "$RELEASE_RECORD")"
  jq -n --arg v "$CANARY_VERSION" --arg s "${STABLE_VERSION:-}" --argjson stages "$stages" \
    --arg verdict "$1" --arg held "${2:-}" --arg image "$image" \
    '{version: $v, stable: $s, stages: $stages, verdict: $verdict,
      held_at: (if $held == "" then null else ($held | tonumber) end),
      image: $image}' > "$RELEASE_RECORD"
  [ "$1" != in-progress ] || return 0
  mkdir -p "$(dirname "$RELEASES_LOG")"
  jq -c --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg t "${RELEASE_TICKET:-}" \
    '{kind: "release", at: $at, ticket: (if $t == "" then null else $t end), record: .}' \
    "$RELEASE_RECORD" >> "$RELEASES_LOG"
}

fail_stage() {
  # fail_stage STAGE REASON: rolls back, records, then NO-GOs with both in one line.
  rollback >&2
  record rolled-back
  nogo "$1: $2 → rolled back"
}

# frozen [STAGE]: returns if release-freeze is false; otherwise
# refuses — before staging with nogo, at a stage with fail_stage. An
# unanswered flag, or an answer that is neither true nor false, refuses
# too: a harness for an untrusted codebase fails closed (docs/adr/0030 §5,
# amended 2026-11-02).
frozen() {
  freeze=$(sh "adapters/$FLAGS/evaluate.sh" release-freeze) || freeze=""
  case "$freeze" in
    false) return 0 ;;
    true) reason="release-freeze is on" ;;
    *) reason="cannot read release-freeze from the Flags port; refusing to release" ;;
  esac
  [ $# -gt 0 ] || nogo "$reason"
  fail_stage "$1" "$reason"
}

# Known state before anything else: idempotent, safe even if a previous run
# was killed mid-flight.
rollback

healthz=$(curl -fsS --connect-timeout 1 --max-time 3 "$APP_URL/healthz") \
  || nogo "could not read the stable version from $APP_URL/healthz; run ./go up"
stable_version=$(printf '%s' "$healthz" | jq -er .version) \
  || nogo "$APP_URL/healthz answered without a version"
export STABLE_VERSION="$stable_version"
export CANARY_VERSION

frozen

# The signature check (docs/adr/0022 §3, 0025 §6): the Registry port
# verifies before anything is staged; an unsigned or mis-signed tag is
# refused by this exact line, not folded into a Delivery adapter's own
# exit 1. cosign's own reason streams to stderr from verify.sh itself.
image=$(sh "adapters/$REGISTRY/verify.sh" "$CANARY_TAG") || nogo "unsigned image"

record in-progress
printf '[launchpad] staging: deploying %s at %s (stable is %s)\n' "$CANARY_VERSION" "$image" "$stable_version" >&2
sh "$delivery/deploy.sh" "$CANARY_VERSION" "$image" >&2 || fail_stage staging "deploy did not become healthy"

for pct in 5 25; do
  frozen "$pct%"

  sh "$delivery/shift.sh" "$pct" >&2 || fail_stage "$pct%" "shift did not take"

  sleep "$STAGE_SECONDS"

  # verify's stderr (the slo: lines) is kept for the record and passed on.
  slo_err=$(mktemp)
  slo_out=$(sh verify/slo.sh 2>"$slo_err") && slo_rc=0 || slo_rc=$?
  cat "$slo_err" >&2
  numbers=$(grep '^slo: ' "$slo_err" | jq -R . | jq -cs .)
  rm -f "$slo_err"
  stages=$(printf '%s' "$stages" | jq -c --argjson pct "$pct" --arg verdict "$slo_out" --argjson numbers "$numbers" \
    '. + [{pct: $pct, verdict: $verdict, numbers: $numbers}]')
  if [ "$slo_rc" -eq 0 ]; then
    printf '[launchpad] %s%%: GO\n' "$pct" >&2
  else
    reason=$(printf '%s' "$slo_out" | sed 's/^NO-GO: slo — //')
    fail_stage "$pct%" "$reason"
  fi
done

record held 25
printf '[launchpad] 25%%: GO — held; ./go promote runs go-for-launch (record at %s)\n' "$RELEASE_RECORD" >&2
go
