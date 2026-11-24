#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-mutation-baseline: `make mutation-baseline` (docs/adr/0015 §3,
# 0042 §6): the workload's MUTATION_CMD once over its whole scope
# (MUTATION_LINES=all), where its TOOLCHAIN_IMAGE says, for
# MUTATION_BASELINE's provenance. Prints the runner's one JSON line on
# stdout and a one-line summary (killed/total, score, seconds, where) on
# stderr; exits 1, nothing measured, when the run fails or the line does
# not carry total, killed and a numeric score. core/init.sh calls this
# and writes the score into the draft. Not a gate: no contract line.
set -eu
name=mutation-baseline
. ci/lib.sh
. core/toolchain.sh

: "${WORKLOAD:?WORKLOAD not set; run this via ./go, not directly}"
[ -n "${MUTATION_CMD:-}" ] || { printf 'mutation-baseline: MUTATION_CMD is empty (docs/adr/0042 §1)\n' >&2; exit 1; }
[ -n "${WORKLOAD_MUTATION_SCOPE:-}" ] || { printf 'mutation-baseline: WORKLOAD_MUTATION_SCOPE is empty\n' >&2; exit 1; }

toolchain_preflight
printf 'mutation-baseline: %s over %s, in %s\n' "$MUTATION_CMD" "$WORKLOAD_MUTATION_SCOPE" "$toolchain_where" >&2

start=$(date +%s)
set +e
result=$(MUTATION_LINES=${MUTATION_LINES:-all} run_in_toolchain "$MUTATION_CMD")
rc=$?
set -e
elapsed=$(( $(date +%s) - start ))

if [ "$rc" -ne 0 ]; then
  printf 'mutation-baseline: in %s: MUTATION_CMD failed (exit %s); nothing measured\n' "$toolchain_where" "$rc" >&2
  exit 1
fi
last=$(printf '%s\n' "$result" | grep -v '^[[:space:]]*$' | tail -1)
if ! total=$(printf '%s' "$last" | jq -er .total 2>/dev/null) \
  || ! killed=$(printf '%s' "$last" | jq -er .killed 2>/dev/null) \
  || ! score=$(printf '%s' "$last" | jq -er '.score | select(type == "number")' 2>/dev/null); then
  printf 'mutation-baseline: in %s: the run did not report total, killed and a numeric score on its last stdout line: %s; nothing measured\n' "$toolchain_where" "$last" >&2
  exit 1
fi
if [ "$total" -le 0 ]; then
  printf 'mutation-baseline: in %s: 0 mutants in %s; nothing measured\n' "$toolchain_where" "$WORKLOAD_MUTATION_SCOPE" >&2
  exit 1
fi
printf '%s\n' "$last"
printf 'mutation-baseline: %s/%s killed, score=%s, %ss wall time, in %s\n' "$killed" "$total" "$score" "$elapsed" "$toolchain_where" >&2
