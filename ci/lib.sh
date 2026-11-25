#!/bin/sh
# Sourced by every gate: `. ci/lib.sh` after setting `name`.
# shellcheck disable=SC2154 # name is set by the sourcing gate
#
# Gate output contract (CLAUDE.md, "Contratto di output dei gate"):
#   - stdout carries exactly one line: "GO: <name>", optionally followed by
#     " — <detail>" (docs/adr/0042 §3: where a toolchain gate ran), or
#     "NO-GO: <name> — <reason>"
#   - no colour when stdout is not a TTY: NO_COLOR is set here, and each tool
#     gets its own no-colour flag where it has one
#   - everything a tool prints goes to stderr; details for a NO-GO go to stderr
#   - GO exits 0, NO-GO exits 1
# ci/contract.sh verifies this on every gate: on the record `./go ci` left
# of the gate's one run (record_check below, docs/adr/0062), or on a cold
# run of the gate when there is none.
NO_COLOR=1
export NO_COLOR

go() {
  printf 'GO: %s\n' "$name"
  exit 0
}

# go_with DETAIL: the one GO line with a detail after " — " (docs/adr/0042
# §3: where a toolchain gate ran), one line like a NO-GO's reason.
go_with() {
  printf 'GO: %s — %s\n' "$name" "$(printf '%s' "$1" | tr '\n' ' ')"
  exit 0
}

# nogo REASON [DETAILS...]: one contract line on stdout, details on stderr,
# and also in the file $NOGO_DETAILS names when a caller sets it (`make
# gates` does, for the rework loop's verdict: docs/adr/0038 §5).
nogo() {
  # A reason built from a tool's output can span lines; the contract is one.
  reason=$(printf '%s' "$1" | tr '\n' ' ')
  shift
  printf 'NO-GO: %s — %s\n' "$name" "$reason"
  for line in "$@"; do
    printf '%s\n' "$line" >&2
  done
  if [ -n "${NOGO_DETAILS:-}" ] && [ $# -gt 0 ]; then
    printf '%s\n' "$@" > "$NOGO_DETAILS" || :
  fi
  exit 1
}

# verdict MODULE [ARG...]: a gate whose logic is Python (docs/adr/0084 §3).
# Runs `python3 -m MODULE ARG...` from the repository root, its stderr
# streaming. Exit 0 is GO, with the first stdout line as the detail when
# there is one; exit 1 is NO-GO, the first line the reason and each further
# line a detail; any other exit, or a 1 with nothing on stdout (an uncaught
# exception), is NO-GO naming the module.
verdict() {
  verdict_module=$1
  shift
  verdict_rc=0
  verdict_out=$(python3 -m "$verdict_module" "$@") || verdict_rc=$?
  verdict_first=$(printf '%s\n' "$verdict_out" | head -n 1)
  if [ "$verdict_rc" -eq 0 ]; then
    [ -z "$verdict_first" ] || go_with "$verdict_first"
    go
  fi
  if [ "$verdict_rc" -ne 1 ] || [ -z "$verdict_first" ]; then
    nogo "$verdict_module failed (exit $verdict_rc) without a verdict; see stderr"
  fi
  verdict_rest=$(printf '%s\n' "$verdict_out" | tail -n +2)
  verdict_ifs=$IFS
  IFS='
'
  set -f
  # shellcheck disable=SC2086 # one detail per line, split on newlines only
  set -- $verdict_rest
  set +f
  IFS=$verdict_ifs
  nogo "$verdict_first" "$@"
}

# record_check SCRIPT NAME: run a check as `ci-NAME` does. When CI_RECORDS
# names a directory, which only `./go ci` sets (docs/adr/0062 §1-§2), the
# run leaves its record there for ci/contract.sh to judge instead of a
# second run: NAME.out, stdout byte for byte, then NAME.rc, the exit code
# on one line, written last so a record cut short has none. Stdout still
# reaches the caller once the check has ended, stderr streams untouched,
# and the check's exit code is returned. With CI_RECORDS empty or unset it
# only runs the check.
record_check() {
  if [ -z "${CI_RECORDS:-}" ]; then
    sh "$1"
    return
  fi
  mkdir -p "$CI_RECORDS"
  rm -f "$CI_RECORDS/$2.rc"
  record_rc=0
  record_t0=$(date +%s)
  sh "$1" > "$CI_RECORDS/$2.out" || record_rc=$?
  # Each check's duration, on stderr: a demo over its budget names the
  # check that took the time (docs/adr/0093 §6).
  printf '[ci] %s: %s s\n' "$2" "$(( $(date +%s) - record_t0 ))" >&2
  cat "$CI_RECORDS/$2.out"
  printf '%s\n' "$record_rc" > "$CI_RECORDS/$2.rc"
  return "$record_rc"
}

# require_range: for the M4 PR gates (docs/adr/0015 §2), which judge a git
# range rather than the working tree. NO-GO in one line, no git call first,
# if BASE or HEAD is unset — what a cold ci/contract.sh run sees, and what
# keeps every gates/*.sh instant with no range and no plane. Otherwise sets
# $base to the merge-base of BASE and HEAD (the PR's own changes, as a code
# host would show them) and $head to the id of the commit HEAD names, for
# the caller to diff against: an id, because a gate may read it from
# another worktree (gates/test-proof.sh, on the base), where a name
# relative to HEAD is another commit.
# The range is the workload's repository's: this one for a bundled
# workload, the attached repository's otherwise (docs/adr/0037 §3).
# Not called by sourcing ci/lib.sh: only gates that need a range call it.
require_range() {
  base_ref=${BASE:-}
  head_ref=${HEAD:-}
  if [ -z "$base_ref" ] || [ -z "$head_ref" ]; then
    nogo "BASE and HEAD must both be set"
  fi
  # A tree as the base (the empty tree, `make gates` on a root commit,
  # docs/adr/0042 §5) has no merge-base: it is the base as given.
  if [ "$(git -C "${WORKLOAD:-.}" cat-file -t "$base_ref" 2>/dev/null)" = tree ]; then
    base=$base_ref
  else
    base=$(git -C "${WORKLOAD:-.}" merge-base "$base_ref" "$head_ref" 2>&1) \
      || nogo "git merge-base $base_ref $head_ref failed" "$base"
  fi
  # shellcheck disable=SC2034 # head is consumed by the sourcing gate
  head=$(git -C "${WORKLOAD:-.}" rev-parse -q --verify "$head_ref^{commit}" 2>/dev/null) \
    || nogo "HEAD=$head_ref names no commit"
}

# effective_policy FILE: policy/merge.env with the workload's own
# policy.env over it (docs/adr/0037 §5), written to FILE by core/policy.py,
# the one reader of either. NO-GO naming the key on an unknown key, a
# malformed value or a missing provenance; an override looser than the
# platform default is reported on stderr and applied.
effective_policy() {
  [ -n "${WORKLOAD_CONTRACT:-}" ] || nogo "WORKLOAD_CONTRACT is not set; run this via ./go, not directly"
  policy_reason=$(python3 core/policy.py --platform policy/merge.env \
    --workload-env "$WORKLOAD_CONTRACT/workload.env" \
    --overrides "$WORKLOAD_CONTRACT/policy.env" --out "$1") \
    || nogo "${policy_reason:-core/policy.py failed without a reason; see stderr}"
}

# adapter_hash DIR: one sha256 over DIR's files, their relative paths and
# contents in a fixed order (docs/adr/0057 §9). The delivery conformance
# suite writes it into its record and production-readiness compares it, so
# an adapter changed after its suite passed is not taken as conformant.
adapter_hash() {
  (cd "$1" && find . -type f ! -path '*/__pycache__/*' | LC_ALL=C sort | xargs sha256sum) | sha256sum | cut -d' ' -f1
}

# egress_flaw LIST: the first thing wrong with LIST, a space-separated list of
# `name:port` (an adapter's EGRESS, a contract's crew-egress.txt, docs/adr/0046
# §4-§5), as a short phrase on stdout, or nothing when every entry is fine. A
# name is lowercase DNS with at least one dot and a last label that is not all
# digits (so no IPv4 literal); there is no `*`; a port is 1 to 65535. An empty
# LIST has no flaw: whether one entry at least is required is the caller's. Never
# calls nogo (callers run it inside $(...)).
egress_flaw() {
  for entry in $1; do
    case "$entry" in
      *'*'*) printf "'%s' has a wildcard" "$entry"; return ;;
      *:*:*) printf "'%s' has more than one colon, so it is not name:port (an IPv6 literal?)" "$entry"; return ;;
      *:*) ;;
      *) printf "'%s' has no port" "$entry"; return ;;
    esac
    host=${entry%:*}
    port=${entry#*:}
    case "$port" in
      ''|*[!0-9]*) printf "'%s' has a port that is not a number" "$entry"; return ;;
    esac
    if [ "${#port}" -gt 5 ] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
      printf "'%s' has a port outside 1-65535" "$entry"
      return
    fi
    case "$host" in
      *[A-Z]*) printf "'%s' is not lowercase" "$entry"; return ;;
      ''|*[!a-z0-9.-]*|.*|*.|*..*|-*|*-|*.-*|*-.*) printf "'%s' is not a DNS name" "$entry"; return ;;
      *.*) ;;
      *) printf "'%s' has a name with no dot" "$entry"; return ;;
    esac
    case "${host##*.}" in
      *[!0-9]*) ;;
      *) printf "'%s' is an IP literal (its last label is all digits)" "$entry"; return ;;
    esac
  done
}
