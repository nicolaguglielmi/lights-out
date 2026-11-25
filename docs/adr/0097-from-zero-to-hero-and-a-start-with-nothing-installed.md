# ADR 0097 — From zero to hero: a levelled path at the head of the tutorial, a dev container for a start with nothing installed, and the frozen console first on the site

- Status: accepted (M64)
- Date: 2026-11-16
- Milestone: M64 (docs/PLAN.md, "M64 — Da zero a eroe, e un avvio senza
  installare niente (ADR 0097)")
- Amends: ADR 0048 (the landing page's first screen); ADR 0093 §4 (where
  a person goes after the demo, in a codespace). ADR 0042's rule that the
  plane half is never drafted stands (§4).

## Context

ADR 0093 made the first run smooth. The owner wants a "zero to hero"
path and an even simpler start. Three steps remain:

1. **Installing podman.** On a Mac a person creates and starts the
   machine by hand, and the first build downloads about 1 GB.
2. **The wait before the wow.** `./go demo` takes 370–480 s on a clean
   runner (ADR 0093) before the rollback can be seen. The site already
   has the frozen console of a real run (ADR 0089), but it sits in the
   fourth section of the page, behind the board's video.
3. **The cliff to the plane on your own app.** `compose.yaml`,
   `/healthz`, `/metrics`, a kill switch and fixtures are written by hand.

Four documents say how to start, each in its own order: the README
("Five ways in", steps 0–4), the tutorial (its own five steps), the guide
(`docs/use-on-your-repo.md`, sections 1–5) and the site ("Try it"). A
person cannot tell from any of them how far they are, or what is next.

### Facts this decision rests on, verified on 2026-11-16

- **The dev container spec.** `hostRequirements` takes `cpus`, `memory`
  and `storage` as minimums; `privileged` is "required for things like
  Docker-in-Docker" (https://containers.dev/implementors/json_reference/).
- **Docker-in-Docker.** `ghcr.io/devcontainers/features/docker-in-docker`
  is at 4.1.3 (its `devcontainer-feature.json` on `main`, and the tags on
  ghcr.io). It runs the container privileged, keeps `/var/lib/docker` on
  a volume and installs Compose v2 by default
  (https://github.com/devcontainers/features/tree/main/src/docker-in-docker).
- **The base image.** `mcr.microsoft.com/devcontainers/base:3.0.13-noble`,
  Ubuntu 24.04 with git and curl, index digest
  `sha256:48be4adec1c1ecbe030064700aef85382f5205786a8f82937fc44a7473717eae`
  (read from mcr.microsoft.com).
- **Codespaces machines.** From 2 cores, 8 GB RAM and 32 GB storage up to
  32 cores, 128 GB RAM and 128 GB storage
  (https://docs.github.com/en/codespaces/about-codespaces/what-are-codespaces);
  a repository's `hostRequirements` hides the machines below it
  (https://docs.github.com/en/codespaces/setting-up-your-project-for-codespaces/configuring-dev-containers/setting-a-minimum-specification-for-codespace-machines).
- **The free quota.** A personal account on GitHub Free gets 120 core
  hours and 15 GB-month of storage a month; on Pro, 180 core hours and
  20 GB-month. A machine spends its core count per hour: a 4-core machine
  spends 4 core hours an hour, so Free covers 30 hours of it
  (https://docs.github.com/en/billing/concepts/product-billing/github-codespaces).
- **The proof without anyone's account.** `devcontainers/ci`
  v0.3.1900000450 (2026-06-01, commit 513af61) builds a dev container with
  the dev container CLI on a GitHub runner and runs `runCmd` inside it
  (https://github.com/devcontainers/ci).
- **The button.** `https://codespaces.new/<owner>/<repo>` redirects to
  `https://github.com/codespaces/new/<owner>/<repo>`, which creates a
  codespace from the default branch; `?quickstart=1` reuses one the
  person already has. The badge is `https://github.com/codespaces/badge.svg`.

## Decision

### 1. One path, seven levels, at the head of the tutorial

`docs/tutorial.md` opens with **"From zero to hero"**: seven levels, each a
section `### Level <n>: <title>` with, in this order, four labelled lines,
the commands, and one verdict line:

- **Time:** how long it takes, with where the number comes from;
- **You need:** what the level adds to the one before;
- **You will see:** what is on the screen at the end;
- **Go deeper:** links into the tutorial's own sections, the reference and
  the guide, instead of copying them;
- the commands, then a fenced block whose last line is the verdict that
  proves the level, right after a `<!-- source: … -->` comment naming the
  run it was copied from, as every output block of the tutorial already
  has.

| Level | What you do | Time | The line that proves it |
|---|---|---|---|
| 0 | See it: the frozen console of a real run on the site | 1 min | `NO-GO: launchpad — 5%: error_rate … → rolled back`, in ticket 0001's timeline |
| 1 | `./go demo`: the canary rolls back by itself | about 10 min | `GO: demo, …` |
| 2 | The gates on your repository: `./go try` or the Action on a pull request | about 15 min | `GO: try …` |
| 3 | Your thresholds (`policy.env`), a ticket, the intake (`./go intake`) | about 30 min | `GO: intake — …` |
| 4 | Your service in the plane: canary and rollback on your code | 1–2 h | `GO: demo-rollback` |
| 5 | Live agents at L1, with a budget and a provider you choose | about 30 min | `GO: gates — …` of a live run |
| 6 | Your CI/CD: Flagger, GitLab CI, Jenkins | it depends | `GO: flagger-proof …`, `GO: demo-gitlab-ci …`, `GO: demo-jenkins …` |

Rules:

a. **Every command and every output comes from a real run**, named in its
   source comment: a GitHub Actions run id, or a local run with its commit
   and date. Nothing is invented. Where no real output exists, the level
   says so and shows none.

b. **Level 0's line is a NO-GO.** It is the canary's own verdict, the one
   the product exists for: at level 0 a person has arrived when they see
   it. Every other level ends on a `GO`.

c. **Level 5 is a documented path, not run for this milestone.** It needs
   a key and money. Its output is copied from a live run already recorded
   in this repository's docs, named as such, never from a new one.

d. **The times.** Levels 1, 2, 4 and 6 come from the runs their blocks are
   copied from, rounded up. Level 0's minute is the page's load and one
   click (a decision). Level 3's 30 minutes and level 4's 1–2 hours count
   the person's own writing (a decision): the commands themselves take
   seconds to minutes, and the level says that number too.

e. **Remove what the path makes redundant** (CLAUDE.md, "before adding,
   see what you can remove"): the README's "Five ways in" table and its
   "0. Look" section become the levels table, each row linking to its
   level; the tutorial's five-step list becomes the levels; the README's
   numbered headings lose their numbers, so no page has two numbering
   schemes. The guide keeps its numbered sections: they are a reference
   for `.lights-out/`, which the levels link into.

### 2. The second door: a dev container, proved on GitHub Actions

`.devcontainer/devcontainer.json`:

- the base image above, pinned by digest; the Docker-in-Docker feature at
  4.1.3; `CONTAINER_ENGINE=docker`, which `./go` already supports and CI
  already runs;
- **Docker-in-Docker, not docker-outside-of-docker**: `./go` mounts the
  checkout into its toolchain container at its own path, so the engine must
  see the same filesystem as the shell that runs `./go`;
- `hostRequirements`: 4 CPUs, 8 GB of memory, 32 GB of storage.
  Provenance: the CPU of the clean runner on which `./go demo` measured
  370–480 s (ADR 0093), a decision not to offer a 2-core machine that has
  not been measured; memory over the preflight's 3,800 MiB floor (ADR 0093
  §1b); storage the smallest Codespaces offers, which holds the 8,000 MB
  the preflight asks for (ADR 0093 §1c);
- the console's port 8098, the app's 8080, Gitea's 3000 and Prometheus's
  9090 forwarded, and one line on attach: run `./go demo`, then
  `./go console` and open the forwarded 8098. In a codespace the demo's
  `file://` link to the frozen console does not open in the person's
  browser: the console served on a forwarded port does.

**The proof.** The `stranger` workflow gets a `codespace` job:
`devcontainers/ci`, pinned by commit, builds `.devcontainer/` and runs
`./go demo` inside it, timed, with the demo's own verdict and its 900 s
budget (plan §2). It runs on every full push, beside the podman `demo`
job. It proves the container and the demo in it, not Codespaces' VM: the
runner is a different machine with the same 4 CPUs.

**The button** goes in the README's quick start and in the site's "Try
it", each time with the same sentence: it needs a GitHub account and
spends that account's Codespaces quota (a demo takes about one 4-core
hour's share, 4 core hours of the 120 a Free account gets). The golden
rule's door stays `git clone && ./go demo` with podman on your own
machine; the button is a second door, never the first. The site shows a
text link, not the badge image: the page loads nothing from anyone else
(ADR 0048).

### 3. The site's first screen: see it, run it, try it

The hero's three actions, in this order:

1. **"See it in 10 seconds"**, the primary button, opens the frozen
   console on ticket 0001 (`console/snapshot.html#ticket/0001`): a real
   `./go demo` run, read-only, down to the rollback and its reason.
2. **"Run it"** goes to "Try it", which now offers the two doors: local
   (`git clone && ./go demo`) and Codespaces.
3. **"Try it on your repo"** goes to the gates on your repository.

The tour's video stays on the page, below the three actions. The console
is linked, not embedded: the snapshot is 1.4 MB, and the first screen
loads nothing a visitor did not ask for. Contrast stays at WCAG 2.1 AA in
both colour schemes (`.impeccable/config.json`'s findings, measured
rendered as for commit 1bb8fec); the site has one dark palette in both,
and the console it opens follows the visitor's scheme.

### 4. Level 4: no drafted plane half

A `./go init`-style draft of the plane half from a Dockerfile or a compose
file was considered and is **not built**:

- What a draft could write without reading the code is mechanical: three
  compose services (`backend`, `backend-canary`, `backend-test`) with the
  build context, a flag file with `release-freeze`, and smoke and load
  scripts that `curl` one path. `workloads/minimal/.lights-out/` already
  holds exactly that, in about 90 lines a person can copy.
- What makes a canary judgeable lives in the application's code: `GET
  /healthz` and `GET /metrics` on `:8000`, the OTel HTTP semconv histogram
  joined to `target_info{service_version}`, and a kill switch the app reads
  from flagd. A draft cannot write those. A drafted compose file that
  `./go up` accepts would bring up a plane whose canary verify cannot
  judge, which is what ADR 0042 and the guide call a hidden mock.

Level 4 makes the cliff smaller with what exists: copy the example's plane
half, the four things your service must do (each with the file of the
example that does it), then the six commands `./go demo-seam` runs, ending
on `GO: demo-rollback`.

### 5. The check: `ci/zero-to-hero.sh`

A new ci check, logic in `ci/checks/zero_to_hero.py` and tests in
`ci/tests/test_zero_to_hero.py` (ADR 0094), on `./go ci`'s list. NO-GO,
naming the file and what is missing, when:

a. `docs/tutorial.md`'s first section is not "From zero to hero" with the
   seven levels in order, or a level lacks one of its four labelled lines,
   or its last fenced block does not end on a verdict line (level 0 only
   may end on `NO-GO`; every other on `GO`), or that block has no source
   comment right before it;
b. the README does not link each of the seven levels, or still has the
   "Five ways in" table;
c. `.devcontainer/devcontainer.json` (comments stripped) does not pin its
   image by digest, does not pin the Docker-in-Docker feature to an exact
   version, does not set `CONTAINER_ENGINE=docker`, or asks for fewer than
   4 CPUs or less storage than the preflight's disk need;
d. the `stranger` workflow has no job that runs `./go demo` through
   `devcontainers/ci` pinned by a full commit;
e. the README or the site links the Codespaces door
   (`codespaces.new/…`) without saying, within 400 characters of the link,
   that it needs a GitHub account and uses Codespaces quota;
f. the site's hero does not offer the frozen console first.

Each case is shown to fail on a planted tree in `ci/tests/`.

## Tests

Written from §5 before the check: `ci/tests/test_zero_to_hero.py` plants a
good tree (seven levels, a README table, a dev container, a workflow, a
site), shows it GO, then breaks one thing per case and asks for the NO-GO
and its reason: a missing level; levels out of order; a level without
"Time:"; level 3 ending on a NO-GO; a level without a source comment; the
README keeping "Five ways in"; an image by tag; a feature at `:4`; no
`CONTAINER_ENGINE`; 2 CPUs; a workflow job pinned to `@v0.3`; a Codespaces
link with no word of the quota; a site whose first hero action is the
tour. The acceptance: `./go ci` GO from a full clone, `./go demo` green,
the `codespace` job green with its time.

## Alternatives rejected

- **A separate `docs/zero-to-hero.md`.** A fourth document saying how to
  start, in a fifth order. The path is the tutorial's opening.
- **Podman inside the dev container.** Nested rootless podman in a
  container needs user namespaces and a storage driver Codespaces does not
  promise; docker is what Codespaces documents and CI already runs.
- **docker-outside-of-docker.** The host's engine would see bind mounts at
  the host's paths, not the container's: `./go`'s mounts break.
- **Codespaces prebuilds.** They spend the owner's Actions minutes and
  storage on every push, to save the toolchain build (about 50 s of a
  demo). Not worth it before anyone asks.
- **The frozen console embedded in the hero.** 1.4 MB on every visit, a
  second scrolling page inside the first. A link opens it in one click.
- **A drafted plane half** (§4).

## After

Measured on 2026-11-16, the first run of the `codespace` job (stranger run
43968199663, job 133653156720, commit f205507): the dev container built
and started in 57 s (22:15:14 to 22:16:11), then `GO: demo, milestone M9,
669s to rollback (budget 900s, plan §2)`, exit 0. The steps: toolchain
image 42 s, plane 91 s, the failing canary 65 s, ticket 0001 4 min 17 s,
the repository's own checks beside the plane 212 s (podman's `demo` job on
a plain runner: 38 s), the console 1 s. The engine inside the container
held 4.59 GB of images, 3.49 GB of containers and 1.76 GB of build cache
at the end, under the 32 GB `hostRequirements` asks for. It is slower than
the plain runner's 427 s mostly in the repository's checks, which share 4
CPUs with the plane under docker-in-docker; it stays under the plan's
900 s, and the level's "about 10 minutes" holds within a minute.

## Consequences

- A person can see the rollback in one click, run the demo with nothing
  installed if they have a GitHub account, and knows at every level how
  far they are and what the next one costs.
- One more ci check, and one more job in `stranger` (about the length of
  its `demo` job). The dev container is proved on every full push, so a
  change to `./go` that breaks it under docker-in-docker is caught.
- Codespaces is not proven on Codespaces itself by CI: no account of the
  owner's is used. The first person to press the button is the first run
  on a real codespace; the README says so.
