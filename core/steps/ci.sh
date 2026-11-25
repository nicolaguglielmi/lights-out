#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# step-ci: wrapper delegating to core/step_ci.py (docs/adr/0019, 0131).
set -eu
exec python3 core/step_ci.py "$@"
