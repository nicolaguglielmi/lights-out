#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# step-review: wrapper delegating to core/step_review.py (docs/adr/0021, 0131).
set -eu
exec python3 core/step_review.py "$@"
