# Project Governance

This document defines how the `lights-out` project is governed: how decisions
are recorded, how maintainers and contributors collaborate, and how autonomous
execution is bounded by deterministic gates and human authority.

## Core Principles

1. **Verifiable autonomy, never blind trust**: Six governed agent roles move a
   ticket from specification to canary verification behind numeric gates, strict
   perimeters, and automatic rollback, with a single mandatory human
   `go-for-launch` gate before 100% production traffic.
2. **Clone-to-rollback without external accounts**: Every milestone keeps
   `./go demo` and `./go ci` reproducible inside the containerized toolchain
   with zero cloud accounts or secret keys required at level L0.
3. **No hidden mocks**: Simulated components declare their scope openly and fail
   closed with an explicit message when unconfigured.
4. **Open, vendor-neutral ports**: Infrastructure integrations sit behind port
   contracts in `adapters/` or external `outside/*` directories verified by
   conformance suites (`core/adapters.py`), keeping `core/` free of vendor and
   model lock-in (`ci/vendor-free.sh`, `ci/app-free.sh`, `ci/model-free.sh`).

## Roles and Responsibilities

### Contributors

Anyone who opens an issue, proposes an Architectural Decision Record (ADR), or
submits a pull request is a contributor. All contributions follow
[CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md):

- Every commit carries a Developer Certificate of Origin (`Signed-off-by:`)
  verified by `ci/authors.sh`.
- All contributions are licensed under Apache 2.0.
- Pull requests must keep `./go ci` green and write specification-driven tests
  before or alongside code changes.

### Maintainers

Maintainers steward architectural coherence, security response
([SECURITY.md](SECURITY.md)), release readiness, and adapter quality:

- **Platform review boundary**: Any change touching high-risk platform surfaces
  (`gates/`, `ci/`, `verify/`, `core/`, `policy/`, `roles/`, `adapters/`, or a
  workload's gate contract, as matched by `RISK_HIGH_PLATFORM` in
  `policy/merge.env`) requires human maintainer review and never auto-merges.
- **Contributed adapters**: In-tree adapters under `adapters/` require passing
  their port conformance suite (`./go delivery-conformance`,
  `./go codehost-conformance`, `./go statestore-conformance`,
  `./go eventbus-conformance`, `./go tracker-conformance`,
  `./go registry-conformance`, `./go flags-conformance`, or
  `./go charts-conformance`), scheduled verification, and a named maintainer.
  Unproven or third-party adapters live outside the repository under
  `ADAPTERS_PATH` and are admitted only on exact SHA-256 proof recorded by
  `core/adapters.py`.
- **Becoming a maintainer**: Contributors who demonstrate sustained, high-bar
  stewardship across specifications, ADRs, security hygiene, and reviews may be
  nominated by existing maintainers and confirmed by lazy consensus (no
  substantive objection within seven calendar days).

## Architectural Decision Process (ADRs)

Structural changes, port contracts, gate rules, and numeric policy thresholds
are governed through Architectural Decision Records in `docs/adr/`:

1. **Proposal**: Any structural change (or change under `RISK_REQUIRE_ADR` in
   `policy/merge.env`) includes a new or amended record in `docs/adr/`, checked
   mechanically by `./go adr-check` (`core/adr_gov.py`).
2. **Required sections**: Every ADR states its `## Context`, `## Decision`, and
   `## Alternatives` (rejected options and why), and justifies every numeric
   threshold in `## Provenance` as a measurement, external constraint, or
   explicit engineering decision.
3. **Immutability and amendments**: Accepted ADRs are historical records of a
   decision at its date. When a subsequent milestone evolves a contract, a new
   ADR or a dated amendment records the change rather than rewriting history in
   place.
4. **Documentation alignment**: Living pages (`README.md`,
   [CONTRIBUTING.md](CONTRIBUTING.md), [ROADMAP.md](ROADMAP.md),
   [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), `GOVERNANCE.md`, and `docs/`) are
   checked continuously by `ci/docs-drift.sh` so every cited path, `./go`
   target, and configuration key exists.

## Governed Agent Autonomy and the Human Gate

`lights-out` enforces a strict scope ceiling of **six agent roles** under
`roles/` (verified by `ci/roles.sh`):

- `roles/product` — clarifies intake specifications and open questions
- `roles/architect` — defines ticket perimeters and structural boundaries
- `roles/dev-worker` — implements changes within the declared ticket perimeter
- `roles/test-reviewer` — reviews test adequacy and specification coverage
- `roles/release` — prepares release notes and rollout briefs
- `roles/ops-triage` — triages alerts, incidents, and post-mortems

Adding or removing roles is an intentional governance decision requiring an ADR
and coordinated updates across `roles/` and living documentation.

Autonomous execution stops before full production promotion: canary stages (`5%`
and `25%`) are verified automatically against SLOs with automatic rollback on
regression, and holding at `25%` awaits an explicit human `go-for-launch`
decision via `./go promote` or `./go console` before shifting `100%` of traffic.

## Security and Conflict Resolution

- Private vulnerability disclosures follow [SECURITY.md](SECURITY.md).
- Technical disagreements are resolved through reproducible test cases,
  conformance suites, and ADR trade-off analysis; when maintainers cannot reach
  consensus, the project lead makes the final call and records the rationale in
  `docs/adr/`.
