#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# gates-orchestrator-conformance: Orchestrator port conformance suite (ADR 0133).
# Cold NO-GO unless CONFORMANCE=1 so ci/contract.sh stays instant and read-only.
set -eu
name=orchestrator-conformance
. ci/lib.sh

if [ "${CONFORMANCE:-}" != "1" ]; then
  nogo "set CONFORMANCE=1: this suite drives the Orchestrator port"
fi
verdict core.gates.orchestrator
