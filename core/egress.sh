#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# core-egress: wrapper delegating to core/egress_cmd.py (docs/adr/0046, 0131).
set -eu
exec python3 core/egress_cmd.py "$@"
