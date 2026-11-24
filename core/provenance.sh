#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-provenance: signed SLSA v1.0 / in-toto provenance attestation
# (docs/adr/0117). Builds PROVENANCE_DIR/<id>.intoto.json via core/provenance.py,
# signs it with COSIGN_KEY (default .crew/cosign.key, docs/adr/0025 §2) into
# <att>.bundle, and verifies it immediately against <pub>. `verify <att>`
# re-verifies an existing attestation and bundle.
set -eu
name=provenance
. ci/lib.sh
: "${COSIGN_KEY:=.crew/cosign.key}"
pub=${COSIGN_KEY%.key}.pub

if [ "${1:-}" = verify ]; then
  att=${2:-}
  [ -n "$att" ] && [ -f "$att" ] && [ -f "$att.bundle" ] || nogo "missing statement or bundle for ${att:-<unset>}"
  [ -f "$pub" ] || nogo "no public key $pub"
  cosign verify-blob --key "$pub" --insecure-ignore-tlog --bundle "$att.bundle" "$att" >&2 || nogo "$att failed cosign verification against $pub"
  why=$(python3 core/provenance.py verify --statement "$att" --bundle "$att.bundle" --repo "${WORKLOAD:-}") || nogo "${why:-$att invalid}"
  go_with "$att verified"
fi

[ "${1:-}" != build ] || shift
out=$(python3 core/provenance.py build "$@") || nogo "${out:-core/provenance.py failed without a reason; see stderr}"
att=$(printf '%s' "$out" | cut -f1)
rm -f "$att.bundle"
[ -f "$COSIGN_KEY" ] || nogo "$att written but not signed: no $COSIGN_KEY; ./go up generates the plane's key"
[ -f "$pub" ] || nogo "$att written but cannot verify: no public key $pub"
# shellcheck disable=SC1007 # deliberately empty: cosign's passwordless-key convention
COSIGN_PASSWORD= cosign sign-blob --key "$COSIGN_KEY" --tlog-upload=false --use-signing-config=false --bundle "$att.bundle" --yes "$att" >&2 || nogo "cosign sign-blob $att failed"
cosign verify-blob --key "$pub" --insecure-ignore-tlog --bundle "$att.bundle" "$att" >&2 || nogo "$att signed but its bundle does not verify against $pub"
printf 'provenance: verify with: cosign verify-blob --key %s --insecure-ignore-tlog --bundle %s.bundle %s\n' "$pub" "$att" "$att" >&2
go_with "$att signed and verified"
