#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# core-promote: wrapper delegating to core/promote_cmd.py (docs/adr/0020, 0131).
set -eu
exec python3 core/promote_cmd.py "$@"
