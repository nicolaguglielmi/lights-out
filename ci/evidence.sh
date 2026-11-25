#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-evidence: proves core/evidence.sh, the evidence pack per release
# (docs/adr/0081 §7, 0089 §5), cold: no plane, no registry, no network.
# Logic lives in ci/checks/evidence_check.py (docs/adr/0094).
set -eu
name=evidence
. ci/lib.sh

verdict ci.checks.evidence_check "$@"
