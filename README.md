# lights-out

![lights-out: six agents ship it, one human turns the lights back on](docs/assets/lights-out.jpg)

**The safety system for a dark factory of AI coding agents.** A lights-out factory runs in the dark because its safety systems are trusted. lights-out is that system, the guardrails for agents that write and ship code, with Claude Code, Gemini CLI or Codex at the keyboard.

**Autonomous agents take a ticket to production on a codebase you do not trust. Numeric gates decide, the ticket's perimeter holds, a failing canary rolls itself back, and one human approves the last step.**

It runs complete on your laptop: no account, no API key, no cloud.

| | |
|---|---|
| **Eight gates on every PR** | risk, secrets, weakened tests, dependencies, static analysis, tests, mutation, and test-proof: a test the change adds must fail on the base. Each prints one line, `GO` or `NO-GO` with the reason, and every threshold says where it comes from. A PR is judged by its base's policy, never its own: the principle GitHub applies to [`pull_request_target`](https://github.blog/changelog/2025-11-07-actions-pull_request_target-and-environment-branch-protections-changes/). GitHub takes the default branch, lights-out the PR's base commit, the same thing for a PR into the default branch. |
| **A perimeter per ticket** | the diff must stay inside the files the ticket declares, checked before a PR exists. A ticket whose files another open ticket holds waits at assign. |
| **The dev phase, visible** | a request becomes questions, answers and a spec, and the spec gate checks its criteria before an agent starts. The console shows every ticket on a kanban and on one timeline, from the request to the release, and says on each whose turn it is. |
| **Main stays green, without a queue** | a PR merges without a second judgment when nothing moved under its perimeter. Main is judged again after merging; a red main is bisected and the culprit reverted, and its ticket goes back to rework. |
| **A canary that rolls itself back** | one signed image per merge, 5% then 25% of real traffic, compared with stable on errors and p95 latency. A bad number rolls it back with nobody paged. |
| **One human gate** | the step to 100% waits for a person's GO. Silence never promotes. It has the semantics of Argo Rollouts' [indefinite pause](https://argo-rollouts.readthedocs.io/en/stable/features/canary/) and Flagger's [confirm-promotion webhook](https://docs.flagger.app/usage/webhooks), as the single human gate of a chain from ticket to production. |
| **Your model, your coding CLI** | Gemini, Claude, OpenAI or GLM by Z.ai, through `gemini-cli`, `claude-code` or `codex`, all behind the same guard ([coding harnesses](docs/reference.md#coding-harnesses)). |
| **Ops, too** | an alert becomes ranked hypotheses; an incident becomes a post mortem anchored to the audit chain, with each action item a ticket. |

What is proven and what is not yet is said plainly below, in [What is real and what is recorded](#what-is-real-and-what-is-recorded).

<p><img src="https://nicolaguglielmi.github.io/lights-out/media/console-kanban.jpg" alt="The console's kanban: lists for the tower's states under four bands, Spec, Build, Test and review, Release, a card per ticket with its labels, whose turn it is and who has the ball"></p>
<p><img src="https://nicolaguglielmi.github.io/lights-out/media/console-ticket.jpg" alt="A ticket's timeline in the console: the request, a question, the answer, the confirmed criteria and the spec gate" width="49%"> <img src="https://nicolaguglielmi.github.io/lights-out/media/console-overview.jpg" alt="The console's overview: what waits for a person first, then the tickets by band and state, perimeters held, docs to realign" width="49%"></p>

<p><a href="https://nicolaguglielmi.github.io/lights-out/media/network.mp4"><img src="https://nicolaguglielmi.github.io/lights-out/media/network.jpg" alt="The console's network screen: the tower, the roles and the dev-worker's workers, the gates on the edges lit GO or NO-GO, each attempt's patch with the gate lines that judged it, and the files by perimeter; a replay of a recorded ticket"></a></p>

The console (`./go console`): the kanban, a ticket's timeline from the request to the spec gate, the overview, the agent network (`./go network` prints the same in the terminal; the image opens a replay), and the interactive `#hive` guided control center + `<dialog id="onboard-wizard">` onboarding modal ([ADR 0111](docs/adr/0111-hive-guided-onboarding-ui-and-proxy-recipes.md)). The overview also shows the four delivery measures and the spend per ticket, role and day, in the words `./go dora` and `./go spend` print. Every card, card's back and timeline says whose turn it is, a role's or yours, and when it is yours, the one thing to do: answer a question, go-for-launch, or decide after the last attempt; the overview lists those first, and the kanban's "Your turn only" keeps only those. A card carries its priority and feature as labels and badges for who has the ball, how long it has sat in its state and its rework attempts; it turns over, above the board, to its timeline in a few lines. It moves only when the tower moves its ticket. A network screen draws one ticket's agents as the harness governs them: the tower, the six roles, the workers with their harness and model, each gate lighting GO or NO-GO, the rework loop and the human gate, beside every attempt's gate lines and patch and the files by perimeter; `./go network` prints the same facts in text. It also has a specs screen, an editor for a new request that the spec gate checks as you type, a search over the project's ADRs from the project map, and a light and a dark theme. Rendered in CI over the logbook the console's tests build ([`fixtures/logbook/golden-1.6.json`](fixtures/logbook/golden-1.6.json)), which holds tickets in more states than one demo leaves; its model is a stub. After `./go demo`, the console shows ticket 0001 from the request to the rollback.

## Quick start

**You need** podman (or Docker on Linux) and git. Nothing else: no account, no key, no cloud.

**Or nothing installed:** [![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/nicolaguglielmi/lights-out?quickstart=1) runs the same `./go demo` in your browser, with docker inside the codespace ([ADR 0097](docs/adr/0097-from-zero-to-hero-and-a-start-with-nothing-installed.md)). It needs a GitHub account and uses your account's Codespaces quota: it asks for a 4-core machine, which spends 4 of the 120 core hours a Free account gets each month for every hour it runs. Run `./go demo` in its terminal, then `./go console` and open the forwarded port 8098: the demo's `file://` link does not open from a codespace. The `stranger` workflow runs the demo in this dev container on every push: `GO: demo, milestone M9, 669s to rollback` on a GitHub runner, plus about a minute to build the container ([stranger run 43968199663](https://github.com/nicolaguglielmi/lights-out/actions/runs/43968199663)); no codespace of the project's own has run it. The way that needs no account at all stays the three commands below.

| Your machine | Install once |
|---|---|
| macOS | `brew install podman`, then `podman machine init --cpus 4 --memory 6144` |
| Linux | `sudo apt-get install -y podman` (or `dnf`), then `systemctl --user enable --now podman.socket` |
| Docker on Linux | start Docker, and put `CONTAINER_ENGINE=docker` before every `./go` |

**Run**

```sh
git clone https://github.com/nicolaguglielmi/lights-out.git
cd lights-out
./go demo
```

`./go demo` checks your machine first: it starts a stopped podman machine, and moves a port another program holds to a free one and tells you which ([ADR 0093](docs/adr/0093-the-first-run-flawless.md)). Then one line per step:

```
lights-out demo: a ticket goes from a request to a canary that rolls itself back, at L0, with no key. About 6 min 10 s on this machine.
[1/7] the engine, memory, disk and ports (about 1 s) ...
      8080 is held by python3 (pid 2693): the app moves to 8081 (APP_PORT in .crew/ports.env)
      3000 is held by python3 (pid 2692): the code host moves to 3001 (CODE_HOST_PORT in .crew/ports.env)
      done, 1 s
[2/7] the toolchain image, built once per revision (about 30 s) ... 49 s
[3/7] the plane: code host, delivery, metrics, flags, registry and the app (about 71 s) ... 1 min 33 s
[4/7] a canary with a failing feature takes 5% of the traffic (about 63 s) ...
      A canary at 5% was judged and rolled back by itself, nobody paged: error_rate canary(0.2.0-waitlist)=1.000 stable(0.1.0-m2)=0.000 delta=1.000 max=0.010
      done, 64 s
[5/7] ticket 0001: request, spec, pull request, eight gates, merge, canary (about 3 min 24 s) ...
      Ticket 0001: requested, specified, a pull request the eight gates passed, merged, released at 5%, rolled back by itself, audited
      done, 3 min 53 s
[6/7] the repository's own checks, run beside the plane (about 0 s) ... 37 s
[7/7] this run's console, frozen into one page (about 1 s) ...
      See why, step by step: file:///home/runner/work/lights-out/lights-out/lights-out/.crew/demo/console.html#ticket/0001
      Next: ./go console serves the live console on localhost:8098; ./go try <path> runs the gates on a repository of yours
      The plane is still up: the app at http://localhost:8081, Gitea at http://localhost:3001, Prometheus at http://localhost:9090; ./go down stops it
      done, 1 s
GO: demo, milestone M9, 480s to rollback (budget 900s, plan §2)
```

At the end it opens the run's own console on ticket 0001: the request, the spec gate, the pull request and its eight gates, the merge, the canary at 5%, and the rollback with its reason. [The same page for the latest run on main](https://nicolaguglielmi.github.io/lights-out/console/snapshot.html#ticket/0001) needs nothing installed. `./go console` serves the live one.

That run took 480 s on a clean GitHub runner, cold, with the toolchain image built in the run and ports 3000 and 8080 held by other servers ([stranger run 43841491822](https://github.com/nicolaguglielmi/lights-out/actions/runs/43841491822)); before ADR 0093 the same run took 700–805 s. The next run on the same machine estimates from this one. Everything the tools print is in `.crew/demo/run.log`; `LIGHTS_OUT_VERBOSE=1 ./go demo` shows it as it goes.

**Then, on your own repository:** `./go try ~/code/your-repo` drafts its contract, runs the eight gates on its last commit, and prints the three commands that keep the draft ([gates on your repository](#gates-on-your-repository)).

**Shorter looks**

```sh
./go start           # dual-mode guided setup wizard (Junior/Expert), discovers cloud/ops defaults, writes non-secret .crew/start-plan.env (ADR 0103, 0108)
./go next            # inspect repo, contract, tickets and logbook; print the exact next step ([MODE=junior|expert] [--json])
./go cloud-setup     # stage Terraform + OIDC workflows for a cloud Delivery plugin: CLOUD=<provider> [SLO_MODE=proxy|otel] (ADR 0104, 0107)
./go registry-conformance  # six-case Registry suite on REGISTRY (zot or oci-registry; ADR 0107)
./go flags-conformance     # six-case Flags suite on FLAGS (flags-file, flags-http, or flagd; ADR 0107)
./go corpus          # verify 24 unknown-repository shapes across Python, JS/TS, Go, Rust, Java, monorepos and stateful templates (ADR 0106, 0110)
./go watch           # continuous tower orchestrator draining EVENT_BUS: [ONCE=1] [POLL_S=<sec>] (ADR 0109)
./go alert-webhook   # normalize an alert JSON payload or serve fail-closed HTTP webhook on localhost:8097: [ALERT_FILE=<json>] (ADR 0109)
./go auto-incident   # generate deterministic incident JSON from rolled-back .crew/release.json (ADR 0109)
./go demo-seam-plain # attach uninstrumented workloads/plain and prove proxy SLO rollback with no app /metrics (ADR 0098)
./go demo-triage     # an alert becomes ranked hypotheses, 3 seconds, nothing to start
./go demo-mainline   # main stays green without a queue: a moved perimeter goes to rework, a red main is bisected and the culprit reverted
./go incident        # build an incident document from the last release record: NAME= FROM= TO= [LOGS=<file>] [TICKET=<id>]
./go postmortem INCIDENT=<file>   # an incident document becomes a post mortem and tickets
./go demo-postmortem # replay the post mortem of the demo-rollback incident (needs a recording, see below)
./go record-postmortem PROFILE=l1 # record that answer once, with your Gemini key
./go up              # start the local plane (about 15 s once downloaded)
./go demo-rollback   # just the self-rollback, about a minute
./go demo-dast       # the passive DAST scan at staging both ways: a leaky canary rolled back; pulls a 1.2 GB scanner, off in ./go demo
./go board           # the pipeline explained, and live while the plane runs: localhost:8099
./go map             # the project map: tickets, files, tests, ADRs and pages of one commit, in .crew/map.sqlite
./go console         # the client's view: kanban (whose turn on every card), specs, a new-request editor, ADR search, each ticket's timeline from request to release, post mortems, thresholds, go-for-launch, and the #journey checklist: localhost:8098
./go request TICKET=<id>   # start a ticket before the code: the product role asks or confirms, then the spec gate
./go answer TICKET=<id> ANSWER="<text>"   # answer its open question, then the product role again
./go down            # stop everything
```

**Ops without the plane**

An incident document holds the alerts, the release record, a log excerpt and the time window. `./go postmortem` turns it into a post mortem in `.crew/postmortem/<name>/`. Each timeline entry is anchored to an id the document holds (the release, a stage, a log line, an alert, and the PR or ticket when the tower released it), and core prints that evidence beside it. Every action item is filed as a ticket in `tickets/`. The ticket has no perimeter: a human adds one before `./go assign`. An anchored timeline is not a true one: the check stops invented releases and log lines, not a wrong story about real ones.

## From zero to hero

Seven levels, each asking a little more than the one before, each ending on the one line that proves you got there. [The tutorial](docs/tutorial.md#from-zero-to-hero) opens with them, every command and output from a real run.

| Level | What you do | Time |
|---|---|---|
| [0. See it](docs/tutorial.md#level-0-see-it) | [The frozen console](https://nicolaguglielmi.github.io/lights-out/console/snapshot.html#ticket/0001) of a real run: a canary rolled back by itself. Or [the site](https://nicolaguglielmi.github.io/lights-out/), [a video tour](https://nicolaguglielmi.github.io/lights-out/media/overview.mp4) and [the board](board/index.html) | 1 min |
| [1. Run the demo](docs/tutorial.md#level-1-run-the-demo) | `./go demo`, [above](#quick-start): the same, on your machine or in a codespace | about 10 min |
| [2. The gates on your repository](docs/tutorial.md#level-2-the-gates-on-your-repository) | `./go try`, or the Action on a pull request ([below](#gates-on-your-repository)) | about 15 min |
| [3. Your thresholds, a ticket, the intake](docs/tutorial.md#level-3-your-thresholds-a-ticket-the-intake) | `policy.env`, a ticket's perimeter, `./go intake` | about 30 min |
| [4. Your service in the plane](docs/tutorial.md#level-4-your-service-in-the-plane) | a canary of your code, rolled back by itself | 1–2 h |
| [5. Live agents at L1](docs/tutorial.md#level-5-live-agents-at-l1) | agents on your tickets, with a budget and the provider you choose ([below](#agents-on-your-repository)) | about 30 min |
| [6. Your CI/CD](docs/tutorial.md#level-6-your-cicd) | Flagger, GitLab CI, Jenkins ([below](#toward-production)) | it depends |

## Gates on your repository

Most people arrive from [the Action on the Marketplace](https://github.com/marketplace/actions/lights-out-gates). It posts the gates on every pull request as one check run, with one line per gate in its summary:

```yaml
permissions:
  contents: read
  checks: write
steps:
  - uses: actions/checkout@v7
    with:
      fetch-depth: 0
  - uses: nicolaguglielmi/lights-out@v1.0.0
```

The gates read a contract committed in your repository, `.lights-out/`: the test command, the mutation scope and baseline, the runners. A range is judged by the contract at its base, so a pull request cannot loosen what judges it. On a repository with none, the first check is NO-GO, because nothing may judge without a contract at the base ([ADR 0064](docs/adr/0064-the-first-run-drafts-the-contract.md)). After 0.3.0 that check also carries the fix: the Action drafts the contract from the base with `lights-out init`, puts its three small files in the check's summary for review, and attaches the whole draft to the run as an artifact, with the two commands that commit it. One commit, and the next check is judged. The 0.3.0 check prints the NO-GO line only; until the next release, draft the contract with the wrapper below.

`gates: test-proof` or `gates: test-integrity` runs one part. Those two are also standalone Actions, `nicolaguglielmi/lights-out/actions/test-proof@v1.0.0` and `nicolaguglielmi/lights-out/actions/test-integrity@v1.0.0`; each also has a repository of its own for its Marketplace page, `nicolaguglielmi/test-proof@v1.0.0` and `nicolaguglielmi/test-integrity@v1.0.0`. [The guide](docs/use-on-your-repo.md#the-action) has the details, and [the hook recipes](docs/use-on-your-repo.md#hook-recipes-for-coding-agents) run the gates before a coding agent commits.

On a pull request of a repository that is not lights-out ([the probe](https://github.com/nicolaguglielmi/lights-out-probe/pull/1)), each in a job of its own so that the first pull of the image is inside the time, the Action posted its check in 27 s, `test-proof` in 14 s and `test-integrity` in 25 s.

On your machine, the `lights-out` wrapper runs init and the gates from the gates image, `ghcr.io/nicolaguglielmi/lights-out-gates:1.0.0` (amd64 and arm64), inside your repository. You need podman or docker, and git. The image is 508 MB unpacked (amd64), with no cloud toolchain in it. `lights-out init` and `gates` took 48 s on a clean runner (`./go demo-package`). The full image, `ghcr.io/nicolaguglielmi/lights-out:1.0.0` (1,246 MB, 1.77 GB before gcloud and terraform left it), runs `triage`, `postmortem`, the socket-free state commands (`next`, `dora`, `spend`, `cloud-setup`) and the opt-in plane and workflow commands ([ADR 0105](docs/adr/0105-plane-and-workflow-from-wrapper.md)).

Version 0.3.0 was published on 2026-11-09 by the tag `v0.3.0`. From a clone, `./go package-gates` builds the same image, and `LIGHTS_OUT_IMAGE=localhost/lights-out-gates:dev` points the wrapper at it.

One command installs both the `lights-out` wrapper (for the container gates) and `lights-out-gates` (for the light tier, Python 3.11+):

```sh
uv tool install lights-out-gates
```

From `0.9.0`, PyPI `lights-out-gates` ships both `lights-out` and `lights-out-gates` together (install from `git+https://github.com/nicolaguglielmi/lights-out` for unreleased commits). Without Python, or when you want only the POSIX `sh` wrapper:

```sh
mkdir -p ~/.local/bin   # any directory on your PATH
curl -fsSL -o ~/.local/bin/lights-out https://raw.githubusercontent.com/nicolaguglielmi/lights-out/v1.0.0/lights-out
chmod +x ~/.local/bin/lights-out
```

```sh
cd ~/code/your-repo
lights-out doctor                  # engine, git, image, key: one line each
lights-out init                    # drafts .lights-out/ outside your repository
cp -R ~/.local/state/lights-out/your-repo/.lights-out/. .lights-out/
git add .lights-out && git commit -m "add lights-out contract"
lights-out gates                   # judges the last commit
lights-out test-proof              # only: the tests a change adds or changes must fail on its base
lights-out test-integrity          # only: no test weakened, the changed lines' mutants killed
```

Each command prints one line, named for itself. The images are signed keyless with Sigstore, through GitHub's OIDC token. There is no key to trust. To check one:

```sh
cosign verify ghcr.io/nicolaguglielmi/lights-out-gates:1.0.0 \
  --certificate-identity-regexp '^https://github.com/nicolaguglielmi/lights-out/.github/workflows/package.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Tags are mutable. To pin the image, set `LIGHTS_OUT_IMAGE` to its digest (in the Action, the `image` input). `lights-out init` prints where the draft is. `LIGHTS_OUT_OUT` moves it. Keep the `/.` in the `cp` command: without it, `cp -R` into an existing `.lights-out/` nests the draft one level deeper. Your repository is always mounted read-only. For `init`, `gates`, `test-proof`, `test-integrity`, `triage`, `postmortem` and the state commands (`next`, `dora`, `spend`, `cloud-setup`), the wrapper never mounts your engine's socket (`gates` works with `TOOLCHAIN_IMAGE=workbench` only; `compose:` or image forms are judged from a clone with `./go gates`). The 12 opt-in plane and workflow commands (`start`, `intake`, `answer`, `intake-confirm`, `assign`, `up`, `down`, `smoke`, `release`, `promote`, `console`, `evidence`) explicitly mount the engine socket with a one-line notice on stderr and stage a host-mirrored platform workspace ([ADR 0105](docs/adr/0105-plane-and-workflow-from-wrapper.md)). `lights-out gates` judges the last commit, a range (`BASE=` and `HEAD=`), or what is staged (`STAGED=1`).

Without an engine, `lights-out-gates --light` runs the gates that need only git and Python in under a second from the same `lights-out-gates` package on PyPI, or `uvx lights-out-gates --light` with no install at all, and its line names the four that need the image ([ADR 0061](docs/adr/0061-gates-without-a-container.md), [ADR 0099](docs/adr/0099-one-install-via-lights-out-gates.md), [the light hook recipes](docs/use-on-your-repo.md#hook-recipes-for-coding-agents)). With git alone, [a `pre-commit` and a `pre-push` hook](docs/use-on-your-repo.md#plain-git-hooks) run the light tier before each commit and the full gates before each push. In Claude Code, the plugin brings the same gates as `/lights-out:gates`, a pre-commit hook and a read-only MCP server over the project map: `claude plugin marketplace add nicolaguglielmi/lights-out`, then `claude plugin install lights-out@lights-out` ([ADR 0069](docs/adr/0069-the-agents-own-toolbelt.md), [the plugin](docs/use-on-your-repo.md#the-claude-code-plugin)). Codex and Gemini CLI get the same two commands and the hook: [the Codex plugin](docs/use-on-your-repo.md#the-codex-plugin), [the Gemini CLI extension](docs/use-on-your-repo.md#the-gemini-cli-extension).

## Agents on your repository

From a clone of lights-out (or directly via `lights-out start` / `lights-out next`):

```sh
./go start                   # dual-mode Junior/Expert setup wizard (ADR 0103)
./go next                    # inspect current state and print the exact next command
./go init ~/code/your-repo   # attaches a clone of HEAD and drafts .lights-out/ in the clone
./go gates                   # judges the last commit: no plane, no key, no engine socket when the toolchain is the workbench
```

`./go init` never writes to your repository (or target a monorepo sub-service via `INIT_SERVICE=<subdir>`). It infers the stack from the repository files — Python (`uv.lock` with `pytest` or stdlib `unittest`, `poetry.lock`, or pinned `requirements.txt`), JavaScript (`npm`, `pnpm`, `yarn`, `bun`), TypeScript (`tsconfig.json`), Go (`go.mod` + `go.sum`), Rust (`Cargo.toml` + `Cargo.lock`), or Java (Maven/Gradle) ([ADR 0102](docs/adr/0102-more-stacks-go-typescript-python-fallbacks.md), [ADR 0110](docs/adr/0110-stateful-monorepo-rust-java-and-js-managers.md)) — and writes `workload.env` with a provenance line over every value, plus `worker.md`, `policy.env` and the stack's runners. When a `Containerfile` or `Dockerfile` is present (under `INIT_SERVICE` or root), it also drafts the plane half (`compose.yaml` with commented stateful dependency hints from `.env.example`/compose files, `flags/flags.json`, `SMOKE_AUTH_HEADER`-aware `smoke.sh` and `load.sh`, and commented `crew-egress.txt.example`, [ADR 0100](docs/adr/0100-init-drafts-the-plane-half.md), [ADR 0110](docs/adr/0110-stateful-monorepo-rust-java-and-js-managers.md)). When `lights-out gates` runs from the container wrapper, default `gates` stay socket-free while `--engine` opts into `compose:`/`docker` toolchains ([ADR 0108](docs/adr/0108-start-plan-wiring-wrapper-engine-gates-and-promote-clarity.md)). It measures `MUTATION_BASELINE` by running the mutation command over the whole scope. Its last line gives the stack, the draft's location and the baseline. Copy `.attached/.lights-out/` into your repository and commit it. During `./go attach`, `core/remote.py` also detects `origin` SSH/HTTPS remotes for GitHub, GitLab and Gitea ([ADR 0101](docs/adr/0101-origin-remote-as-codehost.md)). `./go corpus` verifies 24 unknown-repository shapes continuously in `./go ci` ([ADR 0106](docs/adr/0106-unknown-repository-corpus.md), [ADR 0110](docs/adr/0110-stateful-monorepo-rust-java-and-js-managers.md)).

A toolchain gate's GO line says where it ran: `in workbench`, or the image or service the contract names. On the workbench, `./go gates` runs your repository's tests in lights-out's toolchain container, on this machine. Attach only a repository you would run here.

The plane comes next: `up`, `smoke`, `verify` and rollback on your repository, using the proxy SLO path without requiring app `/metrics` or `target_info` ([ADR 0098](docs/adr/0098-verify-from-the-proxy.md)).

- **Works:** the eight gates on the gates half alone. With the plane half, the canary and its rollback on your stable image, judged by the thresholds you set in `.lights-out/policy.env`. A change to `.lights-out/` never merges on its own.
- **Time:** `./go demo-init` proves init and gates in under 300 s on two small example repositories. Init measures the whole scope, so a large suite takes longer.
- **Needs a model key.** There are no recordings of your code, so on your repository the agents run at L1 only, and say so at L0: `GEMINI_API_KEY` for the coding roles (the free tier), or `AGENT_HARNESS=claude-code` with `ANTHROPIC_API_KEY`, or `AGENT_HARNESS=codex` with `OPENAI_API_KEY`, or `AGENT_HARNESS=claude-code MODEL_PROVIDER=zai` with `ZAI_API_KEY` (with `MODEL_PROVIDER` and `MODEL`; the ADK roles take the same keys). The dev-worker may spend up to 2.6M tokens per run (`roles/dev-worker/role.env`).

Two more optional files shape the agents (ADR 0073): `test-reviewer.md` and `architect.md` in its `roles/` add instructions to a reviewer, and `mcp.json` gives the dev-worker MCP servers, each tool named. Both are read from the base and checked by `./go agents`; see [Customize the agents](docs/use-on-your-repo.md#agents).

[The guide](docs/use-on-your-repo.md) says what `.lights-out/` holds, what each key says, what the plane needs, how to write tickets, the hook recipes for coding agents and the GitHub Action.

## Toward production

What exists today, each opt-in, none yet run end to end on a repository of yours:

- **Pull requests on GitHub.** A merged change is built from your repository's own Containerfile, signed and released on the local plane. By default merges land on the plane's Gitea and you `git fetch` them back; with `CODE_HOST=github` and a token in `.env` the pull request lands on your GitHub repository instead ([ADR 0043](docs/adr/0043-github-code-host-and-attached-release.md)). There is no deploy to your production.
- **Delivery on Kubernetes.** If you already ship with Argo Rollouts, `DELIVERY=argo-rollouts` drives your Rollout with kubectl, stage by stage, instead of Envoy. It needs a traffic router and a Rollout with the steps [its README](adapters/argo-rollouts/README.md) lists. `./go argo-conformance` proves it with the same conformance suite, on a k3s node beside the plane; under podman that needs a rootful engine ([ADR 0051](docs/adr/0051-delivery-on-kubernetes-with-argo-rollouts.md)).
- **Delivery with Flagger.** `DELIVERY=flagger` keeps Flagger's own analysis and its automatic rollback, and adds what Flagger lacks: the launchpad opens each step through Flagger's `confirm-traffic-increase` webhook, and 100% waits for go-for-launch. Flagger's own promotion, the canary copied into the primary, is held at `/gate/halt` until a person changes it. `./go flagger-conformance` runs the same suite on the same k3s node; `./go flagger-proof` shows Flagger rolling a failing canary back by itself and its promotion held at the gate ([its README](adapters/flagger/README.md), [ADR 0095](docs/adr/0095-flagger-under-the-launchpads-gates-and-two-more-ci-surfaces.md)).
- **An adapter of your own, outside the repository.** Set `ADAPTERS_PATH=<absolute dir>` in `.env` and a port to `outside/<name>`, for example `STATE_STORE=outside/json-files`. It runs only if that port's conformance suite said GO on its exact bytes, recorded in `.crew/adapters/register` on this checkout; one changed byte and it is refused again, in one line. Wired ports: `CODE_HOST`, `DELIVERY`, `STATE_STORE`, `EVENT_BUS`, `REGISTRY`, `FLAGS`. Proven in `./go ci` (`ci/outside.sh`) for `STATE_STORE`, `DELIVERY`, `CODE_HOST`, `REGISTRY` (`fixtures/outside-adapters/file-registry/`), and `FLAGS` (`fixtures/outside-adapters/file-flags/`). Not proven: an outside `EVENT_BUS`. Left: an outside `AGENT_HARNESS`, which is refused ([ADR 0058](docs/adr/0058-outside-adapters-and-other-cis-as-surfaces.md), [ADR 0104](docs/adr/0104-cloud-delivery-plugins-and-outside-conformance.md), [ADR 0107](docs/adr/0107-cloud-registry-flags-and-proxy-slos.md)).
- **Cloud delivery plugins & E2E bridge (`plugins/cloud/`).** Four installable `outside/*` Delivery adapters (`gcp-cloud-deploy`, `aws-ecs-codedeploy`, `azure-container-apps`, `hetzner-k3s`) with Terraform starters, keyless OIDC workflows, proxy ingress SLO templates (`plugins/cloud/*/slo-ingress.promql`), and `./go cloud-setup CLOUD=<provider> SLO_MODE=proxy`. Pair them with generic remote OCI image publishing (`REGISTRY=oci-registry`, `./go registry-conformance`) and daemonless/HTTP flags (`FLAGS=flags-file` / `FLAGS=flags-http`, `./go flags-conformance`). Each adapter is contract-tested locally against stub CLIs and mock HTTP servers (`ci/tests/test_cloud_plugins.py`, `ci/tests/test_oci_registry_and_flags.py`); live remote cloud/registry conformance runs require your own cloud account and OIDC configuration ([ADR 0104](docs/adr/0104-cloud-delivery-plugins-and-outside-conformance.md), [ADR 0107](docs/adr/0107-cloud-registry-flags-and-proxy-slos.md)).
- **Continuous ops loop & alert webhooks.** `./go watch` (`core/runner.py`) polls `EVENT_BUS` continuously with `--once` or `--poll-s` and graceful `SIGTERM` handling; `./go alert-webhook` (`core/webhook.py`) normalizes Alertmanager/generic alerts or serves a fail-closed webhook endpoint requiring `ALERT_WEBHOOK_TOKEN` off loopback; `./go auto-incident` and `AUTO_INCIDENT_ON_ROLLBACK=on` write deterministic rollback incidents from `.crew/release.json`. Trigger-ready orchestrator recipes live at `surfaces/github-actions/tower-orchestrator.yml`, `surfaces/gitlab-ci/tower-orchestrator.yml`, and `surfaces/alerts/README.md` ([ADR 0109](docs/adr/0109-continuous-orchestration-auto-incident-and-alert-webhook.md)).
- **Production OIDC console proxy recipes (`deploy/proxy/`).** Ready-to-wire `Caddyfile`, `oauth2-proxy.cfg`, and `cloudflare-access.md` templates set the trusted identity header for `CONSOLE_AUTH=proxy` without holding tokens in the console itself; live remote proxy deployments remain unexecuted until hosted by the user ([ADR 0111](docs/adr/0111-hive-guided-onboarding-ui-and-proxy-recipes.md)).
- **GitLab as a code host.** `CODE_HOST=gitlab` ([`adapters/gitlab/`](adapters/gitlab/), GitLab CE 19.5.5-ce.0, plus remote GitLab support via `GITLAB_PROJECT_PATH` and `GITLAB_TOKEN` over stdin when `./go attach` detects a GitLab `origin`, [ADR 0101](docs/adr/0101-origin-remote-as-codehost.md)). `./go gitlab-conformance` starts GitLab in a compose project of its own, runs the ten cases and removes it. It took about 224 s to serve and held 2.4 GB resident (measured 2026-11-13). The full tower on GitLab is not proven: it does not fit beside the plane on a 4.5 GiB VM.
- **Jenkins as a surface.** [`surfaces/jenkins/Jenkinsfile`](surfaces/jenkins/Jenkinsfile) runs `lights-out gates` on a Jenkins agent and posts the line as the commit status `lights-out/jenkins`. The tower never reads it. `./go demo-jenkins` proves it with Jenkinsfile Runner against a Gitea of its own. Not proven on a real Jenkins controller.
- **GitLab CI and CircleCI as surfaces.** [`surfaces/gitlab-ci/.gitlab-ci.yml`](surfaces/gitlab-ci/.gitlab-ci.yml) posts `lights-out/gitlab-ci` on merge requests; `./go demo-gitlab-ci` proves it with a real gitlab-runner on a local GitLab (a `docker:dind` runner not proven). [`surfaces/circleci/config.yml`](surfaces/circleci/config.yml) posts `lights-out/circleci`; `./go demo-circleci` validates it and runs its step against a local Gitea PR, which is not a CircleCI run: `circleci local execute` cannot run its machine job ([ADR 0095](docs/adr/0095-flagger-under-the-launchpads-gates-and-two-more-ci-surfaces.md)).
- **Your metrics.** `./go verify METRICS_URL=<any Prometheus-compatible endpoint> CANARY_VERSION=<v> STABLE_VERSION=<s>` judges your own canary with the active `slo.promql` (`verify/slo.promql` expects Envoy upstream cluster metrics `envoy_cluster_upstream_rq_*` with `envoy_cluster_name="stable"|"canary"`; pass `SLO_PROMQL=` or `DELIVERY=argo-rollouts` for OTel HTTP semconv histograms joined to `target_info{service_version}`). On other metrics the answer is NO-GO with "no data". An auth header goes in `.env` as `METRICS_AUTH_HEADER`.

Two commands say how far you are:

```sh
./go doctor --level l2        # what L2 still lacks, one line per item, then NO-GO or GO
./go production-readiness     # GO only when all four hold
```

`./go production-readiness` is GO only with all four:

- **Delivery:** the adapter in `DELIVERY` has a `rollback.sh` and passed its conformance suite here, for the adapter's files as they are now.
- **Metrics:** `METRICS=promql`, and `METRICS_URL` answers the readiness count query derived from the active `slo.promql` (`count(envoy_cluster_upstream_rq_completed)` by default, or `count(target_info)` when `target_info` appears).
- **Secrets:** `.env` holds no value for a key ending `_KEY`, `_TOKEN`, `_SECRET` or `_PASSWORD`, nor for `METRICS_AUTH_HEADER`. `./go` passes those keys from your shell's environment, by name.
- **Human gate:** `GO_FOR_LAUNCH` is not preset, and the console runs with `CONSOLE_AUTH=proxy`, a `CONSOLE_IDENTITY_HEADER` and named `CONSOLE_LAUNCHERS`.

At L0 it is NO-GO by construction. [Toward production](docs/production.md) has every key behind these decisions, its default and where the default comes from.

## If something goes wrong

Run `./go doctor` first. It prints one line per item: the engine and whether it answers, the podman API socket, git, whether the workbench image is built, whether the plane's ports (3000, 5050, 8016, 9090, 9901 and `APP_PORT`, or where `.crew/ports.env` moved them; 8085 and 8086 too with the emulators) are free or held by the lights-out plane, and whether the key your profile needs is set in `.env` (the name, never the value). It ends with `GO: doctor` or `NO-GO: doctor — <items>`. `./go` also checks your engine before it builds anything and prints the command that fixes what is missing. The other cases:

| You see | Do this |
|---|---|
| `preflight: ports  3000 is held by …: the code host moves to 3001` | Nothing to do: `./go demo` and `./go up` moved it, and wrote it to `.crew/ports.env`. A port you set in `.env` (`APP_PORT`, `CODE_HOST_PORT`, `METRICS_PORT`, `DELIVERY_ADMIN_PORT`, `FLAGS_PORT`) is never moved. |
| `NO-GO: preflight — port 5050 … cannot move` | The registry's port is part of every image's name. Stop what holds it (the line names it), then run again. |
| `NO-GO: preflight — a lights-out plane started from <dir> is running` | Run `./go down` in that directory first: one plane at a time. |
| `Cannot allocate memory`, or everything crawls | `podman machine stop`, `podman machine set --memory 6144`, `podman machine start` |
| `GO: demo … a first run: over the 900s budget, reported, not judged` | Everything worked; the first download and the toolchain build were slow. Your first run reports its time instead of failing on it. Later runs and CI are judged against the 900 s ([ADR 0013](docs/adr/0013-launchpad-canary-verify-rollback-at-l0.md), amendment 2026-11-04). |
| `NO-GO: demo — rollback reached, but … > 900s budget` | Everything worked, but a run after the first one took longer than 900 s. The engine is usually short of memory or CPU (see the row above), or other containers are running. `./go down`, then `./go demo` again. |
| `NO-GO: launchpad — …: cannot judge: …` | Prometheus could not answer a query, so the canary was rolled back without a verdict on it ([ADR 0066](docs/adr/0066-a-canary-that-cannot-be-judged.md)). `./go down`, then `./go demo` again; if it repeats, open an issue with the line. |
| Every `./go up` recreates every container (podman 4.9.3, the Ubuntu 24.04 package) | A bug of that podman's Docker-compatible API, not of your plane; the demo works with it ([ADR 0066](docs/adr/0066-a-canary-that-cannot-be-judged.md)). |
| Anything else | `./go down`, then `./go demo` again. Still stuck? Open an issue with `.crew/demo/run.log`. |

## What is real and what is recorded

- **Real:** the gates, the signed build, the canary, the metrics, the rollback and the audit chain. Nothing in them is simulated.
- **Recorded:** without a key no model is called. The agents replay answers recorded once against the Gemini API (`fixtures/`), and `./go demo-rework`, `./go demo-network` and `./go demo-mainline` replay patches written by hand and say `model none`. With a key and `PROFILE=l1` the agents run live. The first live run on Claude (`claude-code` 2.1.311, `claude-sonnet-5-5`, ticket 0001, 2026-11-04) is the gates doing their job: the three ADK roles passed their evals, and the coding role's change was stopped by the mutation gate, 5 of 8 mutants killed on the changed lines, 0.625 under the 0.666 threshold. Nothing merged. Then M24 ran ten small, low-risk tickets on tenacity, a repository not written for lights-out, once per provider: Anthropic (`claude-code`) merged 10 of 10, two through a mutation rework; Gemini (`gemini-cli`) merged 7, the other three stopped at `TOKEN_BUDGET` with no PR. One run each, no review, no human gate: not a benchmark (`./go scoreboard`, [the dataset](docs/scoreboard/m24-tenacity.json)).
- **Before the ticket, at L0:** `./go demo` now starts ticket 0001 at `requested`. The product role has no recording for that request, so it is not consulted: the timeline says so, takes the criteria the ticket states, and the spec gate judges them for real ([ADR 0055](docs/adr/0055-the-dev-phase-visible-from-request-to-release.md) §3). A question and its answer replay only for the one request the role was recorded on (`./go ci` proves it), and under replay the answer's text is not compared with the recording: any answer replays the recorded criteria. Recording the product role on ticket 0001 takes `./go record-evals PROFILE=l1` and a key.
- **lights-out judges itself:** every push and pull request here runs the Action on its own change, with the contract in [`.lights-out/`](.lights-out/workload.env) and a judge built from the base ([ADR 0049](docs/adr/0049-lights-out-judges-itself.md)). Risk, secrets and deps cover the whole repository; the test gate is `make ci`, the thirty self-checks; SAST and mutation cover `core/` only, about 5% of the code, against a measured baseline of 0.39. The shell, three quarters of the code, has no mutation score: there is no mutation tool for POSIX sh to run.
- **Where shell is allowed:** on the host (`./go`), in adapter verbs, in the gate wrapper that prints the one line, and in thin entry points; logic goes to Python under `core/` (a gate's under `core/gates/`), with unit tests from the ADRs. `ci/shell-boundary.sh` holds a shell file in `core/` and `gates/` to 30 code lines and names the files over it as debt that can only shrink ([ADR 0052](docs/adr/0052-where-shell-is-allowed.md), [ADR 0084](docs/adr/0084-gates-under-the-shell-boundary.md)).
- **The project map:** a SQLite file rebuilt from one commit at every merge, with no model ([ADR 0054](docs/adr/0054-the-project-map.md)). A merge that changes a path a page or an ADR cites files a docs ticket for the coding role, and `ci/docs-drift.sh` fails when a page cites a path, a `./go` command or a key that no longer exists. Putting the map's slice in the worker's brief is off by default (`MAP_BRIEF=off`) until the experiment on an outside repository measures whether it helps.
- **Not yet:** the GitHub Action is proven on one repository that is not lights-out, [a small probe](https://github.com/nicolaguglielmi/lights-out-probe/pull/1), with the image built in the job, not pulled; beyond tenacity, nothing is proven yet on a codebase that was not written for lights-out, and nothing there needed a review or the human gate; the coding harnesses' network stays open, because each must reach its provider. For plane commands `./go` hands its toolchain container your engine's socket, which means full control of the engine: run it where that is acceptable. The cloud track and the experimental harness are in [the reference](docs/reference.md#the-cloud-track).

Every item, level by level: [what is real and what is recorded, in full](docs/reference.md#what-is-real-and-what-is-recorded).

## Next

- [The tutorial: the demo read stage by stage, then your own repository](docs/tutorial.md)
- [How it works, every command, every level, the coding harnesses](docs/reference.md)
- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/adr/)
- [Contributing](CONTRIBUTING.md) and [roadmap](ROADMAP.md)

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).