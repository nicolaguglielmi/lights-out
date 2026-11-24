#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-genesis: thin POSIX wrapper delegating greenfield creation to core/genesis.py.
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
if [ -n "${REPO:-}" ]; then set -- --repo "$REPO" "$@"; fi
if [ -n "${BRIEF:-}" ]; then set -- --brief "$BRIEF" "$@"; fi
if [ -n "${BRIEF_FILE:-}" ]; then set -- --brief-file "$BRIEF_FILE" "$@"; fi
if [ -n "${STACK:-}" ]; then set -- --stack "$STACK" "$@"; fi
if [ -n "${SERVICE_NAME:-}" ]; then set -- --name "$SERVICE_NAME" "$@"; fi
if [ -n "${PORT:-}" ]; then set -- --port "$PORT" "$@"; fi
if [ -n "${TICKETS_COUNT:-}" ]; then set -- --tickets-count "$TICKETS_COUNT" "$@"; fi
if [ "${NO_COMMIT:-0}" = "1" ]; then set -- --no-commit "$@"; fi
if [ "${JSON:-0}" = "1" ]; then set -- --json "$@"; fi
if [ -n "${GENESIS_ARGS:-}" ]; then
  set -f
  # shellcheck disable=SC2086 # intentional word-splitting for caller GENESIS_ARGS
  set -- "$@" $GENESIS_ARGS
  set +f
fi
exec python3 "$here/core/genesis.py" "$@"
