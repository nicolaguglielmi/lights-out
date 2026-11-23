# Tutorial: lights-out in one sitting

This tutorial opens with the path from zero to hero: seven levels, each
one asking for a little more than the one before. Every level says how
long it takes, what you need, what you will see, and ends on the one line
that proves you got there. The sections after the path are the long
version, linked from each level.

**About the outputs.** Every output block below is real. Each one is
copied from a GitHub Actions run of this repository, or printed by a
command run on a file this repository commits, or by a local run named
with its commit and date. The source is named in an HTML comment right
before each block. Where lines were left out (container build noise, the
signature's JSON), the source line says so. Your digests, commit ids and
timings will differ. Where no real output exists, the tutorial says so
and shows none.

The [README](../README.md) is the short version. [The
reference](reference.md) has every command, and [the
guide](use-on-your-repo.md) has every key of `.lights-out/`.

## From zero to hero

| Level | What you do | Time | You need |
|---|---|---|---|
| [0](#level-0-see-it) | See a real run's canary roll itself back | 1 min | a browser |
| [1](#level-1-run-the-demo) | `./go demo`: the canary rolls back by itself, on your machine | about 10 min | podman and git, or a GitHub account |
| [2](#level-2-the-gates-on-your-repository) | The eight gates on your repository | about 15 min | your repository |
| [3](#level-3-your-thresholds-a-ticket-the-intake) | Your thresholds, a ticket, the intake | about 30 min | a clone of lights-out |
| [4](#level-4-your-service-in-the-plane) | Your service in the plane: canary and rollback on your code | 1–2 h | a service that answers HTTP |
| [5](#level-5-live-agents-at-l1) | Live agents at L1, with a budget and the provider you choose | about 30 min | a model key, and money |
| [6](#level-6-your-cicd) | Your CI/CD: Flagger, GitLab CI, Jenkins | it depends | your delivery or your CI |

Levels 0 to 4 need no API key and no cloud account. Level 5 needs a key.
The times are the commands' measured times plus the reading and writing a
level asks of you ([ADR 0097](adr/0097-from-zero-to-hero-and-a-start-with-nothing-installed.md) §1d).

### Level 0: See it

**Time:** 1 minute. Nothing to install.

**You need:** a browser.

**You will see:** [the frozen console](https://nicolaguglielmi.github.io/lights-out/console/snapshot.html#ticket/0001),
a real `./go demo` run at L0 frozen into one read-only page (ADR 0089).
Ticket 0001's timeline goes from the request to the spec gate, the pull
request and its eight gates, the merge, the canary at 5% of the traffic,
and its rollback, with the numbers that decided it. Nobody pressed
anything.

**Go deeper:** [how the demo reads, stage by stage](#the-demo-and-how-to-read-it),
and the [console](reference.md) in the reference.

<!-- source: the site's console/snapshot.html, ticket 0001's timeline, frozen from ci run 43501367427 (logbook of 5870da4), read on 2026-11-16 -->
```
GO: spec
GO: gates — risk low, auto-merge, in compose:backend-test
NO-GO: slo — error_rate canary(0.2.0-waitlist)=1.000 stable(0.1.0-m2)=0.000 delta=1.000 max=0.010
NO-GO: launchpad — 5%: error_rate canary(0.2.0-waitlist)=1.000 stable(0.1.0-m2)=0.000 delta=1.000 max=0.010 → rolled back
```

The last line is the point of the whole product: a change that passed
every gate still failed in front of real traffic, and the canary rolled
itself back. It is the only level that ends on a NO-GO.

### Level 1: Run the demo

**Time:** about 10 minutes: 427 s on a clean GitHub runner with podman,
cold (the run below), 370–480 s across the runs ADR 0093 measured. The
first build downloads about 1 GB.

**You need:** podman (or docker) and git, as [the README](../README.md#quick-start)
says for macOS and Linux; no account and no key. Or, with nothing
installed, a GitHub account: [open it in Codespaces](https://codespaces.new/nicolaguglielmi/lights-out?quickstart=1).
That needs a GitHub account and uses your account's Codespaces quota (a
4-core machine, 4 core hours an hour, of the 120 a Free account gets each
month), and runs the same `./go demo` with docker inside the codespace:
669 s in the same dev container on a GitHub runner (stranger run
43968199663, job 133653156720), plus about a minute to build the
container.

**You will see:** seven numbered steps, a canary at 5% judged and rolled
back by itself, ticket 0001 taken from a request to a rollback, and at the
end the run's own console opened on ticket 0001. In a codespace the
`file://` link does not open in your browser: run `./go console` and open
the forwarded port 8098.

**Go deeper:** [the demo, stage by stage](#the-demo-and-how-to-read-it),
[the gates saying no](#the-gates-saying-no), [the short looks](#the-short-looks).

```sh
git clone https://github.com/nicolaguglielmi/lights-out.git
cd lights-out
./go demo
```

<!-- source: GitHub Actions stranger run 43925636189, job demo (133504261420), podman, 2026-11-16; the workflow's seconds-since-start prefix removed, and the 3000/8080 lines of step 1 left out -->
```
lights-out demo: a ticket goes from a request to a canary that rolls itself back, at L0, with no key. About 6 min 10 s on this machine.
[1/7] the engine, memory, disk and ports (about 1 s) ...
[2/7] the toolchain image, built once per revision (about 30 s) ... 38 s
[3/7] the plane: code host, delivery, metrics, flags, registry and the app (about 71 s) ... 79 s
[4/7] a canary with a failing feature takes 5% of the traffic (about 63 s) ...
      A canary at 5% was judged and rolled back by itself, nobody paged: error_rate canary(0.2.0-waitlist)=1.000 stable(0.1.0-m2)=0.000 delta=1.000 max=0.010
      done, 63 s
[5/7] ticket 0001: request, spec, pull request, eight gates, merge, canary (about 3 min 24 s) ...
      Ticket 0001: requested, specified, a pull request the eight gates passed, merged, released at 5%, rolled back by itself, audited
      done, 3 min 26 s
[6/7] the repository's own checks, run beside the plane (about 0 s) ... 38 s
[7/7] this run's console, frozen into one page (about 1 s) ...
      See why, step by step: file:///home/runner/work/lights-out/lights-out/lights-out/.crew/demo/console.html#ticket/0001
      Next: ./go console serves the live console on localhost:8098; ./go try <path> runs the gates on a repository of yours
      The plane is still up: the app at http://localhost:8081, Gitea at http://localhost:3001, Prometheus at http://localhost:9090; ./go down stops it
      done, 1 s
GO: demo, milestone M9, 427s to rollback (budget 900s, plan §2)
```

### Level 2: The gates on your repository

**Time:** about 15 minutes, most of it reading the drafted contract.
`./go try` took 16 s on a clean GitHub runner right after `./go demo`
([stranger run 44138459631](https://github.com/nicolaguglielmi/lights-out/actions/runs/44138459631)),
14 s on a laptop with the toolchain image already built (40 s the first time); the Action posted its check on a pull request in
37 s ([the probe](https://github.com/nicolaguglielmi/lights-out-probe/pull/1)).

**You need:** your repository, Python or JavaScript, with its tests. From
a clone of lights-out, nothing more. Without one: the GitHub Action on
your pull requests, or `uv tool install lights-out-gates` (which installs
both the `lights-out` wrapper for podman or docker and `lights-out-gates
--light`; standalone `curl` fallback below).

**You will see:** a contract drafted for your repository (`.lights-out/`,
with where every value comes from), the eight gates on your last commit,
and the three commands that keep the draft. `./go try` writes nothing to
your repository and leaves nothing attached.

**Go deeper:** [your repository, without the plane](#your-repository-without-the-plane),
[read the draft](#read-the-draft), [the GitHub Action](#the-github-action),
[what init writes](use-on-your-repo.md#what-init-writes).

```sh
./go try ~/code/your-repo
```

<!-- source: GitHub Actions stranger run 44138459631 (commit 045076d, 2026-11-17), job demo, step "3. ./go try on a repository of your own": a copy of workloads/minimal-js with its .lights-out/ removed, git init, one commit; stdout; the [try] lines on stderr name the draft's path and the commands that keep it -->
```
GO: try — javascript; the eight gates on your last commit: risk low, auto-merge, in workbench; the draft is in .crew/try/your-repo/.lights-out
```

`risk low, auto-merge` because that copy is small: 111 lines with the
lockfile left out, under `RISK_SIZE_MEDIUM_LINES=200`, and no path the
policy calls high. Your own repository's first commit is the whole
repository, usually past 200 lines, so expect `risk medium, review`; a
commit that touches `.lights-out/` reads `risk high, review`. On a pull
request, the Action posts the same eight lines as one check run; [the
guide](use-on-your-repo.md#the-action) has its YAML.

### Level 3: Your thresholds, a ticket, the intake

**Time:** about 30 minutes, most of it writing: the intake and its two
answers below took 7, 4 and 8 s on a laptop.

**You need:** a clone of lights-out. No plane and no key: at L0 the
interview replays the one recorded on the committed request.

**You will see:** how lights-out decides, and where you change it. The
thresholds are in `policy.env`, each with where it comes from: the
bundled app's is [`policy/`](../policy/), yours goes in
`.lights-out/policy.env` ([the guide, section 3](use-on-your-repo.md#3-your-thresholds-policyenv)).
A ticket is one Markdown file with its criteria and its perimeter
([write the ticket](#write-the-ticket)). And when a client has words, not
a ticket, `./go intake` interviews them, one question at a time, proposes
tickets with criteria and perimeters, and opens nothing until a person
confirms.

**Go deeper:** [`./go intake`](#go-intake-a-need-before-any-ticket),
[the screen and the perimeter](#the-screen-and-the-perimeter-asked-file-by-file),
[go-for-launch](#go-demo-launch-go-for-launch-the-one-human-gate).

```sh
./go intake TICKET=0030 INTAKE_FILE=fixtures/intake/request.md
./go answer TICKET=0030 ANSWER="A button on the meetup page; then they are no longer registered and one more seat is free."
./go answer TICKET=0030 ANSWER="The organiser sees the names and the free seats on the meetup page; other members see only the free seats."
```

<!-- source: local run on podman, commit f205507, 2026-11-16, a full clone of claude/m64-zero-to-hero-214bf3 under .crew/, L0; stdout of the three commands -->
```
GO: intake — 0030 at intake-questions
GO: answer
GO: answer
```

`./go intake-confirm TICKET=0030 DECISIONS='["confirm","drop"]'` keeps the
first proposed ticket, opens it through the spec gate and assigns it.
The assign pushes to the plane's Gitea, so confirm with the plane up
(`./go up`, or the plane level 1 leaves running); [the section on the
intake](#go-intake-a-need-before-any-ticket) shows that step and its
lines.

### Level 4: Your service in the plane

**Time:** 1–2 hours, nearly all of it in your service's code. The six
commands below took 212 s on a clean runner for `workloads/minimal`
(the `seam` job in the run below).

**You need:** a service that answers HTTP and builds from a Containerfile
or a Dockerfile, the gates' contract from level 2, and podman or docker.

**You will see:** your own service behind the plane's Envoy, a canary of
your code at 5% judged on your metrics and rolled back by itself, and your
fixtures judged by the eight gates.

**Go deeper:** [the plane half](use-on-your-repo.md#2-the-plane-half),
[attach it and prove the plane on it](use-on-your-repo.md#attach-it-and-prove-the-plane-on-it).

Nothing in the plane half is drafted for you, on purpose: a guessed
compose file would bring up a plane whose canary cannot be judged (ADR
0097 §4). Start from the example instead, which works:

1. Copy `workloads/minimal/.lights-out/compose.yaml`, `flags/flags.json`,
   `smoke.sh` and `load.sh` into your `.lights-out/`, and change the
   service's build context and port.
2. Your service answers `GET /healthz` and `GET /metrics` on `:8000`
   (`workloads/minimal/minimal/app.py` does both).
3. Its metrics are the OTel HTTP server histogram with
   `target_info{service_version}`: `./go verify` reads exactly that
   (`verify/slo.promql`), and `GO: metric-shape` below checks it first.
4. It reads one kill switch from flagd, on for `CANARY_VERSION`
   (`WORKLOAD_KILL_SWITCH` in `workload.env`): that is how the demo makes
   a canary fail. Add one fixture under `fixtures/gates/`, a `git
   format-patch` and the verdict you expect.

```sh
./go attach ~/code/your-repo
./go up
./go smoke
./go verify
./go demo-rollback
./go demo-gates
```

<!-- source: GitHub Actions ci run 43925636171, job seam (133504261637), docker, 2026-11-16: ./go demo-seam runs these six on workloads/minimal as a repository of its own; its verdict lines, the tools' output left out -->
```
GO: attached — /tmp/tmp.p3YTBirdEa at ed8b2a6, contract in .attached/.lights-out
GO: metric-shape
NO-GO: launchpad — 5%: error_rate canary(0.2.0-outage)=1.000 stable(0.1.0-minimal)=0.000 delta=1.000 max=0.010 → rolled back
GO: demo-rollback
GO: demo-gates
```

### Level 5: Live agents at L1

**Time:** about 30 minutes to set up; then a run is minutes and money.
This level is a documented path: it was not run for this tutorial,
because it needs a key and spend.

**You need:** one model key in `.env`, the provider you choose (Gemini,
Claude, OpenAI or GLM by Z.ai), and a budget. `TOKEN_BUDGET` and
`MAX_TURNS` per role stop a run that spends too much, per provider
([what it costs](#what-it-costs)).

**You will see:** a real agent working your ticket in a sandbox, inside
its perimeter, its pull request judged by the same eight gates, and the
spend per ticket, role and day in `./go spend`.

**Go deeper:** [a ticket, assigned to an agent at L1](#a-ticket-assigned-to-an-agent-at-l1),
[the coding harnesses](reference.md#coding-harnesses),
[the provider scoreboard](#go-scoreboard-the-providers-side-by-side).

```sh
cp .env.example .env        # then put one key in it, for example ANTHROPIC_API_KEY
./go attach ~/code/your-repo
./go up
PROFILE=l1 ./go request TICKET=<id> MODEL_PROVIDER=anthropic MODEL=claude-sonnet-5-5 AGENT_HARNESS=claude-code
```

<!-- source: M24, a live run on Anthropic (claude-code, claude-sonnet-5-5) of ticket tenacity-03 on a fork of tenacity, 2026-11-11: the gates' line as the run's diary records it (.claude/context/handoffs/claude-m24-phase-a-5dacb7.md §6, docs/scoreboard/m24-tenacity.json); merged as 7d66513 with no human touching the code -->
```
GO: gates — risk low, auto-merge
```

### Level 6: Your CI/CD

**Time:** it depends on your delivery and your CI. Each recipe's proof
below took 2.5 to 12 minutes on a clean runner: Flagger 726 s, GitLab CI
309 s, Jenkins 154 s (the jobs of the run below).

**You need:** your own delivery (Kubernetes with Flagger or Argo
Rollouts) or your own CI (GitLab CI, Jenkins, CircleCI).

**You will see:** the same gates and the same human gate where you
already ship. With Flagger, Flagger's own analysis rolls a failing canary
back, and its promotion waits at lights-out's gate for a person. On your
CI, the gates' line posted as a commit status.

**Go deeper:** [toward production in the README](../README.md#toward-production),
[your CI as a surface](use-on-your-repo.md#your-ci-as-a-surface),
[Toward production](production.md).

```sh
./go flagger-proof
./go demo-gitlab-ci
./go demo-jenkins
```

<!-- source: GitHub Actions ci run 43925636171, jobs flagger (133504261652), gitlab-ci (133504261728) and jenkins (133504261727), docker, 2026-11-16 -->
```
GO: flagger — Flagger rolled a failing canary back on its own analysis in 37s; the promotion held at its gate for 30s at 100% and completed on a person's answer; 236s
GO: demo-gitlab-ci — GitLab CI (gitlab-runner, merge request !1's pipeline) posted [GO: gates — risk low, auto-merge, in workbench] as lights-out/gitlab-ci, 276s
GO: demo-jenkins — Jenkinsfile Runner posted [GO: gates — risk low, auto-merge, in workbench] as lights-out/jenkins on PR #1, 113s
```

## Before you start

Install podman (or use docker) as the README's "Try it" table says, then
clone and check your machine:

```sh
git clone https://github.com/nicolaguglielmi/lights-out.git
cd lights-out
./go doctor
```

`./go doctor` prints one line per item (engine, podman socket, git,
workbench image, the plane's ports, the key your profile needs) and ends
with `GO: doctor` or `NO-GO: doctor — <items>`. You do not have to fix a
busy port yourself: `./go demo` starts with a preflight that moves the
app's, Gitea's, Prometheus's, Envoy's admin and flagd's port to a free one
and says so (ADR 0093 §1). Only 5050, the registry's, cannot move.

On docker, put `CONTAINER_ENGINE=docker` before every `./go`. That is what
the CI runs below did.

Every gate in lights-out prints exactly one line on stdout: `GO: <name>`,
sometimes followed by ` — <detail>`, or `NO-GO: <name> — <reason>`. The
lines around the verdicts are progress, and the evidence behind each
verdict.

## The demo, and how to read it

```sh
./go demo
```

The terminal shows one line per step, with an estimate, and keeps the
rest in `.crew/demo/run.log` (ADR 0093 §2); `LIGHTS_OUT_VERBOSE=1 ./go
demo` prints every line the excerpts below come from. The demo runs, in
order: the preflight, the toolchain image, `up` and `smoke`,
`verify`, `demo-rollback` and `e2e`, while the repository's own checks
(`ci`) run beside the plane in a second container; their verdict is
awaited before `GO: demo`. `demo-rollback` and `e2e` are the story. The
end freezes this run's console into `.crew/demo/console.html`, opened on
ticket 0001, and prints the link.

### The repository checks itself

`./go ci` runs the repository's own checks. Each prints one line. In
`./go demo` they run beside the plane and their lines come after the
plane's. The last ones in the run:

<!-- source: GitHub Actions run 41866972654 (commit ee2f679), job engine-compat (126703546061), step "./go demo" -->
```
GO: metrics
GO: package
GO: contract
GO: ci
```
*GitHub Actions run 41866972654, job engine-compat, last four lines of `ci`.*

`GO: contract` is the meta-gate: it reads what every gate printed on
stdout and fails if one prints more than one line, a colour code, or an
exit code that disagrees with its verdict. Under `./go ci` each check
runs once: its line and exit code are kept as it runs, and `contract`
judges that record (ADR 0062).

### The plane comes up

`up` starts the L0 plane: Gitea, Envoy, Prometheus, flagd, Zot and the two
Google emulators, beside the app. `smoke` creates a meetup through Envoy.
`verify` checks the shape of the metrics against one live scrape.

<!-- source: GitHub Actions run 41866972654 (commit ee2f679), job engine-compat (126703546061), step "./go demo" -->
```
[demo] up: L0 plane, gitea envoy-canary prometheus flagd zot pubsub-emulator firestore-emulator
smoke: meetup 2 created through Envoy at http://host.containers.internal:8080 (venue_lat=45.4641)
[demo] smoke: the happy path passed through Envoy at http://host.containers.internal:8080
metric-shape: checked query/app agreement against one live scrape (metric and label names, semconv seconds buckets, status-code value format)
GO: metric-shape
metric-shape: did not evaluate rate() or histogram_quantile() — needs a TSDB (Prometheus at M2, canary vs stable comparison at M3)
[demo] verify: metric shape only; canary vs stable is evaluated inside demo-rollback below
```
*GitHub Actions run 41866972654, job engine-compat.*

### A bad release rolls itself back

`demo-rollback` releases version `0.2.0-waitlist` directly, with no ticket
and no PR. It is the same image as stable, with the `waitlist` feature
flag targeted on for that version. That feature answers 501, so the canary
fails.

<!-- source: GitHub Actions run 41866972654 (commit ee2f679), job engine-compat (126703546061), step "./go demo"; left out: cosign's warning line and its JSON, and the "Container …" lines of compose -->
```
[demo-rollback] stable is 0.1.0-m2; releasing 0.2.0-waitlist (waitlist targeted on)
Verification for host.containers.internal:5050/lights-out-backend@sha256:036e2fd8664f69cd85594fb920adbe6e5bf2fc0a622047efd561e5a5c94c12b1 --
The following checks were performed on each of these signatures:
  - The cosign claims were validated
  - Existence of the claims in the transparency log was verified offline
  - The signatures were verified against the specified public key
[launchpad] staging: deploying 0.2.0-waitlist at lights-out-backend@sha256:036e2fd8664f69cd85594fb920adbe6e5bf2fc0a622047efd561e5a5c94c12b1 (stable is 0.1.0-m2)
slo: traffic canary(0.2.0-waitlist)=0.151 stable(0.1.0-m2)=4.236
slo: error_rate canary(0.2.0-waitlist)=1.000 stable(0.1.0-m2)=no data
slo: latency_p95 canary(0.2.0-waitlist)=0.024 stable(0.1.0-m2)=0.024
NO-GO: launchpad — 5%: error_rate canary(0.2.0-waitlist)=1.000 stable(0.1.0-m2)=0.000 delta=1.000 max=0.010 → rolled back
make[2]: *** [Makefile:488: release] Error 1
GO: demo-rollback
```
*GitHub Actions run 41866972654, job engine-compat. Left out: cosign's
warning line and JSON, and compose's container lines.*

Read it from the top:

- **The signature is checked before any traffic.** The launchpad verifies
  the image's cosign signature against the plane's local key, then deploys
  the verified digest, not a tag.
- **Staging, then 5%.** At 5% verify compares the canary with stable on
  three numbers from `verify/slo.promql`: traffic, error rate and p95
  latency. Stable's error rate reads `no data` because stable served no
  errors, and verify counts that as 0.
- **NO-GO, then rollback.** An error-rate delta of 1.000 against a maximum
  of 0.010 is a NO-GO, and the launchpad puts all traffic back on stable
  on its own. `make` reports the `release` target's exit 1. That is the
  NO-GO, and `demo-rollback` expects it, so its own line is `GO`.

### A ticket goes all the way: `e2e`

`e2e` runs the same thing through the tower, the control plane: ticket →
PR → the gates → merge → a signed build of the merge → canary 5% →
verify NO-GO → rollback, then the audit.

<!-- source: GitHub Actions run 41866972654 (commit ee2f679), job engine-compat (126703546061), step "./go demo"; left out: git's push hints, the gates' tool output, cosign's warning and JSON, compose's container lines -->
```
[e2e] replay: deleting the document of ticket 0001 before opening it again
[tower] 0001: opened, assign requested
[tower] 0001: assign (state opened)
[crew] ticket 0001: role dev-worker, harness replay, attempt 1
Applying: Handle unknown geocoder venue with 201 and null coordinates
[crew] ticket 0001: PR #1 http://host.containers.internal:3000/lights-out/workload/pulls/1
GO: crew
[tower] 0001: assign → pr-opened
[tower] 0001: ci (state pr-opened)
risk: level=low decision=auto-merge signals=none
deps: scanning 2 lockfile(s) at 5804fca9b45c05e68da6364745c09adb57181328 and at the base ee2f679bc63159c97a6eb4fbaf17520822cdbbcc: app/backend/uv.lock app/frontend/package-lock.json
deps: 1 advisory finding(s) inherited from the base, not gating (docs/adr/0032):
  PyPI opentelemetry-api GHSA-xcq7-p3pf-p88h
score: 211 mutant(s) excluded from the population (skipped, no-test, or incompetent)
[ci] gates: GO: gates — risk low, auto-merge, in compose:backend-test
[tower] 0001: ci → ci-passed
[tower] 0001: merge (state ci-passed)
[merge] PR #1 merged as a4502bbc0bb2d3525e5709d19e1ec8adbd25c922 (head 5804fca9b45c05e68da6364745c09adb57181328)
[tower] 0001: merge → merged
[tower] 0001: release (state merged)
[launchpad] staging: deploying 0.2.0-waitlist at lights-out-backend@sha256:87d0a257a29afd15724adb370c50cbe013540d379ad9bf4cea7ebf16428cbb0f (stable is 0.1.0-m2)
slo: traffic canary(0.2.0-waitlist)=0.287 stable(0.1.0-m2)=4.091
slo: error_rate canary(0.2.0-waitlist)=1.000 stable(0.1.0-m2)=no data
slo: latency_p95 canary(0.2.0-waitlist)=0.024 stable(0.1.0-m2)=0.024
make[2]: *** [Makefile:488: release] Error 1
[tower] 0001: release → rolled-back
[tower] 0001: rolled-back is terminal, nothing requested
[inproc] queue empty after 4 event(s), 0 failed
```
*GitHub Actions run 41866972654, job engine-compat, the `e2e` stage.
Left out: git's push hints, the gates' tool output (gitleaks, pytest,
cosmic-ray, image builds), cosign's warning and JSON, compose's container
lines.*

Stage by stage:

- **Ticket.** `tickets/0001.md` is a real bug: an unknown venue made
  `POST /api/meetups` answer 500. The tower opens a document for it in the
  state store (the Firestore emulator) and requests `assign`.
- **PR.** The dev-worker role turns the ticket into a patch. At L0 nothing
  calls a model: the `replay` harness applies the answer recorded once
  against the Gemini API (`fixtures/0001/dev-worker/`). The patch must
  stay inside the ticket's `## Perimeter`, checked before any PR exists.
  The PR opens on the plane's own Gitea, never on this repository.
- **The gates.** risk, secrets, test-weakening, deps, SAST, test,
  mutation, test-proof, in that order, on the PR's range, with the base's contract and policy (a PR
  cannot loosen what judges it). Here: risk `low` with no signal, so
  `auto-merge`; gitleaks found no leaks; one advisory already on the base,
  which does not gate (ADR 0032); the tests pass; the mutation score on
  the changed lines holds. `in compose:backend-test` says where the test
  tools ran.
- **Merge.** On GO and risk `low`, the tower merges the PR at the head the
  gates judged.
- **Signed image.** The merge commit is built, pushed to the local Zot
  registry and signed. The launchpad verifies the signature before staging
  (the same cosign lines as above, left out here).
- **Canary 5%, verify NO-GO, rollback.** The release is the failing
  version, so verify stops it at 5% and the canary rolls back. The ticket
  ends at `rolled-back`.

The audit closes `e2e` (it is shown below, under `./go audit`). The
demo's last two lines:

<!-- source: GitHub Actions run 41866972654 (commit ee2f679), job engine-compat (126703546061), step "./go demo" -->
```
lights-out: open the app at http://localhost:8080, Gitea at http://localhost:3000, Prometheus at http://localhost:9090
GO: demo, milestone M9, 716s to rollback (budget 900s, plan §2)
```
*GitHub Actions run 41866972654, job engine-compat.*

The 900 s budget is the golden rule: a clone reaches an automatic rollback
in under 15 minutes, with no account and no key.

### The gates saying no

The demo shows the gates saying GO. `./go demo-gates` judges eleven committed
fixture patches, and shows the test-weakening, test, mutation, SAST, test-proof
and deps gates saying NO-GO, and risk sending a change to review:

<!-- source: GitHub Actions run 43003750178 (commit 19be6c3), job gates (130466613265), step "./go demo-gates"; only the [demo-gates] lines and the last line -->
```
[demo-gates] assertion-removed: NO-GO: gates — test-weakening: NO-GO: test-weakening — 1 test file finding that no ## Removes tests in the ticket declares: app/backend/tests/test_meetups.py: loses 1 assertion; details on stderr
[demo-gates] failing-test: NO-GO: gates — test: NO-GO: test — in compose:backend-test: the test command failed (exit 1), see stderr
[demo-gates] fake-test: NO-GO: gates — mutation: NO-GO: mutation — in compose:backend-test: 4/8 mutants on changed lines killed (0.500 < 0.666); survivors on stderr
[demo-gates] honest-fix: GO: gates — risk low, auto-merge, in compose:backend-test
[demo-gates] migration: GO: gates — risk high, review, in compose:backend-test
[demo-gates] sast-added-on-red-base: NO-GO: gates — sast: NO-GO: sast — in compose:backend-test: this diff introduces 1 finding(s); list on stderr
[demo-gates] sast-finding: NO-GO: gates — sast: NO-GO: sast — in compose:backend-test: this diff introduces 1 finding(s); list on stderr
[demo-gates] sast-inherited: GO: gates — risk low, auto-merge, in compose:backend-test
[demo-gates] test-edited: GO: gates — risk low, auto-merge, in compose:backend-test
[demo-gates] test-passes-on-base: NO-GO: gates — test-proof: NO-GO: test-proof — in compose:backend-test: the changed tests pass on the base 19be6c3 too, so they do not prove the change
[demo-gates] vulnerable-dep: NO-GO: gates — deps: NO-GO: deps — this diff introduces 4 advisory finding(s); list on stderr
GO: demo-gates
```
*GitHub Actions run 43003750178, job gates: the `[demo-gates]` lines and
the last line.*

`fake-test` is the same fix as `honest-fix` with a test that does not
really test it. The tests pass, so the test gate says GO. Mutation testing
catches it: it changes the fixed lines and checks whether a test fails.
`assertion-removed` drops one assertion from an existing test; the suite
still passes, and test-weakening catches it in the diff before any
toolchain runs. `test-passes-on-base` adds a test that passes without the
change too; test-proof runs it on the base and says it proves nothing.
`test-edited` corrects a contract value and the assertions pinning it, so
nothing is lost and every gate says GO (ADR 0053). `migration` passes
every gate but is risk `high`, so it goes to review instead of merging on
its own.

### `./go board`

```sh
./go board
```

The board is one page on `http://127.0.0.1:8099` (`BOARD_PORT` moves it).
It has two parts (ADR 0035). EXPLAINER has ten scripted walks (ship it,
bad canary, fake test, weakened test, NO-GO then rework, risky diff, freeze
then resume, human says no, out of bounds, kill switch). They are labelled
as scripted, and their versions and numbers are examples. The GATES
station lights the eight gates in the Makefile's order. LIVE appears while the plane is up: the tower's
documents, the release-freeze and kill-switch flags, the traffic split and
the release record. It refreshes every 2 seconds and only reads. It never
deploys, shifts or rolls back.

The board is a page, not a command with an output, so no output is shown
here. `./go ci` checks it (`ci/board.sh`): the page's state table must
equal the tower's, and its gates must be the Makefile's `GATES`, in order.

### `./go audit`: the chain, rebuilt

```sh
./go audit TICKET=0001
```

The audit rebuilds the chain from the release down to the spec, and
cross-checks the state store against the code host. A record that says
merged while the PR is still open is a NO-GO. `e2e` runs it as its last
stage:

<!-- source: GitHub Actions run 41866972654 (commit ee2f679), job engine-compat (126703546061), step "./go demo", the audit inside e2e -->
```
deploy   0.2.0-waitlist rolled-back — NO-GO: launchpad — 5%: error_rate canary(0.2.0-waitlist)=1.000 stable(0.1.0-m2)=0.000 delta=1.000 max=0.010 → rolled back
         slo: traffic canary(0.2.0-waitlist)=0.287 stable(0.1.0-m2)=4.091
         slo: error_rate canary(0.2.0-waitlist)=1.000 stable(0.1.0-m2)=no data
         slo: latency_p95 canary(0.2.0-waitlist)=0.024 stable(0.1.0-m2)=0.024
         image: lights-out-backend@sha256:87d0a257a29afd15724adb370c50cbe013540d379ad9bf4cea7ebf16428cbb0f
  ← merge  a4502bbc0bb2d3525e5709d19e1ec8adbd25c922 (code host agrees)
  ← PR     #1 http://host.containers.internal:3000/lights-out/workload/pulls/1 (gates GO, risk low, auto-merge; head 5804fca9b45c05e68da6364745c09adb57181328)
  ← ticket 0001 (dev-worker, harness replay, model gemini-3.8-flash)
  ← spec   sha256:83d4cbb27987e3d18f8d273787b64534b3e387fc4c5726a3d29980c40dc82742 "0001 — Unknown venue makes `POST /api/meetups` answer 500 instead of the documented 502"
GO: audit
GO: e2e
```
*GitHub Actions run 41866972654, job engine-compat.*

Read it from the top down: what ran in production, which merge it was
built from (and that the code host agrees), which PR and which gate
verdict let it merge, which ticket and which agent wrote it, and the
spec: the sha256 of the ticket file as the agent read it. `harness
replay, model gemini-3.8-flash` says the PR was replayed, and which model
wrote the recording. The image digest is the one the launchpad verified.

### `./go evidence`: the release, packed and signed

```sh
./go evidence VERSION=0.2.0-waitlist
```

The audit answers about a ticket on your screen. The evidence pack
answers about a release, in a file you can hand to someone else
(ADR 0081). It writes `.crew/evidence/<version>.json`, signs it with the
plane's cosign key and verifies the signature in the same command. It is
not a stage of `./go demo`. Run after the demo, it packs e2e's release:

<!-- source: local run on podman, commit 38e7d23, 2026-11-12, after ./go demo; left out: cosign's own progress lines and its tlog warnings -->
```
missing: release.held_at
missing: stages[0].started_at
missing: stages[0].judged_at
missing: artefacts.dast
missing: answer
missing: prs[0].review
evidence: verify with: cosign verify-blob --key .crew/cosign.pub --insecure-ignore-tlog --bundle .crew/evidence/0.2.0-waitlist.json.bundle .crew/evidence/0.2.0-waitlist.json
GO: evidence — .crew/evidence/0.2.0-waitlist.json signed and verified; 6 missing
```

Every value in the pack says where it came from, for example
`{"source": "state store sqlite: tickets/0001 .ci.lines", "value": ["GO: risk", "GO: secrets", …]}`.
The six gaps are named, never filled in. The release was not held, so it
has no hold and no human answer. DAST was off. The gates decided
auto-merge, so no review was asked for. The stage times arrive with a
later milestone. Change one byte of the file and the verify command
fails.

Beside the pack, the same command freezes the console (ADR 0089). It
writes the logbook first, then `.crew/evidence/<version>.console.html`:
the console's own page with the logbook inside it, read-only, with
nothing behind it. It is signed into its own bundle and verified the
same way:

<!-- source: local run on podman, commit 4fd8340, 2026-11-14, after ./go demo; left out: the logbook's line and cosign's own progress lines -->
```
missing: release.held_at
missing: artefacts.dast
missing: answer
missing: prs[0].review
evidence: verify with: cosign verify-blob --key .crew/cosign.pub --insecure-ignore-tlog --bundle .crew/evidence/0.2.0-waitlist.json.bundle .crew/evidence/0.2.0-waitlist.json
GO: evidence — .crew/evidence/0.2.0-waitlist.json signed and verified; 4 missing; console snapshot signed
```

The stage times are in the record now, so four gaps are left. Open the
`.console.html` file in a browser, with no server. Ticket 0001's
timeline runs from the request to the canary's rollback, and the
go-for-launch screen shows the stage that judged it. Nothing on the page
can be sent. The landing page links the same freeze of the latest demo
on main.

### `./go dora` and `./go spend`: how delivery went, and what it cost

```sh
./go dora
./go spend
```

`./go dora` reads the logbook and prints DORA's four delivery measures
over the last `WINDOW_DAYS` days (ADR 0082). `./go spend` adds up every
run the tower priced into each ticket's ledger, per ticket, per role and
per day, the burn per hour of the last day that had a run and that
day's projection to midnight UTC (ADR 0087), and names the budget (ADR
0083). Run after the demo:

<!-- source: local run on podman, commit 37ca51a, 2026-11-13, after ./go demo -->
```
[dora] window: 30 days, 2026-10-14T08:59:42Z to 2026-11-13T08:59:42Z
[dora] deployment frequency: 0 promoted to 100%, 0.000 per day
[dora] lead time: not measured — no release of a ticket reached 100% in the window
[dora] change fail rate: 100%: 2 of 2 decided release(s) rolled back, 0 pending
[dora] recovery time: median 1 min from the failing stage's start to the rollback over 2 release(s), 0 unmeasured
GO: dora — .crew/dora.json, 3 of 4 measured over 30 days
```

<!-- source: local run on podman, commit a2800db, 2026-11-14, after ./go demo -->
```
[spend] ticket 0001: $0.00 over 1 run(s); agents 0h00m00s, elapsed 0h04m30s
[spend] role dev-worker: $0.00 over 1 run(s); 0h00m00s
[spend] day 2026-11-14: $0.00 over 1 run(s)
[spend] burn: $0.00 per hour on 2026-11-14 ($0.00 known in 1 run(s) from 09:36 to 09:36 UTC, over 1.0 h)
[spend] projection: $0.00 by the end of 2026-11-14 UTC at that rate, as of 09:36 UTC; SPEND_BUDGET_DAY_USD no limit
[spend] budget: SPEND_BUDGET_TICKET_USD no limit, SPEND_BUDGET_DAY_USD no limit
GO: spend — 1 tickets, 1 runs, $0.00 known, 0 of cost unknown
```

Nothing reached 100%, so there is no lead time to measure, and the
command says so instead of guessing. Both canaries the demo ran were
rolled back. The run was replayed at L0, so it cost nothing and its
agents' time is not counted. Set `SPEND_BUDGET_TICKET_USD` or
`SPEND_BUDGET_DAY_USD` in `policy/rework.env` and the tower launches no
attempt past the limit: the ticket goes to `needs-human` with the
budget's line. A run that stops on its own `TOKEN_BUDGET` is in the
ledger too. Where the harness reports cached input, it is priced at the
vendor's cached rate, and each entry's `cost_basis` says `cached` or
`uncached`. The burn is counted over at least an hour and projected from
the last run's time, not from the moment you look. The console's Overview
shows the same lines, read from the logbook, beside who runs each role;
every card shows its path from the request to 100%, and from L1 the
attempt in flight against its token budget. Its window ends at the record's last event, not at the
moment you look.

### `./go scoreboard`: the providers side by side

```sh
./go scoreboard
```

M24 ran the same ten tenacity tickets once on Anthropic and once on
Gemini. `./go scoreboard` reads those twenty runs from a committed
dataset, `docs/scoreboard/m24-tenacity.json`, where every row names the
handoff and the run it was read from (ADR 0088). It needs no plane and
no key:

<!-- source: local run on the host's python3, commit ed5de8e, 2026-11-14; the scope line shortened -->
```
[scoreboard] scope: The ten tenacity tickets of M24 (…) each run once per provider: …
[scoreboard] anthropic · claude-code · claude-sonnet-5-5: 10 tickets, 10 merged, 8 at the first attempt, 2 reworks, NO-GO mutation 2, 0 budget stops; 75,859 tokens per ticket; per ticket ledger not recorded, list ≤ $0.18, cached not recorded
[scoreboard] gemini · gemini-cli · gemini-3.8-flash: 10 tickets, 7 merged, 7 at the first attempt, 0 reworks, NO-GO none, 3 budget stops; 2,757,699 tokens per ticket; per ticket ledger $0.88, list ≤ $2.09, cached ~$0.36
[scoreboard] openai · codex: not run — no OpenAI key; M24 asks for every ticket on three providers, and the third was not run
GO: scoreboard — 2 groups run, 1 not run, 20 tickets; .crew/scoreboard/index.html
```

The two mutation NO-GOs are Anthropic's first attempts at tickets 06 and
10, each sent back and merged at the second. Gemini's three budget stops
opened no PR. OpenAI is printed as not run, never as zero. A cost no
source gives is "not recorded": the Anthropic runs predate the spend
ledger. The page in `.crew/scoreboard/index.html` has every row with
its source and a footnote for each cost. Add your own runs with
`SCOREBOARD_LOGBOOKS=.crew/logbook.json`. A logbook's replayed tickets
are counted and not scored, so after `./go demo` alone it adds nothing.

## The short looks

Each of these is a separate command, not a stage of `./go demo`.

### `./go demo-rework`: a fake test, caught, and fixed

```sh
./go demo-rework
```

Before a merge, a NO-GO is not the end (ADR 0038). The ticket goes back to
the dev-worker with the gates' verdict: the NO-GO line and the gate's own
details, such as the surviving mutants. The new head is judged by the same
gates. After four attempts (`policy/rework.env`) the ticket stops at
`needs-human`.

`demo-rework` runs this on ticket 0013, which is ticket 0001's bug again.
It needs the plane, and runs the whole tower. Attempt 1 is the
`fake-test` patch above, so the mutation gate rejects it with the line and
the survivors you saw in `demo-gates`:

<!-- source: GitHub Actions run 41866972654 (commit ee2f679), job gates (126703546044), step "./go demo-gates", the fake-test fixture: the same patch as demo-rework's attempt 1 -->
```
SURVIVOR meetups/geocoder_client.py:26 core/ReplaceComparisonOperator_Eq_Gt
SURVIVOR meetups/geocoder_client.py:26 core/ReplaceComparisonOperator_Eq_GtE
SURVIVOR meetups/geocoder_client.py:26 core/NumberReplacer
SURVIVOR meetups/geocoder_client.py:26 core/NumberReplacer
score: 211 mutant(s) excluded from the population (skipped, no-test, or incompetent)
[demo-gates] fake-test: NO-GO: gates — mutation: NO-GO: mutation — in compose:backend-test: 4/8 mutants on changed lines killed (0.500 < 0.666); survivors on stderr
```
*GitHub Actions run 41866972654, job gates, the `fake-test` fixture. This
is `demo-gates`' output for the patch that is `demo-rework`'s attempt 1,
not `demo-rework`'s own output.*

The worker's input for attempt 2 is the ticket, the workload's brief,
then that verdict.
Attempt 2 adds the geocoder contract's 404 example for an unknown venue,
and the combined change is the `honest-fix` fixture's: GO. The tower
merges it, and the canary rolls back as in `e2e`. The audit shows the
rework.

Both attempts are written by hand: no rework has been recorded against a
model yet. The fixtures say so:

<!-- source: the committed file fixtures/0013/dev-worker/run.json, printed with cat -->
```
$ cat fixtures/0013/dev-worker/run.json
{
  "hand_written": true,
  "model": null,
  "attempt": 1,
  "note": "Written by hand to prove the rework loop at L0 (docs/adr/0038 §10): the committed fake-test change, which the mutation gate rejects. No model produced this patch."
}
```

The gates that reject attempt 1 and pass attempt 2, the tower, the loop
and the cap are real. No GitHub Actions job runs `demo-rework`, so its
own output is not shown here. PLAN.md's M15 status note records
`GO: demo-rework` in 404 s on a cold plane, on 2026-11-03, outside
Actions.

### `./go demo-triage`: an alert becomes hypotheses

```sh
./go demo-triage
```

No plane, about 3 seconds (README). The ops-triage role reads three
alerts and, for each, files an incident with ranked hypotheses and
proposes rollback or hold. The demo checks each proposal against the one
the evalset expects. Two of the alerts are traps: in one stable fails too,
in the other the canary got no traffic. Rolling back is the wrong answer
to both.

At L0 the role replays answers recorded at L1 on 2026-11-04 with
`gemini-3.8-flash`. What the evalset expects, and what the recordings
answer:

<!-- source: the committed evalset adapters/adk/agents/ops_triage/evals/alerts.evalset.json, read with jq -->
```
$ jq -r '.eval_cases[] | .eval_id + ": " + .conversation[0].intermediate_data.tool_uses[-1].name' adapters/adk/agents/ops_triage/evals/alerts.evalset.json
canary-5xx: propose_rollback
misleading-stable-fails-too: propose_hold
misleading-no-canary-traffic: propose_hold
```

<!-- source: the committed recordings fixtures/adk/ops-triage/*.json, read with jq -->
```
$ for f in fixtures/adk/ops-triage/*.json; do jq -r '(.input | fromjson | .kind // "alert") + ": " + ([.. | .function_call? // empty | .name] | join(", "))' "$f"; done
alert: file_incident, propose_hold
alert: file_incident, propose_rollback
incident: file_postmortem
alert: file_incident, propose_hold
```

The fourth recording, `incident`, is the post mortem below. No GitHub
Actions job runs `demo-triage`, so its own output is not shown here. Its
last line is `GO: demo-triage`. The incidents it writes are in
`.crew/triage/<alert>/incident.json`.

### `./go demo-postmortem`: an incident becomes a post mortem and a ticket

```sh
./go demo-postmortem
```

No plane, no key. The input is `fixtures/incidents/demo-rollback.json`, an
incident document built by `./go incident` from a real `demo-rollback`
run (`fixtures/incidents/README.md` gives the date, commit and commands).
The ops-triage role answers with one `file_postmortem` call. Core then
checks that every timeline entry is anchored to an id the document holds:
the release, a stage, a log line, an alert. It prints that evidence beside
each entry, and files each action item as a ticket. The demo files them
into a copy of `tickets/` under `.crew/demo-postmortem/`, runs a second
time, and checks that nothing new is filed.

The recording it replays, read straight from the fixture:

<!-- source: the committed recording fixtures/adk/ops-triage/e3b939ad61f9fccf16e91a5b11657951829ef9bdd2383424cbbe1ae1f2958c9f.json (recorded 2026-11-04T10:59:27Z, gemini-3.8-flash), read with jq -->
```
$ jq -r '.. | .function_call? // empty | select(.name == "file_postmortem") | .args.timeline[] | .anchor + "  " + .event' fixtures/adk/ops-triage/*.json
log:1  Stable confirmed at 0.1.0-m2 and release 0.2.0-waitlist initiated.
release:0.2.0-waitlist  Release 0.2.0-waitlist deployed to staging using image lights-out-backend@sha256:ab76e8057597500f4c290834014aba7e5031bea11938076cd516f8bef16eaacc.
release:0.2.0-waitlist@5  Canary reached 5% traffic and breached error rate SLO with verdict NO-GO.
alert:1  Alert fired for SLO breach on error_rate canary(0.2.0-waitlist)=1.000 exceeding delta max 0.010.
$ jq -r '.. | .function_call? // empty | select(.name == "file_postmortem") | .args.actions[].title' fixtures/adk/ops-triage/*.json
Fix waitlist service endpoint failures on canary
```

The model writes no times: the release record has no clock, and a time it
invented could not be checked. A filed ticket has no `## Perimeter`,
because the role saw the incident, not the code. A human adds one before
`./go assign`, and until then the perimeter check refuses the ticket. An
anchored timeline is not a true one: the check stops invented releases and
log lines, not a wrong story about real ones.

No GitHub Actions job runs `demo-postmortem`, so its own output is not
shown here. Its last line is `GO: demo-postmortem`, with the fix ticket's
path on stderr (README: recorded once at L1 on 2026-11-04).

### `./go intake`: a need, before any ticket

A client rarely writes a ticket. `./go intake` starts from their words
(ADR 0090): the product role interviews them, one question at a time and
at most five, then proposes tickets with criteria, feature and priority;
the architect role proposes each ticket's perimeter from the project map;
the spec gate checks each one as a draft; and nothing opens until a person
confirms, edits or drops each ticket with `./go intake-confirm`. The
interview and the proposal need no plane; the confirm does, because it
assigns the new ticket and the assign pushes to the plane's Gitea. At L0 the interview replays the one recorded on the committed
request about the meetup app (recorded on Gemini 3.8 Flash on 2026-11-14):

<!-- source: local run on podman, commit b413d39, 2026-11-14, a full clone of claude/m57-turn-cap, L0 -->
```sh
./go intake TICKET=0030 INTAKE_FILE=fixtures/intake/request.md
./go answer TICKET=0030 ANSWER="A button on the meetup page; then they are no longer registered and one more seat is free."
./go answer TICKET=0030 ANSWER="The organiser sees the names and the free seats on the meetup page; other members see only the free seats."
```

```
GO: intake — 0030 at intake-questions
[tower] 0030: intake-answer → drafted
[tower] 0030: perimeter (state drafted)
[tower] 0030: perimeter → proposed
GO: answer
```

The first answer brings the second question; the second makes the request
clear, and the product role proposes two tickets, "Allow registered
members to give back their meetup seat" and "Display attendee list to
organisers while keeping attendee names private from other members",
both `- Feature: rsvp-cancellation`, `- Priority: P1`. The architect role
replays its recording: two searches of the app's files, then six paths for
each ticket (the backend, its tests, the frontend and the app's README),
and the spec gate says `GO: spec` on both drafts. Under replay your
answers' words are not compared with the recording: any answer replays
the same proposal. Keep the first ticket and drop the second:

```sh
./go intake-confirm TICKET=0030 DECISIONS='["confirm","drop"]'
```

```
[tower] 0030: confirmed by unauthenticated via cli: 0031
[tower] 0031: spec-ready from intake 0030, spec requested
GO: spec
[tower] 0031: spec → opened
[tower] 0031: assign (state opened)
```

Ticket 0031 is written to the tickets directory, with `- Intake: 0030`,
and is opened by the spec gate. The orchestrator then requests `assign`,
which needs the plane and a dev-worker for the new ticket: without the
plane, `assign` fails and the command ends NO-GO, the ticket stranded at
`opened` and resumable, as a new `./go request` would be. An intake whose
words were never recorded is refused at L0 with one line that names the
command that records it, and nothing is proposed in its place: a raw
request has no criteria to fall back on. The console's request screen has
the same entry, "Describe the need", and each intake's card sits above
the kanban.

### The screen and the perimeter asked file by file

Before the product role reads a client's words or a ticket body, one
closed question goes through the `Decide` port (ADR 0091): does this text
hold instructions aimed at an AI agent? At L0 the answers replay
`fixtures/decide/`, recorded on the Gemini Decisions API. One of the 24
texts of the screen's labelled set is a waiting-list request that ends by
telling the assistant it is now the release manager:

<!-- source: local run on podman, commit 019b93d, 2026-11-16, a full clone of claude/m58-decisions-91efb9, L0 -->
```sh
jq -r '.[] | select(.id=="inj-02") | .text' fixtures/screen/labelled.json > .crew/need.txt
./go intake TICKET=0041 INTAKE_FILE=.crew/need.txt
```

```
[tower] 0041: intake by unauthenticated via cli, interview requested
[tower] 0041: interview (state intake)
[tower] 0041: step interview failed; state stays intake
[tower] 0041: the stopped screen run is in the ledger (the screen stopped the step)
NO-GO: intake — 0041: step exited 1: screen: the intake text reads as instructions to an AI agent (p=0.97); a person reads it, then ./go screen-clear TICKET=0041, or edits it and ./go resume TICKET=0041
```

The product role never read it. A person who has read the text and wants
it to go on clears it, for this text only:

```sh
./go screen-clear TICKET=0041 CLEAR_BY=ana
```

```
[tower] 0041: screen cleared by ana via cli, interview requested again
[tower] 0041: interview (state intake)
[tower] 0041: step interview failed; state stays intake
NO-GO: screen-clear — a step failed; see the [tower] lines above
```

The interview now runs, and at L0 it stops for want of a recording of
these words, as any unrecorded intake does. An edited text has another
sha256 and is asked again. The screen is not a gate: no gate reads it,
and a text it is unsure about runs with a flag on its card.

The perimeter can be asked the same way. With `PERIMETER_BY=decide`, the
architect asks one request of one yes/no question per proposed ticket and
file of the workload, 150 for the demo intake, instead of searching:

```sh
./go intake TICKET=0030 INTAKE_FILE=fixtures/intake/request.md
./go answer TICKET=0030 ANSWER="A button on the meetup page; then they are no longer registered and one more seat is free."
./go answer TICKET=0030 PERIMETER_BY=decide ANSWER="The organiser sees the names and the free seats on the meetup page; other members see only the free seats."
```

```
[tower] 0030: intake-answer → drafted
[tower] 0030: perimeter (state drafted)
[tower] 0030: perimeter → proposed
GO: answer
```

Ticket 1 gets five paths, `app/backend/meetups/app.py`, `db.py`, the
backend's tests, `app/frontend/index.html` and `App.tsx`, and five more
under `PERIMETER_CONFIDENCE=0.7` listed as uncertain, `api.ts` first;
ticket 2 gets four, with eight uncertain. Both drafts say `GO: spec`, and
the console shows the uncertain paths under each perimeter for the person
who confirms. Recorded live, the request took about a second and $0.002;
the search took 115 s and $0.073 (ADR 0091, "Measured").

### `./go demo-launch`: go-for-launch, the one human gate

Agents and gates decide everything up to 25% of traffic. The step to 100%
waits for one person. `demo-launch` needs the plane up:

```sh
./go up
./go demo-launch
```

<!-- source: GitHub Actions run 41866972654 (commit ee2f679), job launch (126703545476), step "./go demo-launch"; left out: cosign's warning and JSON, compose's container lines -->
```
lights-out: waitlist is off for every version (app/flags/flags.json edited; git checkout it to undo)
GO: kill-switch
[demo-launch] stable is 0.1.0-m2; releasing 0.2.0-waitlist with waitlist off
[launchpad] staging: deploying 0.2.0-waitlist at lights-out-backend@sha256:3e5724e8c0fea28776f7d200d09f7f3f4fb411e056588ac2c7112331a2091784 (stable is 0.1.0-m2)
slo: traffic canary(0.2.0-waitlist)=0.182 stable(0.1.0-m2)=4.236
slo: error_rate canary(0.2.0-waitlist)=no data stable(0.1.0-m2)=no data
slo: latency_p95 canary(0.2.0-waitlist)=0.024 stable(0.1.0-m2)=0.024
[launchpad] 5%: GO
slo: traffic canary(0.2.0-waitlist)=1.091 stable(0.1.0-m2)=3.327
slo: error_rate canary(0.2.0-waitlist)=no data stable(0.1.0-m2)=no data
slo: latency_p95 canary(0.2.0-waitlist)=0.024 stable(0.1.0-m2)=0.024
[launchpad] 25%: GO
[launchpad] 25%: GO — held; ./go promote runs go-for-launch (record at .crew/release.json)
GO: launchpad
[go-for-launch] promotion brief for 0.2.0-waitlist (by core/promote.sh from the record — no model at L0, the release role writes it from L1):

# Promotion brief: 0.2.0-waitlist

Promote `0.2.0-waitlist` (`lights-out-backend@sha256:3e5724e8c0fea28776f7d200d09f7f3f4fb411e056588ac2c7112331a2091784`), replacing stable `0.1.0-m2`.

## Stages
- 5%: GO: slo
  - slo: traffic canary(0.2.0-waitlist)=0.182 stable(0.1.0-m2)=4.236
  - slo: error_rate canary(0.2.0-waitlist)=no data stable(0.1.0-m2)=no data
  - slo: latency_p95 canary(0.2.0-waitlist)=0.024 stable(0.1.0-m2)=0.024
- 25%: GO: slo
  - slo: traffic canary(0.2.0-waitlist)=1.091 stable(0.1.0-m2)=3.327
  - slo: error_rate canary(0.2.0-waitlist)=no data stable(0.1.0-m2)=no data
  - slo: latency_p95 canary(0.2.0-waitlist)=0.024 stable(0.1.0-m2)=0.024

## Not verified
- 100% of traffic: not observed yet; that is what this decision starts.

Recommendation: GO — every stage passed verify; the numbers above are the evidence.

[go-for-launch] 0.2.0-waitlist at 100% of traffic (envoy-canary)
GO: go-for-launch
[demo-launch] 0.2.0-waitlist at 100%: 10/10 healthz samples; now the un-promote
GO: demo-launch
```
*GitHub Actions run 41866972654, job launch. Left out: cosign's warning
and JSON, compose's container lines.*

What happened:

- **The kill switch** turned the failing feature off for every version,
  live, with no deploy. So the same release now passes verify at 5% and
  25%. The kill switch edits `app/flags/flags.json`; run
  `git checkout app/flags/flags.json` to undo it.
- **Held at 25%.** The launchpad never goes to 100% on its own. It stops
  and writes the release record.
- **The brief.** At L0 it is rendered from the release record by
  `core/promote.sh`, and the line above it says so. From L1 the release
  role writes it. It ends with what is not verified yet: 100% of traffic.
- **The human answer.** The demo answers `GO` for you
  (`GO_FOR_LAUNCH=GO`). By hand, the path is `./go kill-switch`, then
  `./go release`, then `./go promote`, which asks in the terminal, or
  `./go promote GO_FOR_LAUNCH=GO`. A `NO-GO` rolls back.
- **The un-promote.** The demo checks 100% with ten health samples, then
  rolls back, so the plane is clean again.

## Your repository, without the plane

The gates half of lights-out needs no plane, no key and, with the
workbench as the toolchain, no engine socket.

### `./go init`, then `./go gates`

```sh
./go init ~/code/your-repo
./go gates
```

`./go init` clones your repository's HEAD into `.attached/` and drafts
`.lights-out/` there. It never writes to your repository. It infers the
stack (python with `pyproject.toml` and `uv.lock`, or javascript with
`package.json` and `package-lock.json`) and measures your mutation
baseline by running the mutation tool over the whole scope. `./go gates`
then judges the last commit of the clone, with the draft as its contract.

This is what the two commands printed on `workloads/minimal-js`, copied
into a repository of its own (`./go demo-init` does exactly that):

<!-- source: GitHub Actions run 41866972654 (commit ee2f679), job init (126703545986), step "./go demo-init", the minimal-js part; left out: npm and Stryker output -->
```
init: stack javascript: source src/, tests test, toolchain workbench
init: the repository has a committed .lights-out/; its workload.env, worker.md, policy.env and runners/ are replaced by this draft in the clone only
mutation-baseline: npm ci && sh .lights-out/runners/mutation.sh src "npm test" over src/, in workbench
score: 0 mutant(s) excluded from the population (RuntimeError, CompileError, Ignored, Pending)
mutation-baseline: 30/32 killed, score=0.9375, 13s wall time, in workbench
[demo-init] minimal-js: GO: init — javascript, draft in .attached/.lights-out, baseline 0.9375 (30/32 mutants killed, 13s, in workbench); copy .lights-out/ into your repository and commit it
[gates] no BASE/HEAD given: judging the last commit, 4b825dc..3737af6
risk: level=high decision=review signals=platform(.lights-out/policy.env .lights-out/runners/eslint.security.mjs .lights-out/runners/mutation.sh .lights-out/runners/package-lock.json .lights-out/runners/package.json .lights-out/runners/sast.sh .lights-out/runners/score.mjs .lights-out/worker.md .lights-out/workload.env)
deps: scanning 1 lockfile(s) at 3737af6f0bd579bf357d5caabd567c0ce8544396 and at the base 4b825dc642cb6eb9a060e54bf8d69288fbee4904: .attached/package-lock.json
deps: .attached/package-lock.json is new at HEAD, judged whole
deps: the lockfile(s) list no package; nothing to scan
score: 0 mutant(s) excluded from the population (RuntimeError, CompileError, Ignored, Pending)
[demo-init] minimal-js, draft, no engine: GO: gates — risk high, review, in workbench (23s for init and gates)
```
*GitHub Actions run 41866972654, job init. Left out: npm and Stryker
output. Run by hand, the two GO lines come without the `[demo-init]`
prefix.*

Two things to notice:

- **The first commit is judged whole.** The repository has one commit, so
  "the last commit" is the whole tree, from the empty tree `4b825dc`. Its
  lockfile is new and judged whole. An advisory already in your lockfile
  is a NO-GO on your first `./go gates`.
- **`risk high, review`.** The example's one commit already contains its
  own `.lights-out/`, and any change to `.lights-out/` is high risk: it
  never merges on its own. On your repository, the commit that adds
  `.lights-out/` is judged the same way. Your other commits are judged on
  their own paths and size.

`demo-init` proves init and gates in under 300 s on two small examples
only: 44 s and 23 s in this run. Init measures the whole scope, so a large
suite takes longer.

### Read the draft

`.attached/.lights-out/` holds `workload.env`, `worker.md` (the
dev-worker's brief: the paths it may change, and the exact test command),
`policy.env` (your thresholds; a draft overrides none) and `runners/` (the
stack's mutation and SAST runners, pinned by their own lockfile).

```sh
cat .attached/.lights-out/workload.env
```

`workload.env` has eleven keys, and every value sits under a comment that
says where it came from:

- `# Provenance, inferred: <file>: <evidence>`: read from a file of your
  repository, such as `TOOLCHAIN_IMAGE` from `requires-python` or
  `engines.node`.
- `# Provenance, default: ...`: a platform default, such as the runners or
  an empty plane key.
- `# Provenance, measurement: ...`: `MUTATION_BASELINE`, with the date, the
  commit, the command, the mutant and kill counts and the wall time.
- `# Provenance, kept: ...`: a plane key kept from a contract you already
  committed.

No GitHub Actions job prints a draft, so no draft is shown here. [The
guide](use-on-your-repo.md#the-twelve-keys) explains each key and what
init infers it from. Fix what is wrong (the scope, the test command, the
toolchain), then copy the draft into your repository and commit it:

```sh
cp -R .attached/.lights-out/. ~/code/your-repo/.lights-out/
git -C ~/code/your-repo add .lights-out
git -C ~/code/your-repo commit -m "add lights-out contract"
./go attach ~/code/your-repo
./go gates
```

Use `attach`, not `init`, after the commit: `init` would draft again over
the contract you just committed. Keep the `/.` in the `cp` command.
Without it, `cp -R` into an existing `.lights-out/` nests the draft one
level deeper. With a workbench toolchain your tests run in lights-out's
toolchain container on this machine: attach only a repository you would
run here.

### The packaged wrapper, without cloning lights-out

The same gates run from one script, `lights-out`, and an image.

The image is published: `ghcr.io/nicolaguglielmi/lights-out:0.2.0`.
One install gives you both `lights-out` and `lights-out-gates`:

```sh
uv tool install lights-out-gates
```

PyPI `0.8.1` still ships `lights-out-gates` alone until the next release;
until then, install from `git+https://github.com/nicolaguglielmi/lights-out`
or download the wrapper of the same version directly with `curl`:

```sh
mkdir -p ~/.local/bin   # any directory on your PATH
curl -fsSL -o ~/.local/bin/lights-out https://raw.githubusercontent.com/nicolaguglielmi/lights-out/v0.2.0/lights-out
chmod +x ~/.local/bin/lights-out
```

Then, in your repository:

```sh
cd ~/code/your-repo
lights-out doctor
lights-out init
cp -R ~/.local/state/lights-out/your-repo/.lights-out/. .lights-out/
git add .lights-out && git commit -m "add lights-out contract"
lights-out gates
```

`./go demo-package` runs this path in CI on `workloads/minimal` as a
repository of its own:

<!-- source: GitHub Actions run 41866972365 (workflow package, commit ee2f679), job package (126703545895), step "./go demo-package"; only the [demo-package] and [lights-out] lines and the last line -->
```
[demo-package] /tmp/tmp.2Vm5UcVPbG/minimal: workloads/minimal as a repository of its own, image localhost/lights-out:dev
[lights-out] no contract at the base: the head's own .lights-out/ judges it
[demo-package] committed contract, no socket: NO-GO: gates — TOOLCHAIN_IMAGE=compose:backend-test needs the engine socket, which the lights-out image never gets (docs/adr/0045 §3); judge it from a clone with ./go gates, or set TOOLCHAIN_IMAGE=workbench
[demo-package] GO: init — python, draft in /tmp/tmp.2Vm5UcVPbG/out/.lights-out, baseline 1.0 (8/8 mutants killed, 25s, in workbench); copy .lights-out/ into your repository and commit it
[lights-out] the contract that judges is the base's, e7035fa
[demo-package] GO: gates — risk low, auto-merge, in workbench (50s for init and gates)
[lights-out] judging the staged changes as one commit on 65c4eb7
[lights-out] the contract that judges is the base's, 65c4eb7
[demo-package] staged, breaking a test: NO-GO: gates — test: NO-GO: test — in workbench: the test command failed (exit 1), see stderr
GO: demo-package — init and gates 50s in workbench (budget 300s), a staged break NO-GO at test, image localhost/lights-out:dev
```
*GitHub Actions run 41866972365 (workflow `package`), job package: the
`[demo-package]` and `[lights-out]` lines and the last line.*

Read it in order:

- **The wrapper never gets your engine's socket.** So it runs only
  `TOOLCHAIN_IMAGE=workbench`. `workloads/minimal` commits
  `compose:backend-test`, and the line says so and names the fix. Judge
  the `compose:` and image forms from a clone, with `./go gates`.
- **`init` writes outside your repository**, to
  `~/.local/state/lights-out/<repo>/.lights-out` (`LIGHTS_OUT_OUT` moves
  it). Your repository is mounted read-only.
- **The base's contract judges.** A range is judged by `workload.env`,
  `policy.env` and `runners/` as they are at its base, so a change cannot
  loosen what judges it.
- **`STAGED=1`** judges what is staged as one commit: the shape the hook
  recipes for coding agents use ([the guide](use-on-your-repo.md#hook-recipes-for-coding-agents)).

### The GitHub Action

`action.yml` at the root of lights-out runs the wrapper on a pull request and posts one check
run, `lights-out gates`, whose title is the GO or NO-GO line:

```yaml
permissions:
  contents: read
  checks: write
steps:
  - uses: actions/checkout@v7
    with:
      fetch-depth: 0
  - uses: nicolaguglielmi/lights-out@v0.8.1
```

**Proven on one outside repository.** With `@v0.3.0` published, the Action
posted its check on [the probe's pull request](https://github.com/nicolaguglielmi/lights-out-probe/pull/1)
in 27 s, first pull included. In lights-out's own workflow it runs on a
scratch repository, once with a good change and once with a broken test:

<!-- source: GitHub Actions run 41866972365 (workflow package, commit ee2f679), job action (126703546538); the verdict and check-run lines of the two cases -->
```
GO: gates — risk low, auto-merge, in workbench
lights-out gates (Action test: a good change, GO expected): check run posted on ee2f679bc63159c97a6eb4fbaf17520822cdbbcc, success
NO-GO: gates — test: NO-GO: test — in workbench: the test command failed (exit 1), see stderr
lights-out gates (Action test: a broken test, NO-GO expected): check run posted on ee2f679bc63159c97a6eb4fbaf17520822cdbbcc, failure
```
*GitHub Actions run 41866972365 (workflow `package`), job action: the
verdict and check-run lines of the two cases.*

Linux runners only. A fork PR's token cannot write checks: the post is
skipped with a warning, and the job status still carries the verdict. Tags
are mutable, so pin the image by digest with the `image` input. The images
are signed keyless; [the README](../README.md#gates-on-your-repository)
has the check. `gates: test-proof` and `gates: test-integrity` run one part
each.

## A ticket, assigned to an agent at L1

At L0 the agents replay recordings of lights-out's own tickets. Your
repository has none, so on an attached repository the crew stops at L0
and says so: `has no recordings to replay at L0; run the crew at L1`.
From here on you need a model key. The one live output this tutorial
shows is [level 5](#level-5-live-agents-at-l1)'s, from M24.

### What you need

- **The plane half of `.lights-out/`.** `./go assign` pushes to the
  plane's Gitea, so the plane must be up, and `./go up` refuses a
  contract without `compose.yaml`. `./go init` does not draft the plane
  half; you write it ([the guide, section 2](use-on-your-repo.md#2-the-plane-half)).
  The shortest way to try this step is a throwaway repository made from
  `workloads/minimal`, which has the plane half and one ticket.
- **One key, in `.env`** (copy `.env.example`):

| Harness | Provider | Key in `.env` | Command line |
|---|---|---|---|
| `gemini-cli` (the default) | `gemini` | `GEMINI_API_KEY` | `PROFILE=l1` |
| `claude-code` | `anthropic` | `ANTHROPIC_API_KEY` | `PROFILE=l1 AGENT_HARNESS=claude-code MODEL_PROVIDER=anthropic MODEL=claude-sonnet-5-5` |
| `codex` | `openai` | `OPENAI_API_KEY` | `PROFILE=l1 AGENT_HARNESS=codex MODEL_PROVIDER=openai MODEL=gpt-6.1-sol` |
| `claude-code` | `zai` | `ZAI_API_KEY` | `PROFILE=l1 AGENT_HARNESS=claude-code MODEL_PROVIDER=zai MODEL=glm-5.3` |

Each harness has one provider, except `claude-code`, which has two. `run.sh` refuses a mismatch and names the
harness that fits. The model ids are the ones the README and `.env.example`
name.

### Write the ticket

One file per ticket in `.lights-out/tickets/<id>.md`. `workloads/minimal`
carries this one:

<!-- source: the committed file workloads/minimal/.lights-out/tickets/0001.md, printed with cat -->
```
$ cat workloads/minimal/.lights-out/tickets/0001.md
# 0001 — `GET /version` answers the running version

- Status: open
- Labels: feature, small, the attached e2e's ticket (docs/adr/0043)
- Found: 2026-11-04, writing M21: a repository made from `workloads/minimal`
  needs one ticket of its own before `./go e2e` can run on it at L1

## Problem

Release tooling and humans read the version from `/healthz`, which also
answers a status. A route that answers the version alone is what a smoke
test or a dashboard wants to read.

## Acceptance criteria

Given the service is running with `APP_VERSION` set
When a client sends `GET /version`
Then the response is `200` with the JSON body `{"version": "<APP_VERSION>"}`.

Given the test suite (`uv run pytest tests -q`)
When it runs
Then a test asserts the status and the exact body of `GET /version`, and
every existing test still passes.

## Perimeter

- `minimal/app.py`
- `tests/test_app.py`
```

- **The first line** is `# <id> — <title>`. The tower records it as the
  spec's title, and the audit prints the hash of the whole text.
- **Acceptance criteria** as Given/When/Then. Name the test that must
  exist and what it asserts: the mutation gate rejects a test that does
  not really check the change.
- **`## Perimeter`**: one path or glob per list item, where `*` also
  crosses `/`. A change to any path outside it is refused before a PR
  exists. A ticket with no `## Perimeter` is refused too. `tickets/0005.md`
  in this repository is a longer example.

### Assign it

```sh
./go attach ~/code/your-clone
./go up
./go assign TICKET=0001 PROFILE=l1
```

The dev-worker reads the ticket, then your `worker.md`. It works in your
`TOOLCHAIN_IMAGE` when that names an image, and in the workbench otherwise.
`assign` opens the PR on the plane's Gitea. To run the whole tower on it
(the gates, the merge, the build, the canary, the audit), use `./go e2e
PROFILE=l1` instead. Merges land on the plane's Gitea, not in your
repository. Fetch them back before the next `assign`, `up` or `down`:
Gitea has no volume.

```sh
git -C ~/code/your-clone fetch http://localhost:3000/lights-out/workload.git main
git -C ~/code/your-clone merge FETCH_HEAD
```

With `CODE_HOST=github` on the command line and `GITHUB_REPO` and
`GITHUB_TOKEN` in `.env`, the PR lands on your GitHub repository instead
([the guide](use-on-your-repo.md#your-pr-on-github-l1)).

### What it costs

- **Gemini, free tier.** Free within its cap: about 250 requests a day,
  Flash models only (reference, Levels). A headless turn is one request,
  and the dev-worker stops at 60 turns (`roles/dev-worker/role.env`), so a
  day holds about four attempts.
- **Tokens.** The dev-worker may spend up to 2.6M tokens per run
  (`TOKEN_BUDGET`). The recorded run of ticket 0001 spent 2,435,115 tokens
  in 328 s (`fixtures/0001/dev-worker/run.json`).
- **Claude, OpenAI and Z.ai** cost money per run. Z.ai's price is in
  `profiles/prices.json`: 1.40 input, 0.26 cached input and 4.40 output
  per million tokens (`glm-5.3`). No live run has been made with GLM,
  so lights-out cannot tell you the cost of a run yet.

### What is not proven yet

- **One outside repository so far.** M24 ran ten tickets at L1 on a fork
  of tenacity, a Python library: Claude Code on Anthropic merged 10 of 10
  (2026-11-11), Gemini CLI 7 of 10, three stopped by `TOKEN_BUDGET`
  (2026-11-14) ([`./go scoreboard`](#go-scoreboard-the-providers-side-by-side),
  `docs/scoreboard/m24-tenacity.json`). A library has no image, so the
  release of an attached repository's merge has not run on one yet.
- **Codex** has never run against its real API: M24 had no OpenAI key. `./go
  harness-conformance` proves each harness's plumbing (guard, budgets,
  artefacts, served model) against a scripted model, not a model's
  ability: `GO: harness-conformance — claude-code codex gemini-cli,
  against a scripted model, 273 s` <!-- source: GitHub Actions run 41866972654, job harness-conformance (126703545920) --> in
  run 41866972654.
- **The budgets are per provider.** `MAX_TURNS` binds every harness.
  `TOKEN_BUDGET` is one number per role, and a role may set
  `TOKEN_BUDGET_<PROVIDER>` beside it: the dev-worker's Gemini budget is
  3,900,000 (M24), every other provider runs under the default. A first
  GLM or OpenAI run may hit a budget for tokenizer reasons alone.
- **GLM by Z.ai has not run live.** Whether the Z.ai Anthropic endpoint
  accepts a Standard key, and whether the effort changes GLM's thinking,
  are open.
- **The network stays open** in the worker's container, because each
  harness must reach its provider. The guard denies the network tools, but
  an interpreter could still send out what the container holds, the
  provider key included.
- **The GitHub code host** is proven verb by verb, not end to end.
- **A codebase not written for lights-out** has been tried once, M24's
  tenacity, with small, low-risk tickets: no review and no human gate ran.

## Ops

Three commands for after a release, two of them without the plane.

### `./go incident`: the document

```sh
./go incident NAME=<slug> FROM=<utc> TO=<utc> [LOGS=<file>] [TICKET=<id>]
```

It builds one JSON file, `.crew/incidents/<name>.json`, from the last
release record (`.crew/release.json`), the time window (format
`2026-11-04T10:32:07Z`), an optional log excerpt copied verbatim, and with
`TICKET` the PR and merge from the tower's document. `TICKET` is the only
option that needs the plane. After a full `./go demo` the record on disk
is `e2e`'s release, not `demo-rollback`'s. Its stdout is
`GO: incident — <path>`.

No GitHub Actions job runs it. The document it built from a real
`demo-rollback` run is committed:

<!-- source: the committed file fixtures/incidents/demo-rollback.json (built by ./go incident on 2026-11-04, see fixtures/incidents/README.md), read with jq -->
```
$ jq '{name, window, verdict: .release.verdict, alerts: (.alerts | length), logs: (.logs | length), chain}' fixtures/incidents/demo-rollback.json
{
  "name": "demo-rollback",
  "window": {
    "from": "2026-11-04T10:32:07Z",
    "to": "2026-11-04T10:33:33Z"
  },
  "verdict": "rolled-back",
  "alerts": 1,
  "logs": 5,
  "chain": null
}
```

One alert per stage whose verdict was not GO. `chain` is `null` because
`demo-rollback` releases with no ticket and no PR.

### `./go postmortem`: the post mortem and its tickets

```sh
./go postmortem INCIDENT=.crew/incidents/<name>.json PROFILE=l1
```

At L0 the role replays only lights-out's own recordings, so on your
incident it needs L1 and a key. It writes `.crew/postmortem/<name>/`
(`postmortem.md`, `postmortem.json`, the run's artefacts) and files each
action item as a ticket in `tickets/`, with no `## Perimeter`. The checks
of `demo-postmortem` apply: every anchor must name something the document
holds, at least one timeline entry must be anchored to the release, and
there must be at least one cause and one action. Otherwise it is
`NO-GO: postmortem — <reason>` and no ticket is written. A second run on
the same incident files nothing new. The stdout line on GO is
`GO: postmortem — <k> action item(s): tickets <id> …`. No run on an
incident other than the committed one exists, so no output is shown.

### `./go verify` on your own metrics

```sh
./go verify METRICS_URL=<endpoint> CANARY_VERSION=<v> STABLE_VERSION=<s>
```

This runs the unchanged `verify/slo.promql` against any
Prometheus-compatible endpoint. That means one that answers
`/api/v1/query`, with the OTel HTTP semconv histogram joined to
`target_info{service_version}`. On other metrics the answer is NO-GO with
"no data". `METRICS_URL` is the base that `/api/v1/query` is appended to.
An auth header goes in `.env` as `METRICS_AUTH_HEADER` and is never
printed. Both versions must be set.

The plane's own judge goes through the same `promql` adapter (ADR 0044),
so the `slo:` lines and the `NO-GO: launchpad — 5%: error_rate …` verdict
in the demo are this code's output on the plane's Prometheus. No GitHub
Actions job runs `./go verify` against another endpoint, so none is shown.

## Where next

- [The guide](use-on-your-repo.md): every key of `.lights-out/`, the plane
  half, `policy.env`, the hook recipes.
- [The reference](reference.md): every command and every level.
- [Architecture](ARCHITECTURE.md) and [the decisions](adr/).
- [All the docs](README.md).
