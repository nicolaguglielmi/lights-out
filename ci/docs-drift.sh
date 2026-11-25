#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-docs-drift: every path, `./go` or `make` target and `NAME=` key a page
# cites exists (docs/adr/0054 §3), and the landing page matches the wrapper's
# version and cites nothing gone (2026-11-09 amendment). Logic lives in
# ci/checks/docs_drift.py (docs/adr/0094).
set -eu
name=docs-drift
. ci/lib.sh

[ -n "${MAP_EXCLUDE:-}" ] || nogo "MAP_EXCLUDE not set; run this via ./go ci"
verdict ci.checks.docs_drift --exclude "$MAP_EXCLUDE" "$@"
