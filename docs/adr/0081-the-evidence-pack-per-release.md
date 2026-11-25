# ADR 0081 — The evidence pack per release: one signed file that names its sources and its gaps

- Status: accepted
- Date: 2026-11-12
- Milestone: M49 (docs/PLAN.md, "Sprint 2026-11-12", stream B)

## Context

An auditor asks one question about a release: what let this version
reach users, and who said yes? Today the answer is spread over three
places:

- `.crew/releases.jsonl` (ADR 0047 §2) holds the release record (version,
  stable, stages, verdict, `held_at`, image, SBOM, DAST) and, on its own
  line, the human answer (`kind: answer`, with `answer`, `by`, `via`, `at`).
- The ticket's document in the tower (ADR 0019) holds the PR, every gate
  line of the current attempt (`ci.lines`), the failed gate's `details`,
  each rejected attempt (`rework[]`, ADR 0038), the review (ADR 0021) and
  the spec's sha (ADR 0055).
- The image signature and the SBOM attestation are in the registry
  (ADR 0025, ADR 0072 §2), and only their digests are in the record.

`./go audit TICKET=<id>` (ADR 0019 §6) rebuilds the chain deploy → merge →
PR → spec from the document and checks it against the code host. It
prints the chain on stderr. It does not print the gate lines, the
details, who answered, or when. It writes no file and signs nothing
(code read, `core/audit.py:128-162`). A file that leaves the machine has
to prove it was not edited on the way.

## Decision

1. **`./go evidence VERSION=<v>` writes `.crew/evidence/<v>.json`, signs
   it, and verifies the signature in the same command.**
   - `core/evidence.sh` is the entry point, within ADR 0052's 30 code
     lines. `core/evidence.py` builds the pack (stdlib only). cosign
     signs it and verifies it.
   - `VERSION` must match `[A-Za-z0-9._-]+` and must not start with `.`,
     because it names a file. An unset or malformed `VERSION` is a NO-GO
     that lists the versions in the log.
   - A version with no `kind: release` line in the log is a NO-GO. There
     is nothing to pack, and an empty pack would claim a release that
     never happened.
   - The builder's interface: `python3 core/evidence.py build` reads
     `VERSION`, `RELEASES_LOG` (default `.crew/releases.jsonl`),
     `STATE_STORE` and `EVIDENCE_DIR` (default `.crew/evidence`). On
     success it writes the pack, prints `<path>\t<number missing>` on
     stdout, prints each missing path on stderr, and exits 0. On a
     refusal it prints the one-line reason on stdout and exits 1.
     In-process, `evidence.pack(env)` returns the pack as a dict or
     raises `evidence.Refused`.
   - stdout gets one gate-contract line:
     `GO: evidence — <path> signed and verified; <n> missing` or
     `NO-GO: evidence — <reason>`. stderr gets the verify command, so
     anyone with the public key can run it again, and the list of
     missing entries.

2. **Which release.** A version can be released more than once (the demo
   releases the same canary in `demo-rollback` and in `e2e`). The pack is
   for the **last** `kind: release` line of that version. Its answer is
   the first `kind: answer` line for that version after it and before the
   next `kind: release` line, which is the logbook's rule (ADR 0047 §2).
   The earlier releases of the same version are listed by line number
   (`earlier_releases`), not packed. Line numbers count every line of the file,
   blank ones included. A line that is not JSON is a NO-GO that names it:
   it could be the answer, and the pack never skips what it cannot read.

3. **A versioned schema, `lights-out.evidence/1`, made of entries.** Every
   leaf is an entry, and an entry has exactly one of two shapes:
   - `{"source": S, "value": V}`: evidence found, and where it came from;
   - `{"source": S, "missing": W}`: evidence looked for at S and not found,
     with W saying why.

   S names a file and line (`.crew/releases.jsonl:7 .record.image`) or a
   port, document and path (`state store sqlite: tickets/0001 .ci.lines`).
   The top level holds the schema, the version, `built_at`, the release
   line, and `missing`, the paths of every missing entry in document
   order. A reader can therefore list the gaps without walking the tree.
   A missing entry never carries a `value`, and the builder never writes
   a default in place of evidence it did not find.

4. **What the pack holds, section by section.**
   - `release`: `version`, `stable`, `verdict`, `held_at`, `at`, `ticket`.
   - `stages[]`: one object per stage, with entries for `pct`, `verdict`,
     `numbers`, `started_at` and `judged_at`. Stream A (ADR 0080) adds the
     two times. Until then they are missing, with the reason "the record
     of this stage carries no <field>". Any other field a stage carries is
     packed as found.
   - `artefacts`: `image` (the immutable `repo@sha256:…` reference that
     was deployed, ADR 0025 §6), `sbom` (the SPDX attestation's digest),
     `dast` (the staging line, ADR 0072 §3). A null SBOM is missing
     because the registry port gave none. A null DAST is missing because
     DAST was off for this release.
   - `answer`: entries for `answer`, `by`, `at`, `via`, and for the three
     fields stream A adds (ADR 0079): `reason`, `brief_sha256`,
     `brief_author`. When there is no answer line, the whole section is
     one missing entry. If the release was rolled back, the reason says
     no human answer was asked for. If the release is held, the reason
     says the release still waits for one.
   - `prs[]`: the PRs tied to the release. The log line names at most one
     ticket (`RELEASE_TICKET`, ADR 0047 §2). For that ticket, the
     document is read through the state store's `get.sh`, and the pack
     holds: the PR (`number`, `url`, `head_sha`, `base_sha`); `merge.sha`;
     the gates of the last attempt (`ci.verdict`, `ci.line`, `ci.lines`,
     which are every gate's line, `ci.details`, `ci.risk_level`,
     `ci.decision`, `ci.head_sha`); `rework`, every rejected attempt as
     the document keeps it (an empty list is evidence of no rework, not a
     gap); `review` (`role`, `verdict`, `reason`); and `spec`
     (`sha256`, `title`).
     - The review is missing when `ci.decision` was not `review`. The
       reason then names the decision, for example "the gates decided
       auto-merge; no review was asked for".
     - `prs` is one missing entry in three cases:
       - the log line names no ticket (a release run directly with
         `./go release`);
       - the document cannot be read;
       - the document's `release.version` is not `VERSION`. A replay or
         a later release of the same ticket overwrote it, and the pack
         does not pass off another release's PR as this one's.

5. **Signed with the plane's key at L0.** The key is `COSIGN_KEY`, by
   default `.crew/cosign.key`, the pair `./go up` generates (ADR 0025 §2).
   - The sign command is `cosign sign-blob --key <key> --tlog-upload=false
     --use-signing-config=false --bundle <v>.json.bundle --yes <v>.json`.
     These are the flags `adapters/zot/publish.sh` already needs on
     cosign 3.1.3, and there is no transparency log at L0.
   - The verify command is `cosign verify-blob --key <key>.pub
     --insecure-ignore-tlog --bundle <v>.json.bundle <v>.json`, run right
     after the sign. The pack is GO only when that verify passes.
   - I checked this on the workbench's cosign 3.1.3 on 2026-11-12: one
     appended byte makes the verify fail ("invalid signature").
   - The bundle is removed before the sign. This way a stale signature
     never sits beside a new pack.
   - No key is a NO-GO that names `./go up`. The pack is still written,
     so it can be read, but it is not signed, and the line says so.
   - Beyond L0, the same command works with `COSIGN_KEY` pointing at the
     release key. Keyless signing is not built here.

6. **`./go audit` stays as it is.** `core/evidence.py` reuses
   `core/audit.py` read-only: its `adapter_json` reads the document, and
   its `field` reads a dotted path. No line of `core/audit.py` changes.
   The release log, the release record and the tower's documents are
   read, never written, and their formats do not change.

7. **The proof.**
   - `core/tests/test_evidence.py` is written from this ADR before the
     code.
   - `ci/evidence.sh` runs in `./go ci`, cold, with no plane. In a temp
     root it builds a release log and a stub state store that look like
     a demo release: a rolled-back release tied to a ticket that went
     through one rework, and a held release that was answered. It
     generates a throwaway key pair and runs `core/evidence.sh`. It
     expects GO, the right entries and missing paths, and a pack that
     verifies. A pack changed by one byte must fail verify-blob, and an
     unknown version must be a NO-GO.
   - On GitHub, two existing jobs run `./go evidence` on what the demos
     just released. The `engine-compat` job runs it after `./go demo`,
     for e2e's rolled-back release of ticket 0001. The `launch` job runs
     it after `./go demo-launch`, for a release with a human answer. The
     pack and its bundle are uploaded. Neither demo changes.

## Alternatives rejected

- **Extend `./go audit` to write the pack.** The audit answers about a
  ticket, and the pack answers about a release. The audit's stdout is
  already its gate line, and the sprint keeps it unchanged.
- **Put the pack in the logbook (ADR 0047).** The logbook is the
  console's read model, rebuilt on every call and versioned for the
  console. A signed file must not change shape when the console does,
  and stream A owns the logbook this sprint.
- **Leave out absent fields, or write `null` for them.** An auditor
  cannot tell an absent field from "nothing happened". A `null` is
  indistinguishable from a recorded null. The missing entry with its
  reason is the only shape that cannot pass for evidence.
- **Sign with a detached `.sig` (`--output-signature`).** cosign 3
  writes the bundle format by default, and the bundle carries the
  signature and the key hint in one file. Two files to move would be
  one more way to lose half the proof.
- **Embed the signature inside the JSON.** Then the signed bytes are not
  the file's bytes, and verifying needs a tool of ours instead of
  `cosign verify-blob`.
- **Find every PR merged into the released commit.** The state store has
  no listing verb, and a green main can carry merges no ticket names.
  The pack holds the ticket the log ties to the release. It does not
  guess the others.

## Consequences

- A release has a file an auditor can verify with cosign alone, and its
  gaps are written in it.
- When stream A lands, the stage times and the three answer fields move
  from missing to present without a change here. A new field that A
  adds and this ADR does not name is packed as found under `stages`, but
  is lost under `answer` until this list grows.
- The pack is rebuilt on each run and overwrites the previous one. It is
  not an append-only archive. A deployment that needs one copies
  `.crew/evidence/` elsewhere.

## Golden-rule check

`./go demo` does not run `./go evidence`, and its time does not change.
At L0 the pack needs only the key `./go up` already generates: no
account, no API key, no network.
