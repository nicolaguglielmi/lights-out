# `outside/file-harness` (`AgentHarness` Fixture)

A cold, deterministic, scripted/file-backed `AgentHarness` fixture for proving `AGENT_HARNESS=outside/file-harness` admission, trial, verb execution, tamper refusal, and symlink refusal under ADR 0058 and ADR 0130.

> **Honest test boundary:** `file-harness` is a local scripted/file fixture for cold conformance and lifecycle verification without containers or network access. It does not invoke a live LLM CLI; live CLI harnesses remain verified through `./go harness-conformance` and L1 runs (`adapters/gemini-cli/`, `adapters/claude-code/`, `adapters/codex/`).

## Files

- `run.sh` — POSIX `sh` (`set -eu`, `shellcheck -s sh` clean) entrypoint implementing the `AgentHarness` contract:
  - Enforces required inputs (`ROLE`, `TICKET`, `WORKSPACE`, `BASE`, `OUT`, `TICKET_FILE`, `THINKING`, `MAX_TURNS`, `TIMEOUT_S`, `TOKEN_BUDGET`).
  - Supports scripted JSON inputs via `FILE_HARNESS_SCRIPT` or `FILE_HARNESS_DIR/<ticket>/<role>.json` (and `FILE_HARNESS_MODE=write|review`).
  - Records `$OUT/hooks.log` (`ALLOW <tool> -` / `DENY <tool> <label>`), enforces `MAX_TURNS` and `TOKEN_BUDGET` stops (leaving `$OUT/stop` and omitting `$OUT/pr.json`), blocks unsafe/sensitive paths, commits writer changes under `lights-out-crew`, and emits `$OUT/final.md`, `$OUT/pr.json`, `$OUT/patch`, and `$OUT/run.json`.
  - Scans `$OUT/*` to ensure secret environment values never leak into artefacts.
- `version.sh` — POSIX `sh` (`set -eu`, `shellcheck -s sh` clean) version reporter (`file-harness 1.0.0`, or JSON with `--json`).

## Admission Usage

```sh
ADAPTERS_PATH=/abs/path/to/ext \
  AGENT_HARNESS=outside/file-harness \
  sh -c 'python3 core/adapters.py trial AGENT_HARNESS && ADAPTERS_TRIAL=AGENT_HARNESS make help && python3 core/adapters.py register AGENT_HARNESS harness-conformance'
```
