#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# outside/file-tracker get.sh: self-contained Tracker fixture outside the
# checkout (ADR 0058, ADR 0116). Reads FILE_TRACKER_DIR/<id>.md.
set -eu
id=${1:-}
[ -n "$id" ] || { printf 'file-tracker: usage: get.sh <ticket-id>\n' >&2; exit 1; }
case "$id" in
  */*|*..*) printf 'file-tracker: invalid ticket id: %s\n' "$id" >&2; exit 1 ;;
esac
dir=${FILE_TRACKER_DIR:-.crew/file-tracker}
file="$dir/$id.md"
[ -f "$file" ] || { printf 'file-tracker: no such ticket: %s/%s.md\n' "$dir" "$id" >&2; exit 1; }
cat "$file"
