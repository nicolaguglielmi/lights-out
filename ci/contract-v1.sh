#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-contract-v1: Contract v1.0 freeze validation (docs/adr/0138).
# Kept <= 20 non-blank, non-comment code lines (ci/shell-boundary.sh).
# Logic lives in ci/checks/contract_v1.py and core/contract_spec.py.
set -eu
# shellcheck disable=SC2034 # name is consumed by sourced ci/lib.sh
name=contract-v1
# shellcheck disable=SC1091 # ci/lib.sh is resolved at runtime from repo root
. ci/lib.sh

verdict ci.checks.contract_v1 "$@"
