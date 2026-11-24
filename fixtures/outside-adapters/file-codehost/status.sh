#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
sha=${1:?usage: status.sh <sha> <state> <context> <description>}
state=${2:?usage: status.sh <sha> <state> <context> <description>}
context=${3:?usage: status.sh <sha> <state> <context> <description>}
description=${4:?usage: status.sh <sha> <state> <context> <description>}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-codehost/env.sh
. "$here/env.sh"

case "$state" in
  pending | success | failure | error) ;;
  *)
    printf 'file-codehost: invalid status state %s\n' "$state" >&2
    exit 1
    ;;
esac

init_store
jq -cn --arg s "$state" --arg c "$context" --arg d "$description" \
  '{state: $s, context: $c, description: $d}' >> "$FILE_CODEHOST_STATUSES/$sha.jsonl"
