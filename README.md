# lights-out

Six agents ship it. One human turns the lights back on.

**Autonomous agents take a ticket to production on a codebase you do not trust. Numeric gates decide, the ticket's perimeter holds, a failing canary rolls itself back, and one human approves the last step.**

It runs complete on your laptop: no account, no API key, no cloud.

## Try it

**1. Install a container engine (once)**

| Your machine | Run |
|---|---|
| macOS | `brew install podman`<br>`podman machine init --cpus 4 --memory 6144`<br>`podman machine start` |
| Linux | install podman, then<br>`systemctl --user enable --now podman.socket` |
| Docker on Linux | start Docker and put `CONTAINER_ENGINE=docker` before every `./go` (what CI runs) |

**2. Clone and run**

```sh
git clone https://github.com/nicolaguglielmi/lights-out.git
cd lights-out
./go demo
```

**3. Watch**

- An agent turns a ticket into a pull request.
- Six gates judge it: risk, secrets, dependencies, static analysis, tests, mutation.
- It merges, builds and signs a release, and sends it 5% of the traffic.
- Verify sees the errors and rolls the canary back on its own. No human involved.
- The run ends with `GO: demo`, then prints where to open the app, Gitea and Prometheus.

The first run downloads about 1.2 GB and builds the toolchain image (3–6 minutes), so expect 10–15 minutes. After that a run takes about 7 (measured: 434 s on a MacBook with podman).

**Shorter looks**

```sh
./go demo-triage     # an alert becomes ranked hypotheses, 3 seconds, nothing to start
./go incident        # build an incident document from the last release record: NAME= FROM= TO= [LOGS=<file>] [TICKET=<id>]
./go postmortem INCIDENT=<file>   # an incident document becomes a post mortem and tickets
./go demo-postmortem # replay the post mortem of the demo-rollback incident (needs a recording, see below)
./go record-postmortem PROFILE=l1 # record that answer once, with your Gemini key
./go up              # start the local plane (about 15 s once downloaded)
./go demo-rollback   # just the self-rollback, about a minute
./go board           # the pipeline explained, and live while the plane runs: localhost:8099
./go console         # the client's view: tickets, the audit chain, post mortems, thresholds, go-for-launch from a browser: localhost:8098
./go down            # stop everything
```

**Ops without the plane**

An incident document holds the alerts, the release record, a log excerpt and the time window. `./go postmortem` turns it into a post mortem in `.crew/postmortem/<name>/`. Each timeline entry is anchored to an id the document holds (the release, a stage, a log line, an alert, and the PR or ticket when the tower released it), and core prints that evidence beside it. Every action item is filed as a ticket in `tickets/`. The ticket has no perimeter: a human adds one before `./go assign`. An anchored timeline is not a true one: the check stops invented releases and log lines, not a wrong story about real ones.

To judge your own metrics, run `./go verify METRICS_URL=<any Prometheus-compatible endpoint> CANARY_VERSION=<v> STABLE_VERSION=<s>`. It uses the unchanged `verify/slo.promql`. "Any Prometheus-compatible endpoint" means one that answers `/api/v1/query` with the OTel HTTP semconv histogram joined to `target_info{service_version}`. On other metrics the answer is NO-GO with "no data". An auth header goes in `.env` as `METRICS_AUTH_HEADER`.

**On your repository, without cloning lights-out**

You need podman or docker, and git. The image is not published yet (see below).

```sh
cd ~/code/your-repo
lights-out doctor                  # engine, image, key: one line each
lights-out init                    # drafts .lights-out/ outside your repository
cp -R ~/.local/state/lights-out/your-repo/.lights-out/. .lights-out/
git add .lights-out && git commit -m "add lights-out contract"
lights-out gates                   # judges the last commit
```

`lights-out init` prints where the draft is. `LIGHTS_OUT_OUT` moves it. Keep the `/.` in the `cp` command: without it, `cp -R` into an existing `.lights-out/` nests the draft one level deeper. The wrapper mounts your repository read-only and never gives the image your engine's socket. `lights-out gates` judges the last commit, a range (`BASE=` and `HEAD=`), or what is staged (`STAGED=1`). It works with `TOOLCHAIN_IMAGE=workbench` only; a `compose:` or image form is judged from a clone with `./go gates`. [The guide](docs/use-on-your-repo.md) has the details, the hook recipes for coding agents and a GitHub Action.

Not published yet: the image `ghcr.io/nicolaguglielmi/lights-out:0.1.0` does not exist on any registry. The workflow `package.yml` builds and tests it on every push, and publishes only on a `v*` tag or a manual dispatch, which is the owner's act. The package is private until the owner makes it public, and the wrapper's raw-URL download works only once the repository is public. Until then, from a clone: `./go package` builds `localhost/lights-out:dev`, copy the file `lights-out` onto your PATH, and run `LIGHTS_OUT_IMAGE=localhost/lights-out:dev lights-out gates`.

## If something goes wrong

Run `./go doctor` first. It prints one line per item: the engine and whether it answers, the podman API socket, git, whether the workbench image is built, whether the plane's ports (3000, 5050, 8016, 8085, 8086, 9090, 9901 and `APP_PORT`) are free or held by the lights-out plane, and whether the key your profile needs is set in `.env` (the name, never the value). It ends with `GO: doctor` or `NO-GO: doctor — <items>`. `./go` also checks your engine before it builds anything and prints the command that fixes what is missing. The other cases:

| You see | Do this |
|---|---|
| A port is already in use | Put `APP_PORT=9000` in a file named `.env` for the app port. Ports 3000, 5050, 8016, 8085, 8086, 9090 and 9901 must be free. |
| `Cannot allocate memory`, or everything crawls | `podman machine stop`, `podman machine set --memory 6144`, `podman machine start` |
| `NO-GO: demo — rollback reached, but … > 900s budget` | Everything worked; the first download was slow. Run `./go demo` again. |
| Anything else | `./go down`, then `./go demo` again. Still stuck? Open an issue with the last 30 lines. |

## What is real and what is recorded

- **Real:** the gates, the signed build, the canary, the metrics, the rollback, the audit chain. Nothing in them is simulated.
- **Gates without the plane:** `./go demo-init` proves attach, init and gates on the two examples, `workloads/minimal` and `workloads/minimal-js`, with no plane and no engine for the draft's run. It does not prove the same on your repository.
- **Recorded:** no model is called without a key. The agents replay answers recorded once against the Gemini API (`fixtures/`), and the promotion brief at this level is rendered from the release record and labelled so. With `GEMINI_API_KEY` in `.env` and `PROFILE=l1`, the agents run live.
- **Hand-written (rework):** `./go demo-rework` sends ticket 0013's first attempt back to the worker with the gates' verdict (ADR 0038). No rework has been recorded against the Gemini API yet, so at L0 both attempts replay patches written by hand (`fixtures/0013/`, `hand_written: true`), and the run says `model none`. The gates that reject the first attempt and pass the second, the tower, the loop and the cap are real.
- **Hand-written (network):** `./go demo-network` splits ticket 0012 between two workers behind one harness port (ADR 0036). No network run has been recorded yet, so at L0 the two workers replay patches written by hand, and the run, the PR and the audit say `model none`. The governance around them (perimeters, gates, canary, rollback, audit) is real.
- **Recorded (post mortem):** `./go demo-postmortem` replays the ops-triage recording of the incident captured from a real `demo-rollback` run (`fixtures/incidents/`). It was recorded once at L1 on gemini-3.8-flash (`./go record-postmortem PROFILE=l1`, 2026-11-04), so `demo-postmortem` prints GO with a fix ticket and no key. `./go postmortem` on your own incident needs a key at L1 for the same reason as any agent on your code.
- **The image (built and tested, not published):** `./go demo-package` builds the image and runs `lights-out init` and `lights-out gates` from it on `workloads/minimal` as a repository of its own, in GitHub Actions. `ghcr.io/nicolaguglielmi/lights-out:0.1.0` is not published yet. The Action (`action/`) is proven in lights-out's own workflow only, not on another repository.
- **Local only:** at L0 merges happen on a Gitea inside the plane, never on this repository. At L1 an attached repository can use GitHub (ADR 0043). No GitHub answer is recorded and there is no fake GitHub; the adapter is proven against the real API only by a nightly that needs the owner's throwaway repository.
- **Your engine:** for plane commands, `./go` hands its toolchain container your engine's socket, which means full control of the engine. Run it where that is acceptable.
- **Cloud:** Google Cloud profiles are designed, not yet run.

## Coding harnesses

The coding roles run on one of three CLI harnesses, chosen by `AGENT_HARNESS`. Each has one provider, and `run.sh` refuses a mismatch with a line naming the harness that fits.

| Harness | Provider | Key variable | Guard | Boundary | Vendor sandbox |
|---|---|---|---|---|---|
| `gemini-cli` | `gemini` | `GEMINI_API_KEY` | `BeforeTool` hook | container | not used |
| `claude-code` (Claude Code 2.1.311) | `anthropic` | `ANTHROPIC_API_KEY` | `PreToolUse` hook | container | not used |
| `codex` (Codex 0.166.0) | `openai` | `OPENAI_API_KEY`, passed to the container as `CODEX_API_KEY` | `PreToolUse` hook | container | off (`danger-full-access`) |

The guard is the same `guard.sh`, copied byte-identical into each adapter. The container is the boundary for all three: no git credential, no engine socket, and only the run's directory and `/roles` (read-only) mounted. Codex's own sandbox is off because its bubblewrap cannot start in rootless podman. The network stays open for all three, because each must reach its provider. The hook denies the network tools, but that does not stop an interpreter from sending out what the container holds, the provider key included. Closing egress is an open question before M24 runs on a repository the owner does not control.

`MAX_TURNS`, `TOKEN_BUDGET` and `TIMEOUT_S` stop each harness, each read from the vendor's own signal. The budgets in `roles/` are Gemini measurements and bind every harness until recordings re-measure them.

`./go harness-conformance` runs the real pinned CLI in its real container against a scripted model (`gates/harness-conformance/stub.py`). It needs no key and no account. The model is simulated: the far end of the wire is a script, not a vendor. The suite proves a harness's plumbing in nine cases (guard, budgets, artefacts, served model, no key in artefacts). It does not prove a model's ability or the real vendor wire. It builds three CLI images (about 350 MB of binaries for codex on arm64, about 560 MB of `node_modules` for claude-code) and is not part of `./go demo`. `./go ci` runs its engine-free half, `harness-shape` and `hooks`. The full suite is its own target and a workflow job.

Found while building it, three of them older than M19, and closed: a repository's own `.env` could point gemini-cli's `GOOGLE_GEMINI_BASE_URL`, key included, at another host; a repository's own `.claude/settings.json` (`disableAllHooks`) or `.codex/config.toml` (`[features] hooks = false`) switched the guard off; the worker mounted all of `.crew/`, including the release signing key.

Recording with another harness is implemented and not yet run, because it spends the owner's keys:

```sh
./go record TICKET=0001 PROFILE=l1 AGENT_HARNESS=claude-code MODEL_PROVIDER=anthropic MODEL=claude-sonnet-5-5
./go record TICKET=0001 PROFILE=l1 AGENT_HARNESS=codex MODEL_PROVIDER=openai MODEL=gpt-6.1-sol
```

These write `fixtures/<ticket>/<role>/<harness>/`. L0 keeps replaying gemini-cli's `fixtures/<ticket>/<role>/`, and replay does not read the new directories yet. Claude and OpenAI cost money per run. The cost per provider will be stated here once those recordings exist.

## On your own codebase

```sh
./go init ~/code/your-repo   # attaches a clone of its HEAD and drafts the gates half of .lights-out/ in the clone
./go gates                   # judges the last commit: no plane, no key, no engine socket when the toolchain is the workbench
```

`./go init` never writes to your repository. It infers the stack from the files at the root (python or javascript), and writes `workload.env` with a provenance line over every value, plus `worker.md`, `policy.env` and the stack's runners. It measures `MUTATION_BASELINE` by running the mutation command over the whole scope. Its last line gives the stack, the draft's location and the baseline. Copy `.attached/.lights-out/` into your repository and commit it.

A toolchain gate's GO line says where it ran: `in workbench`, or the image or service the contract names. On the workbench, `./go gates` runs your repository's tests in lights-out's toolchain container, on this machine. Attach only a repository you would run here.

The plane comes next: attach, up, smoke, verify and rollback on your repository. It needs more of the contract: a compose file, a flag file, and smoke and load scripts. [The guide](docs/use-on-your-repo.md) says what each part is and what is proven.

What works today, and what does not yet:

- **Works:** the six gates on the gates half alone. With the plane half, the canary and its rollback on your stable image, judged by the thresholds you set in `.lights-out/policy.env`. A change to `.lights-out/` never merges on its own.
- **Time:** `./go demo-init` proves init and gates in under 300 s on two small example repositories. Init measures the whole scope, so a large suite takes longer.
- **Needs a model key.** There are no recordings of your code, so on your repository the agents run at L1 only, and say so at L0: `GEMINI_API_KEY` for the coding roles (the free tier), or `AGENT_HARNESS=claude-code` with `ANTHROPIC_API_KEY`, or `AGENT_HARNESS=codex` with `OPENAI_API_KEY` (with `MODEL_PROVIDER` and `MODEL`; the ADK roles take the same keys). The dev-worker may spend up to 2.6M tokens per run (`roles/dev-worker/role.env`).
- **Python and javascript.** `./go init` infers both. The dev-worker runs in your toolchain image when `TOOLCHAIN_IMAGE` names an image, and in the workbench otherwise. Building that image is checked; no run at L1 has been made.
- **Release at L1.** A merged change is built from your repository's own Containerfile, signed and released on the local plane (ADR 0043). This is implemented and has not been run on a repository of yours.
- **Pull requests.** By default merges land on the plane's Gitea and you `git fetch` them back. At L1 the pull request can land on your GitHub repository instead, with `CODE_HOST=github` and a token in `.env`. That path is implemented, proven verb by verb by `./go codehost-conformance`, and not yet run end to end. See [the guide](docs/use-on-your-repo.md) and [ADR 0043](docs/adr/0043-github-code-host-and-attached-release.md). There is no deploy to your production.
- **Not yet proven** on a codebase that was not written for lights-out.

[The guide](docs/use-on-your-repo.md) says what `.lights-out/` holds, what each key says, what the plane needs and how to write tickets.

## Next

- [How it works, every command, every level](docs/reference.md)
- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/adr/)
- [Contributing](CONTRIBUTING.md) and [roadmap](ROADMAP.md)

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
