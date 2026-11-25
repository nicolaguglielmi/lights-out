# ADR 0134 — CodeHost Merge Attestations and Provenance Attachment

- Status: accepted
- Date: 2026-11-25
- Milestone: M36 / M92
- Builds on: ADR 0019, ADR 0025, ADR 0043, ADR 0052, ADR 0058, ADR 0081, ADR 0101, ADR 0117

## Context

`core/provenance.py` and `core/provenance.sh` (ADR 0117) build and sign offline-verifiable in-toto `Statement/v1` provenance documents (`https://lights-out.dev/provenance/v1`) and Sigstore `.bundle` files linking ticket, intake, ADR SHA-256 digests, agent runs/spend, PR gates, merge commit, and `go-for-launch` sign-off. However, those signed provenance artefacts remained strictly local in `.crew/provenance/` and were not attached back to the active `CODE_HOST` (`github`, `gitlab`, or local `gitea`), nor was there a CodeHost port contract for remote attestation storage or honest local reporting (`adoption-roadmap-2026-11.md` K3 / M36).

## Decision

### 1. Optional CodeHost verb `attest.sh <subject-sha> <statement-or-bundle-file>`

Introduce `attest.sh` as an optional CodeHost port verb (following the Registry port's optional `sbom.sh` pattern in ADR 0072 / ADR 0107, so external adapters such as `outside/file-codehost` stay compatible with `ci/codehost.sh`'s required `verbs` surface):

- **`adapters/github/attest.sh`**:
  - Sources `adapters/github/env.sh`, preserving ADR 0043 §1-§2 credential rules: missing or placeholder `GITHUB_TOKEN` or missing/malformed `GITHUB_REPO` refuses in `1` stderr line naming `.env` and exits `1` before any network call.
  - Requires non-empty `<subject-sha>`, readable non-empty JSON `<statement-or-bundle-file>`, and `WORKLOAD_ATTACHED` (refusing unattached repositories in `1` stderr line and exiting `1` before any `curl` call).
  - Submits `{"bundle": ...}` via `github_api POST "/repos/$GITHUB_REPO/attestations"` (`Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`), passing `Authorization: Bearer $GITHUB_TOKEN` strictly on `curl` stdin via `-H @-`, never in argv, stdout, or stderr.
  - Accepts HTTP `200`, `201`, or `202` and emits `1` JSON receipt line on stdout: `{"stored":"remote","id":<id>,"url":<url>,"sha":<sha>,"verify_cmd":"gh attestation verify --repo ..."}`.
- **`adapters/gitlab/attest.sh`**:
  - Sources `adapters/gitlab/env.sh`, passing `PRIVATE-TOKEN` strictly on `curl` stdin (`-H @-`) via `api()`.
  - Computes the SHA-256 digest of `<statement-or-bundle-file>` and extracts non-secret statement/bundle metadata, posting a structured attestation note to `POST $GITLAB_PROJECT/repository/commits/<sha>/comments`.
  - Accepts HTTP `200`, `201`, or `202` and emits `1` JSON receipt line on stdout with `"stored":"remote"`, `id`, `url`, `sha`, `digest`, and `verify_cmd`.
- **`adapters/gitea/attest.sh`**:
  - Local L0 Gitea has no remote attestations API. Rather than mocking remote storage, it validates `<subject-sha>` and readable JSON `<statement-or-bundle-file>`, optionally writes a sidecar `.attestation-receipt.json` beside the statement if the directory is writable, and prints `1` honest JSON receipt line on stdout: `{"stored":"local","id":null,"sha":<sha>,"note":"local codehost has no remote attestations API; verify with cosign"}`.

### 2. Vendor-free attachment in `core/provenance.py` and `core/provenance.sh`

- Add `python3 core/provenance.py attach --statement <path> [--bundle <path>] [--sha <commit>] [--out-receipt <path>] [--required]` and `--attach` / `--attach-required` (plus optional `--bundle`, `--sha`, `--out-receipt`) on `build`.
- Resolve `os.path.join("adapters", code_host, "attest.sh")` dynamically from `CODE_HOST` with path-traversal validation and zero hardcoded vendor names in `core/` (preserving `ci/vendor-free.sh`).
- Resolve the subject `<sha>` from `--sha`, `predicate.merge.sha`, `predicate.merge.head_sha`, `predicate.gates.head_sha`, `subject[].digest.gitCommit`, `subject[].digest.sha256`, or `HEAD`, and invoke `["sh", script, sha, target]` (preferring `--bundle` as `target` when supplied, otherwise `--statement`).
- Parse and secret-scrub the adapter's one-line JSON receipt, record it additively into `--out-receipt` (default `<statement>.attestation-receipt.json`) without mutating the signed `.intoto.json` statement, and print verification guidance (`verify_cmd`, `url`, `note`, local verify command, receipt path) on stderr.
- When `CODE_HOST` is unset or its adapter lacks `attest.sh`, report explicitly on stderr (`provenance: attach skipped: ...`) and exit `0` unless `--attach-required` / `--required` / `PROVENANCE_ATTACH_REQUIRED` is set (which refuses with exit `1`).
- Extend `core/provenance.sh` with `attach` subcommand dispatch and post-sign `PROVENANCE_ATTACH` / `--attach` / `--attach-required` handling while staying at `24` non-blank, non-comment code lines (within the `25`-line safety cap under `ci/shell-boundary.sh`'s `30`-line ceiling, ADR 0052).

### 3. Cold CI proof

- Extend `ci/codehost.sh` `recorder_flaw` (leaving the required `verbs` list unchanged for optional-verb compatibility) so `adapters/github/attest.sh` passes the curl stdin Bearer hygiene check, empty/unset token and missing repo `.env` refusals, unattached refusal, and a planted unattached-refusal self-test.
- Add `ci/tests/test_codehost_attest.py` covering `github`, `gitlab`, and `gitea` `attest.sh` against local HTTP / curl recorders and error cases, and `core/tests/test_provenance_attach.py` covering vendor-free `core/provenance.py` `attach` and `build --attach` via a temporary synthetic adapter.

## Alternatives rejected

- **Make `attest.sh` mandatory in `ci/codehost.sh`'s `verbs` list**: Rejected — breaks existing external CodeHost adapters such as `fixtures/outside-adapters/file-codehost` that implement the core 8-verb + `ready.sh` surface; treating `attest.sh` as an optional capability mirrors `Registry`'s `sbom.sh` (ADR 0072 / ADR 0107) and degrades cleanly.
- **Embed remote attachment receipts inside `.intoto.json` after signing**: Rejected — modifying the statement bytes after `cosign sign-blob` invalidates the `.bundle` signature; recording receipts additively in a sidecar `.attestation-receipt.json` (or `--out-receipt`) preserves offline signature verification.
- **Fake remote attestation storage in `adapters/gitea/attest.sh`**: Rejected — L0 Gitea has no attestations API; returning `"stored":"local"` with `id:null` and a cosign note keeps L0 honest and matches the no-hidden-mock rule.

## Consequences

- Teams using `CODE_HOST=github` on attached repositories can upload signed provenance bundles to GitHub Attestations and verify them with `gh attestation verify --repo <owner>/<repo> ...`.
- Teams using `CODE_HOST=gitlab` get a verifiable SHA-256 attestation digest and metadata comment anchored to the merge commit without exposing `PRIVATE-TOKEN` in argv or logs.
- L0 `CODE_HOST=gitea` and outside CodeHost adapters without `attest.sh` continue operating cleanly with honest local receipts or explicit skipped status unless `--attach-required` is requested.

## Provenance

| Constant / Threshold | Value | Provenance |
|---|---|---|
| GitHub REST API version | `2022-11-28` | External GitHub REST API header pinned by ADR 0043 §1 (`X-GitHub-Api-Version: 2022-11-28`). |
| Accepted HTTP success codes | `200`, `201`, `202` | External REST conventions (`201 Created` on GitHub `/attestations` and GitLab commit `/comments`, plus `200`/`202` idempotent/accepted responses). |
| Refusal / receipt line contract | `1` stderr refusal line / `1` stdout JSON line | Derived from ADR 0012 §6 and ADR 0043 §2-§3 adapter contract (`exit 1` with 1 stderr line naming `.env` on missing credentials). |
| `core/provenance.sh` code line cap | `<= 25` lines (measured `24`, ceiling `30`) | Derived from ADR 0052 §4 (`ci/shell-boundary.sh` `limit=30`) with a 5-line safety margin; measured directly via `awk '!/^[[:space:]]*(#|$)/' core/provenance.sh \| wc -l`. |
| CLI exit codes | `0`, `1`, `2` | Derived from ADR 0117 (`0` success/skipped, `1` refusal, `2` usage error). |
