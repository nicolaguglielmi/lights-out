#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-delivery/env.sh
. "$here/env.sh"

init_state
updated=$(jq -c '{stable: .stable, canary: null, weight: 0}' "$FILE_DELIVERY_STATE")
write_state "$updated"
