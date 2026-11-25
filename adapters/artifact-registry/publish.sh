#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# publish-artifact-registry: resolves Artifact Registry OIDC/ADC credentials
# and delegates to adapters/oci-registry/publish.sh.
set -eu
tag=${1:?usage: publish.sh <tag>}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/artifact-registry/env.sh
. "$here/env.sh"

exec sh "$here/../oci-registry/publish.sh" "$tag"
