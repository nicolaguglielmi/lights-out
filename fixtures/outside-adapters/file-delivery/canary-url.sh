#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-delivery/env.sh
. "$here/env.sh"

printf '%s\n' "$FILE_DELIVERY_CANARY_URL"
