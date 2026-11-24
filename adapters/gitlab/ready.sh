#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-gitlab: polls the API with the token on stdin (never argv), not a
# gate (docs/adr/0012 §6): silent on success, one stderr line and exit 1 on
# timeout. In remote mode (GITLAB_PROJECT_PATH set) verifies project access;
# in local conformance mode verifies /api/v4/version with the seeded token.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/gitlab/env.sh
. "$here/env.sh"
: "${READY_TIMEOUT:=60}"
probe_url="$GITLAB_API/version"
if [ -n "${GITLAB_PROJECT_PATH:-}" ]; then
  probe_url="$GITLAB_PROJECT"
fi
i=0
while :; do
  if api -f "$probe_url" >/dev/null 2>&1; then
    exit 0
  fi
  i=$((i + 1))
  [ "$i" -lt "$READY_TIMEOUT" ] || break
  sleep 1
done
printf 'ready: gitlab not ready after %ss: GET %s\n' "$READY_TIMEOUT" "$probe_url" >&2
exit 1
