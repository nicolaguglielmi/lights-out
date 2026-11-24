#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# The lights-out image's entrypoint (docs/adr/0045 §3), what the
# `lights-out` wrapper runs: /repo is the user's repository, read-only;
# /out is the one directory written on the host. There is no engine socket
# in here, ever: a toolchain other than the workbench is refused up front.
#
#   init [HEAD=]             attach /repo, draft .lights-out/ from HEAD (or that commit), copy it to /out/.lights-out, its summary to /out/init
#   gates [BASE= HEAD=]      attach /repo, judge the range (default the last commit)
#   gates STAGED=1           ... with what /repo has staged as one more commit, judged alone
#   gates ... JUDGED=<shas>  ... mutation skips the lines unchanged from those judged trees (docs/adr/0074)
#   test-proof [BASE= HEAD=]     the gates test and test-proof only: the tests a change adds fail on its base
#   test-integrity [BASE= HEAD=] the gates test-weakening and mutation only: no test weakened, the changed lines' mutants killed
#   triage ALERT=<file>      a path under /repo (the full image only)
#   postmortem INCIDENT=<file>   a path under /repo; tickets go to /out/tickets (the full image only)
#   version
#
# One stdout line in the gate output contract (CLAUDE.md), the make
# target's own; the rest on stderr. make exits 2 when a recipe fails: the
# contract's NO-GO is 1, so every non-zero exit leaves here as 1.
set -eu
cd /opt/lights-out

sub=${1:-help}
[ $# -gt 0 ] && shift
name=$sub
. ci/lib.sh

case "$sub" in
  init|gates|test-proof|test-integrity) ;;
  triage|postmortem)
    # The gates image has no ADK and no recordings' runtime (docs/adr/0057 §3).
    [ "${LIGHTS_OUT_FLAVOUR:-full}" != gates ] \
      || nogo "this is the gates image; $sub runs in ghcr.io/nicolaguglielmi/lights-out:${LIGHTS_OUT_VERSION:-<version>} (LIGHTS_OUT_IMAGE)" ;;
  version)
    printf 'lights-out %s (%s, %s)\n' "${LIGHTS_OUT_VERSION:-?}" "${LIGHTS_OUT_FLAVOUR:-full}" "${LIGHTS_OUT_REVISION:-?}"
    exit 0 ;;
  *)
    sed -n 's/^#   //p' "$0" >&2
    name=lights-out
    nogo "unknown command '$sub'; one of: init gates test-proof test-integrity triage postmortem version" ;;
esac

host_out=${LIGHTS_OUT_HOST_OUT:-/out}
# This image is the workbench. Nothing in here can start it (no socket);
# the contract check's compose render only needs the name set.
WORKBENCH_IMAGE=${WORKBENCH_IMAGE:-ghcr.io/nicolaguglielmi/lights-out:${LIGHTS_OUT_VERSION:-0.0.0}}
export WORKBENCH_IMAGE
staged=""
for a do
  shift
  case "$a" in
    STAGED=*) staged=${a#STAGED=}; continue ;;
    ALERT=/*|INCIDENT=/*) ;;
    ALERT=*) a="ALERT=/repo/${a#ALERT=}" ;;
    INCIDENT=*) a="INCIDENT=/repo/${a#INCIDENT=}" ;;
    BASE=*|HEAD=*|JUDGED=*|PROFILE=*|MODEL_PROVIDER=*|MODEL=*) ;;
    *) nogo "'$a' is not an argument lights-out takes; one of BASE= HEAD= JUDGED= STAGED= ALERT= INCIDENT= PROFILE= MODEL_PROVIDER= MODEL=" ;;
  esac
  set -- "$@" "$a"
done

# At L0 the roles replay lights-out's own recordings only (docs/adr/0045
# §3): on any other alert or incident the NO-GO says what runs it live.
role() {
  out=$(make --no-print-directory "$@") && { printf '%s\n' "$out"; exit 0; }
  if [ "${PROFILE:-l0}" = l0 ]; then
    out="$out; at L0 only lights-out's own recordings replay: on yours, PROFILE=l1 with GEMINI_API_KEY in your environment (ANTHROPIC_API_KEY, OPENAI_API_KEY or ZAI_API_KEY with MODEL_PROVIDER=anthropic, openai or zai)"
  fi
  printf '%s\n' "$out"
  exit 1
}

case "$sub" in
  triage)
    role triage TRIAGE_OUT=/out/triage "$@" ;;
  postmortem)
    mkdir -p /out/tickets
    role postmortem POSTMORTEM_OUT=/out/postmortem TICKETS_DIR=/out/tickets "$@" ;;
esac

# init and gates: the repository's HEAD, cloned where only this container
# sees it (docs/adr/0037 §1: lights-out reads the repository, never writes it).
# On a podman machine /repo can lag the host for a few seconds after a
# commit or a git switch: core/await-head.sh waits for HEAD, bounded.
sh core/await-head.sh /repo 15 \
  || nogo "the current directory is not a git repository with a commit"
attached=/tmp/lights-out/attached
mkdir -p /tmp/lights-out
sh core/attach.sh /repo "$attached" || nogo "could not clone the repository (see above)"

if [ "$sub" = init ]; then
  # HEAD=<commit> drafts from that commit (docs/adr/0064 §3): the Action
  # drafts from the base, the commit whose contract would judge.
  rm -rf /out/init
  for a do
    case "$a" in
      HEAD=*)
        h=${a#HEAD=}
        git -C "$attached" checkout -q --detach "$h^{commit}" 2>/dev/null \
          || nogo "HEAD=$h is not a commit of the repository (a shallow checkout? fetch-depth: 0)" ;;
    esac
  done
  from=$(git -C "$attached" rev-parse --short HEAD)
  out=$(make --no-print-directory init WORKLOAD="$attached" ATTACHED="$attached") && rc=0 || rc=$?
  if [ -d "$attached/.lights-out" ]; then
    rm -rf /out/.lights-out
    cp -R "$attached/.lights-out" /out/.lights-out
  fi
  # init/summary.md, what the Action posts (docs/adr/0064 §3): the line and,
  # on GO, the three small files and the runners by size. No path of this
  # container or of the host: the clone is the repository, the draft .lights-out.
  line=$(printf '%s\n' "$out" | tail -n 1 | sed -e "s|$attached/\.lights-out|.lights-out|g" -e "s|$attached|the repository|g")
  mkdir -p /out/init
  # shellcheck disable=SC2016 # the backticks are Markdown, not a command
  {
    printf '### lights-out init, drafted from %s\n\n`%s`\n' "$from" "$line"
    if [ "$rc" = 0 ]; then
      for f in workload.env policy.env worker.md; do
        [ -f "/out/.lights-out/$f" ] || continue
        # A fence one backtick longer than any run in the file: it cannot close early.
        fence=$(awk '{ while (match($0, /`+/)) { if (RLENGTH > n) n = RLENGTH; $0 = substr($0, RSTART + RLENGTH) } }
          END { n = (n < 3 ? 3 : n + 1); while (n--) printf "`" }' "/out/.lights-out/$f")
        printf '\n`.lights-out/%s`:\n\n%s\n' "$f" "$fence"
        cat "/out/.lights-out/$f"
        printf '%s\n' "$fence"
      done
      if [ -f /out/.lights-out/compose.yaml ]; then
        printf '\nPlane half (`.lights-out/`):\n\n'
        for f in compose.yaml flags/flags.json smoke.sh load.sh; do
          [ -f "/out/.lights-out/$f" ] || continue
          printf -- '- `.lights-out/%s`, %s bytes\n' "$f" "$(wc -c < "/out/.lights-out/$f" | tr -d ' ')"
        done
      fi
      if [ -d /out/.lights-out/runners ]; then
        stack=${line#GO: init — }
        printf '\n`.lights-out/runners/`, the %s stack'"'"'s runners as lights-out ships them:\n\n' "${stack%%,*}"
        (cd /out/.lights-out && find runners -type f | LC_ALL=C sort | while read -r f; do
          printf -- '- `.lights-out/%s`, %s bytes\n' "$f" "$(wc -c < "$f" | tr -d ' ')"
        done)
      fi
    else
      printf '\nno draft is offered.\n'
    fi
  } > /out/init/summary.md 2>/dev/null || :
  printf '%s\n' "$out" | sed -e "s|$attached/.lights-out|$host_out/.lights-out|g" -e "s|$attached|the repository|g"
  [ "$rc" = 0 ] || exit 1
  exit 0
fi

base=""; head=""
for a do
  case "$a" in BASE=*) base=${a#BASE=} ;; HEAD=*) head=${a#HEAD=} ;; esac
done
if [ -n "$head" ]; then
  git -C "$attached" checkout -q --detach "$head" 2>/dev/null \
    || nogo "HEAD=$head is not a commit of the repository (a shallow checkout? fetch-depth: 0)"
fi
# No contract at the head (docs/adr/0064 §2): a change that removes the one
# its base has is refused as such, with no draft; with none at the base
# either, or no base, it is the first run, and the Action drafts on that
# line, matched exactly. STAGED=1 judges on HEAD: its base is HEAD itself.
if ! git -C "$attached" cat-file -e HEAD:.lights-out/workload.env 2>/dev/null; then
  if [ -z "$staged" ] || [ "$staged" = 0 ]; then
    from=$base
    [ -n "$from" ] || from=$(git -C "$attached" rev-parse -q --verify HEAD~1 2>/dev/null) || from=""
    if [ -n "$from" ] && git -C "$attached" cat-file -e "$from:.lights-out/workload.env" 2>/dev/null; then
      nogo "the change removes .lights-out/workload.env, which its base has; a change cannot remove the contract that judges it"
    fi
  fi
  nogo "the repository has no .lights-out/workload.env committed; lights-out init drafts one"
fi

if [ -n "$staged" ] && [ "$staged" != 0 ]; then
  [ -z "$base$head" ] || nogo "STAGED=1 judges the staged changes on HEAD; it takes no BASE or HEAD"
  patch=/tmp/lights-out/staged.patch
  # The wrapper writes the patch with the host's git (a mounted .git/index
  # can be stale right after `git add`); without one, read it here.
  if [ -f /out/staged.patch ]; then
    cp /out/staged.patch "$patch"
  else
    git -C /repo diff --cached --binary > "$patch"
  fi
  [ -s "$patch" ] || nogo "STAGED=1, and nothing is staged"
  git -C "$attached" apply --index "$patch" >&2 || nogo "the staged changes do not apply on HEAD"
  git -C "$attached" -c user.name=lights-out -c user.email=lights-out@localhost.invalid -c commit.gpgsign=false \
    commit -q --no-verify -m "lights-out: the staged changes, judged before they are committed" \
    || nogo "could not commit the staged changes in the clone"
  printf '[lights-out] judging the staged changes as one commit on %s\n' "$(git -C /repo rev-parse --short HEAD)" >&2
fi

# A range is judged by its base (docs/adr/0045 §3, ADR 0033): with a
# contract at the base, its workload.env, policy.env and runners/ replace
# the head's, no-overlay, so a change cannot loosen what judges it. The
# base is the one make gates would pick: BASE, else the parent of HEAD.
if [ -n "$base" ]; then
  git -C "$attached" rev-parse -q --verify "$base^{commit}" >/dev/null 2>&1 \
    || nogo "BASE=$base is not a commit of the repository (a shallow checkout? fetch-depth: 0)"
  judge=$base
else
  judge=$(git -C "$attached" rev-parse -q --verify HEAD~1 2>/dev/null) || judge=""
fi
if [ -n "$judge" ] && git -C "$attached" cat-file -e "$judge:.lights-out/workload.env" 2>/dev/null; then
  present=""
  for j in .lights-out/workload.env .lights-out/policy.env .lights-out/runners; do
    if git -C "$attached" cat-file -e "$judge:$j" 2>/dev/null || [ -e "$attached/$j" ]; then
      present="$present $j"
    fi
  done
  # shellcheck disable=SC2086 # a list of paths, split on purpose
  git -C "$attached" restore --source="$judge" --worktree -- $present \
    || nogo "could not take the contract from the base $judge"
  printf '[lights-out] the contract that judges is the base'"'"'s, %s\n' "$(git -C "$attached" rev-parse --short "$judge")" >&2
else
  printf '[lights-out] no contract at the base: the head'"'"'s own .lights-out/ judges it\n' >&2
fi

toolchain=$(sed -n 's/^TOOLCHAIN_IMAGE=//p' "$attached/.lights-out/workload.env" | tail -1)
[ "$toolchain" = workbench ] \
  || nogo "TOOLCHAIN_IMAGE=$toolchain needs the engine socket, which the lights-out image never gets (docs/adr/0045 §3); judge it from a clone with ./go gates, or set TOOLCHAIN_IMAGE=workbench"

# gates runs the eight; test-proof and test-integrity (docs/adr/0057 §5) the
# same target on a subset, its one line renamed for the subcommand: GO joins
# the gates' own details, NO-GO names the gate that stopped it. test runs
# before test-proof: a test-proof GO (the changed tests fail on the base)
# proves nothing when they fail on the head too.
case "$sub" in
  gates) subset=$(sed -n 's/^GATES := //p' Makefile) ;;
  test-proof) subset="test test-proof" ;;
  test-integrity) subset="test-weakening mutation" ;;
esac
out=$(make --no-print-directory gates WORKLOAD="$attached" ATTACHED="$attached" GATES_OUT=/out/gates GATES="$subset" "$@") && rc=0 || rc=$?
case "$out" in
  "GO: gates"*) [ "$rc" = 0 ] || out="NO-GO: gates — the gates exited $rc after a GO line: $out" ;;
  "NO-GO: gates — "*) ;;
  *) out="NO-GO: gates — the gates gave no verdict: $out" ;;
esac
if [ "$sub" = gates ]; then
  line=$out
else
  case "$out" in
    "NO-GO: gates — "*)
      # "<gate>: NO-GO: <gate> — <reason>" reads "<gate>: <reason>".
      line="NO-GO: $sub — $(printf '%s' "${out#NO-GO: gates — }" | sed 's/^\([a-z-]*\): NO-GO: \1 — /\1: /')" ;;
    *)
      details=$(sed -n 's/^GO: [a-z-]* — //p' /out/gates/lines 2>/dev/null | awk 'NR > 1 { printf "; " } { printf "%s", $0 }')
      line="GO: $sub — ${details:-$subset GO}" ;;
  esac
fi

# The summary the Action posts (docs/adr/0057 §6): the line, one line per
# gate in the order run, the gates not run, and the first 20 lines of a
# NO-GO's details (20: a decision, the survivors a changed-line run leaves).
# shellcheck disable=SC2016 # the backticks are Markdown, not a command
{
  printf '### lights-out %s\n\n`%s`\n\n' "$sub" "$line"
  for g in $subset; do
    l=$(grep -E "^(NO-)?GO: $g( |\$)" /out/gates/lines 2>/dev/null | head -n 1) || l=""
    if [ -n "$l" ]; then printf -- '- `%s`\n' "$l"; else printf -- '- %s: not run, stopped at the first NO-GO\n' "$g"; fi
  done
  for f in /out/gates/*.details; do
    [ -s "$f" ] || continue
    printf '\n%s, details (first 20 lines):\n\n```\n' "$(basename "$f" .details)"
    head -n 20 "$f"
    printf '```\n'
  done
} > /out/gates/summary.md 2>/dev/null || :

printf '%s\n' "$line"
case "$line" in GO:*) exit 0 ;; *) exit 1 ;; esac
