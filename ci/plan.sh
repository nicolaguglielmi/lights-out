#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-plan: the plan job of the workflows (docs/adr/0065, 0075 §1, 0077 §4).
# Proves `.github/plan.sh classify` and checks every workflow job is gated
# on `plan`. Logic lives in ci/checks/plan_check.py (docs/adr/0094).
set -eu
name=plan
. ci/lib.sh

verdict ci.checks.plan_check "$@"
