#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-shell-boundary: where shell is allowed in core/ and gates/ (docs/adr/
# 0052 §4, extended to gates/ by docs/adr/0084 §5). A shell file there has
# at most 30 code lines (non-blank, not a comment); past that, its logic
# belongs in Python (core/*.py, core/gates/*.py). The files over it today are
# named below with their exact count: debt, paid down by moving logic out,
# never raised. NO-GO on an unlisted file over 30, a listed file whose
# count is not its entry (grown: move the logic; shrunk: lower the entry),
# or an entry whose file is gone or under 30. The host files run before
# any image exists, where there is no Python (§1): exempt, listed apart,
# and an entry for a file that is gone is a NO-GO too. The check proves it
# can fail on a temp tree before it judges this one.
set -eu
name=shell-boundary
. ci/lib.sh

limit=30
host='core/doctor.sh core/attach.sh core/preflight.sh core/progress.sh'
debt='
core/crew.sh 87
core/egress.sh 42
core/launchpad.sh 70
core/mutation-baseline.sh 32
core/postmortem.sh 49
core/promote.sh 84
core/steps/ci.sh 85
core/steps/release.sh 56
core/steps/review.sh 71
core/steps/rework.sh 51
'

# flaws ROOT HOST DEBT: what is wrong under ROOT/core and ROOT/gates, one
# line each, or nothing.
flaws() (
  cd "$1"
  find core gates -name '*.sh' 2>/dev/null | sort | while IFS= read -r f; do
    case " $2 " in *" $f "*) continue ;; esac
    n=$(awk '!/^[[:space:]]*(#|$)/' "$f" | wc -l | tr -d ' ')
    ceiling=$(printf '%s\n' "$3" | awk -v f="$f" '$1 == f { print $2 }')
    if [ -z "$ceiling" ]; then
      [ "$n" -le "$limit" ] || echo "$f has $n code lines, over $limit: move its logic to Python"
    elif [ "$n" -le "$limit" ]; then echo "$f has $n code lines, at most $limit: remove its debt entry"
    elif [ "$n" -gt "$ceiling" ]; then echo "$f grew to $n code lines past its debt of $ceiling: move logic to Python"
    elif [ "$n" -lt "$ceiling" ]; then echo "$f is down to $n code lines: lower its debt entry from $ceiling"
    fi
  done
  for f in $2 $(printf '%s\n' "$3" | awk '{ print $1 }'); do
    [ -f "$f" ] || echo "$f is listed and gone: remove its entry"
  done
)

# The check must be able to fail: each file below breaks one clause, in
# core/ and in gates/, and ok.sh (30 code lines, comments and blanks
# besides) and the host file pass.
probe=$(mktemp -d)
trap 'rm -rf "$probe"' EXIT
mkdir -p "$probe/core/steps" "$probe/gates/suite"
lines() { i=0; while [ "$i" -lt "$1" ]; do printf ': %s\n\n# a comment\n' "$i"; i=$((i + 1)); done; }
lines 30 > "$probe/core/steps/ok.sh"
lines 31 > "$probe/core/new.sh"
lines 33 > "$probe/core/grown.sh"
lines 31 > "$probe/core/shrunk.sh"
lines 5 > "$probe/core/paid.sh"
lines 90 > "$probe/core/host.sh"
lines 30 > "$probe/gates/ok.sh"
lines 31 > "$probe/gates/new.sh"
lines 40 > "$probe/gates/suite/grown.sh"
lines 38 > "$probe/gates/shrunk.sh"
got=$(flaws "$probe" core/host.sh "$(printf 'core/grown.sh 32\ncore/shrunk.sh 32\ncore/paid.sh 40\ncore/gone.sh 50\ngates/suite/grown.sh 39\ngates/shrunk.sh 39')" | sort)
want='core/gone.sh is listed and gone: remove its entry
core/grown.sh grew to 33 code lines past its debt of 32: move logic to Python
core/new.sh has 31 code lines, over 30: move its logic to Python
core/paid.sh has 5 code lines, at most 30: remove its debt entry
core/shrunk.sh is down to 31 code lines: lower its debt entry from 32
gates/new.sh has 31 code lines, over 30: move its logic to Python
gates/shrunk.sh is down to 38 code lines: lower its debt entry from 39
gates/suite/grown.sh grew to 40 code lines past its debt of 39: move logic to Python'
[ "$got" = "$want" ] || nogo "the check does not catch what docs/adr/0052 §4 and docs/adr/0084 §5 say it does" "got:" "$got" "want:" "$want"

found=$(flaws . "$host" "$debt")
[ -z "$found" ] || nogo "$(printf '%s\n' "$found" | head -n1)$( [ "$(printf '%s\n' "$found" | wc -l)" -gt 1 ] && printf ' (and more, see stderr)')" "$found"
go
