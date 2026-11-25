#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# core-crew: wrapper delegating to core/crew_cmd.py (docs/adr/0017, 0131).
set -eu
exec python3 core/crew_cmd.py "$@"
