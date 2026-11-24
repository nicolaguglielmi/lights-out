#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
version=${1:?usage: deploy.sh <version> <image>}
image=${2:?usage: deploy.sh <version> <image>}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-delivery/env.sh
. "$here/env.sh"

init_state
updated=$(jq -c --arg v "$version" --arg img "$image" \
  '{stable: .stable, canary: $v, canary_image: $img, weight: 0}' "$FILE_DELIVERY_STATE")
write_state "$updated"
