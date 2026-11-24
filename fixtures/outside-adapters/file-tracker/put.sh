#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# outside/file-tracker put.sh: self-contained Tracker fixture outside the
# checkout (ADR 0058, ADR 0116). Idempotent on `- Origin: <origin>`.
set -eu
[ $# -ge 2 ] && [ -n "$1" ] && [ -n "$2" ] || { printf 'file-tracker: usage: put.sh <title> <origin> < body\n' >&2; exit 1; }
title=$1
origin=$2
dir=${FILE_TRACKER_DIR:-.crew/file-tracker}
mkdir -p "$dir"

for f in "$dir"/*.md; do
  [ -f "$f" ] || continue
  if grep -qxF -- "- Origin: $origin" "$f"; then
    basename "$f" .md
    exit 0
  fi
done

max=0
for f in "$dir"/*.md; do
  [ -f "$f" ] || continue
  n=$(basename "$f" .md)
  case "$n" in *[!0-9]* | '') continue ;; esac
  n=$(printf '%s' "$n" | sed 's/^0*//')
  [ "${n:-0}" -le "$max" ] || max=$n
done
id=$(printf '%04d' $((max + 1)))

tmp=$(mktemp "$dir/.put.XXXXXX")
trap 'rm -f "$tmp"' EXIT
{
  printf '# %s — %s\n\n- Status: open\n- Origin: %s\n\n' "$id" "$title" "$origin"
  cat
} > "$tmp" || { printf 'file-tracker: could not write %s\n' "$tmp" >&2; exit 1; }
mv "$tmp" "$dir/$id.md" || { printf 'file-tracker: could not write %s/%s.md\n' "$dir" "$id" >&2; exit 1; }
trap - EXIT
printf '%s\n' "$id"
