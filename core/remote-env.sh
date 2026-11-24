#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-remote-env: detects CodeHost settings from a repository's origin
# remote using core/remote.py and writes .crew/remote.env (docs/adr/0101).
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
repo=${1:-${WORKLOAD:-.}}
out=${2:-${REMOTE_ENV_OUT:-.crew/remote.env}}
adapters=${REMOTE_ADAPTERS:-$here/adapters}
exec python3 "$here/core/remote.py" detect "$repo" --adapters "$adapters" --out "$out"
