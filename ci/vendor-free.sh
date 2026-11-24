#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-vendor-free: core/ never names a Delivery vendor (docs/adr/0024 §1).
# Adding a Delivery adapter is a directory under adapters/ and a DELIVERY=
# line in a profile, zero edits to core/; the proof is that no file under
# core/ contains the name of an adapter that exists or is planned, in code
# or in comments — a comment saying "no Envoy here" is a coupling too, the
# day Envoy goes it turns false. The word list is the Delivery column of
# plan §4 plus the third candidate of plan §11, grown to cover Flags too
# now that core/launchpad.sh reads the freeze flag through adapters/$FLAGS/
# evaluate.sh instead of a hardcoded flagd URL (docs/adr/0026 §3), and
# the CodeHost column since a second code host exists (docs/adr/0043 §7):
# extend it when the table grows, never shrink it.
set -eu
name=vendor-free
. ci/lib.sh

[ -d core ] || nogo "core/ not found"

words='envoy|cloud[ _-]?deploy|(^|[^a-z])argo|rollouts|flagger|flagd|unleash|gitea|github|gitlab'

hits=$(grep -rniE "$words" core --include='*.sh' --include='*.py' --include='*.md' \
  --exclude-dir=__pycache__ --exclude-dir=.pytest_cache || true)

[ -z "$hits" ] || nogo "core/ names a Delivery, Flags or CodeHost vendor" "$hits"

go
