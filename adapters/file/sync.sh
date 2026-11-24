#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# sync-file: atomically updates `- Status:`, `- PR:`, and `- ADR:` metadata
# lines in TICKETS_DIR/<id>.md (ADR 0116). Silent on success, one stderr
# line and exit 1 on failure.
set -eu
id=${1:-}
state=${2:-}
pr_url=${3:-}
adr_ref=${4:-}
[ -n "$id" ] && [ -n "$state" ] || { printf 'sync-file: usage: sync.sh <ticket-id> <state> [pr_url] [adr_ref]\n' >&2; exit 1; }
case "$id" in
  */*|*..*) printf 'sync-file: invalid ticket id: %s\n' "$id" >&2; exit 1 ;;
esac

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
dir=${TICKETS_DIR:-tickets}
case "$dir" in
  /*) file="$dir/$id.md" ;;
  *) file="$here/$dir/$id.md" ;;
esac
[ -f "$file" ] || { printf 'sync-file: no such ticket: %s/%s.md\n' "$dir" "$id" >&2; exit 1; }

tmp=$(mktemp "$(dirname -- "$file")/.sync.XXXXXX")
trap 'rm -f "$tmp"' EXIT

SYNC_STATE=$state SYNC_PR=$pr_url SYNC_ADR=$adr_ref awk '
BEGIN {
  st = ENVIRON["SYNC_STATE"]
  pr = ENVIRON["SYNC_PR"]
  adr = ENVIRON["SYNC_ADR"]
}
{
  sub(/\r$/, "")
  lines[NR] = $0
}
END {
  meta_end = 1
  last_meta = 0
  in_header = 1
  for (i = 1; i <= NR; i++) {
    ln = lines[i]
    if (i == 1 && ln ~ /^# /) {
      meta_end = i
      continue
    }
    if (in_header) {
      if (ln == "") {
        meta_end = i
        if (last_meta > 0) in_header = 0
      } else if (ln ~ /^- [A-Za-z0-9_-]+:/) {
        meta_end = i
        last_meta = i
      } else {
        in_header = 0
      }
    }
  }
  seen_st = 0; seen_pr = 0; seen_adr = 0
  for (i = 1; i <= meta_end; i++) {
    if (!seen_st && lines[i] ~ /^- Status:/) {
      lines[i] = "- Status: " st
      seen_st = 1
    } else if (pr != "" && !seen_pr && lines[i] ~ /^- PR:/) {
      lines[i] = "- PR: " pr
      seen_pr = 1
    } else if (adr != "" && !seen_adr && lines[i] ~ /^- ADR:/) {
      lines[i] = "- ADR: " adr
      seen_adr = 1
    }
  }
  insert_after = (last_meta > 0 ? last_meta : 1)
  for (i = 1; i <= NR; i++) {
    print lines[i]
    if (i == insert_after) {
      if (last_meta == 0 && (NR < 2 || lines[2] != "")) {
        print ""
      }
      if (!seen_st) print "- Status: " st
      if (pr != "" && !seen_pr) print "- PR: " pr
      if (adr != "" && !seen_adr) print "- ADR: " adr
    }
  }
}
' "$file" > "$tmp" || { printf 'sync-file: could not update %s\n' "$file" >&2; exit 1; }

mv "$tmp" "$file" || { printf 'sync-file: could not replace %s\n' "$file" >&2; exit 1; }
trap - EXIT
