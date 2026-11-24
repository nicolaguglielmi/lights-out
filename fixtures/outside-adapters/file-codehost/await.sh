#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
sha=${1:?usage: await.sh <sha> <context>}
context=${2:?usage: await.sh <sha> <context>}
: "${AWAIT_TIMEOUT:=10}"
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-codehost/env.sh
. "$here/env.sh"

init_store
i=0
state=""
file="$FILE_CODEHOST_STATUSES/$sha.jsonl"
while [ "$i" -lt "$AWAIT_TIMEOUT" ]; do
  if [ -f "$file" ]; then
    state=$(jq -rs --arg c "$context" '[.[] | select(.context == $c)] | .[-1].state // empty' "$file" 2>/dev/null) || state=""
    case "$state" in
      success | failure | error)
        printf '%s\n' "$state"
        exit 0
        ;;
    esac
  fi
  i=$((i + 1))
  sleep 1
done
printf 'file-codehost: %s on %s still %s after %ss\n' "$context" "$sha" "${state:-pending}" "$AWAIT_TIMEOUT" >&2
exit 1
