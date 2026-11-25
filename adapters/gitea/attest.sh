#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# attest-gitea: L0 local Gitea code host has no remote attestations API
# (docs/adr/0134). Validates <subject-sha> and readable JSON
# <statement-or-bundle-file>, optionally writes a sidecar
# .attestation-receipt.json beside the statement if writable, and prints
# one honest JSON receipt line on stdout ("stored":"local").
set -eu
sha=${1:-}
att_file=${2:-}
[ -n "$sha" ] && [ -n "$att_file" ] \
  || { printf 'usage: attest.sh <subject-sha> <statement-or-bundle-file>\n' >&2; exit 1; }
case "$sha" in
  *[!A-Za-z0-9:._-]* | -* | .*)
    printf 'attest-gitea: invalid subject sha %s\n' "$sha" >&2
    exit 1 ;;
esac
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck source=adapters/gitea/env.sh
. "$here/adapters/gitea/env.sh"

[ -f "$att_file" ] && [ -r "$att_file" ] && [ -s "$att_file" ] \
  || { printf 'attest-gitea: attestation file missing or unreadable: %s\n' "$att_file" >&2; exit 1; }
jq -e 'type == "object"' "$att_file" >/dev/null 2>&1 \
  || { printf 'attest-gitea: %s is not a valid JSON object\n' "$att_file" >&2; exit 1; }

receipt=$(jq -cn --arg sha "$sha" \
  '{stored: "local", id: null, sha: $sha, note: "local codehost has no remote attestations API; verify with cosign"}')
sidecar="${att_file%.bundle}.attestation-receipt.json"
sidecar_dir=$(dirname -- "$sidecar")
if [ -w "$sidecar_dir" ] && { [ ! -e "$sidecar" ] || [ -w "$sidecar" ]; }; then
  printf '%s\n' "$receipt" > "$sidecar" 2>/dev/null || true
fi
printf '%s\n' "$receipt"
