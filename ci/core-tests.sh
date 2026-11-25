#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-core-tests: unit tests for core/risk.py (docs/adr/0016 §7). stdlib
# unittest, no engine, no pytest. Needs no range, unlike the gates/*.sh PR
# gates: this runs cold, every time, like every other ci/*.sh self-check.
set -eu
name=core-tests
. ci/lib.sh

# core/tests and ci/tests: each module in a process of its own, as many at
# once as the machine has CPUs (docs/adr/0093 §6, amended 2026-11-25: run
# one after the other they took 386 s of the demo's 900 s on a runner).
# Module names are dotted from the repo root, so `from core.risk import
# classify` resolves as under `python3 -m unittest discover -t .`.
cpus=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)
find core/tests ci/tests -maxdepth 1 -name 'test_*.py' | sort | sed 's|\.py$||; s|/|.|g' \
  | xargs -P "$cpus" -n 4 python3 -m unittest >&2 || nogo "unittest reported failures"
# The python stack's runners, run for real (docs/adr/0074, amendment of
# 2026-11-11; docs/adr/0076).
python3 -m unittest discover -s stacks/python/tests -t stacks/python >&2 || nogo "stacks/python/tests reported failures"

go
