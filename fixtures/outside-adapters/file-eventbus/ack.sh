#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-eventbus/env.sh
. "$here/env.sh"

[ $# -eq 1 ] && [ -n "$1" ] || { printf 'file-eventbus: usage: ack.sh <ackId>\n' >&2; exit 1; }
eventbus_op ack "$1"
