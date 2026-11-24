#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
tag=${1:-}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-registry/env.sh
. "$here/env.sh"

[ -n "$tag" ] || { printf 'file-registry: usage: publish.sh <tag>\n' >&2; exit 1; }
init_registry

digest="sha256:$(printf '%s:%s\n' "$FILE_REGISTRY_REPO" "$tag" | sha256sum | awk '{ print $1 }')"
sbom="sha256:$(printf 'sbom:%s\n' "$digest" | sha256sum | awk '{ print $1 }')"

tmp_tag=$(mktemp "$FILE_REGISTRY_DIR/tags/.tag.XXXXXX")
jq -cn --arg d "$digest" --arg k "$FILE_REGISTRY_KEY" '{digest: $d, key: $k}' > "$tmp_tag"
mv "$tmp_tag" "$FILE_REGISTRY_DIR/tags/$tag.json"

tmp_sbom=$(mktemp "$FILE_REGISTRY_DIR/sboms/.sbom.XXXXXX")
printf '%s\n' "$sbom" > "$tmp_sbom"
mv "$tmp_sbom" "$FILE_REGISTRY_DIR/sboms/$digest"

printf '%s@%s\n' "$FILE_REGISTRY_REPO" "$digest"
