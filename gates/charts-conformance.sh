#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# gates-charts-conformance: Charts port conformance suite (ADR 0122).
# Cold NO-GO unless CONFORMANCE=1 so ci/contract.sh stays instant and read-only.
set -eu
name=charts-conformance
. ci/lib.sh

if [ "${CONFORMANCE:-}" != "1" ]; then
  nogo "set CONFORMANCE=1: this suite queries the Charts port"
fi
verdict core.gates.charts
