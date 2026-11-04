# lights-out reference

Everything the [README](../README.md) leaves out: what runs at each milestone, every command, the levels, the plane, the release controller, how `./go` runs, and the repository layout.

## Status

Milestones M19 (harnesses, ADR 0041), M20 (gates anywhere, ADR 0042), M21 (GitHub as code host, ADR 0043), M22 (ops without the plane, ADR 0044) and M23 (the packaged command, ADR 0045) closed. Next, in plan §16's order: M24 (the public legacy experiment). What runs today:

| Stage | Milestone | Real |
|---|---|---|
| ci | M0 | ✓ |
| engine-compat | M2 | ✓ host-side, selected engine |
| up | M2 | ✓ L0 plane, 8 services beside the app |
| smoke | M2 | ✓ a meetup created through Envoy |
| verify | M1 | ✓ metric shape at M1, canary vs stable at M3 |
| deploy | M3 | ✓ staging → 5% → 25%, held for human gate |
| rollback | M3 | ✓ automatic on verify NO-GO, triggered by `./go demo-rollback` |
| PR gates | M4 | ✓ six fixture PRs prove risk, SAST, deps, test, mutation, secrets |
| ticket → PR | M5 | ✓ crew driver, six roles, one real run recorded, replay at L0 |
| e2e | M6 | ✓ ticket → PR → CI → merge → canary → verify NO-GO → rollback, audited |
| ADK agents | M7 | ✓ product, ops-triage, release on ADK 2.9.1; adk eval in ci; replay at L0 |
| go-for-launch | M7 | ✓ held canary → brief → human GO → 100% (`./go promote`), proved by `./go demo-launch` |
| review | M9 | ✓ test-reviewer and architect on gemini-cli; risk medium/high PR goes review → ci-passed/ci-failed; ticket 0003 perimeter rule |
| seam | M12 | ✓ Workload port (ADR 0030) lets the platform operate on any workload, not just the app; app is the default (`WORKLOAD=app`) |
| attach | M13 | ✓ `./go attach <path>` makes a repository of yours the workload, its contract in `.lights-out/`, its thresholds in `.lights-out/policy.env` (ADR 0037); `./go demo-seam` attaches `workloads/minimal/` as a repository of its own |
| rework | M15 | ✓ a gates NO-GO or a CHANGES-REQUESTED review goes back to dev-worker with the verdict, up to four attempts (`policy/rework.env`), then `needs-human`; `./go demo-rework` on ticket 0013's hand-written attempts (ADR 0038) |
| harnesses | M19 | ✓ `claude-code` (2.1.311) and `codex` (0.166.0) beside `gemini-cli`, one provider each, the guard in each CLI's pre-tool hook, `./go harness-conformance` against a scripted model (no key; plumbing, not a model's ability); recording with them is implemented, not yet run (ADR 0041) |
| ops without the plane | M22 | ✓ `./go incident` and `./go postmortem` (ADR 0044); `./go verify METRICS_URL=…` on any Prometheus-compatible endpoint; `demo-postmortem` replays the ops-triage recording made at L1 on 2026-11-04 (`./go record-postmortem PROFILE=l1`, gemini-3.8-flash) and prints GO with a fix ticket |
| packaged command | M23 | ✓ built and tested in CI, **not published**: the image and the `lights-out` wrapper (`init`, `gates`, `triage`, `postmortem`, `doctor`, `version`), the Action, `./go doctor`, `./go package`, `./go demo-package` (ADR 0045); the Action is proven in lights-out's own workflow only |
| gates anywhere | M20 | ✓ `./go init [<path>]` drafts the gates half of `.lights-out/` in the clone (eleven keys, the three `TOOLCHAIN_IMAGE` forms, a measured `MUTATION_BASELINE`); `./go gates` judges the last commit with no engine socket on the workbench form; `./go demo-init` proves it on `workloads/minimal` and `workloads/minimal-js` without the plane, 32 s and 7 s for init plus gates, measured warm on 2026-11-03 (ADR 0042) |
| GitHub code host | M21 | ✓ implemented: `adapters/github/` with the eight CodeHost verbs, an attached repository's merge built, signed and released by the release step at any code host, `./go codehost-conformance` (Gitea on every push, GitHub nightly once the owner sets the throwaway repository). Not yet run: `./go e2e CODE_HOST=github PROFILE=l1` (ADR 0043) |

| | |
|---|---|
| **Frontend** | No tests until M11. TypeScript type-check in the image build is the only frontend check at M1. |

A green `./go demo` reaches automatic rollback: the failing version (same image, `waitlist` targeted on) clears staging, fails verify at 5%, rolls back to 100/0 on its own, and exits nonzero, then runs the whole golden path once more through the tower (`e2e`). Promotion to 100% is go-for-launch, `./go promote` after a held release; the brief is composed by the release role from L1, rendered from the release record at L0.

The PR gates (`./go demo-gates`) run on six committed fixture patches and prove both verdicts: risk scoring, dependency scanning, SAST, test failure, mutation inadequacy, and secrets. `./go demo-crew` proves the ticket→PR pipeline: the crew container runs the dev-worker role on ticket 0001, opening a PR that passes `make gates`. At L0 the fixture is replayed; at L1 a real run against the Gemini API is recorded.

## Control plane

The tower (`core/tower.sh`) is a state machine over a JSON document per ticket in the state store, driven by step events on the event bus, with the `inproc` orchestrator pulling one event at a time. `./go e2e` runs it end to end and `./go audit TICKET=<id>` rebuilds the chain deploy → merge → PR → ticket → spec from the document and cross-checks it against the code host: a record that says merged while the PR is open is a NO-GO.

Before merge, a NO-GO is not the end (ADR 0038). A gates NO-GO or a review's CHANGES-REQUESTED puts the ticket at `ci-failed`, which requests `rework`: dev-worker runs again on the same ticket, from its own PR head, with the ticket followed by the verdict as its input (the NO-GO line and the gate's own details, such as surviving mutants, or the reviewer's reason; never a transcript). The new head goes through the same `ci` step and, if routed, the same review. Each history entry carries its attempt number and the document's `rework` list keeps every rejected attempt's verdict. At `REWORK_MAX_ATTEMPTS` (`policy/rework.env`, four) the ticket stops at `needs-human`. After merge nothing loops: a failing canary rolls back.

Declared at L0 (ADR 0019, amended by ADR 0025): the CI leg of `e2e` is still the six gates run by the tower on the PR head (with the base's gates, Makefile and policy, and the ticket's perimeter checked again, ADR 0033) and posted as the `gates` commit status on the PR, not a Gitea Actions job — the runner's first job, from M8, is the release image build on `main`; the PR-gates job itself moves to the milestone that runs the tower on GCP. The canary the tower releases after the merge is a real build of the merge commit, signed by the `Registry` port and verified by the launchpad before it takes any traffic — `./go audit` prints the verified digest, not a placeholder. The bus carries the tower's own messages back to itself in one process — at L2 the same message starts a job; `e2e` deletes the ticket's document before opening it again, a replay on a per-`up` plane that a production tower never does. The merge happens on the L0 Gitea only; nothing merges into this repository's own `main`.

Declared at L0 (ADR 0020): the three ADK roles (product, ops-triage, release) answer from model responses recorded once at L1 under `fixtures/adk/<role>/`, keyed by the input text — the tools, the eval and the drivers run for real, the model does not; a missing recording is a NO-GO naming `./go record-evals PROFILE=l1`, never a fallback. `./go promote` at L0 renders the promotion brief from the release record itself (a live record carries live numbers no recording can match) and says so on the line above the brief; from L1 the release role writes it. Promotion at L0 is Envoy weight 100 on the canary cluster; the canary does not become the new stable image (M8). Since M22 (ADR 0044) ops-triage has a fourth tool, `file_postmortem(title, timeline, causes, actions)`, answered once for an incident document; its recording does not exist until the owner runs `./go record-postmortem PROFILE=l1`. Each timeline entry carries one anchor that core checks against the document, and each action item becomes a ticket through the Tracker's `put` verb, with no `## Perimeter`. The tower does not call the triage role on a real rollback at M7; `./go triage ALERT=<file>` and `./go demo-triage` do, on alerts taken from the evalset. Since M18 (ADR 0040) a recording and its `run.json` carry `provider` and the served `model`; all seven ADK recordings are Gemini's, and recording the roles on Claude or OpenAI is `./go record-evals PROFILE=l1 MODEL_PROVIDER=anthropic MODEL=claude-sonnet-5-5` (or `openai` / `gpt-6.1-sol`), a path implemented and not yet run.

Declared at L0 (ADR 0036, proposed): `CREW_HARNESS=network` makes the assign step fan a ticket's `## Network` section out to its members — each a run of the profile's harness in its own clone of the base, judged alone (exit code, the harness check, its own perimeter, no path shared with another member) and then integrated in the declared order into one PR, which the tower governs like any other. Only `core/crew.sh` reads the variable, so the review step keeps its own harness; a ticket without `## Network` runs as a single worker. Ticket 0012's two members (`impl`, `tests`) replay patches written by hand under `fixtures/0012/`, marked `hand_written` in their `run.json`: no network has been recorded at L1 yet, and `./go record` cannot save one.

## Commands

On macOS, podman runs inside `podman machine`, a VM. The docker CLI is compatible but not the documented path; use `CONTAINER_ENGINE=docker` if needed.

If port 8080 is already taken on your machine, put `APP_PORT=9000` (or any free port) in `.env`: the app then answers at `http://localhost:9000`, and every command follows it.

```sh
./go up              # start the L0 plane and wait for it
./go smoke           # create a meetup through Envoy
./go engine-compat   # host-side engine gate: versions, then up and smoke
./go down            # stop the plane
./go test            # run backend tests
./go verify          # check the metric shape
./go demo-rollback   # release the failing version and prove automatic rollback
./go demo-seam       # attach workloads/minimal as a repository of its own and run the plane on it
./go attach <path>   # make a repository of yours the workload (its .lights-out/ is the contract)
./go detach          # back to the bundled app
./go init [<path>]   # draft the gates half of .lights-out/ in .attached/ (attaches <path> first); the baseline is measured (ADR 0042 §6)
./go gates           # judge a range: BASE=<ref> HEAD=<ref>, default the last commit; no plane, no engine socket on the workbench toolchain (ADR 0042 §4-§5)
./go demo-init       # prove attach, init and gates on workloads/minimal and workloads/minimal-js without the plane, timed against 300 s (ADR 0042 §10)
./go demo-hold       # release the same version with kill switch on and prove it holds at 25%
./go demo-gates      # run all six PR gate fixtures and prove both verdicts
./go e2e             # run the full tower: ticket → PR → CI → merge → canary → verify NO-GO → rollback, audited
./go audit           # rebuild the chain from a ticket's state: ./go audit TICKET=0001
./go resume          # request again the step a ticket failed, history kept: TICKET=<id>
./go demo-rework     # prove a NO-GO goes back to the worker with its verdict, then GO and merged (ticket 0013)
./go assign          # turn a ticket into a PR: TICKET=<id> [ROLE=dev-worker]
./go demo-crew       # prove ticket 0001 becomes a PR that passes the M4 gates (L0 replay)
./go demo-network    # prove a network of members on ticket 0012: one PR through the whole tower (ADR 0036)
./go record          # run a ticket for real at L1 and save the fixture: TICKET=<id> [ROLE=dev-worker] [AGENT_HARNESS=claude-code|codex MODEL_PROVIDER=anthropic|openai MODEL=<id>]; a non-gemini harness writes fixtures/<ticket>/<role>/<harness>/ (ADR 0041 §10); refuses a set MODEL_BASE_URL
./go harness-conformance # every CLI harness against a scripted model, nine cases, no key: [CONFORMANCE_HARNESS=<name>] (ADR 0041 §8); builds three CLI images, not part of ./go ci or demo
./go codehost-conformance # ten cases on the CODE_HOST set: Gitea with the plane up, or `CODE_HOST=github` on an attached throwaway repository (ADR 0043 §5); not part of ./go ci or demo
./go review          # run the review step on a PR: TICKET=<id> [ROLE=test-reviewer]
./go demo-review     # prove a medium-risk PR goes through review (L0 replay of fixture)
./go record-review   # record the review role's response at L1 (TICKET=<id> [ROLE=test-reviewer] PROFILE=l1)
./go triage          # run the ops-triage role on an alert: ALERT=<file>
./go demo-triage     # prove a fake alert becomes an incident with ranked hypotheses and a proposal (no plane)
./go incident        # build an incident document from the release record: NAME= FROM= TO= [LOGS=<file>] [TICKET=<id>] (the plane only for TICKET)
./go postmortem      # run ops-triage on an incident document: INCIDENT=<file>; writes .crew/postmortem/<name>/ and files a ticket per action item
./go demo-postmortem # replay the post mortem of fixtures/incidents/demo-rollback.json, tickets into a copy of tickets/ (no plane); NO-GO until it is recorded
./go record-postmortem # record that answer at L1 (PROFILE=l1); keeps the evalset and fixtures only if the eval passes
./go promote         # go-for-launch: the brief, then GO_FOR_LAUNCH=GO|NO-GO (or the terminal), then 100% or rollback
./go demo-launch     # prove go-for-launch: held canary, brief, GO → 100%, then the un-promote
./go record-evals    # record the ADK roles' responses at L1 (PROFILE=l1)
./go mutation-baseline # whole-scope mutation score of the workload, where its TOOLCHAIN_IMAGE says (ADR 0042 §6)
./go kill-switch     # turn off waitlist for all versions (manual flag edit)
./go freeze          # stop launchpad from deploying or shifting (manual flag edit)
./go unfreeze        # re-enable launchpad after freeze
./go board           # the pipeline explained, live while the plane runs, on localhost:8099 (BOARD_PORT to move it)
./go console         # the client's console on localhost:8098: five screens over the logbook, go-for-launch through core/promote.sh (CONSOLE_PORT; CONSOLE_SNAPSHOT=<file> without the plane; ADR 0047)
./go logbook         # write the logbook, the one versioned record the console and a consumer read: LOGBOOK_FILE (default .crew/logbook.json)
./go demo-console    # prove go-for-launch pressed from the console: held canary, POST GO → 100% via console, then the un-promote
./go doctor          # host-side: engine, podman socket, git, workbench image, the plane's ports, the key the profile needs; one line each, then GO or NO-GO (ADR 0045 §4)
./go package         # build the lights-out image as localhost/lights-out:dev, for the lights-out wrapper (ADR 0045 §1)
./go demo-package    # prove the wrapper: build the image, workloads/minimal as its own repository, init, commit the draft, gates GO in the workbench under 300 s, then a staged break NO-GO at test (ADR 0045 §8)
./go help            # list all targets
./go shell           # interactive shell inside workbench
./go clean           # remove the workbench image
```

The first run builds the workbench image (about 1 GB on disk, 350–400 MB download, 3–6 minutes depending on connection speed). The plane pulls about 840 MB (amd64) / 720 MB (arm64) of images on the first run, the emulators image being more than half (measured 2026-09-14, ADR 0012). The image includes gcloud and terraform, although nothing uses them before M8. `./go demo` now needs the network for the `deps` gate (osv-scanner) inside e2e, not only for image pulls.

## The image, the wrapper and the Action

ADR 0045 (M23) packages the gates half so that it runs on a repository without cloning lights-out. Status: the image is built and tested in CI, and **not published yet**.

- **The image.** `ghcr.io/nicolaguglielmi/lights-out:0.1.0` is the workbench plus the committed tree. `.github/workflows/package.yml` builds and tests it on every push (`./go package`, `./go demo-package`, the Action's test job). It publishes only on a `v*` tag or a manual dispatch, which is the owner's act. The package is private until the owner makes it public. Tags are mutable and the image is unsigned: pin it by digest. Until the first publish, `./go package` builds `localhost/lights-out:dev` and `LIGHTS_OUT_IMAGE=localhost/lights-out:dev` points the wrapper at it.
- **The wrapper.** `lights-out`, a POSIX script at the repository root. The host needs podman or docker, and git. Download by raw URL works only once the repository is public; until then copy it from a clone onto your PATH.

```sh
lights-out init                      # draft to ~/.local/state/lights-out/<repo name>/.lights-out (LIGHTS_OUT_OUT moves it)
lights-out gates                     # the last commit; BASE= HEAD= for a range; STAGED=1 for what is staged, as one commit
lights-out triage ALERT=<file>
lights-out postmortem INCIDENT=<file>
lights-out doctor                    # engine, image, key: one line each
lights-out version
```

  Your repository is mounted read-only, and `init` never writes into it. Copy the draft with `cp -R <out>/.lights-out/. .lights-out/` (the `/.` matters). Keys are read from your environment by name, never from `.env`. The engine socket is never given to the image, so `gates` and `init` work for `TOOLCHAIN_IMAGE=workbench` only; `compose:` and image forms get a NO-GO naming the socket, and are judged from a clone with `./go gates`. A range is judged by the contract at its base (`workload.env`, `policy.env`, `runners/`), so a change cannot loosen what judges it. At L0, `triage` and `postmortem` replay only lights-out's own recordings: on your alerts and incidents use `PROFILE=l1` and the key in your environment. The plane (`./go demo`, canary, rollback) stays the clone path.
- **The Action.** `action/action.yml`, used as `nicolaguglielmi/lights-out/action@v0.1.0`. It runs the wrapper of the same ref and posts one check run, `lights-out gates`, with the line as its title. Linux runners only. Fork PRs cannot post the check (a warning; the job status still carries the verdict). A private package needs `docker login ghcr.io` before it. Proven in lights-out's own workflow (`uses: ./action` on a scratch repository, one GO and one NO-GO check run). Not proven on another repository: that needs the first publish. Usage is in [the guide](use-on-your-repo.md).

Decisions and rejected alternatives: [ADR 0045](adr/0045-one-command-one-package.md).

## Levels

Local first, cloud optional (ADR 0022): L0 and L1 are the whole product, no capability exists only in the cloud; L2 and L3 swap containers for managed Google services.

| | **Local — the product** | | **Cloud — optional** | |
|---|---|---|---|---|
| | L0 · Replay | L1 · Local | L2 · Cloud | L3 · Platform |
| **Needs** | podman rootless (podman machine on macOS) | + the chosen provider's API key (Gemini's free tier is the free path) | + GCP project with billing | + Agent Engine quota |
| **Models** | Recorded responses (fixtures) | The profile's `MODEL_PROVIDER`/`MODEL` (ADR 0040): Gemini API `gemini-3.8-flash` by default; Claude or OpenAI for the ADK roles | idem, Gemini via Gemini Enterprise Agent Platform | idem + thinking level per role |
| **Harness worker** | Replay from `fixtures/` | Gemini CLI headless in compose | Gemini CLI on Cloud Run Job | + adapter Antigravity SDK |
| **Git and PR** | Gitea in compose | Gitea; GitHub for an attached repository with `CODE_HOST=github` (M21, ADR 0043; run end to end only by the owner, once) | GitHub real | GitHub real |
| **CI** | Gitea Actions (act runner) | Gitea Actions | GitHub Actions or Cloud Build | idem |
| **Registry and signing** | Zot local, local key, signature verified before staging (M8) | idem | Artifact Registry, KMS key, Binary Authorization | idem |
| **App database** | Postgres in compose | idem | Cloud SQL — does not scale to zero, the one fixed cost; stop or delete it between runs (`infra/README.md`) | idem |
| **Canary** | Envoy weighted cluster | Envoy | Cloud Deploy → Cloud Run | + option GKE |
| **Metrics** | `promql` on `METRICS_URL`; `prometheus` is its case at `http://$PLANE_HOST:9090` | idem | Managed Prometheus + Cloud Monitoring | idem |
| **Flags** | flagd (file) | flagd | flagd on Cloud Run or Unleash | idem |
| **Events and state** | Pub/Sub emulator + Firestore emulator | idem | Pub/Sub + Firestore | + adapter Cloud Workflows |
| **Agent ADK** | Replay of recorded model responses (`fixtures/adk/`) | ADK 2.9.1 in workbench on the profile's model: Gemini natively, Claude and OpenAI through ADK's `LiteLlm` (`litellm` 1.106.6), thinking per role mapped in `adapters/adk/thinking.env` | ADK on Cloud Run | Agent Engine + BigQuery analytics |
| **Time to first rollback** | < 15 min | < 20 min | ~45 min (terraform apply) | ~90 min |

*Note: Google renamed Vertex AI to Gemini Enterprise Agent Platform in April–May 2026. The endpoints, `GOOGLE_GENAI_USE_VERTEXAI` environment variable, and `aiplatform.googleapis.com` remain unchanged; `vertex` still appears in env vars and Terraform.*

*Note: since June 18, 2026 Gemini CLI no longer serves Google AI Pro/Ultra, Gemini Code Assist for individuals, or the free login with Google. The documented path is a Gemini API key (free tier: about 250 requests per day, Flash models only) or Gemini Enterprise Agent Platform. Whether a free-tier key still works with Gemini CLI is verified at M5.*

Only L0 exists today. Levels L1–L3 describe the plan.

## The app

This workload (the default, `WORKLOAD=app`) is a meetup RSVP platform: FastAPI backend (`app/backend/`), Postgres database, React frontend (`app/frontend/`). The app is one implementation of the `Workload` port (ADR 0030); another is `workloads/minimal/`, a minimal second workload that exists only to prove the seam. After `./go up` it answers behind Envoy at `http://localhost:8080` on the host or `http://host.containers.internal:8080` from inside workbench. Three auth-free endpoints list meetups, two auth routes signup and login, one create route, one RSVP route. Geocodes venues through a fake `GEOCODER_URL`. Feature-flagged partial waitlist (evaluates but returns 501 at M1).

Test and verify commands:
- `./go test` — Run the backend test suite against compose Postgres (pytest, 25 tests).
- `./go verify` — Check `verify/slo.promql` against the live app (metric shape only; evaluation at M3).

## PR gates

Six gates run in order on a git range (the merge-base of `BASE` and `HEAD`, or the last commit when no range is given; a root commit's base is the empty tree, ADR 0042 §5), with both verdicts proven by committed fixture patches. Run with `./go demo-gates` on the six fixtures, or `./go gates BASE=<ref> HEAD=<ref>` on your own range.

**Order:** risk (rules-based classification from path and size), secrets (gitleaks history and working tree), deps (osv-scanner on both lockfiles, a time bomb by design), SAST (bandit on the active workload's `WORKLOAD_MUTATION_SCOPE`), test (pytest, one failing assertion proves it works), mutation (cosmic-ray on changed lines, scored against a measured baseline).

**Risk levels:** `low` → `auto-merge`; `medium` or `high` → review. The risk classifier reads five signals: auth, payments, migrations, platform (gates/ci/verify/policy/core and the toolchain), and diff size. Policy lives in `policy/merge.env`, readable by sh and Python.

**Six fixtures:** `honest-fix` (ticket 0001 resolved, every gate GO, risk low); `fake-test` (same fix, inadequate test, mutation NO-GO); `migration` (adds a schema column, every gate GO, risk high); `sast-finding` (one bandit issue); `vulnerable-dep` (a known advisory); `failing-test` (one assertion fails). Each fixture is a git-format-patch `.patch` file applied in a worktree, with expected verdicts and JSON data recorded in `expect`.

**Declared limits:** The six gates do not name the app or any workload (verified by `ci/app-free.sh`); instead, they read the active workload's `workload.env` configuration (`WORKLOAD_MUTATION_SCOPE`, `WORKLOAD_LOCKFILES`, `WORKLOAD_KILL_SWITCH`, `MUTATION_BASELINE`). Dependency scan covers both dev and runtime dependencies in the workload and workbench (cosmic-ray and other test tools are included) and is live-validated against the OSV database over the network; `adapters/gemini-cli/package-lock.json` (the Gemini CLI platform toolchain) is declared and not scanned, like workbench's own pinned binaries; the same commit can go from GO to NO-GO overnight with no code change when a new advisory lands (the fix is a version bump or an `osv-scanner.toml` ignore entry with a reason). The migration fixture's column lands in the shared `postgres-data` named volume (the same one `./go up` and `./go test` use) and stays there across runs, harmless to every current test (they use explicit column lists, never `SELECT *`); `./go down` does not remove it, only `docker-compose --profile test --profile canary down -v` from inside `./go shell` does, or a fresh clone. At M4 the Gitea runner has no engine socket; the `gates` CI job runs only on GitHub (`if: github.server_url == 'https://github.com'`). At M8 the socket arrives, but not for this job: it goes to a new `image` job that builds, signs and pushes the merge commit (below); `gates` stays GitHub-only until the milestone that runs the tower on GCP. The socket's three conditions (ADR 0015 §7, ADR 0025 §4): the runner is the only compose service with it, jobs run only on refs the workbench user pushed, and the trusted-network line below says so.

**Model reality check:** The worker pins Gemini CLI `0.61.0-preview.0`, deliberately on the preview channel, because `0.59.0` and the current stable `0.60.0` silently serve `gemini-3.5-flash` when asked for `gemini-3.8-flash` (upstream fix google-gemini/gemini-cli#29252, in no stable release yet); the pin returns to the stable channel at `0.61.0`. A live L1 run on this pin answers from `gemini-3.8-flash` (verified 2026-09-16). The fixture at `fixtures/0001/dev-worker/` stays recorded on `gemini-3.5-flash` — its `run.json` says so — so at L0 the replayed PR was written by 3.5 while a live L1 run answers from 3.8, until the fixture is next re-recorded. See ADR 0018. Since M18 the id comes from `MODEL` in the profile (ADR 0040), still `gemini-3.8-flash`. Since M19 (ADR 0041) the other two harnesses are pinned the same way, by `package-lock.json` in their adapter: Claude Code `2.1.311` (the npm `stable` tag; latest was 2.1.318) with provider `anthropic` and `ANTHROPIC_API_KEY`, and Codex `0.166.0` (latest; no stable channel) with provider `openai` and `OPENAI_API_KEY` passed to the container as `CODEX_API_KEY`. Each `run.sh` refuses another provider. `MAX_TURNS` and `TOKEN_BUDGET` in `roles/` are Gemini measurements and bind all three until recordings re-measure them; the first Claude or OpenAI run may hit a budget for tokenizer reasons alone. The served model is read from the vendor's own signal (the `assistant` events for Claude, a trace line on stderr for Codex) and is `unknown` when unreadable. Neither pin has run against its real API yet: the suite uses a scripted model.

**Found building M19 (ADR 0041):** three holes, each closed. A repository's own `.env` could point gemini-cli's `GOOGLE_GEMINI_BASE_URL`, key included, at another host; the adapter now always sets it. A repository's own `.claude/settings.json` with `disableAllHooks`, and its `.codex/config.toml` with `[features] hooks = false`, each switched the guard off; the adapters now run Claude Code with `--setting-sources user` and Codex with `--enable hooks`, and conformance case 4 checks it. The worker used to mount all of `.crew/`, including the release signing key `.crew/cosign.key`; it now mounts only its run directory (the parent of `OUT`).

## The L0 plane

Eight services run beside the app in `compose.yaml`. `./go up` returns only after each adapter's `adapters/<name>/ready.sh` answered.

| Service | Host port | What |
|---|---|---|
| Envoy | 8080 | The app's only entrance; shifts traffic between `stable` and `canary` via weighted clusters, admin API on 9901 |
| Envoy admin | 9901 | Runtime config: `GET /runtime` reads weights, `POST /runtime_modify` shifts traffic between stable and canary |
| backend | implicit | Stable revision, answering at port 8000 inside compose |
| backend-canary | implicit | Canary revision (started on-demand by deploy, same image as backend); answering at port 8000 inside compose |
| Gitea | 3000 | Rootless, sqlite; bootstrap creates a fixed admin user (`lights-out` / `lights-out-local-not-a-secret`) and a public `lights-out/workload` repo holding the workload's repository (this checkout, or the attached one; Actions on only for this checkout); the runner registers on start in host mode |
| Prometheus | 9090 | Scrapes both backends every 5 s; canary and stable tell apart by the `service_version` label reached via `target_info` join |
| Zot | 5050 | OCI registry, empty until a merged PR is built into an image (M5 at the earliest); 5000 is macOS AirPlay |
| flagd | 8016 | OFREP; serves the active workload's `flags/flags.json` (`$WORKLOAD_CONTRACT/flags/flags.json`, bind-mounted dynamically); edit the file and the flag flips within seconds |
| Pub/Sub emulator | 8085 | Event bus for the tower's step events (topic `tower`, subscription `tower-inproc`) |
| Firestore emulator | 8086 | State store: one JSON document per ticket (`tickets/<id>`) |

`./go smoke` signs up, logs in and creates a meetup through Envoy.

**L0 assumes a trusted network**: every port is unauthenticated on every interface, the runner registration token in compose.yaml is public by design, and anyone on the LAN can register a runner or push to Zot. From M8, the Gitea runner also controls your container engine (docs/adr/0015 §7, docs/adr/0025 §4): it is the only compose service with the socket, and it only runs jobs on refs pushed through this same workbench.

At L0 the canary revision reuses the same image as stable, with the `waitlist` feature flag (the app's `WORKLOAD_KILL_SWITCH`) targeted at the version string `0.2.0-waitlist` (the app's `CANARY_VERSION`): the failing version is the same code, different flag targeting. The Workload port generalizes this to any workload (ADR 0030). The first real code diff arrives with the M5 worker.

## Release controller

`core/launchpad.sh` is the production release flow at every level. It stages a canary through four phases: `staging` (app readiness), `5%`, `25%`, then holds for the human gate (M7 `go-for-launch`). At each phase except staging it evaluates `verify/slo.sh`, comparing the canary against stable on three metrics: `traffic`, `error_rate`, and `latency_p95`. A NO-GO at any stage rolls back automatically to 100/0 on stable.

The controller itself generates no load; the Makefile starts the active workload's `load.sh` (`$(WORKLOAD_CONTRACT)/load.sh`, default `app/load.sh`) beside it, stopping when the controller exits.

At L0, the release is a manual Makefile target (`./go release` with `CANARY_VERSION`). Three proof commands demonstrate the flow:

- `./go demo-rollback` releases the failing version (waitlist on), expects NO-GO at 5%, and asserts the rollback (weights.canary=0, traffic on stable). This is now `./go demo`'s last stage.
- `./go demo-hold` releases the same failing version with the kill switch thrown first (waitlist off), expects GO all the way, and asserts the canary is held at 25% (not silently rolled back), then rolls back itself for clean state.
- `./go demo-launch` proves go-for-launch: a held canary, the brief, `GO_FOR_LAUNCH=GO` to promote to 100%, then the un-promote. A reader can step through the stages or let it run end-to-end (~150 s warm).

**Release of an attached repository (ADR 0043 §4), at any code host.** The bundled app still waits for the Gitea `image` job. For an attached repository `core/steps/release.sh` fetches the default branch, checks that the merge is on it, builds the merge commit in a throwaway worktree with `make publish` (the repository's own Containerfile, through the `backend` service of its `.lights-out/compose.yaml`) and signs it. The unchanged launchpad then verifies the signature and releases. `TOOLCHAIN_IMAGE` is not a release artefact: it holds no application. A workload without `.lights-out/compose.yaml` has no release. The build has no timeout of its own.

`demo-rollback` and `demo-hold` are manual, optional; only `./go demo` (which includes `demo-rollback`) is on the critical path. `demo-launch` is also optional; the manual human path is `./go kill-switch`, `./go release`, `./go promote` (the answer travels as `./go promote GO_FOR_LAUNCH=GO`; `go` forwards no host environment).

**Kill switch and freeze** are feature-flag edits in the active workload's `flags/flags.json`, live within seconds through flagd's file poller, no deploy:
- `./go kill-switch` turns off the active workload's kill-switch flag (named by `WORKLOAD_KILL_SWITCH`; for the app it is literally `waitlist`) for every version: the canary stops producing 501s.
- `./go freeze` / `./go unfreeze` toggle the `release-freeze` flag: `on` stops launchpad from deploying or shifting.

**Timing budget**: plan §2 requires `./go demo` to reach automatic rollback in under 900 s on a fresh container (cold pulls included). M3 measured this at 92 s warm (images already present); with the baseline 258 s cold from M2, a reasoned estimate is roughly 350 s total, well under budget. (Caveat: the 350 s figure is an estimate, not a fresh cold measurement this session, because forcing a safe cold re-pull of ~50 host images was not performed here.)

## The Workload port

A workload is any directory that owns a `workload.env` file and provides the services and scripts the platform expects. The app and `workloads/minimal/` are two implementations; `WORKLOAD=<path>` (default `app`) selects which one runs. The contract is declared in ADR 0030: seven environment variables (e.g., `CANARY_VERSION`, `WORKLOAD_KILL_SWITCH`, `WORKLOAD_MUTATION_SCOPE`), three compose services (`backend`, `backend-canary`, `backend-test`), health and metrics endpoints on `:8000`, one flag file, `smoke.sh` and `load.sh`, in-image test runners (`tests/mutation/run.sh`, `tests/sast/run.sh`), and gate fixtures. Every gate and control system reads these values instead of naming the app, verified by `ci/app-free.sh`. `./go demo-seam` (like `demo-rollback` and `demo-launch`, outside the main `demo`) proves the seam is real: two distinct workloads, same platform.

A workload can also live in a repository of its own (ADR 0037). `./go attach <path>` clones it into `.attached/`, which becomes the default `WORKLOAD` until `./go detach`. Its contract is in `.lights-out/` at its root and nowhere else: the files above plus `worker.md` (the dev-worker's brief), an optional `policy.env` (nine overridable thresholds, each with a provenance line, read by `core/policy.py`) and optional `tickets/`. The gates diff its history, Gitea mirrors it, and a PR is judged by its base's `policy.env`. `workloads/minimal/` is laid out this way; `./go demo-seam` copies it into a fresh repository and attaches it. [Use it on your own codebase](use-on-your-repo.md) is the walkthrough.

## How it runs

`./go <target>` builds `workbench/Containerfile` and runs `make <target>` inside it with the repo mounted at the same absolute path as on the host. The workbench image carries make, git, gcloud, terraform, and all verification tools, so the host needs only a container engine and a shell.

Profiles: `./go demo PROFILE=l2` selects `profiles/l2.env`. Secrets go in `.env` (gitignored, template `.env.example`). L0 needs no `.env`. Two optional keys serve `./go verify` on your own metrics (ADR 0044): `METRICS_URL`, the base that `/api/v1/query` is appended to, and `METRICS_AUTH_HEADER`, one header line such as `Authorization: Bearer …`, never printed. `./go demo` and the plane's own judge ignore both.

Two more keys serve the GitHub code host (ADR 0043): `GITHUB_REPO=<owner>/<name>` and `GITHUB_TOKEN`, a fine-grained token for that one repository with Contents, Pull requests and Commit statuses, read and write. `CODE_HOST=github` goes on the command line, never in `.env`, because in `.env` it would also redirect `./go demo`. The token reaches curl on stdin and git through a credential helper the Makefile adds as env-scoped config; it is in no argv, no `.git/config` and no model container.

### Workflows

`.github/workflows/ci.yml` runs `./go ci` and the `launch` job. Since M21 the `launch` job ends with `./go codehost-conformance` against Gitea, with the plane up, on every push. `.github/workflows/nightly-github.yml` runs the same suite daily against GitHub, on a throwaway repository named by the repository variable `LIGHTS_OUT_GITHUB_REPO`, with the secret `LIGHTS_OUT_GITHUB_TOKEN`. While the variable is unset it prints a notice and skips; a variable without the secret fails. The nightly does not run the full e2e: it proves the eight verbs on GitHub, not the tower on GitHub.

### Engine socket

Targets `up`, `down`, `test`, `demo`, `demo-rollback`, `demo-seam`, `e2e`, and `shell` mount the engine's API socket into workbench, which gives the container full control of the engine. It can start privileged containers, read any volume, and delete images. For `gates`, `init`, `mutation-baseline` and `demo-init` the socket is optional (ADR 0042 §4): mounted when it exists, and a toolchain other than `workbench` answers NO-GO naming it when it does not.

The `ci` target never gets the socket. `engine-compat` is the one gate that runs on the host itself, before workbench starts: `./go engine-compat` and `./go demo` both begin with `ci/engine-compat.sh`.

### Rootless notes

The engine socket is `unix:///run/user/$(id -u)/podman/podman.sock`. Enable it with:

```sh
systemctl --user enable --now podman.socket
```

(On macOS, `podman machine` enables it automatically.)

This is the same socket path for the Gitea runner.

Rootless podman usage:

- All published ports must be above 1024.
- Bind mounts get no `:z` (relabelling fails on podman machine's virtiofs, podman #26465/#21269); the one bind-mounted service (flagd) uses `security_opt: label=disable`.
- Use `host.containers.internal` instead of `host.docker.internal`.
- Prefer compose network aliases over any `host.*.internal` name.

Only the docker-compose features proven by `./go engine-compat` are allowed; proven at M2 on podman: `build` with `args`, `depends_on` long syntax, `profiles`, `ports`, `user`, `restart`, `security_opt`, a read-only bind mount.

## Repository

```
lights-out/
  LICENSE                   # Apache 2.0 license
  NOTICE                    # copyright
  README.md                 # this file
  Makefile                  # targets: help, ci, ci-*, demo, up, smoke, down, test, verify, demo-rollback, demo-seam, demo-hold, demo-launch, triage, demo-triage, incident, postmortem, demo-postmortem, record-postmortem, promote, record-evals, kill-switch, freeze, unfreeze, e2e, board, logbook, console, demo-console
  go                        # host entrypoint, POSIX sh
  .env.example              # template for secrets (gitignored)
  .gitignore

  workbench/
    Containerfile           # the toolchain: make, gcloud, terraform, tools
    fetch.sh                # script for verifying downloads

  board/
    index.html              # the board: scripted scenarios, live view (ADR 0035)
    serve.py                # serves it and /state.json, read through the port verbs

  console/
    index.html              # M11: the client's five screens over the logbook (ADR 0047)
    serve.py                # serves it, /v1/logbook.json, and the one POST: go-for-launch through core/promote.sh
    README.md               # how to run it, CONSOLE_AUTH, what is real at L0, who built it

  core/
    launchpad.sh            # release controller: stages, verify gates, automatic rollback
    incident.sh             # M22: ./go incident, the incident document from the release record
    postmortem.sh           # M22: ./go postmortem, runs the role, checks and files tickets
    postmortem.py           # M22: the document and anchor checks
    logbook.sh              # M11: ./go logbook, the one versioned record (ADR 0047 §2)
    logbook.py              # M11: its reader, by the rules in docs/plans/console.md
    logbook/1.0.schema.json # M11: the logbook's schema, one file per minor
    risk.py                 # risk classifier: rules-based PR scoring
    tests/                  # unit tests for risk.py

  policy/
    merge.env               # policy-as-code: risk levels, mutation baseline and margin

  gates/                    # PR gates: risk, secrets, deps, sast, test, mutation
    *.sh                    # individual gate scripts (read WORKLOAD_* from active workload)

  profiles/
    l0.env                  # L0: podman, no key, no account
    l1.env                  # L1: + MODEL_PROVIDER, MODEL and that provider's key
    l2.env                  # L2: + GCP project
    l3.env                  # L3: + Agent Engine quota

  ci/                       # repo self-checks; each prints one line, GO or NO-GO
    lib.sh                  # go / nogo helpers, the output contract
    contract.sh             # meta-gate: every gate honours the contract
    harness-shape.sh        # every CLI harness: pinned version and lockfile, thinking.env, conformance.env, guard.sh identical to gemini-cli's, fragments name every harness service (ADR 0041 §9)
    hooks.sh                # the guard's cases in each vendor's input shape (gemini, Claude, Codex)
    secrets.sh              # gitleaks, history and working tree
    lint.sh                 # actionlint on the workflow
    shell.sh                # shellcheck on every sh script
    help.sh                 # make help exits 0
    readme.sh               # every ./go X in README exists
    gitea-compat.sh         # workflow uses only what Gitea Actions supports
    codehost.sh             # every code host adapter: the eight verbs and ready.sh, credential.sh answers, the Makefile's git config index, the token never in argv (curl and git replaced by recorders; no network, no fake GitHub; ADR 0043 §5)
    profiles.sh             # profiles are plain KEY=value, same keys
    workloads.sh            # validate every workload's workload.env (replaces compose.sh)
    app-free.sh             # verify core/gates/verify/adapters never name the app (ADR 0030)
    engine-compat.sh        # host side, runs first in ./go (ADR 0012)

  docs/
    ARCHITECTURE.md         # ports and adapters
    PLAN.md                 # working plan (Italian)
    adr/                    # architecture decision records
      0001-profiles-and-single-makefile.md
      0002-self-ci-is-make-ci.md
      0003-make-demo-before-m3.md
      0004-containerised-toolchain.md
      0005-backend-stack-and-auth.md
      0006-external-service-geocoder-fake-and-contract.md
      0007-metrics-and-feature-flags.md
      0008-sql-migrations-expand-contract.md
      0009-frontend-built-into-backend-image.md
      0010-compose-layout-and-app-tests.md
      0011-slo-queries-and-metric-shape-gate-at-m1.md
      0012-l0-plane-layout-runner-envoy-readiness.md
      0013-launchpad-canary-verify-rollback-at-l0.md
      0014-prove-the-go-branch-with-demo-hold.md
      0015-pr-gates-at-m4.md
      0016-risk-model-and-merge-policy.md
      0017-agent-runtime-at-m5.md
      0018-gemini-cli-pin-for-the-model-id-fix.md
      0019-control-plane-at-m6.md
      0020-adk-agents-and-go-for-launch-at-m7.md

  verify/                   # SLO verification; shape-checked at M1, evaluated at M3
    slo.promql               # traffic, error_rate and latency_p95, canary vs stable via target_info join
    metric-shape.sh         # checks them against one live /metrics scrape
    slo.sh                  # verifies canary metrics against stable, the release gate

  app/                      # the default workload (docs/adr/0005-0010)
    backend/                 # FastAPI + psycopg, meetups/, migrations/, tests/
    frontend/                # React + Vite + TypeScript, built into the backend image
    fakes/geocoder/          # the external-service dependency, stdlib only
    contracts/geocoder/      # the fake's committed example-pair contract
    flags/                   # flagd file-resolver flag definitions
    load.sh                  # load generator: loops registration POSTs through Envoy
    smoke.sh                 # signup, login, meetup through Envoy
    fixtures/gates/          # gate fixtures proving risk, test, mutation, deps, sast, secrets
      <name>/
        *.patch              # git-format-patch applied in worktrees
        expect               # expected gate outcomes and JSON data

  workloads/                # other workloads behind the Workload port
    minimal/                 # a second minimal workload (no database, no frontend; proves the seam)
      fixtures/gates/        # gate fixture for this workload (at least one required)
    minimal-js/              # a second workload, javascript, gates half only: the gates on a repository with no plane (ADR 0042 §9)

  stacks/                   # the gate runners ./go init copies into a repository, one directory per stack, each with its lockfile (ADR 0042 §7)
  compose.yaml               # the L0 plane: workload + Gitea, runner, Zot, Envoy, Prometheus, flagd, emulators

  adapters/                 # one directory per adapter named in profiles/*.env
    adk/                    # M7: ADK roles, replay/recording model, evalsets
    envoy-canary/           # Containerfile, envoy.yaml, ready.sh, deploy.sh, shift.sh, rollback.sh
    prometheus/             # Containerfile, prometheus.yml, ready.sh, query.sh (calls promql with http://$PLANE_HOST:9090)
    promql/                 # M22: query.sh, any Prometheus-compatible endpoint at METRICS_URL
    gitea/                  # ready.sh, runner/Containerfile, runner/config.yaml
    github/                 # M21: the eight CodeHost verbs, ready.sh, env.sh, credential.sh; GitHub's REST API, an attached repository only (ADR 0043)
    flagd/                  # ready.sh
    zot/                    # ready.sh
    pubsub-emulator/        # ready.sh
    firestore-emulator/     # ready.sh

  roles/                    # M5/M7: agent role definitions (prompt.md + role.env; the three ADK roles name HARNESS=adk)
  policy/                   # from M4: policy-as-code
  tickets/                  # project tickets, one per file; 0001 is the first bug found
  gates/                    # from M4: PR gates, identical at every level
  fixtures/                 # M5: recorded agent responses at L1, replayed at L0; fixtures/adk/: recorded ADK model responses; fixtures/incidents/: the incident documents (M22)
  .crew/                    # M5: per-ticket workspace and output directory (gitignored)
  design/                   # from M11: Stitch artifacts for the console (ADR 0023)
  console/                  # from M11: the view over git and the state store (ADR 0023)
  infra/                    # from M8: Terraform for GCP
  e2e/                      # from M6: golden path test
```

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — the port/adapter pattern, how levels compose
- [docs/use-on-your-repo.md](use-on-your-repo.md) — put your own codebase behind the gates and the rollback
- [docs/adr/](adr/) — architecture decision records
- [PLAN.md](PLAN.md) — the working plan (Italian)
- [app/README.md](../app/README.md) — the target application: routes, auth, migrations, metrics, flags
