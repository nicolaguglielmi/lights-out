#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-flags-file: verifies $FLAGS_FILE exists, is valid flag JSON, and
# defines release-freeze evaluating to a boolean. Silent on success, one
# stderr line and exit 1 on failure.
set -eu
: "${FLAGS_FILE:?FLAGS_FILE not set}"
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

[ -f "$FLAGS_FILE" ] || { printf 'ready-flags-file: %s does not exist\n' "$FLAGS_FILE" >&2; exit 1; }

val=$(sh "$here/evaluate.sh" release-freeze 2>/dev/null) || val=""
case "$val" in
  true|false) exit 0 ;;
  *)
    printf 'ready-flags-file: %s has no valid release-freeze boolean flag\n' "$FLAGS_FILE" >&2
    exit 1 ;;
esac
