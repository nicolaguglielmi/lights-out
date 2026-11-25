#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-shell-boundary: where shell is allowed in core/, gates/, and ci/
# (docs/adr/0052 §4, 0084 §5, 0094 §2, 0131, 0136). A shell file there has
# at most 30 non-blank, non-comment code lines unless exempt as a host file
# or listed in CI_DEBT (never raised). Logic lives in
# ci/checks/shell_boundary.py.
set -eu
name=shell-boundary
. ci/lib.sh

# Core/gates debt retired by M90 (ADR 0131); kept here for test_shell_debt_zero.py.
debt=''
[ -z "$debt" ] || nogo "core/ and gates/ shell debt must remain empty"
verdict ci.checks.shell_boundary "$@"
