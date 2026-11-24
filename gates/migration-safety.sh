#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# gates-migration-safety: a migration must be safe for the previous revision
# (docs/adr/0123). The canary and every rollback run two revisions of the code
# against one schema, so a migration in the PR may only expand: no dropped or
# renamed table or column, no TRUNCATE, no type change that is not provably
# widening, no NOT NULL without a default. A contract cleanup passes only when
# TICKET_FILE or a commit message of the range declares `Contract-After: <ref>`
# and <ref> is a commit or migration that exists before the base.
# No engine and no network. The judgement is core/gates/migration.py
# (docs/adr/0084).
set -eu
name=migration-safety
. ci/lib.sh
require_range

out_dir=${GATES_OUT:-.gates-out}
mkdir -p "$out_dir"
effective_policy "$out_dir/effective-policy.env"
verdict core.gates.migration "$base" "$head" "$out_dir/effective-policy.env"
