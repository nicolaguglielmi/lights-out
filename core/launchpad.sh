#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# core-launchpad: wrapper delegating to core/launchpad_cmd.py (docs/adr/0013, 0131).
set -eu
exec python3 core/launchpad_cmd.py "$@"
