#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-perimeter: ticket 0003's rule (docs/adr/0021 §5). A ticket declares
# the paths it may touch in a `## Perimeter` section, one glob per list
# item; this reads changed paths on stdin (core/risk.py's stdin-from-git
# shape) and checks each one with a case pattern (the same "* crosses /"
# semantics fnmatch gives risk.py). Prints one line and exits 1 on the
# first path matching no glob, or when the ticket has no `## Perimeter`
# section at all — never a silent pass. Not a gate (no GO:/NO-GO: line,
# no ci/lib.sh): a driver-internal check, core/harness-check.sh's own
# convention. Runs identically at every level and on a replayed PR,
# because it reads the diff the workspace actually has, not a record of
# one (docs/adr/0021's second criterion).
#
#   git -C <workspace> diff --name-only <base>..HEAD \
#     | sh core/perimeter.sh <ticket-file>
#   sh core/perimeter.sh --list <ticket-file>
#
# --list prints the declared globs, one per line, and reads no stdin: the
# logbook's reader shows a ticket's perimeter from here, so the section is
# parsed in one place (docs/adr/0047, provisional decision 15).
#
# set -f: `for glob in $globs` must split on newlines without expanding
# each glob against the caller's directory first (crew runs from the repo
# root), or a declared glob that already matches files on disk turns into
# that list of files and refuses a new path it should accept.
set -euf

list=""
if [ "${1:-}" = --list ]; then
  list=1
  shift
fi
ticket=${1:?usage: core/perimeter.sh [--list] <ticket-file>}
[ -f "$ticket" ] || { printf 'no such ticket file: %s\n' "$ticket" >&2; exit 1; }

globs=$(awk '
  /^## Perimeter/ { found = 1; next }
  found && /^## /  { exit }
  found && /^- /   { sub(/^- +/, ""); gsub(/`/, ""); print }
' "$ticket")

[ -n "$globs" ] || { printf 'ticket %s has no ## Perimeter section\n' "$ticket" >&2; exit 1; }

if [ -n "$list" ]; then
  printf '%s\n' "$globs"
  exit 0
fi

while IFS= read -r path; do
  [ -n "$path" ] || continue
  matched=0
  old_ifs=$IFS
  IFS='
'
  for glob in $globs; do
    # shellcheck disable=SC2254 # unquoted on purpose: $glob is meant to act
    # as a case pattern (risk.py's own "* crosses /" semantics), not to
    # match literally.
    case "$path" in
      $glob) matched=1; break ;;
    esac
  done
  IFS=$old_ifs
  if [ "$matched" -eq 0 ]; then
    printf '%s is outside the ticket'"'"'s declared perimeter\n' "$path" >&2
    exit 1
  fi
done

exit 0
