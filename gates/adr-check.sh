#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# gates-adr-check: docs/adr/0115 §5. Whether a range that the effective
# policy's RISK_REQUIRE_ADR says needs an architectural decision record
# carries one: off (the default) is GO; high asks a high-risk range for a
# new or updated valid record under docs/adr/ or adr/, or a commit message
# naming ADR-NNNN; structural asks a high-risk range, or one adding a
# manifest or lockfile, for a valid record in the range itself. A record in
# the range that lacks its Context, Decision or Alternatives, or states a
# numeric threshold with no provenance, is a NO-GO with each defect on
# stderr. require_range first (docs/adr/0015 §2); the judging is
# core/gates/adr_check.py over core/adr_gov.py (docs/adr/0084).
set -eu
name=adr-check
. ci/lib.sh
require_range

out_dir=${GATES_OUT:-.gates-out}
mkdir -p "$out_dir"
# The base's policy/merge.env and the workload's policy.env, merged
# (docs/adr/0037 §5); the range is the workload repository's (§3).
effective_policy "$out_dir/effective-policy.env"
verdict core.gates.adr_check "$base" "$head" "$out_dir/effective-policy.env"
