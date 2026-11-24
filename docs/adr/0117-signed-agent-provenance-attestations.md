# ADR 0117 — Signed agent provenance attestations (SLSA v1.0 / in-toto)

- Status: proposed
- Date: 2026-11-24
- Milestone: M77 (Signed Agent Provenance Attestations)
- Builds on: ADR 0019 (tower document and audit chain), ADR 0025 (cosign key pair and artefact signing), ADR 0047 (agent run metadata and release log), ADR 0081 (evidence pack per release), ADR 0083 (spend ledger), ADR 0115 (decision-record lifecycle)

## Context

`core/audit.py` (ADR 0019 §6) checks the links between a deployed version, a merge commit, a pull request, and a ticket specification on stderr, while `core/evidence.py` (ADR 0081) packs a release's canary stages, human promotion answer, and PR gate summary into `.crew/evidence/<version>.json`. Neither produces a standard supply-chain attestation envelope (`https://in-toto.io/Statement/v1`) that binds the merged commit or container image digest to the full agentic execution lineage:

1. **Agent runs are not attested in standard in-toto format.** A tower ticket document records `product`, `tasks` (every dev-worker attempt), `review`, and `spend`, including role, harness, provider, model, thinking level, turn count, prompt `input_sha256`, token counts, and USD cost, yet external supply-chain verifiers cannot consume a bespoke internal state document.
2. **Architectural decision records lack cryptographic digest binding.** When a ticket or commit range adds, modifies, or cites `docs/adr/NNNN-*.md` (or `adr/NNNN-*.md`, ADR 0115), nothing binds the exact SHA-256 digests of those decision records into a signed attestation that can later detect post-hoc file edits.
3. **Pre-release merges have no standalone signed receipt.** `core/evidence.py` requires a completed `kind: release` entry in `.crew/releases.jsonl`, so a merged ticket waiting on main before release has no signed provenance bundle of its own, and when a release pack is built later it cannot cross-reference an in-toto provenance bundle alongside `image`, `sbom`, and `dast`.

## Decision

### 1. Statement envelope and predicate (`core/provenance.py`)

`core/provenance.py` (Python standard library only, vendor-free, app-free, and model-free) builds and verifies in-toto Statement v1 documents carrying the `https://lights-out.dev/provenance/v1` predicate:

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {
      "name": "registry/service@sha256:...",
      "digest": {"sha256": "<64 lowercase hex chars>"}
    }
  ],
  "predicateType": "https://lights-out.dev/provenance/v1",
  "predicate": {
    "builder": {"id": "https://lights-out.dev/builder/tower/v1"},
    "built_at": "2026-11-24T12:00:00Z",
    "ticket": {"id": "0001", "state": "held", "spec_sha256": "...", "spec_title": "..."},
    "intake": null,
    "adrs": [{"path": "docs/adr/0117-signed-agent-provenance-attestations.md", "sha256": "..."}],
    "agents": [
      {
        "kind": "task",
        "role": "dev-worker",
        "attempt": 1,
        "harness": "replay",
        "provider": "synthetic",
        "model": "worker-v1",
        "thinking": "high",
        "turns": 4,
        "input_sha256": "...",
        "tokens": {"input": 1200, "output": 350},
        "usd": 0.00825
      }
    ],
    "gates": {
      "verdict": "GO",
      "line": "GO: gates — risk low, auto-merge",
      "head_sha": "...",
      "decision": "auto-merge",
      "failed": null,
      "lines": ["GO: risk — low, auto-merge", "..."],
      "durations_s": {"risk": 0, "secrets": 1},
      "checks": [
        {"gate": "risk", "verdict": "GO", "line": "GO: risk — low, auto-merge", "duration_s": 0}
      ]
    },
    "merge": {
      "sha": "...",
      "head_sha": "...",
      "pr": {"number": 7, "url": "http://host/pr/7", "head_sha": "...", "base_sha": "..."},
      "risk_level": "low"
    },
    "human_gate": {
      "gate": "go-for-launch",
      "answer": "GO",
      "by": "nicola",
      "via": "console",
      "at": "2026-11-24T12:30:00Z",
      "brief_sha256": "...",
      "reason": null
    },
    "missing": []
  }
}
```

### 2. Strict subject normalization

`subject` is a non-empty list of `{name, digest}` entries whose digest map is strictly normalized before writing and re-checked during verification:

- **Container image digest (`sha256`)**: when the release record, ticket release block, or `IMAGE` variable carries an immutable `@sha256:<hex>` digest (or `SUBJECT_SHA256` is provided), any `sha256:` prefix is stripped, hexadecimal letters are lowercased, and the value must be exactly 64 lowercase hexadecimal characters (`^[0-9a-f]{64}$`) because SHA-256 digests are 256 bits wide.
- **Git commit object (`gitCommit`)**: when no valid 64-character image `sha256` digest is present, the subject falls back to the merged commit (`merge.sha`), judged PR head (`ci.head_sha` or `pr.head_sha`), or `HEAD` of `WORKLOAD` (default `.`), resolved via `git rev-parse --verify <ref>^{commit}` when needed and normalized to lowercase hexadecimal of 40 characters (Git SHA-1) or 64 characters (Git SHA-256) under `"digest": {"gitCommit": "<hex>"}`, derived from the in-toto `DigestSet` specification.
- If neither a valid `sha256` digest nor a valid `gitCommit` digest can be resolved, `build` refuses (`exit 1`) without writing a statement rather than fabricating a digest.

### 3. Honest gaps and secret hygiene

- **No fabricated values.** Absent sections or unrecorded fields are written as `null` (`None`) and enumerated in deterministic order in `predicate.missing` (for example `"intake.document"`, `"agents[0].usd"`, `"gates.durations_s"`, `"human_gate"`). When no agent consult occurred (`consulted: false` at L0), no phantom agent run is invented. When a model has no entry in `profiles/prices.json` and no `cost_usd` in `spend`, `usd` is `null` and `"agents[<i>].usd"` is recorded in `predicate.missing` (matching ADR 0083).
- **8 PR gates and durations.** `predicate.gates.checks` projects the 8 standard PR gates (`risk`, `secrets`, `test-weakening`, `deps`, `sast`, `test`, `mutation`, `test-proof`, derived from `Makefile` `GATES`) plus any extra recorded gate lines, pairing each gate's verdict line from `ci.lines` with its integer duration from `ci.seconds` when recorded (`null` otherwise).
- **ADR discovery and SHA-256 binding.** When a repository directory (`WORKLOAD` or `.`) is available, `build` collects ADR files under `docs/adr/` or `adr/` changed in the PR/merge diff or `task.files`, as well as `ADR-NNNN` / `ADR NNNN` references in `spec.title`, the ticket file, or commit messages, and records `{"path": "<relpath>", "sha256": "<64-hex>"}` sorted by path. If a cited `ADR-NNNN` resolves to no file, no hash is invented and `"adrs:ADR-NNNN"` is added to `predicate.missing`.
- **Zero secret leakage.** `core/provenance.py` extracts only the closed allowlist of fields above, never reads secret environment variables into the statement, and scrubs string leaves against credential assignments (`*_(KEY|TOKEN|SECRET|PASSWORD)=...`), `Authorization: Bearer|Basic ...` headers, and URL userinfo (`scheme://user:pass@host`) before serialization.

### 4. CLI contract (`core/provenance.py` and `core/provenance.sh`)

- `python3 core/provenance.py build`:
  - Reads optional `VERSION` + `RELEASES_LOG` (default `.crew/releases.jsonl`), optional `TICKET` + `STATE_STORE` (via `core/audit.py`'s `adapter_json`, read-only), and optional `WORKLOAD` (default `.`).
  - Writes `<PROVENANCE_DIR>/<subject-id>.intoto.json` (`PROVENANCE_DIR` default `.crew/provenance`), where `<subject-id>` is `VERSION` when set, else `TICKET` when set, else the resolved commit SHA.
  - Prints `<att_path>\t<missing_count>` on stdout (`exit 0`) and each `missing: <path>` on stderr, matching `core/evidence.py` (ADR 0081 §1). Refusals print a single-line reason on stdout and `exit 1`.
- `python3 core/provenance.py verify --statement <path> [--bundle <bundle>] [--key <pub>] [--repo <dir>]`:
  - Validates JSON syntax, `_type == "https://in-toto.io/Statement/v1"`, `predicateType == "https://lights-out.dev/provenance/v1"`, non-empty `subject` with strictly normalized `sha256` / `gitCommit` digests, required `predicate` keys and types, absence of unredacted secret patterns, optional bundle structure (and `cosign verify-blob` when `--key` or `COSIGN_PUB` is supplied), and—when `--repo <dir>` is given—re-hashes every file listed in `predicate.adrs` inside `<dir>` (refusing path traversal, missing files, or any single-byte modification).
  - Prints `OK: <path> verified` on stdout (`exit 0`) or a one-line refusal reason (`exit 1`).
- `core/provenance.sh` (at most 28 non-blank, non-comment code lines because of ADR 0052 §4's 30-line shell boundary, executable, sourcing `ci/lib.sh`):
  - Default / `build` mode runs `python3 core/provenance.py build`, removes any stale `<att>.bundle`, signs `<att>` with `COSIGN_PASSWORD= cosign sign-blob --key "$COSIGN_KEY" --tlog-upload=false --use-signing-config=false --bundle "$att.bundle" --yes "$att" >&2` (`COSIGN_KEY` default `.crew/cosign.key`, ADR 0025 §2), verifies immediately with `cosign verify-blob --key "$pub" --insecure-ignore-tlog --bundle "$att.bundle" "$att" >&2`, and emits `GO: provenance — <att> signed and verified` (or `NO-GO: provenance — <reason>`).
  - `verify <att>` mode runs `cosign verify-blob` against `$pub` plus `python3 core/provenance.py verify --statement "$att" --bundle "$att.bundle"` and emits `GO: provenance — <att> verified` or `NO-GO: provenance — <reason>`.

### 5. Additive `core/evidence.py` integration

`core/evidence.py` checks `PROVENANCE_DIR` (default `.crew/provenance`) in `artefacts_section()` for `<version>.intoto.json` + `.bundle` (falling back to `<ticket>.intoto.json` + `.bundle` when the release names a ticket). When both the statement and its `.bundle` are present and valid JSON, `artefacts["provenance"]` is added as a standard `found(source, value)` entry carrying `statement`, `sha256`, `bundle`, `bundle_sha256`, `predicate_type`, and `subject`. When absent or unsigned, `artefacts` keeps its exact three keys (`image`, `sbom`, `dast`) with zero change to `missing` counts, preserving 100% compatibility with ADR 0081, `core/tests/test_evidence.py`, and `ci/evidence.sh`.

## Alternatives rejected

- **Emit custom non-standard JSON instead of in-toto `Statement/v1`.** Supply-chain policies, Sigstore verifiers, and admission controllers understand `https://in-toto.io/Statement/v1` (`_type`, `subject`, `predicateType`, `predicate`); a custom envelope would require bespoke parsers at every boundary.
- **Require an OCI registry and `cosign attest` at L0.** Pre-release merges and local repository checks have a commit SHA before any container image is built or pushed; `cosign sign-blob` / `verify-blob` with a `.bundle` works cold offline on both commit-bound and image-bound statements (ADR 0081 §5).
- **Make absent `.crew/provenance/` a `missing` gap in `core/evidence.py`.** Existing releases and ADR 0081's strict gap assertions (`ci/evidence.sh`, `core/tests/test_evidence.py`) predate per-merge provenance; surfacing `"provenance"` additively when signed preserves backward compatibility while binding the attestation digest into the release pack whenever present.
- **Put non-hex or arbitrary strings inside `subject[].digest.sha256`.** The in-toto `DigestSet` specification reserves `sha256` for 64-character hex SHA-256 digests and `gitCommit` for 40- or 64-character hex Git commit IDs; mixing arbitrary labels into `sha256` breaks downstream SLSA consumers.

## Consequences

- Every release or merged ticket can produce a standalone, offline-verifiable `.intoto.json` + `.intoto.json.bundle` tying its image or commit digest to intake, spec, ADRs, agent runs, token/USD spend, 8 gate verdicts/timings, merge metadata, and `go-for-launch` sign-off.
- Editing one byte of either `.intoto.json` or any attested ADR file under `docs/adr/` causes verification to fail deterministically (`NO-GO`).
- `core/evidence.py` automatically links signed provenance bundles into `.crew/evidence/<version>.json` under `artefacts.provenance` without breaking cold releases that have none.
- Wiring `provenance` into `Makefile`, `./go`, `lights-out`, and `core/cli.py` remains isolated to the integration slice.

## Provenance

| Threshold / Constant | Value | Provenance |
|---|---|---|
| `sha256` subject & ADR digest length | `64` lowercase hex chars | External standard: FIPS 180-4 SHA-256 (256 bits = 64 hexadecimal digits) and in-toto `DigestSet` v1 specification. |
| `gitCommit` subject digest length | `40` or `64` lowercase hex chars | External standard: Git SHA-1 (160 bits = 40 hex digits) and Git SHA-256 (256 bits = 64 hex digits) object IDs in in-toto `DigestSet` v1. |
| Standard PR gate list (`PR_GATES`) | `8` gates | Measured from `Makefile` `GATES` (`risk`, `secrets`, `test-weakening`, `deps`, `sast`, `test`, `mutation`, `test-proof`; ADR 0015, ADR 0016, ADR 0042, ADR 0053). |
| `core/provenance.sh` code lines | `<= 28` lines (ceiling `30`) | Derived from `ci/shell-boundary.sh` (`limit=30`, ADR 0052 §4 and ADR 0084 §5), keeping a 2-line safety margin under the 30-line ceiling. |
| Agent USD cost precision | `6` decimal places | Derived from `core/spend.py` (`round(..., 6)` over per-million-token prices, ADR 0083) so attested spend matches the tower ledger exactly. |
| CLI exit codes | `0`, `1`, `2` | Derived from `ci/lib.sh` and `core/evidence.py` (ADR 0081 §1, ADR 0112): `0` GO / verified, `1` NO-GO / refusal, `2` usage error. |

## Tests, from this ADR

- `core/tests/test_provenance.py` — unit and specification tests for `Statement/v1` envelope, strict `sha256`/`gitCommit` subject normalization, ADR discovery and tamper detection, agent runs and spend linkage, 8 gate verdicts and durations, `go-for-launch` human gate, secret scrubbing, missing-count stdout contract, and ADR 0117 validation.
- `core/tests/test_evidence.py` — existing ADR 0081 tests unchanged plus additive `artefacts.provenance` tests for version-matched, ticket-matched, unsigned, and absent provenance bundles.
- `ci/tests/test_provenance_cosign_e2e.py` — end-to-end Podman test generating an ephemeral passwordless `cosign` keypair, building, signing, and verifying via `core/provenance.sh`, and proving that a single-byte tamper in `.intoto.json` and in an attested ADR file each fail verification (`NO-GO`).
