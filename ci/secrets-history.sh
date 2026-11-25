#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-secrets-history: the secrets gate on an attached repository's history
# (docs/adr/0049 §4), on a throwaway repository, gitleaks for real.
# Logic lives in ci/checks/secrets_history.py (docs/adr/0094).
set -eu
name=secrets-history
. ci/lib.sh

verdict ci.checks.secrets_history "$@"
