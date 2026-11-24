#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
tag=${1:-}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-registry/env.sh
. "$here/env.sh"

[ -n "$tag" ] || { printf 'file-registry: usage: verify.sh <tag>\n' >&2; exit 1; }
init_registry

rec="$FILE_REGISTRY_DIR/tags/$tag.json"
[ -f "$rec" ] || { printf 'file-registry: %s is absent or unsigned\n' "$tag" >&2; exit 1; }

key=$(jq -r '.key // empty' "$rec")
digest=$(jq -r '.digest // empty' "$rec")
[ "$key" = "$FILE_REGISTRY_KEY" ] || { printf 'file-registry: untrusted signature for %s\n' "$tag" >&2; exit 1; }
printf '%s' "$digest" | grep -Eq '^sha256:[0-9a-f]{64}$' || { printf 'file-registry: malformed digest for %s\n' "$tag" >&2; exit 1; }

printf '%s@%s\n' "$FILE_REGISTRY_REPO" "$digest"
