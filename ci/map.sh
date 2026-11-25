#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-map: proves docs/adr/0054 end to end, cold: no engine, no network,
# temp git repositories and the real `make map`, core/map.py and the file
# Tracker. Logic lives in ci/checks/map_check.py (docs/adr/0094).
set -eu
name=map
. ci/lib.sh

verdict ci.checks.map_check "$@"
