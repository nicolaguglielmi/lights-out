# ADR 0132 — Closed Deterministic Self-Healing Demo Loop (`./go demo-loop`)

- Status: accepted
- Date: 2026-11-25
- Milestone: M38
- Builds on: [ADR 0019](0019-event-bus-and-tower.md), [ADR 0020](0020-adk-roles-and-evals.md), [ADR 0044](0044-incident-in-postmortem-out.md), [ADR 0052](0052-shell-boundary-and-python-core.md), [ADR 0056](0056-main-green-without-a-queue.md), [ADR 0089](0089-frozen-console-snapshot.md), [ADR 0109](0109-continuous-ops-loop.md), [ADR 0115](0115-autonomous-adr-lifecycle-and-policy.md), [ADR 0117](0117-signed-agent-provenance-attestations.md), [ADR 0121](0121-closed-deterministic-self-healing-loop.md)

## Context

`lights-out` already implements every individual stage of governed autonomous software delivery:

1. Progressive canary staging and SLO evaluation (`core/launchpad.sh`, `core/releases.py`, `verify/slo.sh`).
2. Authenticated alert normalization, deterministic triage checking, policy-gated rollback mitigation, and redacted rollback incident capture (`core/webhook.py`, `core/triage.py`, `core/ops_loop.py`).
3. Structured post-mortem generation, tracker ticket filing, and merge-derived `## Perimeter` injection passing `gates/spec.sh` (`core/postmortem.py`, `core/ops_loop.py`, `core/spec.py`).
4. Eight PR verification gates plus `adr-check`, `spec`, `perimeter`, and post-merge `mainline` verification (`gates/*.sh`, `core/mainline.py`).
5. Human `go-for-launch` sign-off to full traffic, signed SLSA/in-toto provenance attestations, and read-only frozen console snapshots (`core/promote.sh`, `core/provenance.py`, `core/logbook.py`, `core/freeze.py`).

However, no single deterministic L0 command exercised the complete closed loop end to end — from a live held canary regression alert through autonomous triage, rollback, post-mortem, derived-perimeter fix ticket, full PR gates, mainline check, recovery canary promotion, provenance attestation, and frozen console snapshot — without external API keys, remote cloud services, or mutating the host repository checkout (`PLAN.md` §14 / `adoption-roadmap-2026-11.md` K4 / M38).

## Decision

Implement `core/demo_loop.py` (pure Python standard library) and `core/demo_loop.sh` (thin POSIX `sh` wrapper within the `ci/shell-boundary.sh` cap) executing an isolated, deterministic, zero-API-key closed self-healing loop inside a throwaway workspace and exporting reproducible artifacts to `DEMO_LOOP_OUT` (default `.crew/demo-loop`).

### 1. Five-Phase Closed Loop Execution

1. **Initial held canary**: Inside an isolated temporary workspace with a real minimal Python HTTP/checkout service, unit test suite, contract directory, and bare + working git repository wired to local file-backed adapters (`sqlite` StateStore, `spool` EventBus, `flags-file` Flags, `file` Tracker, plus local file-backed Delivery, Registry, CodeHost, and live-probed Metrics), merge the initial `v0.2.0-rc1` feature ticket (`0001`), build the project map (`core/map.py`), and run `core/launchpad.sh` to stage `v0.2.0-rc1` through `5%` and `25%` (`held`).
2. **Alert → Triage → Policy Rollback**: Exercise the injected regression route on the `v0.2.0-rc1` service to emit real `5xx` canary logs (alongside credential-shaped tokens to exercise redaction), POST a canary `5xx` alert JSON payload to `core.webhook.WebhookServer` with Bearer authentication and `dispatch=True`, evaluate recorded L0 `ops-triage` replay fixtures via `core/triage.sh` and `core/triage.py`, execute `triage.mitigate(..., AUTO_MITIGATE_ON_TRIAGE=on)` rolling back traffic to `0%` (`rolled-back`), and invoke `ops_loop.on_rollback()` to capture bounded redacted tail logs, structured exemplars, and the `{ticket, pr, merge}` audit chain.
3. **Post-mortem → Action Ticket + Derived Perimeter**: Run `core/postmortem.sh` (`ReplayLlm` + `core/postmortem.py` + `adapters/file/put.sh`) to file fix ticket `0002`, execute `ops_loop.derive_action_perimeters()` with `AUTO_PROMOTE_POSTMORTEM_TICKETS=on` to inject `## Perimeter` from the rolled-back merge commit and map/test edges, and verify the ticket passes `gates/spec.sh` (`GO: spec`).
4. **Autonomous Fix → 8/8 Gates → Merge & Mainline**: Open PR `#2` on `ticket/0002` fixing the injected regression and adding a regression test, validate `core/perimeter.sh`, run real PR gates (`risk`, `secrets`, `test-weakening`, `deps`, `sast`, `test`, `mutation`, `test-proof`, plus `adr-check` and `spec`), verify `core.mainline.rejudge()`, merge to `main` via the local CodeHost adapter, refresh the project map, and run `core.mainline.check()` plus tree gates to confirm `main-green`.
5. **Fix Canary → Human GO → 100% + Provenance & Frozen Console**: Publish `v0.2.1` to the local Registry, verify healthy SLO metrics, stage `v0.2.1` through `5%` and `25%` (`GO`) via `core/launchpad.sh`, record `GO_FOR_LAUNCH=GO` via `core/promote.sh` shifting traffic to `100%`, build and verify the SLSA/in-toto provenance receipt via `core/provenance.py` (with `cosign` bundle signing and verification via `core/provenance.sh` when `cosign` is available), assemble `logbook.json` via `core/logbook.py`, and render the read-only `console-frozen.html` snapshot via `core/freeze.py`.

### 2. Output, Budget, and Failure-Injection Contract

- **Contract line**: Default invocation prints `[demo-loop] ...` progress lines on stderr and exactly one contract line on stdout:
  - Success (exit `0`): `GO: demo-loop — closed loop (alert -> rollback -> postmortem -> ticket -> gates -> canary -> 100%) in <N>s (budget 300s)`
  - Refusal or failure (exit `1`): `NO-GO: demo-loop — <reason>`
- **Flags**:
  - `--json`: emits a structured JSON report covering overall verdict, elapsed seconds, budget, per-phase verdicts, and exported artifact paths.
  - `--inject-break <stage>` (`triage`, `spec`, `gates`, `canary`): injects a genuine defect at the chosen stage so automated tests prove each verification boundary fails closed with `NO-GO: demo-loop — ...` and exit `1`.
  - `--out <dir>`: overrides `DEMO_LOOP_OUT`.
  - `--budget <seconds>`: overrides `DEMO_LOOP_BUDGET_SECONDS` (default `300`).
- **Purity and isolation**: `core/demo_loop.py`, `core/demo_loop.sh`, and `core/tests/test_demo_loop.py` remain 100% Python standard library, vendor-free (`ci/vendor-free.sh`), app-free (`ci/app-free.sh`), model-free (`ci/model-free.sh`), and never mutate the host repository's working tree, git history, or `.crew/` state outside `DEMO_LOOP_OUT`.

## Alternatives

- **Composing an external shell orchestrator inside `Makefile` only**: Rejected — logic over 30 lines in shell violates ADR 0052 (`ci/shell-boundary.sh`), cannot be imported by unit tests, and cannot cleanly manage an isolated temporary sandbox + HTTP webhook server in-process.
- **Stubbing gates or provenance with synthetic pass dicts instead of invoking real scripts**: Rejected — weakens verification fidelity; running the real `core/webhook.py`, `core/triage.py`, `core/ops_loop.py`, `core/postmortem.py`, `gates/*.sh`, `core/mainline.py`, `core/launchpad.sh`, `core/promote.sh`, `core/provenance.py`, and `core/freeze.py` proves the actual contracts meet end to end.
- **Requiring container-in-container (`TOOLCHAIN_ISOLATE=1`) or network PyPI/OSV access**: Rejected — the L0 Podman workbench does not mount an outer container socket by default, and offline/airgap L0 runs must complete deterministically without network fetches.

## Consequences

- Teams and contributors can verify the entire autonomous delivery and self-healing loop locally in a single cold L0 command without API keys, cloud accounts, or mutating repo state.
- Every intermediate seam — webhook auth, ADK replay triage, policy mitigation, redacted incident capture, post-mortem ticket filing, derived perimeter validation, 8/8 PR gates, mainline check, canary promotion, SLSA provenance, and frozen console rendering — is continuously tested as an integrated system.
- Adding a future standalone `./go demo-loop` CLI entrypoint requires only wiring `core/demo_loop.sh` into `Makefile` and `core/cli.py` without changing the loop logic.

## Provenance

| Constant / Threshold | Value | Provenance |
|---|---|---|
| `DEMO_LOOP_BUDGET_SECONDS` | `300` | Decision / derived: cold L0 local loop ceiling matching `PLAN.md` §16's 300 s fast-feedback target (`./go init` + `./go gates` <= 300 s, ADR 0042 / ADR 0057) and well inside `PLAN.md` §2's `900` s full demo ceiling (`TIMEOUT_S=900`, ADR 0013 §7) so K4 runs cleanly in CI and terminal demos (`adoption-roadmap-2026-11.md` K4 / M38). |
| `STAGE_PCTS` (`5`, `25`, `100`) | `5`%, `25`%, `100`% | External to this ADR: canary stage progression and human promotion target defined in ADR 0013 / ADR 0014 / `core/launchpad.sh` and `core/promote.sh`. |
| `PR_GATES` (`8` gates) | `8` | External to this ADR: the eight merge gates defined in `Makefile` (`GATES`) and `core/provenance.py` (`PR_GATES`, ADR 0117). |
| `MAX_LOG_LINES` / `MAX_LOG_LINE_CHARS` / `PAYLOAD_MAX_BYTES` | `200` / `320` / `65536` | External to this ADR: inherited unchanged from ADR 0121 (`core/ops_loop.py`) and ADR 0109 (`core/webhook.py`). |
| `core/demo_loop.sh` shell line cap | `<= 20` code lines | Decision: stricter than `ci/shell-boundary.sh`'s `30`-line ceiling (ADR 0052 §4) by delegating all logic to `core/demo_loop.py`. |
