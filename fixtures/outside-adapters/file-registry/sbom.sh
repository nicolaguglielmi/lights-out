#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
ref=${1:-}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-registry/env.sh
. "$here/env.sh"

[ -n "$ref" ] || { printf 'file-registry: usage: sbom.sh <repo>@sha256:...\n' >&2; exit 1; }
case "$ref" in
  *@sha256:*) digest="sha256:${ref#*@sha256:}" ;;
  sha256:*) digest=$ref ;;
  *) printf 'file-registry: expected digest reference, got %s\n' "$ref" >&2; exit 1 ;;
esac

path="$FILE_REGISTRY_DIR/sboms/$digest"
[ -f "$path" ] || { printf 'file-registry: %s has no SBOM attestation\n' "$ref" >&2; exit 1; }
cat "$path"
