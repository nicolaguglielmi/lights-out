# External Coding Orchestrator Gate Recipes

This directory documents how external coding orchestrators — **OpenHands**, **Paperclip**, and **Gas Town** — call `lights-out` as a deterministic, fail-closed PR verification gate without replacing the orchestrator's own task scheduler or UI.

> **Surface vs. judge rule (ADR 0033, [ADR 0058](../../docs/adr/0058-outside-adapters-and-other-cis-as-surfaces.md) §6, [ADR 0130](../../docs/adr/0130-cross-provider-jury-and-outside-harness.md)):** An external orchestrator is a **caller / surface**, never a judge. It decides *when* to trigger a task or merge attempt; `lights-out` computes the verdict (`GO` exit `0` vs. `NO-GO` exit `1`) from the repository's base contract and diff.
>
> **Honest verification note:** These files are integration recipes tested against `lights-out`'s CLI contracts and drift check (`ci/docs-drift.sh`); they do not run live OpenHands, Paperclip, or Gas Town servers in cold CI.

## Two Gate Entrypoints

External orchestrators choose between the full container suite and the zero-container light tier depending on whether Podman or Docker is available in the orchestrator's execution environment:

| Tier | Command | Container engine? | Gates run | Exit contract |
|---|---|---|---|---|
| **Full 8-gate judge** | `lights-out gates BASE=<base> HEAD=<head>` | Yes (Podman or Docker, socket-free by default) | `risk`, `secrets`, `test-weakening`, `deps`, `sast`, `test`, `mutation`, `test-proof` | `0` on `GO: gates — ...`, `1` on `NO-GO: gates — ...` |
| **Zero-container light tier** | `lights-out-gates --light BASE=<base> HEAD=<head>` | No (Git + Python `>= 3.11` only, via `uvx` or `uv tool install lights-out-gates`) | `risk`, optional `perimeter`, `test-weakening`, optional `test` + `test-proof` (`TEST_ON_HOST=1`), optional `spec` (`TICKET=<file>`) | `0` on `GO: gates-light — ...`, `1` on `NO-GO: gates-light — ...` |

From a checkout of `lights-out` itself, `./go gates` (containerized) and `python3 -m core gates --light` (light tier) expose the exact same verdict contract.

## Machine-Readable Artifacts & Optional Jury / Provenance Hooks

- **Verdict summary (`GATES_OUT`):** Pass `GATES_OUT=<dir>` to read `<dir>/verdict.env` (`GATES=GO|NO-GO`, `FAILED=<gate>`, `RISK_LEVEL=low|medium|high`, `RISK_DECISION=auto-merge|review`) and per-gate lines in `<dir>/lines`.
- **Cross-provider jury (`M37`, [core/jury.py](../../core/jury.py)):** On `medium` or `high` risk PRs when `JURY_CROSS_PROVIDER=auto` (default) or `JURY_CROSS_PROVIDER=strict` and at least two provider keys exist in the environment, run `sh core/jury.sh select-env` to resolve an independent reviewer `(provider, model, harness)` distinct from the PR author's provider.
- **Outside `AgentHarness` admission ([ADR 0130](../../docs/adr/0130-cross-provider-jury-and-outside-harness.md)):** External orchestrators plugging a custom local harness into `lights-out` set `ADAPTERS_PATH=<abs-dir>` and `AGENT_HARNESS=outside/<name>` (see [fixtures/outside-adapters/file-harness/README.md](../../fixtures/outside-adapters/file-harness/README.md)) and register a `harness-conformance` proof via [core/adapters.py](../../core/adapters.py).

## Recipes

- [OpenHands recipe](openhands.md) — PR verification step and sandboxed workspace hook for OpenHands agent tasks.
- [Paperclip recipe](paperclip.md) — Approval-stage quality gate and budget-aware escalation hook for Paperclip runs.
- [Gas Town recipe](gastown.md) — Merge-queue verification gate for Gas Town convoys and polecat branches.
