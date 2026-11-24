# File-Backed Flags Adapter (`FLAGS=flags-file`)

Daemonless Flags adapter operating directly on `$FLAGS_FILE` (OFREP/flagd JSON format) without running a `flagd` service.

> Tested locally in CI against real temporary flag JSON files (`ci/tests/test_oci_registry_and_flags.py`) and verified by `CONFORMANCE=1 FLAGS=flags-file sh gates/flags-conformance.sh`.

## Required configuration

- `FLAGS_FILE`: path to the JSON flags configuration file (exported automatically from `$WORKLOAD_CONTRACT/flags/flags.json` by `Makefile`).

## Contract

- `ready.sh`: verifies `$FLAGS_FILE` exists, parses cleanly, and defines `release-freeze` evaluating to a boolean (`true` or `false`); silent on stdout.
- `evaluate.sh <flag> [version]`: evaluates targeting rules (JSONLogic `if`, `==`, `!=`, `in`, `and`, `or`, `!`, `var`) against `{"version": ...}` and falls back to `defaultVariant`; requires `state` (when present) to be `"ENABLED"`; fails closed (exit 1, empty stdout) on missing file, disabled flag, or unknown flag/variant.
- `set.sh <flag> <variant>`: validates flag and variant exist, clears any `targeting` rule, updates `defaultVariant` in `$FLAGS_FILE`, and confirms via `evaluate.sh`.
