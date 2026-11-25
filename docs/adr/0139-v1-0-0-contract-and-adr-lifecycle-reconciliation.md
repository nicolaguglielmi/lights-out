# ADR 0139 — `v1.0.0` GA Release Contract and Shipped ADR Lifecycle Reconciliation

- Status: accepted
- Date: 2026-11-25
- Note (2026-11-25): `docs/plans/enterprise-farm-roadmap.md`, cited below, was private planning and was taken out of the repository before publication; the milestones it tracked are in `ROADMAP.md`.
- Milestone: M97
- Amends: [ADR 0027](0027-l2-on-gcp-terraform-and-cloud-deploy.md) (clarifies L2/L3 adapter delivery under ADR 0128 vs. unexecuted Terraform provisioning and live GCP project apply); [ADR 0028](0028-workbench-cloud-image-split.md) (marks superseded by [ADR 0057](0057-the-stepped-way-in.md)); [ADR 0031](0031-worker-brief-report-and-durable-tower-documents.md) (marks superseded in part across ADR 0034, ADR 0037, ADR 0047, ADR 0060, and ADR 0083); [ADR 0036](0036-the-dev-worker-step-as-a-network.md) (reconciles L0 hand-written ticket 0012 fixtures alongside ADR 0120 and ADR 0135 network execution); [ADR 0050](0050-antigravity-cli-harness.md) (marks `experimental` due to unredistributed binary, absent L1 recording, and open §7 workspace-hook gap); [ADR 0098](0098-verify-from-the-proxy.md) and [ADR 0101](0101-origin-remote-as-codehost.md) (normalizes `- Status: accepted` header syntax for `core/map.py` and `package/wiki.py`).

## Context

Ahead of cutting `v1.0.0` GA across Sprints 6–7 (`M91`–`M97`), auditing `docs/adr/` against shipped code on `main`, `ROADMAP.md`, `docs/ARCHITECTURE.md`, and `docs/plans/enterprise-farm-roadmap.md` exposed four categories of documentation drift:

1. **Stale `proposed` status headers on shipped ADRs:** Over half of `ADR 0001`–`ADR 0111` (`55` of `108` audited files, counting numbers `0001`–`0111` where `0067`, `0070`, and `0096` were never allocated) still carried `- Status: proposed` despite their ports, gates, stacks, console views, logbook, security scans, and packaging having shipped and passed verification on `main`.
2. **Header syntax invisible to tooling:** `ADR 0098` and `ADR 0101` formatted their status as `- **Status**: Accepted`, which fails to match `STATUS = re.compile(r"^- Status:(.*)$", re.MULTILINE)` in `core/map.py` and `x.startswith("- Status:")` in `package/wiki.py`, leaving their Decisions table entries empty.
3. **Unmarked supersession, partial implementation, or experimental status:**
   - `ADR 0028` (`workbench/cloud/` split) was never built because `ADR 0057` consolidated all image stages into `workbench/Containerfile`.
   - `ADR 0031` shipped partially via `ADR 0037` (`worker.md`/`input.md`), `ADR 0034` (error history), and `ADR 0060` (`firestore-emulator` readiness beside default `sqlite`), while `./go report` (`core/report.sh`) was never built and was superseded by the `ADR 0047` logbook and `ADR 0083` spend ledger.
   - `ADR 0050` (`antigravity-cli`) relies on a proprietary unredistributed binary, has no live L1 replay recording, and retains an open workspace-hook enforcement gap (§7), so `accepted` overstated its maturity.
   - `ADR 0027` (`L2 on GCP`) had its six GCP adapters shipped and mock-verified under `ADR 0128`, whereas live Terraform provisioning and live GCP project `apply` remain unexecuted by the owner.
   - `ADR 0036` (`network` harness) shipped its adapter and gained opt-in autonomous `## Network` splitting (`ADR 0120`) and partial member spend accounting (`ADR 0135`), while its L0 ticket 0012 fixture remains hand-written.
4. **Enterprise Farm roadmap & `v1.0.0` release gap:** `docs/plans/enterprise-farm-roadmap.md` tracked only `M72`–`M89`, omitting `M91`–`M97`, and `docs/releases/v1.0.0.md` did not yet exist.

## Decision

1. **Pre-`0112` ADR Lifecycle Reconciliation (`0001`–`0111`):**
   - Update `60` files across `docs/adr/0001`–`0111` after inspecting each ADR against `main` (`e89d4ed`), `ci/logbook.sh`, `core/map.py`, and `package/wiki.py` (confirmed zero tests assert literal `proposed` on committed repo ADRs):
     - Reconcile `53` previously `proposed` shipped ADRs (`0042`, `0044`, `0045`, `0047`, `0048`, `0049`, `0051`–`0056`, `0059`–`0063`, `0065`, `0066`, `0068`, `0069`, `0071`–`0095`, `0097`, `0099`, `0100`, `0103`, `0105`, `0108`, `0111`) plus `0036` to `- Status: accepted` while preserving existing amendment notes.
     - Normalize `0098` and `0101` from `- **Status**: Accepted` to `- Status: accepted` so `core/map.py` and `package/wiki.py` index every ADR consistently.
     - Mark `0028` as `- Status: superseded by ADR 0057 (...)` and `0031` as `- Status: superseded in part (...)`.
     - Mark `0050` as `- Status: experimental (...)`.
     - Add explicit v1.0.0 reconciliation notes to `0027` (shipped mock-verified GCP adapters under `ADR 0128` vs. unexecuted Terraform provisioning and live GCP project apply) and `0036` (shipped adapter, hand-written L0 ticket 0012 fixtures, `ADR 0120` spec-split, and `ADR 0135` member spend).
   - Post-reconciliation audit across `docs/adr/0001`–`0111` (`108` files): `105` `accepted`, `2` `superseded` (`0028`, `0031`), `1` `experimental` (`0050`), and `0` `proposed`.

2. **Respect Parallel Isolation for `ADR 0112`–`0136`:**
   - Auditing `docs/adr/0112`–`0136` (`25` files, audited without modifying files outside `M97` scope) shows `17` `accepted` and `8` remaining `- Status: proposed` (`0113`, `0114`, `0115`, `0117`, `0118`, `0123`, `0124`, `0127`) even though their features (`M73`–`M87`) shipped in Waves 1–6. Those `25` files remain untouched here to honor `M97` file boundaries; harmonizing those `8` headers is left as a clean coordinator/post-merge housekeeping follow-up.

3. **`v1.0.0` GA Release Contract & Roadmap Closure:**
   - Publish `docs/releases/v1.0.0.md` summarizing Sprint 6 (`ADR 0133`–`ADR 0136`, `M91`–`M94`) and Sprint 7 (`ADR 0137`–`ADR 0139`, `M95`–`M97`) on top of `v0.9.0`.
   - Keep claims strictly truthful: never fabricate unmeasured latencies, live cloud proofs, commercial provider runs (`anthropic` credit-blocked, `openai`/`zai` unrun), or Sprint 7 live evaluation catch rates. Place explicit `measured-at-integration` markers in `docs/releases/v1.0.0.md` where the Sprint 7 integrator inserts post-merge `reviewer-eval` Gemini measurements, `58/58` `make ci` results, and `ADR 0137`/`ADR 0138` blob links once `M95` and `M96` merge.
   - Extend `docs/plans/enterprise-farm-roadmap.md` with `M91`–`M97` so all `25` tracked farm milestones (`M72`–`M89` plus `M91`–`M97`, with `M90` retired as the core shell-debt closure milestone) carry verified status and Mermaid dependency edges.

## Provenance

Every count and status decision in this ADR was measured directly from committed files and Podman checks at `e89d4ed` on `2026-11-25` (`adr_gov`, `ci/logbook.sh`, `ci/wiki.sh`, `ci/map.sh`, `ci/docs-drift.sh`, `ci/authors.sh`, and `gitleaks`):

| Quantity / Scope | Measured Value | Provenance / Source |
| :--- | :--- | :--- |
| Audited `0001`–`0111` ADRs | `108` files (`0067`, `0070`, `0096` absent) | Direct directory inspection of `docs/adr/` at `e89d4ed` |
| Reconciled `0001`–`0111` distribution | `105` `accepted`, `2` `superseded`, `1` `experimental`, `0` `proposed` | Status header scan verified against `ROADMAP.md`, `docs/ARCHITECTURE.md`, and `core/map.py` |
| `ADR 0112`–`0136` audit (read-only) | `25` files: `17` `accepted`, `8` `proposed` | Inspected `docs/adr/0112`–`0136`; left unmodified to preserve `M97` file boundaries |
| Baseline CI checks at `e89d4ed` | `57/57` `CI_CHECKS`, `2,791/2,791` `ci/core-tests.sh` tests | Verified in Sprint 6 integration & independent audit (`make ci`, `ci/core-tests.sh`) |
| Tracked enterprise farm milestones | `25` milestones (`M72`–`M89` plus `M91`–`M97`) | Recorded in `docs/plans/enterprise-farm-roadmap.md` (`M90` retired as core shell-debt milestone) |

## Alternatives

1. **Mass-flip every `proposed` ADR to `accepted` blindly:** Rejected because `ADR 0028` (`workbench/cloud/`) was superseded by `ADR 0057`, `ADR 0031` (`core/report.sh`) was only partially adopted, `ADR 0050` has an open security hook gap (`§7`) and proprietary unredistributed binary, and `ADR 0027` still lacks live GCP execution. Honest GA governance requires reflecting actual repository state.
2. **Modify `ADR 0112`–`0138` or invent Sprint 7 benchmark numbers upfront in `v1.0.0.md`:** Rejected because `0112`–`0138` sit outside `M97` ownership and because Sprint 7 live `reviewer-eval` scores and full `make ci` runs can only be measured accurately after `M95` and `M96` merge. Using `measured-at-integration` prevents speculative numbers from reaching GA docs.

## Consequences

- `core/map.py`, `ci/map.sh`, `ci/wiki.sh`, and `package/wiki.py` now index all `0001`–`0111` ADRs cleanly (`0098` and `0101` no longer appear blank in the generated Wiki Decisions table), with truthful `accepted`, `superseded`, and `experimental` badges.
- Any release verifier auditing `docs/adr/` against `main` gets an explicit, honest map of what shipped vs. what remains unexecuted (`ADR 0027` live GCP `apply`), experimental (`ADR 0050`), superseded (`0028`, `0031`), or outside `M97` write scope (`8` `proposed` drafts in `0112`–`0136`).
- The integration commit must replace the `measured-at-integration` placeholders in `docs/releases/v1.0.0.md` (and link `ADR 0137`/`ADR 0138` plus bump package version references from `0.9.0` to `1.0.0`) after merging `M95` and `M96` and running live `reviewer-eval` + full `make ci`.
