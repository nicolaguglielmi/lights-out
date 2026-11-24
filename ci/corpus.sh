#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-corpus: verify core/init.py, core/init_plane.py, core/stacks.py,
# core/light.py, and ci/workloads.sh over an isolated corpus of synthetic
# repositories (docs/adr/0106). Fast engine-free mode by default; set
# CORPUS_FULL=1 to also execute runnable host gates and OSV scans.
# Logic lives in ci/checks/corpus.py (docs/adr/0094).
set -eu
name=corpus
. ci/lib.sh

verdict ci.checks.corpus
