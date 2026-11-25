#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-board: the board tells the truth about the tower and about the plane
# (docs/adr/0035 §2-§4, §6; docs/adr/0053 §4), cold: no plane, no socket.
# Logic lives in ci/checks/board.py (docs/adr/0094).
set -eu
name=board
. ci/lib.sh

verdict ci.checks.board "$@"
