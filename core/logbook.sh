#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-logbook: the logbook, one versioned document a client's console and
# any consumer read instead of the tower, .crew/ or an adapter (docs/adr/
# 0047 §2, §6). core/logbook.py assembles it, by the rules in docs/plans/
# console.md "Decided at build", into LOGBOOK_FILE (default
# .crew/logbook.json), validated by its committed schema in ci/logbook.sh,
# never here. Stdout is one line: `GO: logbook — <file>, <n> tickets`, or
# `NO-GO: logbook — <why>` when the file cannot be written. A source it
# cannot read is not a NO-GO: the document says {ok: false, error} where
# that source's value would be, and the console shows it.
#
# Not scanned by ci/contract.sh (core/, not ci/ gates/ verify/), but reuses
# ci/lib.sh's go/nogo for the same GO:/NO-GO: line shape.
set -eu
name=logbook
. ci/lib.sh

: "${LOGBOOK_FILE:=.crew/logbook.json}"

out=$(python3 core/logbook.py --out "$LOGBOOK_FILE") || nogo "${out:-core/logbook.py failed without a reason; see stderr}"
go_with "$LOGBOOK_FILE, $out tickets"
