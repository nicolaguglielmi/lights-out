#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-secrets: no committed or uncommitted secret (docs/adr/0002, 0025,
# 0037 §3, 0042 §4, 0049 §4, 0083 §6). Logic lives in
# ci/checks/secrets_check.py (docs/adr/0094).
set -eu
name=secrets
. ci/lib.sh

verdict ci.checks.secrets_check "$@"
