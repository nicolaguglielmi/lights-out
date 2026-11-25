#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-roles: the number of roles the repo states is the number it has. A
# role is a directory under roles/ with a role.env. Every "<number>
# agents", "<number> roles" or "<number> agent roles" in the living text
# must say that number. Logic lives in ci/checks/roles.py (docs/adr/0094).
set -eu
name=roles
. ci/lib.sh

verdict ci.checks.roles "$@"
