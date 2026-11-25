#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# step-rework: wrapper delegating to core/step_rework.py (docs/adr/0038, 0131).
set -eu
exec python3 core/step_rework.py "$@"
