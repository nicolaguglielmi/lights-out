# ADR 0072 — Security scanning: SAST judges the diff, a signed SBOM per release, a passive DAST at staging

- Status: accepted, deferred by the owner on 2026-11-11, then asked to be built the same day as M42 in three slices (renumbered M43 when it landed, M42 being ADR 0073's); 2026-11-11, decision 1 (SAST on the diff), decision 2 (the SBOM) and decision 3 (DAST at staging, off by default) built the same day, decision 4 in part; 2026-11-12, the scanner runs on one passive-scan thread ("The stable against itself"); see "Built" below
- Date: 2026-11-11
- Would amend: ADR 0015 §4 and ADR 0030 §3 (the SAST verdict and the runner's output), ADR 0053 §6 (`sast` leaves the green-base check), ADR 0024 and 0026 (an optional fifth Delivery verb), ADR 0013 §4 (a check at staging), ADR 0025 §1 (`publish.sh` also attests)

## Context

`gates/sast.sh` NO-GOes on any finding in the whole package. On M24's two
real repositories (2026-11-10, measure), sqlparse's base was red at `sast`
because of one `assert` (bandit B101), and tenacity's because of `random`
used for jitter (B311). ADR 0053 §6 holds every ticket until the base is
green, so nothing moved until a human added `# nosec` outside every ticket's
perimeter. `gates/deps.sh` already had this problem and solved it (ADR 0032).
Nothing produces an SBOM, and nothing scans a running revision.

A correction to the brief. The workload's release is signed with a local
key at L0/L1 (ADR 0025 §2, `adapters/zot/publish.sh`). Keyless signing
(ADR 0057 §4) applies only to lights-out's own images on GHCR. K3, the
attestation of what the agent did (`docs/plans/adoption-roadmap-2026-11.md`,
M36), is not built yet.

## Decision

1. **SAST judges the diff, the same way `deps` does.** The runner contract
   gains `items`, one entry per finding. The line becomes
   `{"findings": N, "items": [{"rule", "file", "line"}]}`, with `file`
   relative to the repository root. The gate runs the base's runner (ADR 0033)
   on worktrees at the merge-base and at HEAD. It fingerprints each item as
   `rule, file, sha256 of the flagged line's text with whitespace stripped`,
   read from git at that revision. Line numbers stay out of the fingerprint.
   Base paths are mapped through `git diff -M` renames. The comparison counts
   duplicates, as `deps.sh`'s sorted lists and `comm` already do.

   The verdict is NO-GO only for a fingerprint that HEAD has more times than
   the base. Inherited findings go to stderr and to `sast.json`
   (`new`, `inherited`). If either side's runner prints no `items`, the gate
   judges HEAD whole, as today, and says so on stderr. `# nosec` stays the
   escape hatch for a new finding. `sast` leaves `core/base-gates.sh`, so
   `test` alone judges a tree. The lines are
   `GO: sast — in <where>, none new (<n> inherited)` and
   `NO-GO: sast — in <where>: this diff introduces <n> finding(s); list on stderr`.
2. **SCA: one SPDX 2.3 SBOM per released image, attested next to its
   signature.** `publish.sh` saves the image to an archive with the engine.
   It then runs `osv-scanner scan image --archive <tar> --format spdx-2-3
   --all-packages`, accepting exit 0 or 1, because 1 only means advisories
   were found. Then it runs `cosign attest --type spdxjson` with the same key
   and flags as the signature. If any of these steps fails, publish fails,
   so a signed image without an SBOM cannot be released. The release record
   gains `sbom`, the digest of the attestation. `verify.sh` does not change
   and no gate judges the SBOM.

   The SBOM is separate from K3, with a different subject. The SBOM describes
   the image digest: what shipped. K3 describes the merge: who changed it,
   under what policy, and who said GO. When K3 is built, its predicate names
   the image digest and this attestation's digest. Neither statement embeds
   the other.
3. **DAST: ZAP's baseline scan, passive only, run at the staging stage
   before any traffic.** The launchpad calls it after `deploy.sh` and before
   the shift to 5%, from a new gate, `verify/dast.sh`. The gate scans the
   stable at `APP_URL` (weight 0 at staging, which `status.sh` proves). It
   reaches the canary through a new, optional Delivery verb, `canary-url.sh`,
   which prints a URL that reaches only the canary revision. Every delivery
   system has this concept: an Envoy route to the canary cluster, Argo
   Rollouts' `canaryService` ("send traffic to only the canary ReplicaSet"),
   and a Cloud Run revision tag URL.

   An alert is the tuple `(plugin id, method, path without query)`, with
   the host stripped. The verdict is NO-GO only for a tuple the canary has
   and the stable does not, at any risk level, with no severity floor
   (ADR 0015 §4). ZAP's own exit codes 1 and 2 are ignored. Exit 3, or a
   report that cannot be read, means the canary cannot be judged, which
   rolls back (ADR 0066). The image is pinned by index digest, for example
   `zaproxy/zap-stable:20261103@sha256:9624669d…`, because the `2.17.0` tag
   moves with every monthly rebuild.

   It is off by default at L0 and L1 (`DAST=off`). `./go demo-dast` proves
   both verdicts at L0 with no key. With `DAST=on`, an adapter that has no
   `canary-url.sh` is refused before staging. The gate prints exactly one
   line:
   `GO: dast — canary <v>: nothing new (<n> inherited from <stable>)` or
   `NO-GO: dast — canary <v> raises <n> alert(s) stable <stable> does not; list on stderr`.

   Before DAST may be turned on in `./go demo`, these must be measured:
   - the cold pull of the pinned image on both Actions jobs and on the
     4.66 GiB podman VM;
   - one warm scan of the bundled app;
   - repeated stable-against-stable runs, which must give an empty diff.

   The pull plus two scans must fit in the measured margin of the slower
   job, today 222 s (678 s against 900 s, ADR 0062 §6). If they fit, DAST is
   on at L0. If they do not, it stays off at L0, and turning it on at L1 is
   the owner's call, since no gate measures L1's time.
4. **One wording everywhere.** The gate's stdout line is stored in
   `GATES_OUT/lines` and the release record. `./go`, `lights-out gates` and
   the console print it verbatim. `./go audit` and the console's timeline
   show the inherited lists and the SBOM digest, with the `cosign
   verify-attestation` command beside it. The console gains no write
   (ADR 0047). The wrapper gets the SAST change for free. It has no release
   and no engine socket (ADR 0045), so SBOM and DAST exist only on `./go`
   and in the console.

## Alternatives rejected

- **SAST as today (whole tree, `# nosec`)**: on M24 it blocked every ticket on debt no ticket created.
- **Line numbers in the fingerprint**: one insertion above a finding makes it "new".
- **Only findings on changed hunks (one scan)**: re-indented or moved code reads as new.
- **bandit 1.9.4 `-b` baseline** (PyPI 2026-02-25, Apache-2.0; `issue.py` and `manager.py` on main, read 2026-11-11): it matches text, severity, CWE, confidence, file and test, but not line or code, and checks membership rather than counts, so a second identical finding in a file passes. It is also Python only.
- **ESLint 10.14.0 bulk suppressions** (MIT; read 2026-11-11): JavaScript only, and needs a committed file in the user's repository.
- **semgrep 1.185.0 `--baseline-commit`** (PyPI, LGPL-2.1-or-later, 2026-11-11): diff-aware out of the box, but its rules are under the Semgrep Rules License v1.0, which forbids making them available to others. ADR 0015's reasons stand. **Opengrep 1.33.1** (2026-10-29, 46–50 MB binaries): no rule set of its own was verified.
- **syft 1.57.1** (Apache-2.0, 2026-11-09, 31/29 MB): the specialised tool, but a new binary. It is the fallback if osv-scanner's image inventory misses packages that `uv.lock` names.
- **CycloneDX from osv-scanner**: it embeds a vulnerabilities section, so the signed document would change with the advisory feed. SPDX output is inventory only (osv-scanner docs, 2026-11-11).
- **BuildKit `--sbom`**: Docker only, and podman is the reference engine (ADR 0004).
- **Nuclei v3.11.1** (MIT, 2026-08-08, 46/41 MB): template probes for known exposures, with templates fetched at run time. It does not analyse the app's own responses passively.
- **ZAP full scan**: active attacks against a revision that shares stable's database.
- **zap-bare** (509 MB, amd64 only): it lacks the packaged scans and has no arm64 build.
- **Scanning at 5% or later**: real users already reach the canary.

## Consequences

- **The cost that will hurt: the DAST image is about 1.2 GB compressed**
  (1,202,475,445 bytes amd64, 1,169,359,316 arm64, Docker Hub, 2026-11-03).
  For comparison, the gates image is 508 MB unpacked (ADR 0057 §3); ZAP's
  unpacked size has not been measured. Its spider is
  bounded by time (default 1 minute), so a large app can crawl differently
  on two runs and give a false NO-GO.
- Every SAST runner, init's copies included, changes its output. A
  repository whose runners predate this ADR keeps the whole-tree verdict
  until `./go init` refreshes them.
- A finding moved to another file reads as new. `main` can carry known
  findings with a green gate; they are printed on every run.
- The SBOM check depends on osv-scanner 2.6.0 (the pin) having
  `spdx-2-3` output and `scan image --archive`. The docs read are those of
  2.7.1, published 2026-11-09. If the pin lacks them, the bump is a commit
  of its own. Cosign 3.1.3's `attest` must round-trip on Zot with ADR 0025
  §2's flags before anything is wired.
- ZAP's requests reach the canary's metrics. The scan ends before the 1m
  window of the 5% stage opens (ADR 0013 §2).

## Golden-rule check

SAST: two scans instead of one, no new tool, and the cost is measured at
implementation. SBOM: no new binary, two attestations per `./go demo` (at
`up` and in the release). The time they add is measured on both Actions
jobs before merging. DAST stays out of `./go demo` until §3's measures show
it fits under 900 s. Passes, provided the SBOM measure fits.

Sources, read 2026-11-11: zaproxy.org/docs/docker (baseline-scan, about);
hub.docker.com zaproxy/zap-stable and zap-bare tags; GitHub API releases of
zaproxy (v2.17.0, Apache-2.0), nuclei, syft, opengrep, cosign (v3.1.4,
2026-10-20), osv-scanner (v2.7.1); google.github.io/osv-scanner (output,
scan-image); PyPI bandit and semgrep; eslint.org/docs/latest/use/suppressions;
argoproj.github.io/argo-rollouts/features/canary; Cloud Run tag URLs
(cloud.google.com blog on gradual rollouts).

## Built

### Decision 1, 2026-11-11 (M43, first slice)

Built as decided, with four precisions found writing it. None changes the
verdict.

- **`file` is relative to the directory the runner runs from**, the
  contract's `$WORKLOAD/$WORKLOAD_TEST_ROOT`, not to the repository root:
  a runner inside `backend-test` or an image cannot know where it sits in
  the repository. The gate prefixes the run directory's path from the root
  (`git rev-parse --show-prefix` plus the test root), the mapping
  `gates/mutation.sh` already uses. Fingerprints and `sast.json` carry
  repository paths.
- **The flagged line is read from the tree the runner scanned**, HEAD's
  checkout and the base's worktree, which are those revisions' files: the
  line number refers to what was scanned, and the comparison needs no
  process. The logic is `core/sastdiff.py` (ADR 0052 §2), stdlib, no
  process; `gates/sast.sh` is the glue.
- **A HEAD with no finding is GO without scanning the base**: nothing can
  be new, and nothing is inherited. The second scan costs only where HEAD
  has findings, so `./go demo`'s PR (a clean HEAD) runs one scan as before.
- **Each side runs the runner its checkout carries.** In the tower's CI
  step HEAD's `.lights-out/runners/` are the base's (ADR 0033), and the
  merge-base's are the base's by definition. An in-tree runner such as
  `tests/sast/run.sh` stays the PR's own (ADR 0033's amendment).

The mainline step (ADR 0056 §3) still runs `sast test` on the range from
the last green main: its `sast` now judges what the group adds. Runners
changed: `app/` and `workloads/minimal` (bandit, `-f json`), both stack
runners and their copies (bandit, eslint with paths made relative). Tests,
written from this ADR without the implementation: 38 unit tests in
`core/tests/test_sastdiff.py`, and `ci/sast-diff.sh` in `./go ci`, which
drives the real gate on scratch repositories (renames, re-indentation,
duplicates, the empty tree, a subdirectory workload, old runners on either
side). `./go demo-gates` keeps the nine verdicts and has two fixtures on a
base already red at `sast` (a `base/` directory of patches applied first):
`sast-inherited` GO, `sast-added-on-red-base` NO-GO.

### Decision 2, 2026-11-11 (M43, second slice)

Built as decided. osv-scanner 2.6.0, the pin, has `scan image --archive`
and `--format spdx-2-3` (its `--help` in the workbench image), so no bump
and no new binary. Precisions:

- **The archive comes from the engine's API socket**, `GET
  /images/<name>/get`, the Docker API that podman serves too: the
  workbench has the socket `./go` mounts and no engine CLI.
- **The order is SBOM, attestation, `cosign verify-attestation`, then the
  signature.** A failure at any step before the signature leaves an
  unsigned image, which `verify.sh` refuses, so even a publish that dies
  half-way cannot leave a signed image without an SBOM. Reading the
  attestation back on every publish is the round trip, on every `./go up`
  and every release.
- **The scan needs the network**, as `deps` does: `--offline` exited 127
  with one package, because no local advisory database exists (measured on
  a scratch Zot, 2026-11-11).
- **The record reads the digest through an optional Registry verb,
  `sbom.sh <repo>@<digest>`.** `verify.sh` does not change and launchpad
  names no registry, so something behind the port has to answer; for Zot
  it is the OCI referrers API, the sigstore bundle annotated
  `dev.sigstore.bundle.predicateType: https://spdx.dev/Document`. Without
  the verb, or without an attestation, `sbom` is null; it is never a
  verdict. This verb is a structural addition the decision did not name:
  the owner confirms it or names another way.
- The tower document's `release` carries `sbom` from the record, and
  `./go audit` prints it with the `cosign verify-attestation` command.
- **Measured** on 1ce7cea (GitHub Actions run 43010516593, ci's
  engine-compat job, docker; run 43010516570, stranger's demo job, rootless
  podman). Each publish now prints its SBOM time: 10–16 s on docker, 4–5 s
  on podman, for the bundled image's 1248 SPDX packages. `./go demo`
  publishes four times, not twice (three times the stable's digest, once
  the canary's; the log shows the four lines), so the SBOM adds 40–60 s on
  docker and about 20 s on podman.
  The demo took 718 s on docker (663 s on 19be6c3, the run before) and
  573 s on podman (663 s on 19be6c3): within 900 s, and the run-to-run
  spread on podman is larger than the SBOM's cost.
- **The inventory covers `uv.lock`.** On the bundled backend image, the
  SPDX document names all 40 runtime packages of `app/backend/uv.lock`
  that install on Linux (the 41st, `tzdata`, is marked
  `sys_platform == 'win32'`); the 31 absent are dev dependencies the image
  does not ship (measured 2026-11-11 on a saved image, no plane). syft
  stays unneeded.

### Decisions 3 and 4, 2026-11-11 (M43, third slice)

Built as decided, off by default (`DAST=off` in every profile). Precisions:

- **The scanner is the plane's `zap` service** (`compose.yaml`, profile
  `dast`, `zaproxy/zap-stable:20261103@sha256:9624669d…`), run once per
  side with `run --rm` and a tmpfs `/zap/wrk`; the JSON report is printed
  on stdout, ZAP's log on stderr.
- **envoy-canary's `canary-url.sh` names a canary-only Envoy listener**,
  `http://envoy:8081`, on the plane's network, where the scanner runs. It
  is published on no host port: one more port on the host is one more to
  collide on, for a check that is off by default. Both scans go through
  Envoy, so they see the same proxy's headers. argo-rollouts has no
  `canary-url.sh` yet, so `DAST=on` with it is refused before staging.
- **The stable is scanned at `APP_URL`, its host replaced by the address
  the workbench resolves `PLANE_HOST` to**: the scanner is another
  container, where `host.containers.internal` need not resolve (docker
  gives it to the workbench through `--add-host`), and the address works
  on both engines. The host is stripped from every alert anyway. Before
  either scan the gate reads `status.sh`: a weight other than 0 is
  `cannot judge`, since `APP_URL` is then not the stable alone.
- **launchpad**: `DAST=on` without `canary-url.sh` is refused before
  staging; after `deploy.sh` the gate runs and a NO-GO rolls back at
  staging (`NO-GO: launchpad — staging: <the gate's reason> → rolled
  back`). The line is the record's `dast`, the tower document's
  `release.dast` and a `staging:` line of `./go audit`. Room in
  launchpad's line debt (ADR 0052 §4) came from moving the stage's JSON
  into `core/releases.py stage`.
- **`./go demo-dast`** (and a `dast` job in `ci.yml` on docker and in
  `podman.yml` on rootless podman): the pull, timed; `DAST_RUNS` (2) times
  the stable's own image deployed as the canary under another version,
  which must be GO with nothing new; then a canary built from
  `app/fixtures/dast/leaky-header` (a middleware adding
  `X-Powered-By: meetups/<version>`, ZAP rule 10037), published, signed
  and SBOM'd as any release and released with `DAST=on`, which must roll
  back at staging on the dast line with the stable still answering.
- **Decision 4, partly.** The line is in the release record and `./go
  audit` prints it, and the SBOM digest with the `cosign
  verify-attestation` command. Not built: the console's timeline (the
  logbook's schema `lights-out/logbook/1.2` has no field for either; a
  version bump is the console's own change) and `./go audit` listing
  `sast`'s inherited findings (the CI step keeps only the gates' line in
  the tower document, not `sast.json`). The wrapper gets the SAST change
  for free, as the ADR says.

**Measured** with `./go demo-dast` on ed19c07, 2026-11-11, on the 4.66 GiB
podman VM (podman 6.1.4, libkrun, macOS; this machine's network):

| What | Time | Provenance |
|---|---|---|
| Cold pull of the pinned image | 298 s | measure, `[demo-dast] measure: pull of the zap image` |
| One scan, warm, of the bundled app | 34–40 s (six scans) | measure, `dast: … zap exited 0 in <n>s` |
| The stable against itself (two scans) | 76 s and 74 s, both `nothing new (13 inherited)` | measure, two runs |
| The leaky canary, deploy to rollback | 80 s, 4 new alerts (rule 10037 on 4 paths) | measure |

And on GitHub Actions, the same commit, `ubuntu-latest`:

| What | ci.yml `dast` (docker), run 43025578953 | podman.yml `dast-podman` (podman 5.7.0, rootless), run 43025579032 | Provenance |
|---|---|---|---|
| Cold pull of the pinned image | 21 s | 22 s | measure |
| One scan, warm | 34–39 s | 37–38 s | measure |
| The stable against itself (two scans) | 77 s, 70 s, both `nothing new (13 inherited)` | 74 s, 76 s, both `nothing new (13 inherited)` | measure |
| The leaky canary, deploy to rollback | 79 s, 4 new alerts | 79 s, 4 new alerts | measure |

The margin of the slower job on this commit: `./go demo` took 696 s in
ci's engine-compat (docker) and 599 s in stranger (podman), so 900 − 696
= 204 s (measure; it was 222 s on ADR 0062's commit). The pull plus two
scans is 92–99 s on Actions, 372–378 s on the VM.

**Verdict on the default.** By this ADR's own rule, measured on the slower Actions
job, DAST fits: 99 s against 204 s. It stays off at L0 anyway, and that
is the owner's call to change. The rule measures a datacentre's network;
the VM, a laptop on an ordinary connection, pulled the 1.2 GB image in
298 s, and 372 s on top of stranger's 599 s is 971 s, over the 900 s the
golden rule allows a first `./go demo`. A cloner pays the pull once, on
the run the golden rule is about. The choices for the owner: keep it off
(as built), turn it on at L0 and accept that a slow first pull breaks the
15 minutes, or turn it on only when the image is already present. At L1
it is also the owner's call, as decided, since no gate measures L1's time.

### The stable against itself, 2026-11-12

`./go demo-dast` NO-GOed once on Actions (run 43236481626, `dast`,
docker, c72da16, attempt 1; the rerun was green): in the stable against
itself, the canary scan had `10020 GET /` (Missing Anti-clickjacking
Header) and the stable scan did not. Both sides ran the same image, the
spider found the same five URLs with the same statuses, and the rules
raise alerts deterministically for a given response.

**Cause (measure).** ZAP 2.17.0's passive scanner, on its default
threads, sometimes leaves out of the report an alert a rule raised. The
app's `/` is recorded twice, and the two records are scanned on parallel
threads, which raise the same alerts within milliseconds. With ZAP's
debug log on (`org.zaproxy.zap.extension.alert`), one scan logged
`alertFound Storable and Cacheable Content …/sitemap.xml` 31 s before
the report was written, and the report has no such instance. Timing is
not the cause: ZAP's `passiveScan-wait` did not return early. The exact
line in ZAP was not found; the measure below is what the fix rests on.

| Scans of the same stable | Passive scan threads | Scans missing one tuple | Provenance |
|---|---|---|---|
| Actions, 7 `dast`/`dast-podman` jobs, runs 43198017866, 43198017849, 43236481626 (both attempts), 43236481651, 43239393780, 43239394037 | default | 6 of 42 | measure; always `/`, `/robots.txt` or `/sitemap.xml` |
| 4.66 GiB podman VM, 2 to 5 scans in parallel | default | 3 of 39 | measure; one is the same `10020 GET /` |
| the same VM, 2 or 3 scans in parallel | 1 | 0 of 35 | measure |
| Actions, fd9a2f7, `./go demo-dast DAST_RUNS=5`, runs 43259058744 (docker) and 43259059125 (rootless podman) | 1 | 0 of 24 | measure; every stable-against-stable run `nothing new (13 inherited)` |

**Decision.** `verify/dast.sh` passes `-z "-config pscans.threads=1"`
to `zap-baseline.py`. One is not a tuning: it is the only count with no
concurrent raises (decision, from the measure above). The rule does not
change: NO-GO only for a tuple the canary has and the stable lacks. It
costs nothing measurable: one scan took 34–40 s on those runs, against
34–39 s on default threads (measure). The `dast` and
`dast-podman` jobs run the stable against itself five times
(`DAST_RUNS=5`, ten scans per job): at six missing in 42, a regression
shows within a run of both jobs (decision).

Rejected: scanning the stable twice and taking the union, which makes a
false NO-GO rarer but not impossible and costs a scan; a delay before
`passiveScan-wait`, since the lost alert was raised long before the
report.

Still open, the owner's call: `zap-baseline.py` runs ZAP once with
`-addonupdate -addoninstall pscanrulesBeta` before every scan, so the
beta rules (10049, 10063, 90004 among the 13 inherited) and
`webdriverlinux` come from GitHub at scan time, not from the pinned
image. If it fails in one scan of a pair, the two sides may run
different rules (not measured). `-z -silent` skips the download and
drops the beta rules.

### The stable scanned again when its report lost whole paths, 2026-11-17

`./go demo-dast DAST_RUNS=5` NO-GOed once more on Actions (run
44156393128, `dast-podman`, 2302cbc, attempt 1; the rerun was green),
with `pscans.threads=1` set. In the stable against itself, run 3, the
stable's report had alerts on `/` only: 7 tuples, against 13 in every
other scan of the job. The six the canary had and the stable lacked were
every tuple on `/assets/index-….js`, `/robots.txt` and `/sitemap.xml`.
The spider found the same 5 URLs in all ten scans (`Total of 5 URLs`),
and ZAP's own summary of that stable scan already counted one instance
per rule (`x 1` where the others had `x 2` or `x 4`): the passive scan
reported nothing for three of the five URLs. The exact cause in ZAP was
not found (no debug log on that run).

| Over | dast jobs | failed on the stable against itself | Provenance |
|---|---|---|---|
| The last 100 `ci` and 100 `podman` runs on Actions, to 2026-11-17 | 203 (some skipped on docs-only pushes, not counted apart) | 1 | measure |

**Decision.** A paths check, then one more scan of the stable:

1. `core/dastdiff.py diff` exits 3, not 1, when the canary has new
   tuples and every one of them is on a path where the stable's report
   has no tuple at all. A whole path missing from the stable's report is
   what a lost passive scan looks like; a canary that changes a response
   the stable also serves raises its new tuple on a path the stable
   reports (the leaky canary's four `10037` tuples are on `/`, the
   script, `/robots.txt` and `/sitemap.xml`, where the stable has 13).
2. On exit 3, `verify/dast.sh` says so on stderr, scans the stable once
   more, and compares the canary with the union of the two stable
   reports. A stable scan cannot raise an alert the stable does not
   have, so the union only restores what a scan lost.
3. On that second comparison, exit 1 and exit 3 are both a NO-GO with
   the same line as before: a canary that adds a new path with alerts is
   still rolled back, one scan later. There is no third scan.

It costs one scan, about 32 s (measure, the scans of run 44156393128),
only when every new tuple is on a path the stable did not report. The
rule does not change: NO-GO only for a tuple the canary has and the
stable lacks.

Rejected: comparing only the paths both reports have, which would let a
canary that adds a leaking path through; a stable scanned twice on every
staging (rejected above, and it costs a scan each time); retrying until
GO, which is a verdict bought with reruns.
