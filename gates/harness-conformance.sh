#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# gates-harness-conformance: every CLI harness's entry requirement (docs/adr/
# 0041 §8), against a scripted model, no key: eleven cases per harness, the
# first disagreement a NO-GO naming the harness and the case. The suite is
# core/gates/harness.py (docs/adr/0084, amendment A1): its docstring is the
# spec, the cases and the provider pairing are tables there, the harnesses
# are discovered. This file keeps the guards, in the order a cold run sees
# them, and the one line.
#
# Lives in gates/, so ci/contract.sh cold-runs it inside `make ci` with no
# CONFORMANCE set and no engine: the guard below must be the first thing
# that runs. `make harness-conformance` is the only caller that sets
# CONFORMANCE=1, after gates/harness-conformance/proof.sh has proved the
# suite can fail; the acceptance command is `./go harness-conformance
# [CONFORMANCE_HARNESS=<name>]`, never a bare `sh gates/…` (CLAUDE.md).
set -eu
name=harness-conformance
. ci/lib.sh

# Instant NO-GO, before anything touches the engine (docs/adr/0026 §4).
if [ "${CONFORMANCE:-}" != "1" ]; then
  nogo "set CONFORMANCE=1: this suite builds each CLI harness's image and runs it against a scripted model (./go harness-conformance)"
fi

for tool in jq git python3; do
  command -v "$tool" >/dev/null 2>&1 || nogo "$tool is not on PATH; run this via ./go harness-conformance"
done
[ -f roles/test-reviewer/prompt.md ] || nogo "roles/test-reviewer/prompt.md is missing: case 10a runs the test-reviewer"
case "${AGENT_HARNESS:-}:${ADAPTERS_TRIAL:-}" in
  outside/*:AGENT_HARNESS) ;;
  *)
    [ -n "${WORKBENCH_IMAGE:-}" ] || nogo "WORKBENCH_IMAGE is not set; run this via ./go harness-conformance"
    command -v docker-compose >/dev/null 2>&1 || nogo "docker-compose is not on PATH; run this via ./go harness-conformance"
    # The suite's compose view: compose.yaml and the smallest workload fragment
    # that defines `backend` (the Makefile's to say; this directory never names a
    # workload, ci/app-free.sh).
    [ -n "${COMPOSE_FILE:-}" ] || nogo "COMPOSE_FILE is not set; run this via ./go harness-conformance"
    [ -n "${WORKLOAD:-}" ] || nogo "WORKLOAD is not set; run this via ./go harness-conformance"
    [ -n "${WORKLOAD_CONTRACT:-}" ] || nogo "WORKLOAD_CONTRACT is not set; run this via ./go harness-conformance"
    # Case 10's fixture and the module that reads a contract (docs/adr/0073).
    [ -f "$WORKLOAD_CONTRACT/mcp.json" ] && [ -f "$WORKLOAD_CONTRACT/mcp/notes.py" ] \
      || nogo "case 10's fixture is missing: $WORKLOAD_CONTRACT/mcp.json and $WORKLOAD_CONTRACT/mcp/notes.py (docs/adr/0073)"
    [ -f core/agents.py ] || nogo "core/agents.py is missing: case 10 reads a contract through it (docs/adr/0073 §7)"
    ;;
esac

verdict core.gates.harness
