#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# shift-cloud-deploy: native DELIVERY=cloud-deploy adapter delegating to
# plugins/cloud/gcp-cloud-deploy/shift.sh (5/25/100%).
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CLOUD_DEPLOY_ADAPTER_DIR=$here
# shellcheck source=adapters/cloud-deploy/env.sh
. "$here/env.sh"
exec sh "$CLOUD_DEPLOY_PLUGIN_DIR/shift.sh" "$@"
