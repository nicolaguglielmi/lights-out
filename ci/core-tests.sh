#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-core-tests: unit tests for core/risk.py (docs/adr/0016 §7). stdlib
# unittest, no engine, no pytest. Needs no range, unlike the gates/*.sh PR
# gates: this runs cold, every time, like every other ci/*.sh self-check.
set -eu
name=core-tests
. ci/lib.sh

# core/tests and ci/tests, every test case dealt round-robin into eight
# batches per CPU, as many batches at once as there are CPUs (docs/adr/0093
# §6, amended 2026-11-25): one after the other they took 386 s of the
# demo's budget on a runner, and the six slowest cases sit in one module.
# Ids are dotted from the repo root, as under `unittest discover -t .`; a
# module that fails to import shows up as a failing id.
cpus=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)
python3 - "$cpus" <<'PY' | xargs -P "$cpus" -L 1 python3 -m unittest >&2 || nogo "unittest reported failures"
import sys, unittest
def ids(s):
    for t in s:
        yield from (ids(t) if isinstance(t, unittest.TestSuite) else [t.id()])
every = [i for d in ("core/tests", "ci/tests") for i in ids(unittest.defaultTestLoader.discover(d, top_level_dir="."))]
n = 8 * int(sys.argv[1])
for b in range(n):
    if every[b::n]:
        print(" ".join(every[b::n]))
PY
# The python stack's runners, run for real (docs/adr/0074, amendment of
# 2026-11-11; docs/adr/0076).
python3 -m unittest discover -s stacks/python/tests -t stacks/python >&2 || nogo "stacks/python/tests reported failures"

go
