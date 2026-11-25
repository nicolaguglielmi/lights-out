# ADR 0138 — Multi-Repo Fleet Portfolio Federation Hook and Contract v1.0 Freeze

- Status: accepted
- Date: 2026-11-25
- Milestone: M96
- Builds on: [ADR 0029](0029-focus-governed-autonomy-app-as-a-port.md), [ADR 0030](0030-workload-port.md), [ADR 0037](0037-attach-a-repository.md), [ADR 0042](0042-gates-anywhere-init-and-the-toolchain-contract.md), [ADR 0052](0052-where-shell-is-allowed.md), [ADR 0053](0053-tests-that-prove-and-tickets-that-wait.md), [ADR 0082](0082-delivery-measures-from-the-logbook.md), [ADR 0094](0094-ci-under-the-shell-boundary-later.md), [ADR 0098](0098-verify-from-the-proxy.md), [ADR 0100](0100-init-drafts-the-plane-half.md), [ADR 0131](0131-retire-core-shell-boundary-debt.md), [ADR 0135](0135-m17-thresholds-and-complete-worker-ledger.md), [ADR 0136](0136-activate-ci-shell-boundary-ratchet.md)

## Context

Two public interface gaps remained between single-repository operation and `v1.0.0` ecosystem adoption:

1. **Multi-repository federation boundary (`docs/PLAN.md` §14):** [ADR 0029](0029-focus-governed-autonomy-app-as-a-port.md) established the boundary rule that multi-engagement portfolio management lives in an external consumer rather than inside `lights-out`, and that any hook required by external consumers enters openly as a public, read-only port. Without a dedicated read-only aggregation hook (`core/portfolio.py` and `core/portfolio.sh`), external fleet consumers had no versioned schema (`lights-out/portfolio/1.0`) summarizing contract health, open tickets, held canaries, human-turn queues, mainline status, spend, and measured DORA metrics across multiple repositories.
2. **Contract `v1.0` freeze ([adoption-roadmap-2026-11.md](../plans/adoption-roadmap-2026-11.md) §5):** Adopters building workflows, GitHub Actions, and attached contracts against `.lights-out/workload.env`, the `TOOLCHAIN_IMAGE` forms, and the `1`-line gate stdout contract (`ci/lib.sh`) needed a formal `v1.0` specification (`core/contract_spec.py`) and an automated CI gate (`ci/contract-v1.sh`, `ci/checks/contract_v1.py`) guaranteeing backward compatibility when `CONTRACT_VERSION` is omitted and catching breaking schema/version drift.

## Decision

### 1. Versioned Workload and Gate Contract `v1.0` (`core/contract_spec.py`)

- **Canonical `v1.0` specification:** `core/contract_spec.py` defines `CONTRACT_VERSION = "1.0"` and `CONTRACT_MAJOR = 1`, freezing:
  - The `12` required `workload.env` keys (`CANARY_VERSION`, `MUTATION_BASELINE`, `WORKLOAD_KILL_SWITCH`, `WORKLOAD_SAMPLE_PATH`, `WORKLOAD_LOCKFILES`, `WORKLOAD_MUTATION_SCOPE`, `WORKLOAD_TEST_ROOT`, `WORKLOAD_TEST_PATHS`, `TOOLCHAIN_IMAGE`, `TEST_CMD`, `SAST_CMD`, `MUTATION_CMD`).
  - The `3` optional `v1.0` keys (`CONTRACT_VERSION`, `WORKLOAD_HEALTH_PROBE`, `WORKLOAD_PORT`).
  - The `3` `TOOLCHAIN_IMAGE` execution forms (`workbench`, `compose:<service>`, and OCI `<image-reference>`).
  - The `1`-line gate stdout output contract (`GO: <name>` / `GO: <name> — <detail>` with exit `0`, or `NO-GO: <name> — <reason>` with exit `1`, free of ANSI escapes).
- **Backward compatibility and major-version enforcement (`validate_workload_contract`):** Existing contracts that omit `CONTRACT_VERSION` default to `"1.0"` (`version_explicit=False`). Explicit `CONTRACT_VERSION` values are parsed as `<major>.<minor>[.<patch>]`; unknown major versions (`CONTRACT_VERSION=2.0`, `0.9`, or malformed/empty values), missing required keys, duplicate or unknown keys, invalid `TOOLCHAIN_IMAGE` forms, `$`/`#` in runner commands, and out-of-range `WORKLOAD_PORT` / `WORKLOAD_HEALTH_PROBE` / `MUTATION_BASELINE` values are rejected.

### 2. Contract `v1.0` Freeze Check (`ci/checks/contract_v1.py`, `ci/contract-v1.sh`)

- `ci/checks/contract_v1.py` runs an exhaustive planted-failure self-test (`self_test()`) against temporary contracts covering omitted vs. explicit `CONTRACT_VERSION=1.0`, missing required keys, extra unknown keys, duplicate keys, `CONTRACT_VERSION=2.0`, `CONTRACT_VERSION=0.9`, malformed versions, invalid `TOOLCHAIN_IMAGE`, missing `compose.yaml` for `compose:<service>`, invalid runner commands, invalid optional keys, and broken `1`-line gate stdout formats BEFORE validating the repository's own `.lights-out/workload.env` and workload fixture contracts.
- `ci/contract-v1.sh` delegates directly through `verdict ci.checks.contract_v1 "$@"` in `<= 20` non-blank, non-comment code lines, emitting `GO: contract-v1 — v1.0 frozen, <n>/<n> contracts valid` on pass and requiring zero `CI_DEBT` entries in `ci/checks/shell_boundary.py`.

### 3. Read-Only Multi-Repo Fleet Portfolio Hook (`core/portfolio.py`, `core/portfolio.sh`)

- **Strictly read-only federation:** `core/portfolio.py` accepts `--repos <path1>,<path2>`, repeatable `--repo <path>`, positional paths, or `PORTFOLIO_REPOS` (defaulting to `.`), and NEVER writes, locks, or mutates any target repository.
- **Contract and telemetry resolution:** For each repository, `core/portfolio.py` locates `workload.env` (checking `.lights-out/workload.env`, `.crew/attached` fallbacks, or root `workload.env`), validates it via `core/contract_spec.py`, reads `.crew/logbook.json` (plus `.crew/dora.json` when logbook omits `dora`, and read-only SQLite/JSON mainline state via `?mode=ro` when present), and marks missing or unreadable logbooks honestly (`status: "unread"` and `error`, never fabricated metrics).
- **Schema `lights-out/portfolio/1.0` (`--json`):**
  - Per-repo summary: `name`, `path`, `status`, `error`, `contract_path`, `contract_version`, `contract_valid`, `contract_errors`, `open_tickets`, `held_canaries`, `your_turn_count`, `mainline_red`, `spend_day_usd`, `spend_total_usd`, and `dora` (measured keys only, never averaged over `not measured`).
  - Fleet totals: `repo_count`, `healthy_repos`, `unread_repos`, `held_canaries_total`, `your_turn_total`, `mainline_red_repos`, `spend_day_usd_total`, `spend_total_usd_total`, `contract_drift_repos`, `dora` (averaged strictly across measured repos only), `your_turn_queue` (deterministically sorted actionable items across repos), and `repos`.
- **Thin wrapper (`core/portfolio.sh`):** Kept at `<= 25` non-blank, non-comment code lines, emitting `GO: portfolio — <n> repos, <held> held, <your_turn> your-turn` (and details to stderr) by default or `lights-out/portfolio/1.0` JSON when `--json` or `JSON=1` is passed.

### 4. Hexagonal Purity

`core/contract_spec.py`, `core/portfolio.py`, `core/portfolio.sh`, and `core/tests/test_portfolio.py` remain strictly stdlib-only, vendor-free (`ci/vendor-free.sh`), app-free (`ci/app-free.sh`), and model-free (`ci/model-free.sh`), resolving default contract paths without introducing hardcoded workload or vendor coupling.

## Alternatives rejected

1. **Require `CONTRACT_VERSION=1.0` on every existing `workload.env` immediately:** Rejected because omitting `CONTRACT_VERSION` preserves seamless backward compatibility with all already-initialized repositories while still enforcing the frozen `v1.0` key and toolchain schema and rejecting `2.0+` breaking majors.
2. **Maintain a background fleet daemon or SQLite cache inside `lights-out`:** Rejected by `docs/PLAN.md` §14 and [ADR 0029](0029-focus-governed-autonomy-app-as-a-port.md): multi-repo coordination belongs in the external consumer, while `lights-out` exposes a stateless, read-only federation hook.
3. **Average unmeasured DORA metrics as zeros across fleet repositories:** Rejected because treating `not measured` (`measured: false`) as `0` fabricates velocity and stability metrics; only measured entries enter per-repo and fleet DORA statistics.

## Consequences

- External fleet orchestrators and CI pipelines gain a versioned, read-only `lights-out/portfolio/1.0` feed (`core/portfolio.py`, `core/portfolio.sh`, unit-tested in `core/tests/test_portfolio.py`) without crossing the `6`-role or single-instance plane boundaries.
- Breaking changes to `workload.env` keys, `TOOLCHAIN_IMAGE` forms, or `1`-line gate output are blocked by `ci/contract-v1.sh` (`ci/checks/contract_v1.py`, unit-tested in `ci/tests/test_contract_v1.py`) while remaining compatible with all `9` existing repository contracts.
- Both new shell entrypoints (`core/portfolio.sh` and `ci/contract-v1.sh`) stay well within the `30` code-line shell boundary without adding `CI_DEBT`.

## Provenance

| Constant / Bound | Value | Provenance |
|---|---|---|
| `CONTRACT_VERSION` / `CONTRACT_MAJOR` | `"1.0"` / `1` | Decision — cuts the initial stable v1.0 contract major promised in [adoption-roadmap-2026-11.md](../plans/adoption-roadmap-2026-11.md) §5 (`1` major version). |
| `REQUIRED_WORKLOAD_KEYS` count | `12` keys | Imported unchanged from [ADR 0030](0030-workload-port.md) §2, [ADR 0042](0042-gates-anywhere-init-and-the-toolchain-contract.md) §1, and [ADR 0053](0053-tests-that-prove-and-tickets-that-wait.md) §1 (`ci/workloads.sh`). |
| `OPTIONAL_WORKLOAD_KEYS` count | `3` keys | Derived from [ADR 0098](0098-verify-from-the-proxy.md) (`WORKLOAD_HEALTH_PROBE`), [ADR 0100](0100-init-drafts-the-plane-half.md) (`WORKLOAD_PORT`), and this record (`CONTRACT_VERSION`). |
| `TOOLCHAIN_FORMS` count | `3` forms | Imported unchanged from [ADR 0042](0042-gates-anywhere-init-and-the-toolchain-contract.md) §2 (`workbench`, `compose:<service>`, image reference). |
| Gate stdout line count | `1` line | Imported unchanged from `ci/lib.sh` and [ADR 0011](0011-slo-queries-and-metric-shape-gate-at-m1.md) (`ci/contract.sh`). |
| `_MAX_PORT` | `65535` | External — TCP port range `1..65535` preserved from [ADR 0100](0100-init-drafts-the-plane-half.md) and `ci/workloads.sh`. |
| `DORA_KEYS` count | `4` measures | Imported unchanged from [ADR 0082](0082-delivery-measures-from-the-logbook.md) (`core/dora.py`: `deployment_frequency`, `lead_time`, `change_fail_rate`, `recovery_time`). |
| `core/portfolio.sh` code-line cap | `25` code lines | Decision — tightened wrapper ceiling matching [ADR 0131](0131-retire-core-shell-boundary-debt.md) and [ADR 0135](0135-m17-thresholds-and-complete-worker-ledger.md) under the `30`-line `LIMIT` of [ADR 0052](0052-where-shell-is-allowed.md). |
| `ci/contract-v1.sh` code-line cap | `20` code lines | Decision — matches the tight `verdict` wrapper ceiling of [ADR 0136](0136-activate-ci-shell-boundary-ratchet.md) under `LIMIT = 30` so zero `CI_DEBT` is added. |
| Repository contracts validated | `9` contracts | Measured in this repository: `.lights-out/workload.env`, `app/workload.env`, and `7` `workloads/*/.lights-out/workload.env` fixtures. |
| Supported major version rejection target | `2.0` / `0.9` | Decision — representative higher (`2.0`) and pre-1.0 (`0.9`) major versions used in `ci/checks/contract_v1.py` planted-failure self-tests. |
