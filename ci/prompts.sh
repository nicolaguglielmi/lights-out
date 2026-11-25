#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-prompts: ticket 0004 / docs/adr/0020 §6, 0090 §3. A `roles/*/*.md`
# prompt states the objective and its acceptance; it names no gate, no
# gate's tool, no Makefile target, and not even `make` or `./go`.
# Logic lives in ci/checks/prompts.py (docs/adr/0094).
set -eu
name=prompts
. ci/lib.sh

verdict ci.checks.prompts "$@"
