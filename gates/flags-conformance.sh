#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# gates-flags-conformance: Flags port conformance suite (ADR 0107).
# Cold NO-GO unless CONFORMANCE=1 so ci/contract.sh stays instant and read-only.
set -eu
name=flags-conformance
. ci/lib.sh

if [ "${CONFORMANCE:-}" != "1" ]; then
  nogo "set CONFORMANCE=1: this suite evaluates and toggles flags on the Flags port"
fi
verdict core.gates.flags
