#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-attach: proves docs/adr/0037 cold (§7): no engine, no network, a few
# local git repositories and make probes. workloads/minimal, an attachable
# repository, is committed as "the user's repository" with decoys at its
# root that would pass for a contract, and attached by the real
# core/attach.sh into a temp ATTACHED, never this checkout's .attached/.
#   (a) only .lights-out/ is read (§1, §2): make takes the clone's contract
#       and nothing at its root; without .lights-out/workload.env make stops
#       naming it; attach refuses a repository without it committed at HEAD.
#   (b) an attached PR is judged by its base's policy.env (§4): the real
#       core/steps/ci.sh on a PR that drops its base's auth path.
#   (c) .lights-out/* never auto-merges (§5), even under the loosest policy
#       a workload may set; a policy.env that lets high merge fails CI.
#   (d) a bad policy.env is a NO-GO of gates/risk.sh naming the key (§5).
#   (e) at L0 the crew refuses an attached repository first thing (§8).
#   (f) an attached PR is judged by its base's runners/ (docs/adr/0033's
#       2026-11-04 amendment): the real core/steps/ci.sh on a PR that
#       rewrites the runner its base's SAST_CMD calls to report no finding.
#   (g) a contract with the gates half only (docs/adr/0042 §7: the plane
#       half is required only to run the plane) still gives docker-compose a
#       project that loads: every path COMPOSE_FILE names exists, and
#       `docker-compose config` accepts it (no engine needed), so the crew's
#       own services (egress, the harness) run. Found by M24 on 2026-11-11:
#       every docker-compose call on an attached library failed.
# Written from the ADR, not from the scripts; ci/tower.sh case (h)'s shape.
set -eu
name=attach
. ci/lib.sh

for f in Makefile core/attach.sh core/crew.sh core/steps/ci.sh core/policy.py core/risk.py \
  core/perimeter.sh gates/risk.sh policy/merge.env workloads/minimal/.lights-out/workload.env; do
  [ -f "$f" ] || nogo "$f not found"
done

# The temp repositories below are committed to and fetched into: a GIT_DIR
# or GIT_WORK_TREE inherited from a hook would aim that at this checkout's
# own repository instead.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY
export GIT_AUTHOR_NAME=ci GIT_AUTHOR_EMAIL=ci@localhost GIT_COMMITTER_NAME=ci GIT_COMMITTER_EMAIL=ci@localhost
# core/ is reached through a symlink below: no bytecode written into it.
export PYTHONDONTWRITEBYTECODE=1

repo=$(pwd)
t=$(mktemp -d)
trap 'rm -rf "$t"' EXIT
log="$t/run.log"
: > "$log"
mismatch=""
fail_case() {
  [ -z "$mismatch" ] || return 0
  mismatch="$1"; printf 'ci-attach: %s: %s\n' "$1" "$2" >&2; cat "$log" >&2
}
commit() { git -c commit.gpgsign=false commit -q "$@"; }

# Every make, CI step and crew run below starts from the environment
# `make ci` exports for the bundled app, written out so that the cold
# ci/contract.sh run sees the same one: the contract's values must win over
# it. WORKLOAD itself is unset (ATTACHED or the caller names it), ATTACHED
# is a temp path, and no MAKEFLAGS of the enclosing make reaches a nested one.
as_ci() {
  env -u WORKLOAD -u MAKEFLAGS -u MFLAGS -u MAKELEVEL -u HARNESS -u METRICS_URL -u METRICS_AUTH_HEADER \
    -u GEMINI_API_KEY -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u ZAI_API_KEY -u MODEL_PROVIDER -u MODEL \
    -u GITHUB_TOKEN -u GITHUB_REPO -u GITLAB_TOKEN -u GITLAB_URL -u GITLAB_PROJECT_PATH -u GIT_CONFIG_KEY_1 -u GIT_CONFIG_VALUE_1 GIT_CONFIG_COUNT=1 \
    WORKLOAD_CONTRACT=app WORKLOAD_ATTACHED= COMPOSE_FILE=compose.yaml:app/compose.yaml \
    FLAGS_FILE=app/flags/flags.json TICKETS_DIR=tickets CANARY_VERSION=0.0.0-environment \
    ATTACHED="$t/never-attached" "$@"
}

src="$t/src"
att="$t/attached"
contract="$att/.lights-out"
sha() { git -C "$src" rev-parse "$1"; }

# (a) The user's repository: its contract's CANARY_VERSION made
# recognisable, and at its root a complete workload.env (CANARY_VERSION
# 0.0.0-decoy), a compose file, a flag file and a smoke.sh. A fallback to
# any of them, to app/ or to the caller's environment shows in the probe.
(
  mkdir -p "$src" && cd "$src" && cp -R "$repo/workloads/minimal/." . &&
  sed 's/^CANARY_VERSION=.*/CANARY_VERSION=0.0.0-contract/' .lights-out/workload.env > contract.env &&
  mv contract.env .lights-out/workload.env &&
  grep -qx 'CANARY_VERSION=0.0.0-contract' .lights-out/workload.env &&
  sed 's/^CANARY_VERSION=.*/CANARY_VERSION=0.0.0-decoy/' .lights-out/workload.env > workload.env &&
  cp .lights-out/compose.yaml compose.yaml &&
  mkdir -p flags && cp .lights-out/flags/flags.json flags/flags.json &&
  printf '#!/bin/sh\necho decoy smoke\n' > smoke.sh &&
  git init -q -b main . && git add -A && commit -m 'minimal, with decoys at its root'
) >"$log" 2>&1 || fail_case a-setup "could not build the user's repository"
if [ -z "$mismatch" ]; then
  sh core/attach.sh "$src" "$att" >"$log" 2>&1 \
    || fail_case a-attach "core/attach.sh refused a repository with .lights-out/workload.env committed at HEAD"
fi

# The variables every contract read goes through, printed by a rule given
# with --eval: make reads it before the Makefile, and expands its recipe
# only when it runs, with the Makefile's final values. No new target.
# shellcheck disable=SC2016 # the $(...) are make's, not the shell's
probe='lo-attach-probe: ; $(foreach v,WORKLOAD WORKLOAD_ATTACHED WORKLOAD_CONTRACT CANARY_VERSION FLAGS_FILE COMPOSE_FILE TICKETS_DIR,$(info $(v)=$($(v))))'
probed() { sed -n "s|^$1=||p" "$t/probe"; }
if [ -z "$mismatch" ]; then
  as_ci make --no-print-directory -s ATTACHED="$att" --eval "$probe" lo-attach-probe >"$t/probe" 2>"$log" \
    || fail_case a-probe "make with ATTACHED=$att exited $?"
  [ "$(probed WORKLOAD)" = "$att" ] || fail_case a-workload "WORKLOAD=$(probed WORKLOAD), want the clone $att"
  [ "$(probed WORKLOAD_ATTACHED)" = 1 ] || fail_case a-attached "WORKLOAD_ATTACHED=$(probed WORKLOAD_ATTACHED), want 1"
  [ "$(probed WORKLOAD_CONTRACT)" = "$contract" ] \
    || fail_case a-contract "WORKLOAD_CONTRACT=$(probed WORKLOAD_CONTRACT), want $contract"
  [ "$(probed CANARY_VERSION)" = 0.0.0-contract ] \
    || fail_case a-workload-env "CANARY_VERSION=$(probed CANARY_VERSION), want .lights-out/workload.env's 0.0.0-contract"
  [ "$(probed FLAGS_FILE)" = "$contract/flags/flags.json" ] \
    || fail_case a-flags "FLAGS_FILE=$(probed FLAGS_FILE), want $contract/flags/flags.json"
  [ "$(probed TICKETS_DIR)" = "$contract/tickets" ] \
    || fail_case a-tickets "TICKETS_DIR=$(probed TICKETS_DIR), want $contract/tickets"
  case ":$(probed COMPOSE_FILE):" in
    *":$att/compose.yaml:"* | *":app/compose.yaml:"*)
      fail_case a-compose "COMPOSE_FILE=$(probed COMPOSE_FILE) names a workload compose file outside $contract" ;;
    *":$contract/compose.yaml:"*) : ;;
    *) fail_case a-compose "COMPOSE_FILE=$(probed COMPOSE_FILE) does not name $contract/compose.yaml" ;;
  esac
  as_ci make --no-print-directory -n ATTACHED="$att" smoke >"$t/smoke" 2>"$log" \
    || fail_case a-smoke "make -n smoke exited $?"
  grep -qF "$contract/smoke.sh" "$t/smoke" \
    || fail_case a-smoke "make smoke would run: $(cat "$t/smoke") (want $contract/smoke.sh)"
fi
# No fallback: with .lights-out/workload.env gone, make stops and names it,
# although a workload.env sits at the clone's root and app/ has one.
if [ -z "$mismatch" ]; then
  rm -f "$contract/workload.env"
  if as_ci make --no-print-directory -s ATTACHED="$att" --eval "$probe" lo-attach-probe >"$t/probe" 2>"$log"; then
    fail_case a-no-fallback "make ran with no $contract/workload.env: $(tr '\n' ' ' <"$t/probe")"
  fi
  grep -qF "$contract/workload.env" "$log" \
    || fail_case a-no-fallback "make's error does not name $contract/workload.env"
fi
# A repository whose .lights-out/workload.env is not committed at HEAD
# (here it is in the working tree only, and a complete workload.env is
# committed at the root) is attached all the same, for ./go init to draft
# a contract in the clone (docs/adr/0042 §6, amending §1): attach says so,
# names .lights-out/workload.env, and the clone holds HEAD, where no
# contract is -- the root's decoy is never one (§2), so make on that clone
# stops naming the contract file, as a-no-fallback proved.
if [ -z "$mismatch" ]; then
  (
    mkdir -p "$t/uncommitted/.lights-out" && cd "$t/uncommitted" &&
    cp "$src/workload.env" workload.env &&
    git init -q -b main . && git add -A && commit -m 'a contract at the root only' &&
    cp "$src/.lights-out/workload.env" .lights-out/workload.env
  ) >"$log" 2>&1 || fail_case a-draftable "could not build the repository without a committed contract"
fi
if [ -z "$mismatch" ]; then
  sh core/attach.sh "$t/uncommitted" "$t/draftable" >"$log" 2>&1 \
    || fail_case a-draftable "core/attach.sh refused a repository with no .lights-out/workload.env committed at HEAD; docs/adr/0042 §6 attaches it for ./go init"
  [ -d "$t/draftable/.git" ] || fail_case a-draftable "no clone at $t/draftable"
  [ ! -e "$t/draftable/.lights-out/workload.env" ] \
    || fail_case a-draftable "the clone holds a .lights-out/workload.env that was never committed"
  if ! grep -qF '.lights-out/workload.env' "$log" || ! grep -qF 'init' "$log"; then
    fail_case a-draftable "attach's line does not name .lights-out/workload.env and ./go init"
  fi
  if as_ci make --no-print-directory -s ATTACHED="$t/draftable" --eval "$probe" lo-attach-probe >"$t/probe" 2>"$log"; then
    fail_case a-draftable "make ran on the contract-less clone: $(tr '\n' ' ' <"$t/probe")"
  fi
  grep -qF "$t/draftable/.lights-out/workload.env" "$log" \
    || fail_case a-draftable "make's error on the contract-less clone does not name its .lights-out/workload.env"
fi

# (b), (c): the real core/steps/ci.sh, run from a platform root that is
# this checkout's judges (symlinks) with GATES narrowed to risk, the one
# gate that routes (the other five need an engine or the network), a stub
# CodeHost serving the user's repository and a stub Tracker. Each case is a
# base branch with its own .lights-out/policy.env and a PR ticket/<id> off it.
p="$t/platform"
if [ -z "$mismatch" ]; then
  (
    mkdir -p "$p/adapters/stub-codehost" "$p/adapters/stub-tracker" "$t/tickets" &&
    for d in ci core gates policy profiles; do ln -s "$repo/$d" "$p/$d" || exit 1; done &&
    sed 's/^GATES := .*/GATES := risk/' Makefile > "$p/Makefile" &&
    grep -qx 'GATES := risk' "$p/Makefile"
  ) >"$log" 2>&1 || fail_case bc-setup "could not build the platform root with GATES := risk"
fi
if [ -z "$mismatch" ]; then
  cat > "$p/adapters/stub-codehost/clone-url.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
printf '%s\n' "$d/src"
STUB
  cat > "$p/adapters/stub-codehost/status.sh" <<'STUB'
#!/bin/sh
printf 'stub-codehost: status %s %s\n' "$1" "$2" >&2
STUB
  cat > "$p/adapters/stub-tracker/get.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cat "$d/tickets/$1.md"
STUB
  ticket() {  # ID GLOB...: a ticket whose perimeter is the globs
    id=$1; shift
    { printf '# %s - ci/attach.sh\n\n## Perimeter\n\n' "$id"; for g in "$@"; do printf '%s\n' "- $g"; done; } > "$t/tickets/$id.md"
  }
  ticket 0001 .lights-out/policy.env minimal/auth.py
  ticket 0002 .lights-out/smoke.sh
  ticket 0003 .lights-out/smoke.sh
  # b: the base names minimal/auth.py the auth path; the PR puts back a
  #    policy.env without it and edits that path.
  # c1: the base lets medium auto-merge, looser than the platform but
  #    allowed; the PR touches only .lights-out/smoke.sh.
  # c2: the base lets high auto-merge, which no policy.env may.
  (
    cd "$src" &&
    git checkout -q -b base-b main &&
    printf '\n# Provenance, decision: ci/attach.sh case (b), the one module that checks credentials.\nRISK_HIGH_AUTH=minimal/auth.py\n' >> .lights-out/policy.env &&
    printf 'def allowed(token):\n    return token == "ok"\n' > minimal/auth.py &&
    git add -A && commit -m 'minimal/auth.py is the auth path' &&
    git checkout -q -b ticket/0001 &&
    git show main:.lights-out/policy.env > .lights-out/policy.env &&
    printf 'def allowed(token):\n    return True\n' > minimal/auth.py &&
    commit -am 'drop the auth path, then change auth' &&
    git checkout -q -b base-c1 main &&
    printf '\n# Provenance, decision: ci/attach.sh case (c), the loosest allowed.\nRISK_AUTO_MERGE_LEVELS=low:medium\n' >> .lights-out/policy.env &&
    commit -am 'medium auto-merges' &&
    git checkout -q -b ticket/0002 &&
    printf '# a comment\n' >> .lights-out/smoke.sh &&
    commit -am 'touch the smoke test only' &&
    git checkout -q -b base-c2 main &&
    printf '\n# Provenance, decision: ci/attach.sh case (c), not allowed.\nRISK_AUTO_MERGE_LEVELS=low:medium:high\n' >> .lights-out/policy.env &&
    commit -am 'high auto-merges' &&
    git checkout -q -b ticket/0003 &&
    printf '# a comment\n' >> .lights-out/smoke.sh &&
    commit -am 'touch the smoke test only' &&
    git checkout -q main
  ) >"$log" 2>&1 || fail_case bc-setup "could not build the base and PR branches"
fi
# Attached again: replaces the clone case (a) took the contract out of.
if [ -z "$mismatch" ]; then
  sh core/attach.sh "$src" "$att" >"$log" 2>&1 || fail_case bc-attach "core/attach.sh could not re-attach $src"
fi
step_ci() {  # TICKET BASE HEAD: the real CI step; its JSON on stdout, its stderr in $log
  (cd "$p" && as_ci env TICKET="$1" PR_NUMBER=1 BASE_SHA="$(sha "$2")" HEAD_SHA="$(sha "$3")" \
    CODE_HOST=stub-codehost TRACKER=stub-tracker WORKLOAD="$att" WORKLOAD_CONTRACT="$contract" \
    WORKLOAD_ATTACHED=1 GATES_OUT="$t/gates-out" sh core/steps/ci.sh) 2>"$log"
}
signals() { sed -n 's/^risk: .* signals=//p' "$log"; }
fields='.state + " " + .ci.verdict + " " + .ci.risk_level + " " + .ci.decision'

# (b) Premise, or the case could pass for the wrong reason: judged by its
# own policy.env, the PR raises platform (for .lights-out/policy.env) and
# no auth signal.
if [ -z "$mismatch" ]; then
  own=$( (cd "$p" &&
    git -C "$src" show ticket/0001:.lights-out/policy.env > "$t/own-policy.env" &&
    git -C "$src" show ticket/0001:.lights-out/workload.env > "$t/own-workload.env" &&
    python3 core/policy.py --platform policy/merge.env --workload-env "$t/own-workload.env" \
      --overrides "$t/own-policy.env" --out "$t/own-effective.env" &&
    git -C "$src" diff --no-renames --numstat base-b ticket/0001 \
      | python3 core/risk.py --policy "$t/own-effective.env" | jq -r '[.signals[].name] | join(",")') 2>"$log") \
    || fail_case b-premise "could not classify the PR by its own policy.env"
  case ",$own," in
    *,auth,*) fail_case b-premise "the PR's own policy.env already raises auth ($own)" ;;
    *,platform,*) : ;;
    *) fail_case b-premise "the PR by its own policy.env raises '$own', want platform at least" ;;
  esac
fi
if [ -z "$mismatch" ]; then
  got=$(step_ci 0001 base-b ticket/0001) || fail_case b "the CI step exited $?"
  [ "$(printf '%s' "$got" | jq -r "$fields")" = "review GO high review" ] \
    || fail_case b "a PR dropping its base's auth path: $got (want review GO high review)"
  case "$(signals)" in *"auth(minimal/auth.py)"*) : ;;
    *) fail_case b-auth "signals: $(signals) (want auth(minimal/auth.py), which only the base's policy.env names)" ;; esac
  case "$(signals)" in *"platform(.lights-out/policy.env)"*) : ;;
    *) fail_case b-platform "signals: $(signals) (want platform(.lights-out/policy.env))" ;; esac
fi

# (c) .lights-out/* never auto-merges.
if [ -z "$mismatch" ]; then
  got=$(step_ci 0002 base-c1 ticket/0002) || fail_case c-looser "the CI step exited $?"
  [ "$(printf '%s' "$got" | jq -r "$fields")" = "review GO high review" ] \
    || fail_case c-looser "a PR touching only .lights-out/smoke.sh, medium auto-merging: $got (want review GO high review)"
  case "$(signals)" in *"platform(.lights-out/smoke.sh)"*) : ;;
    *) fail_case c-looser "signals: $(signals) (want platform(.lights-out/smoke.sh))" ;; esac
  grep 'RISK_AUTO_MERGE_LEVELS' "$log" | grep -q 'looser' \
    || fail_case c-looser "no stderr line reports RISK_AUTO_MERGE_LEVELS=low:medium as looser than the default"
fi
if [ -z "$mismatch" ]; then
  got=$(step_ci 0003 base-c2 ticket/0003) || fail_case c-high "the CI step exited $?"
  [ "$(printf '%s' "$got" | jq -r '.state + " " + .ci.verdict + " " + .ci.failed')" = "ci-failed NO-GO risk" ] \
    || fail_case c-high "a base letting high auto-merge: $got (want ci-failed NO-GO risk)"
  case "$(printf '%s' "$got" | jq -r .ci.line)" in *"NO-GO: risk — "*RISK_AUTO_MERGE_LEVELS*) : ;;
    *) fail_case c-high "the risk gate's NO-GO does not name RISK_AUTO_MERGE_LEVELS: $(printf '%s' "$got" | jq -r .ci.line)" ;; esac
fi

# (f) An attached PR is judged by its base's runners/ (docs/adr/0033, the
# 2026-11-04 amendment, §3). The base's contract has TOOLCHAIN_IMAGE=
# workbench and SAST_CMD=sh .lights-out/runners/sast.sh over a runner that
# reports one finding; the PR's ticket perimeter holds that runner and the
# PR rewrites it to report none.
#
# The sast gate is a STAND-IN here: the platform root of this case replaces
# gates/sast.sh with a script that runs the worktree's SAST_CMD in-process.
# The real gate runs under the step's TOOLCHAIN_ISOLATE=1, which needs the
# engine socket, and `./go ci` mounts none: the real gate would NO-GO on the
# socket and the case would pass for the wrong reason. What is under test is
# the restore of runners/, not the gate. Every other gate file is linked
# from this checkout, and GATES is narrowed to sast alone, so the NO-GO
# the step reports can only be sast's.
pf="$t/platform-f"
if [ -z "$mismatch" ]; then
  (
    mkdir -p "$pf/gates" "$t/tickets" &&
    cp -R "$p/adapters" "$pf/adapters" &&
    for d in ci core policy profiles; do ln -s "$repo/$d" "$pf/$d" || exit 1; done &&
    for g in "$repo"/gates/*; do
      [ "$(basename "$g")" = sast.sh ] || ln -s "$g" "$pf/gates/$(basename "$g")" || exit 1
    done &&
    sed 's/^GATES := .*/GATES := sast/' Makefile > "$pf/Makefile" &&
    grep -qx 'GATES := sast' "$pf/Makefile"
  ) >"$log" 2>&1 || fail_case f-setup "could not build the platform root with GATES := sast"
  cat > "$pf/gates/sast.sh" <<'STUB'
#!/bin/sh
# Stand-in for gates/sast.sh (ci/attach.sh case f): the worktree's SAST_CMD
# run in-process, same output contract and same $GATES_OUT/sast.json.
set -eu
name=sast
. ci/lib.sh
require_range
out_dir=${GATES_OUT:-.gates-out}
mkdir -p "$out_dir"
[ -n "${SAST_CMD:-}" ] || nogo "SAST_CMD is empty"
err=$(mktemp)
trap 'rm -f "$err"' EXIT
rc=0
result=$(cd "$WORKLOAD" && sh -c "$SAST_CMD" 2>"$err") || rc=$?
cat "$err" >&2
result=$(printf '%s\n' "$result" | grep -v '^[[:space:]]*$' | tail -1) || result=""
count=$(printf '%s' "$result" | jq -r '.findings' 2>/dev/null) || count=""
case "$count" in '' | *[!0-9]*) count="" ;; esac
printf '{"findings": %s, "where": "in-process stand-in"}\n' "${count:-null}" > "$out_dir/sast.json"
[ -n "$count" ] || nogo "SAST_CMD did not print valid JSON on stdout (exit $rc): $result" "$(cat "$err")"
if [ "$count" -gt 0 ]; then
  nogo "$count SAST finding(s): $(head -1 "$err")" "$(cat "$err")"
fi
[ "$rc" -eq 0 ] || nogo "SAST_CMD exited $rc with no findings parsed" "$(cat "$err")"
go
STUB
fi
if [ -z "$mismatch" ]; then
  ticket 0004 .lights-out/runners/sast.sh
  (
    cd "$src" &&
    git checkout -q -b base-f main &&
    sed -e 's/^TOOLCHAIN_IMAGE=.*/TOOLCHAIN_IMAGE=workbench/' \
      -e 's|^SAST_CMD=.*|SAST_CMD=sh .lights-out/runners/sast.sh|' .lights-out/workload.env > "$t/f-workload.env" &&
    cp "$t/f-workload.env" .lights-out/workload.env &&
    mkdir -p .lights-out/runners &&
    printf '#!/bin/sh\necho "sast.sh: B999 base-finding in minimal/app.py" >&2\nprintf '"'"'{"findings": 1}\\n'"'"'\n' > .lights-out/runners/sast.sh &&
    git add -A && commit -m 'a contract with a runner that reports one finding' &&
    git checkout -q -b ticket/0004 &&
    printf '#!/bin/sh\nprintf '"'"'{"findings": 0}\\n'"'"'\n' > .lights-out/runners/sast.sh &&
    commit -am 'the runner reports none' &&
    git checkout -q main
  ) >"$log" 2>&1 || fail_case f-setup "could not build the base and PR branches"
fi
# Premise, or the case could pass for the wrong reason: the head's own
# SAST_CMD, run in-process on the head, reports zero findings.
if [ -z "$mismatch" ]; then
  { mkdir "$t/head-f" && git -C "$src" archive -o "$t/head-f.tar" ticket/0004 &&
    tar -x -f "$t/head-f.tar" -C "$t/head-f"; } >"$log" 2>&1 \
    || fail_case f-premise "could not check out the PR's head"
fi
if [ -z "$mismatch" ]; then
  own_cmd=$(sed -n 's/^SAST_CMD=//p' "$t/head-f/.lights-out/workload.env")
  [ "$own_cmd" = 'sh .lights-out/runners/sast.sh' ] \
    || fail_case f-premise "the head's SAST_CMD is '$own_cmd'"
  own=$(cd "$t/head-f" && sh -c "$own_cmd" 2>"$log") || fail_case f-premise "the head's own SAST_CMD exited $?"
  [ "$own" = '{"findings": 0}' ] || fail_case f-premise "the head's own SAST_CMD printed '$own', want zero findings"
fi
if [ -z "$mismatch" ]; then
  got=$(p=$pf step_ci 0004 base-f ticket/0004) || fail_case f "the CI step exited $?"
  [ "$(printf '%s' "$got" | jq -r '.state + " " + .ci.verdict + " " + .ci.failed')" = "ci-failed NO-GO sast" ] \
    || fail_case f "a PR rewriting its base's SAST runner to report none: $got (want ci-failed NO-GO sast)"
  case "$(printf '%s' "$got" | jq -r .ci.line)" in *"B999 base-finding"*) : ;;
    *) fail_case f-finding "the NO-GO line does not name the base's finding: $(printf '%s' "$got" | jq -r .ci.line)" ;; esac
fi

# (d) gates/risk.sh on the clone with its own .lights-out/policy.env
# rewritten (core/tests/test_policy.py has the rules; this is the wiring).
# The control first: the same range and contract with a valid policy.env
# is GO, so each NO-GO after it is the policy's.
risk_gate() {  # stdout in $t/risk.out, stderr in $log
  (cd "$p" && as_ci env WORKLOAD="$att" WORKLOAD_CONTRACT="$contract" WORKLOAD_ATTACHED=1 \
    BASE="$(sha base-c1)" HEAD="$(sha ticket/0002)" GATES_OUT="$t/risk-out" sh gates/risk.sh) >"$t/risk.out" 2>"$log"
}
expect_policy_nogo() {  # NAME KEY COMMENT LINE: policy.env is COMMENT then LINE; gates/risk.sh must NO-GO in one stdout line naming KEY
  printf '%s\n%s\n' "$3" "$4" > "$contract/policy.env"
  risk_gate && rc=0 || rc=$?
  out=$(cat "$t/risk.out")
  [ "$rc" -eq 1 ] || fail_case "d-$1" "gates/risk.sh exited $rc, want 1: $out"
  [ "$(awk 'END{print NR}' "$t/risk.out")" -eq 1 ] || fail_case "d-$1" "stdout is not one line: $out"
  case "$out" in "NO-GO: risk — "*"$2"*) : ;; *) fail_case "d-$1" "gates/risk.sh said: $out (want a NO-GO naming $2)" ;; esac
}
if [ -z "$mismatch" ]; then
  risk_gate || fail_case d-control "gates/risk.sh with a valid policy.env: $(cat "$t/risk.out")"
  [ "$(cat "$t/risk.out")" = "GO: risk" ] || fail_case d-control "gates/risk.sh with a valid policy.env: $(cat "$t/risk.out")"
fi
if [ -z "$mismatch" ]; then
  expect_policy_nogo unknown-key RISK_MEDIUM_LINES \
    '# Provenance, decision: a typo the platform must not read as a default.' 'RISK_MEDIUM_LINES=50'
  expect_policy_nogo not-overridable RISK_HIGH_PLATFORM \
    '# Provenance, decision: not one of the nine overridable keys.' 'RISK_HIGH_PLATFORM=docs/*'
  expect_policy_nogo malformed RISK_SIZE_MEDIUM_LINES \
    '# Provenance, measurement: not a number.' 'RISK_SIZE_MEDIUM_LINES=lots'
  expect_policy_nogo no-provenance MUTATION_MARGIN \
    '# Stricter, and nothing says where the number comes from.' 'MUTATION_MARGIN=0.05'
fi

# (e) At L0 a crew run on an attached repository refuses first thing:
# exit 1, one NO-GO line that says it is attached and points at L1 and the
# key. The code host and tracker named do not exist, so a bootstrap, ticket
# fetch or clone attempted first would fail with another line. It runs
# from a temp root holding only ci/, core/ and roles/: a .crew/ prepared
# before the refusal shows there, never in this checkout.
if [ -z "$mismatch" ]; then
  (
    mkdir -p "$t/crew" &&
    for d in ci core roles; do ln -s "$repo/$d" "$t/crew/$d" || exit 1; done
  ) >"$log" 2>&1 || fail_case e-setup "could not build the crew's temp root"
fi
if [ -z "$mismatch" ]; then
  (cd "$t/crew" && as_ci env AGENT_HARNESS=replay ROLE=dev-worker TICKET=0001 WORKLOAD="$att" \
    WORKLOAD_ATTACHED=1 WORKLOAD_CONTRACT="$contract" CODE_HOST=no-such-codehost TRACKER=no-such-tracker \
    sh core/crew.sh) >"$t/crew.out" 2>"$log" && rc=0 || rc=$?
  out=$(cat "$t/crew.out")
  [ "$rc" -eq 1 ] || fail_case e "core/crew.sh exited $rc, want 1: $out"
  [ "$(awk 'END{print NR}' "$t/crew.out")" -eq 1 ] || fail_case e "stdout is not one line: $out"
  case "$out" in "NO-GO: crew — "*) : ;; *) fail_case e "core/crew.sh said: $out (want NO-GO: crew — ...)" ;; esac
  for word in attached L1 MODEL_PROVIDER; do
    case "$out" in *"$word"*) : ;; *) fail_case e "the refusal does not mention $word: $out" ;; esac
  done
  [ ! -e "$t/crew/.crew" ] || fail_case e "core/crew.sh prepared $t/crew/.crew before refusing"
fi

# (g) The gates half only: workloads/minimal-js, committed as a repository
# of its own and attached; make's COMPOSE_FILE names files that exist.
if [ -z "$mismatch" ]; then
  (
    mkdir -p "$t/gates-only" && cd "$t/gates-only" && cp -R "$repo/workloads/minimal-js/." . &&
    [ ! -e .lights-out/compose.yaml ] &&
    git init -q -b main . && git add -A && commit -m 'a library: the gates half only'
  ) >"$log" 2>&1 || fail_case g-setup "could not build a repository with the gates half only"
fi
if [ -z "$mismatch" ]; then
  sh core/attach.sh "$t/gates-only" "$t/gates-only-att" >"$log" 2>&1 \
    || fail_case g-attach "core/attach.sh refused the gates-only repository"
fi
if [ -z "$mismatch" ]; then
  as_ci make --no-print-directory -s ATTACHED="$t/gates-only-att" --eval "$probe" lo-attach-probe >"$t/probe" 2>"$log" \
    || fail_case g-probe "make on the gates-only clone exited $?"
  cf=$(probed COMPOSE_FILE)
  [ -n "$cf" ] || fail_case g "COMPOSE_FILE is empty, want the plane's compose.yaml"
  old_ifs=$IFS; IFS=:
  for f in $cf; do
    [ -e "$f" ] || fail_case g "COMPOSE_FILE=$cf names $f, which does not exist: the crew's compose services would not load"
  done
  IFS=$old_ifs
fi
if [ -z "$mismatch" ]; then
  as_ci env WORKLOAD="$t/gates-only-att" WORKLOAD_CONTRACT="$t/gates-only-att/.lights-out" COMPOSE_FILE="$cf" \
    docker-compose config -q >"$log" 2>&1 \
    || fail_case g "docker-compose does not load COMPOSE_FILE=$cf: $(tr '\n' ' ' <"$log")"
fi

[ -z "$mismatch" ] || nogo "case $mismatch disagrees with docs/adr/0037, 0033 or 0042"
go
