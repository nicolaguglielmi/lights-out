#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# run-cloud-workflows: ORCHESTRATOR=cloud-workflows runner (profiles/l3.env).
# With 0 arguments, drains EVENT_BUS via adapters/$EVENT_BUS/{pull,ack}.sh and
# dispatches each {ticket, step} payload to Google Cloud Workflows Executions
# REST v1 with bounded polling (WORKFLOW_POLL_INTERVAL_S=2,
# WORKFLOW_POLL_TIMEOUT_S=300). With 2 arguments (<ticket> <step>), executes
# one workflow dispatch directly.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/cloud-workflows/lib.sh
. "$here/lib.sh"

wf_resolve_token || exit 1

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

dispatch_step() {
  _ticket=$1 _step=$2 _payload_json=${3:-"{}"}
  _arg_str=$(jq -cn --arg t "$_ticket" --arg s "$_step" --argjson p "$_payload_json" \
    '($p | if type == "object" then . else {} end) + {ticket: $t, step: $s}')
  jq -cn --arg a "$_arg_str" '{argument: $a}' > "$tmp/exec_req.json"

  _create_url="$WORKFLOWS_API_URL/v1/$WORKFLOWS_PARENT/executions"
  _code=$(wf_curl POST "$_create_url" "$tmp/exec_req.json" "$tmp/exec_create.json")
  if [ "$_code" != "200" ] && [ "$_code" != "201" ]; then
    printf '[cloud-workflows] execution create failed for %s %s (HTTP %s)\n' "$_ticket" "$_step" "$_code" >&2
    return 1
  fi

  _exec_name=$(jq -r '.name // empty' "$tmp/exec_create.json" 2>/dev/null) || _exec_name=""
  if [ -z "$_exec_name" ]; then
    printf '[cloud-workflows] execution create returned no name for %s %s\n' "$_ticket" "$_step" >&2
    return 1
  fi
  case "$_exec_name" in
    projects/*) _poll_url="$WORKFLOWS_API_URL/v1/$_exec_name" ;;
    *) _poll_url="$WORKFLOWS_API_URL/v1/$WORKFLOWS_PARENT/executions/$_exec_name" ;;
  esac

  _start=$(date +%s)
  while :; do
    _pcode=$(wf_curl GET "$_poll_url" "" "$tmp/exec_poll.json")
    if [ "$_pcode" != "200" ]; then
      printf '[cloud-workflows] poll %s returned HTTP %s\n' "$_exec_name" "$_pcode" >&2
      return 1
    fi
    _state=$(jq -r '.state // "ACTIVE"' "$tmp/exec_poll.json" 2>/dev/null) || _state=""
    case "$_state" in
      SUCCEEDED)
        printf '[cloud-workflows] execution %s (%s %s) succeeded\n' "$_exec_name" "$_ticket" "$_step" >&2
        return 0 ;;
      FAILED|CANCELLED|UNAVAILABLE)
        _err=$(jq -r '.error.message // .state // "failed"' "$tmp/exec_poll.json" 2>/dev/null) || _err="$_state"
        printf '[cloud-workflows] execution %s (%s %s) %s: %s\n' "$_exec_name" "$_ticket" "$_step" "$_state" "$_err" >&2
        return 1 ;;
      ACTIVE|QUEUED|STATE_UNSPECIFIED) ;;
      *)
        printf '[cloud-workflows] execution %s unknown state %s\n' "$_exec_name" "$_state" >&2
        return 1 ;;
    esac
    _now=$(date +%s)
    if [ $((_now - _start)) -ge "$WORKFLOW_POLL_TIMEOUT_S" ]; then
      printf '[cloud-workflows] execution %s (%s %s) timed out after %ss\n' \
        "$_exec_name" "$_ticket" "$_step" "$WORKFLOW_POLL_TIMEOUT_S" >&2
      return 1
    fi
    sleep "$WORKFLOW_POLL_INTERVAL_S"
  done
}

if [ "$#" -eq 2 ]; then
  dispatch_step "$1" "$2"
  exit $?
elif [ "$#" -ne 0 ]; then
  printf 'usage: run.sh [<ticket> <step>]\n' >&2
  exit 1
fi

: "${EVENT_BUS:?EVENT_BUS not set; run this via ./go, not directly}"

failed=0
applied=0
while :; do
  msg=$(sh "adapters/$EVENT_BUS/pull.sh") || exit 1
  [ -n "$msg" ] || break
  ackid=${msg%% *}
  data=${msg#* }
  ticket=$(printf '%s' "$data" | jq -r '.ticket // empty' 2>/dev/null) || ticket=""
  step=$(printf '%s' "$data" | jq -r '.step // empty' 2>/dev/null) || step=""
  if [ -z "$ticket" ] || [ -z "$step" ]; then
    printf '[cloud-workflows] dropping a message that is not {ticket, step}: %s\n' "$data" >&2
  elif ! dispatch_step "$ticket" "$step" "$data"; then
    failed=$((failed + 1))
  fi
  applied=$((applied + 1))
  sh "adapters/$EVENT_BUS/ack.sh" "$ackid" || exit 1
done

printf '[cloud-workflows] queue empty after %s event(s), %s failed\n' "$applied" "$failed" >&2
[ "$failed" -eq 0 ]
