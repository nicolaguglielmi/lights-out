#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# gates-registry-conformance: Registry port conformance suite (ADR 0107).
# Cold NO-GO unless CONFORMANCE=1 so ci/contract.sh never touches a registry.
set -eu
name=registry-conformance
. ci/lib.sh

if [ "${CONFORMANCE:-}" != "1" ]; then
  nogo "set CONFORMANCE=1: this suite publishes and verifies artifacts in the registry"
fi
verdict core.gates.registry
