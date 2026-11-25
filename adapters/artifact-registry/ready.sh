#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-artifact-registry: resolves Artifact Registry OIDC/ADC credentials
# and delegates to adapters/oci-registry/ready.sh.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/artifact-registry/env.sh
. "$here/env.sh"

exec sh "$here/../oci-registry/ready.sh"
