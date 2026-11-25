#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# core-mutation-baseline: wrapper delegating to core/mutation_baseline.py (docs/adr/0015, 0131).
set -eu
exec python3 core/mutation_baseline.py "$@"
