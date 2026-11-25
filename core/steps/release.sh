#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# step-release: wrapper delegating to core/step_release.py (docs/adr/0019, 0131).
set -eu
exec python3 core/step_release.py "$@"
