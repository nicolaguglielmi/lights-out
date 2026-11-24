#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-shell: shellcheck on every POSIX sh script the repo ships: the host
# entrypoint, the workbench fetch helper, the self-check scripts themselves,
# the readiness pollers and the rest of the per-adapter scripts under
# adapters/*/, the release controller under core/, any script under gates/,
# verify/ or e2e/ once those exist, the stacks' gate runners that ./go init
# copies (stacks/*/runners/*.sh, docs/adr/0042 §6), and every workload's
# own scripts (app/, workloads/*/, docs/adr/0030 §5): smoke.sh, load.sh,
# the optional crew-prepare.sh, each in-image runner (tests/mutation/run.sh,
# tests/sast/run.sh) found by suffix, not by a fixed depth — a workload
# with no backend/-style subdirectory places them one level shallower than
# app/'s do — and the runners under its contract's runners/.
set -eu
name=shell
. ci/lib.sh

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

{
  printf '%s\n' go lights-out workbench/fetch.sh
  find package -type f -name '*.sh'
  find ci -maxdepth 1 -type f -name '*.sh'
  find adapters -type f -name '*.sh'
  find core -type f -name '*.sh'
  for d in gates verify e2e; do
    [ -d "$d" ] && find "$d" -type f -name '*.sh'
  done
  [ -d stacks ] && find stacks -path '*/runners/*.sh' -type f
  # lights-out's own contract (docs/adr/0049).
  [ -d .lights-out/runners ] && find .lights-out/runners -name '*.sh' -type f
  # A workload laid out to be attached keeps these in .lights-out/
  # (docs/adr/0037 §2).
  for w in app workloads/*; do
    [ -d "$w" ] || continue
    for c in "$w" "$w/.lights-out"; do
      [ -f "$c/smoke.sh" ] && printf '%s\n' "$c/smoke.sh"
      [ -f "$c/load.sh" ] && printf '%s\n' "$c/load.sh"
      [ -f "$c/crew-prepare.sh" ] && printf '%s\n' "$c/crew-prepare.sh"
    done
    find "$w" \( -path '*/tests/mutation/run.sh' -o -path '*/tests/sast/run.sh' -o -path '*/.lights-out/runners/*.sh' \) -type f
  done
  find plugins/cloud fixtures/outside-adapters -name '*.sh' -type f
  true
} > "$tmp"

xargs shellcheck -s sh --color=never < "$tmp" >&2 || nogo "shellcheck reported problems"

go
