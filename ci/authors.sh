#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-authors: every commit reachable from HEAD is attributed to the account
# that made it (docs/adr/0074 §5 amendment 2026-11-12). Logic lives in
# ci/checks/authors.py (docs/adr/0094).
set -eu
name=authors
. ci/lib.sh

verdict ci.checks.authors "$@"
