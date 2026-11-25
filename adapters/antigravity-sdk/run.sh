#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# harness-antigravity-sdk: AgentHarness port backed by the Python
# google-antigravity SDK (PLAN §4, §5; ADR 0133). Supports coding roles,
# review roles, and conversational roles without containers.
set -eu

: "${ROLE:?ROLE not set}"
: "${OUT:?OUT not set}"
: "${THINKING:?THINKING not set}"
: "${TIMEOUT_S:=900}"

if [ -n "${WORKSPACE:-}" ]; then
  : "${TICKET:?TICKET not set}"
  : "${BASE:?BASE not set}"
  : "${TICKET_FILE:?TICKET_FILE not set}"
  : "${MAX_TURNS:?MAX_TURNS not set}"
  : "${TOKEN_BUDGET:?TOKEN_BUDGET not set}"
  [ -d "$WORKSPACE" ] || { printf 'harness-antigravity-sdk: WORKSPACE %s is not a directory\n' "$WORKSPACE" >&2; exit 1; }
  [ -f "$TICKET_FILE" ] || { printf 'harness-antigravity-sdk: TICKET_FILE %s is not a file\n' "$TICKET_FILE" >&2; exit 1; }
else
  : "${INPUT:?INPUT not set}"
  [ -f "$INPUT" ] || { printf 'harness-antigravity-sdk: INPUT %s is not a file\n' "$INPUT" >&2; exit 1; }
fi

case "${MODEL_PROVIDER:-}" in
  gemini) ;;
  anthropic|openai|zai)
    fits=claude-code
    [ "$MODEL_PROVIDER" = openai ] && fits=codex
    printf 'harness-antigravity-sdk: MODEL_PROVIDER=%s runs on AGENT_HARNESS=%s (docs/adr/0041 §2); this harness runs gemini only\n' "$MODEL_PROVIDER" "$fits" >&2
    exit 1
    ;;
  *)
    printf 'harness-antigravity-sdk: MODEL_PROVIDER=%s is not gemini; this harness runs gemini only (docs/adr/0041 §2)\n' "${MODEL_PROVIDER:-}" >&2
    exit 1
    ;;
esac

[ -n "${MODEL:-}" ] || { printf 'harness-antigravity-sdk: MODEL is empty; set the exact model id in the profile or .env (docs/adr/0040 §1)\n' >&2; exit 1; }
case "${GEMINI_API_KEY:-}" in
  ""|replace-me)
    printf 'harness-antigravity-sdk: MODEL_PROVIDER=gemini needs a real GEMINI_API_KEY in .env (see .env.example)\n' >&2
    exit 1
    ;;
esac

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
thinking_key="GEMINI_$(printf '%s' "$THINKING" | tr '[:lower:]' '[:upper:]')"
AGY_THINKING_LEVEL=$(grep "^${thinking_key}=" "$here/thinking.env" | head -n1 | cut -d= -f2-)
[ -n "$AGY_THINKING_LEVEL" ] || {
  printf 'harness-antigravity-sdk: %s has no %s= line (docs/adr/0040 §2)\n' "$here/thinking.env" "$thinking_key" >&2
  exit 1
}
export AGY_THINKING_LEVEL

if [ -n "${CREW_MCP:-}" ] && [ -f "$CREW_MCP/decl.json" ]; then
  printf 'harness-antigravity-sdk: declared MCP servers are unsupported without mcp.sh (ADR 0073 §8)\n' >&2
  exit 1
fi

mkdir -p "$OUT"
rm -f "$OUT/stop"

rc=0
PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" LIGHTS_OUT_ROOT="$here/../.." \
  CREW_OUT="$OUT" timeout "$TIMEOUT_S" python3 -m lights_out_agy_sdk.run </dev/null || rc=$?
if [ "$rc" -eq 124 ]; then
  printf 'harness-antigravity-sdk: %s: over TIMEOUT_S=%s\n' "$ROLE" "$TIMEOUT_S" >&2
  exit 1
fi
[ "$rc" -eq 0 ] || exit 1

if [ -z "${WORKSPACE:-}" ]; then
  if [ "$(jq 'length' "$OUT/calls.json" 2>/dev/null || printf 0)" -eq 0 ]; then
    printf 'harness-antigravity-sdk: %s made no tool call; exit 0 is not success (docs/adr/0020 §1)\n' "$ROLE" >&2
    exit 1
  fi
fi
