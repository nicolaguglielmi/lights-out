#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# gates-tracker-conformance: Tracker port conformance suite (ADR 0116).
# Cold NO-GO unless CONFORMANCE=1 so ci/contract.sh never touches remote trackers.
set -eu
name=tracker-conformance
. ci/lib.sh

if [ "${CONFORMANCE:-}" != "1" ]; then
  nogo "set CONFORMANCE=1: this suite creates and updates tickets on the Tracker port"
fi
verdict core.gates.tracker
