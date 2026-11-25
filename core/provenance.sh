#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-provenance: signed SLSA v1.0 / in-toto provenance attestation
# (docs/adr/0117, 0134). Builds PROVENANCE_DIR/<id>.intoto.json via
# core/provenance.py, signs with COSIGN_KEY (default .crew/cosign.key)
# into <att>.bundle, verifies against <pub>, and optionally attaches via
# adapters/$CODE_HOST/attest.sh. `verify <att>` and `attach ...` delegate
# to core/provenance.py.
set -eu
name=provenance
. ci/lib.sh
: "${COSIGN_KEY:=.crew/cosign.key}"
pub=${COSIGN_KEY%.key}.pub

if [ "${1:-}" = verify ] || [ "${1:-}" = attach ]; then
  cmd=$1; shift
  [ "$cmd" != verify ] || set -- --statement "${1:-}" --bundle "${1:-}.bundle" --key "$pub" --repo "${WORKLOAD:-}"
  why=$(python3 core/provenance.py "$cmd" "$@") || nogo "${why:-provenance $cmd failed}"
  go_with "${why#OK: }"
fi

[ "${1:-}" != build ] || shift
out=$(PROVENANCE_DEFER_ATTACH=1 python3 core/provenance.py build "$@") || nogo "${out:-core/provenance.py failed without a reason; see stderr}"
att=$(printf '%s' "$out" | cut -f1)
rm -f "$att.bundle"
[ -f "$COSIGN_KEY" ] && [ -f "$pub" ] || nogo "$att written but not signed: missing $COSIGN_KEY or $pub; ./go up generates the plane's key"
# shellcheck disable=SC1007 # deliberately empty: cosign's passwordless-key convention
COSIGN_PASSWORD= cosign sign-blob --key "$COSIGN_KEY" --tlog-upload=false --use-signing-config=false --bundle "$att.bundle" --yes "$att" >&2 || nogo "cosign sign-blob $att failed"
cosign verify-blob --key "$pub" --insecure-ignore-tlog --bundle "$att.bundle" "$att" >&2 || nogo "$att signed but its bundle does not verify against $pub"
printf 'provenance: verify with: cosign verify-blob --key %s --insecure-ignore-tlog --bundle %s.bundle %s\n' "$pub" "$att" "$att" >&2
case " $* " in *" --attach-required "*) PROVENANCE_ATTACH=1; PROVENANCE_ATTACH_REQUIRED=1 ;; *" --attach "*) PROVENANCE_ATTACH=1 ;; esac
if [ -n "${PROVENANCE_ATTACH:-}" ]; then
  why=$(PROVENANCE_ATTACH_REQUIRED="${PROVENANCE_ATTACH_REQUIRED:-}" python3 core/provenance.py attach --statement "$att" --bundle "$att.bundle") || nogo "${why:-attach failed for $att}"
fi
go_with "$att signed and verified"
