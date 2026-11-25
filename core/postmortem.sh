#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# core-postmortem: wrapper delegating to core/postmortem_cmd.py (docs/adr/0044, 0131).
set -eu
exec python3 core/postmortem_cmd.py "$@"
