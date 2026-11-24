#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-registry/env.sh
. "$here/env.sh"

init_registry
