#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# outside/file-harness version.sh: report outside AgentHarness version metadata.
set -eu

if [ "${1:-}" = "--json" ]; then
  printf '{"harness":"outside/file-harness","version":"1.0.0"}\n'
  exit 0
fi

printf 'file-harness 1.0.0\n'
