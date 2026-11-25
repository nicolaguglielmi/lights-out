# ADR 0042 — Gates anywhere: a workload declares how its tests, SAST and mutation run (`TEST_CMD`, `SAST_CMD`, `MUTATION_CMD`) and where (`TOOLCHAIN_IMAGE`: `workbench`, `compose:<service>` or an image), the three gates run that command through one function and say where on their line, `./go gates` needs no engine socket when the toolchain is the workbench, and `./go init` drafts the gates half of `.lights-out/` in the clone with a measured baseline

- Status: accepted (amends ADR 0030 §2 [eleven keys], §3 [runners are commands, not fixed in-image paths; the plane half is required only to run the plane], §5 [`ci/workloads.sh`]; ADR 0037 §1 [attach without a contract], §3 [an attached range's secret scan is its history only], §7 [checks]; ADR 0015 §2 [`gates` defaults its range and no longer requires the socket], §3–§4 [mutation and SAST read a command's line, never a tool]; ADR 0011 §4, ADR 0015 §2 and CLAUDE.md's output contract [`GO: <name> — <detail>`]; ADR 0017 §3 [the worker image's base]; plan §16 M20 [no semgrep; a `compose:` workload's worker stays on the workbench])
- Date: 2026-11-03
- Milestone: M20

## Context

Plan §16 adds a second golden-rule measure: `./go init <repo> && ./go gates`
under 300 s on a repository, no `./go up`, no socket, no key. Today:
`gates/test.sh`, `sast.sh` and `mutation.sh` call `docker-compose … backend-test`;
the port fixes the runners at `tests/{mutation,sast}/run.sh` in the test image
(ADR 0030 §3); `./go` dies without the socket for `gates`; `core/attach.sh` and
the Makefile's `include $(WORKLOAD_CONTRACT)/workload.env` refuse a repository
with no contract; `make gates` without `BASE`/`HEAD` is a NO-GO; the worker
image is the workbench plus gemini-cli, so every repository gets Python 3.13
and Node 24; and both workloads are Python (ADR 0030's "one that will hurt").
The workbench already holds Debian trixie, python3 3.13, uv 0.12.13, node
24.21.0 with npm, git and jq (`workbench/Containerfile`).

## Decision

1. **The runner contract: `workload.env` has eleven keys.** New: `TEST_CMD`,
   `SAST_CMD`, `MUTATION_CMD`, `TOOLCHAIN_IMAGE`. Each `*_CMD` is one
   `sh -c` line, run from `$WORKLOAD_TEST_ROOT` under the repository root
   (`compose:`: the image's own working directory), with ADR 0030 §3's
   output contracts unchanged: the
   test command's exit code is the verdict; the SAST command prints one line
   `{"findings": N}`, non-zero only if the tool failed; the mutation command
   reads `MUTATION_LINES` (ADR 0015 §3's wire format, `all` for the whole
   scope) and prints one line `{"total","killed","survived","score"}`, `total`
   counting valid mutants only, survivors as `SURVIVOR file:line operator` on
   stderr (what the rework loop lifts, ADR 0038 §5). The gate reads the
   last non-empty stdout line: what a command prints before its runner
   (an `npm ci` ahead of it) is not the verdict. The gate reads a score,
   never a tool: cosmic-ray and Stryker are two commands behind one line.
   `ci/workloads.sh` checks the four keys: non-empty commands, a known form.
2. **`TOOLCHAIN_IMAGE` has three forms.** `workbench`: the command runs in
   the workbench itself, no engine. `compose:<service>`: a profile-`test`
   service of the contract's `compose.yaml`, built, then `docker-compose
   --profile test run --rm -T <service> sh -c "<cmd>"` under its own project
   name, torn down after; this is plan §16's "the repository's Containerfile",
   through the fragment that builds it. The bundled app and workloads/minimal's
   committed contract keep `compose:backend-test`. An image reference
   (`docker.io/library/node:24`, a digest accepted): a throwaway
   image built through compose (`FROM <ref>`, `COPY . /work`), the command
   run from `/work/$WORKLOAD_TEST_ROOT`; nothing of the host is mounted,
   and the judged code cannot write into the clone (Resolved, 1). One function in a new
   `core/toolchain.sh`, sourced by the three gates and by init, resolves the
   form and runs the command, passing `MUTATION_LINES`, `TEST_CMD` and the
   workload's path keys into its environment; the workbench form sets
   `UV_PYTHON_DOWNLOADS=never`, so a mismatched Python fails loudly instead
   of downloading one. No fourth "build the Containerfile" form: `compose:`
   is it. Whether the workbench fits is decided once, by `./go init` (6),
   never probed at gate time.
3. **Where it ran goes on the line.** `ci/lib.sh` gains `go_with DETAIL`
   beside `go`: `GO: <name>` or `GO: <name> — <detail>`. The three gates print
   `GO: test — in workbench` / `— in compose:backend-test` / `— in
   docker.io/library/node:24@sha256:…`, and every NO-GO reason of theirs starts
   `in <where>:`. `ci/contract.sh` accepts the detail form for GO (`make
   gates`, `attached` already print it); `make gates` matches both. This
   amends the output contract (CLAUDE.md, ADR 0011 §4, ADR 0015 §2):
   CLAUDE.md's paragraph gains "optionally followed by ` — <detail>`".
4. **The no-socket path.** `./go` mounts the socket for `gates`, `init` and
   `mutation-baseline` when it exists and says on stderr when it does not;
   other targets still require it. `core/toolchain.sh` NO-GOes naming the
   socket when the form needs the engine and the socket is absent. `risk`,
   `secrets`, `deps` and the perimeter already run in the bare workbench
   (`deps` needs the network, ADR 0015 §5). On an attached range
   `ci/secrets.sh` runs `gitleaks git` only, no `gitleaks dir`: the clone's
   tree is HEAD plus what lights-out put there (the draft, `.venv`,
   `node_modules`), and scanning `node_modules` is slow and the wrong
   subject. Lost: nothing a user had: attach clones HEAD, so an uncommitted
   secret never reaches the clone (ADR 0037 §1); a line at HEAD is in the
   commit that added it.
5. **`./go gates` with no range judges the last commit.** In the `gates`
   recipe only, never as a make variable (a cold `ci/contract.sh` must still
   see no range): `BASE` defaults to `HEAD~1` when it exists, else the empty
   tree (`git hash-object -t tree /dev/null`), `HEAD` to `HEAD`.
   `require_range` takes a tree as base as is (no merge-base). On a
   single-commit repository the last commit is the whole tree: mutation
   re-measures the whole scope, which is what init measured.
6. **`./go attach` and `./go init`.** Attach accepts a repository without
   `.lights-out/workload.env` at HEAD, clones it, prints `attach: <repo> has
   no .lights-out/ at HEAD; ./go init drafts one` and skips the contract
   check. `./go init [<path>]` (attach first when given a path) runs `make
   init`: `core/init.py` (stdlib `tomllib`, `json`) reads the clone, infers
   python (`pyproject.toml`/`uv.lock`) or javascript
   (`package.json`/`package-lock.json`), and NO-GOes naming both when
   neither is there. It writes into `.attached/.lights-out/` only, never
   into the user's repository (ADR 0037 §1): `workload.env` (eleven keys,
   each under a provenance comment: "inferred from <file>: <evidence>",
   "platform default", "measured by ./go init: …"), `worker.md` (`## Scope`,
   `## Tests`), `policy.env` (a header, platform defaults), and `runners/`
   copied from `stacks/<stack>/runners/`. Python: a cosmic-ray runner taking
   `<scope> <test command>` and `MUTATION_LINES`, a scorer, a bandit runner
   (`uv run --with bandit==1.9.4`). Javascript: a Stryker runner
   (`@stryker-mutator/core@10.1.1` in a temporary npm prefix, the command
   test runner), a scorer over its JSON report, an eslint 10 +
   eslint-plugin-security 4.2.1 runner with a shipped flat config.
   `TOOLCHAIN_IMAGE=workbench` when the declared runtime (`requires-python`,
   `engines.node`) admits the workbench's versions, the comment saying so;
   the user may change it. Then init runs `MUTATION_CMD` with
   `MUTATION_LINES=all` through `core/toolchain.sh` and writes
   `MUTATION_BASELINE` with its provenance (date, commit, tool, mutants,
   killed, seconds); a failed run is NO-GO naming the command and leaves the
   key empty, which every gate refuses (ADR 0030 §2). It ends with the
   contract check and `GO: init — <stack>, draft in <path>, baseline <score>
   (<n> mutants, <s>s)`. A committed contract is overwritten in the clone
   only, said on stderr.
7. **The contract has two halves.** Gates: `workload.env`, `worker.md`,
   optional `policy.env` (ADR 0037 §5) and the runners the commands call.
   Plane: `compose.yaml`, `flags/flags.json`, `smoke.sh`, `load.sh`,
   `fixtures/gates/`, required only to run the plane: `ci/workloads.sh`
   checks services and flag file only when `compose.yaml` exists; `make up`
   refuses a contract without one, naming it. `compose:` requires the plane
   half.
8. **The dev-worker runs in the toolchain.** `adapters/gemini-cli/Containerfile`
   takes `HARNESS_BASE_IMAGE` (the Makefile exports the reference for an
   image form, the workbench image for `workbench` and `compose:`), with
   the workbench as a named stage that supplies the harness's own node
   24.21.0 under `/opt/harness`, never on the repository's `PATH`; git and
   jq via apt when missing; a base without apt fails the build with its own
   message. Checked at M20 close by building on
   `docker.io/library/python:3.13-slim-trixie` and running `gemini
   --version` in it; not run end to end at L1 (no key in the session).
9. **The second stack: `workloads/minimal-js/`**, gates-only and attachable:
   `package.json` (`node --test`, no runtime dependencies),
   `package-lock.json`, `src/`, `test/`, `.lights-out/` with the gates half,
   `TOOLCHAIN_IMAGE=docker.io/library/node:24` by digest committed (so acceptance
   exercises the image form), runners byte-identical to
   `stacks/javascript/runners/` (a ci check). `ci/app-free.sh` already keeps
   `workloads/` out of `core/`, `gates/`, `verify/`, `adapters/`.
10. **Acceptance: `./go demo-init`**, outside `./go demo` (ADR 0003). For
   `workloads/minimal` and `workloads/minimal-js`, each copied into a fresh
   git repository outside the checkout (demo-seam's shape): attach; `gates`
   on the committed contract (through the engine, the line says where);
   `init`; `gates` on the draft with `DOCKER_HOST` naming a path that does
   not exist, proving no engine call. Init plus gates is timed and refused
   above 300 s (provenance: plan §16, owner decision 2026-11-03, to be
   re-measured at the first run). Ends `GO: demo-init — minimal <s>s in
   workbench, minimal-js <s>s in workbench`. `demo-gates`, `demo-seam`,
   `ci`, `demo` unchanged. New cold checks: `ci/toolchain.sh` (three forms
   resolve, `workbench` runs, both engine forms NO-GO naming the socket when
   absent, an unknown form NO-GOes); `ci/init.sh` (inference on throwaway
   python and javascript repositories, provenance on every key, nothing
   written to the source, runners equal to `stacks/`, NO-GO on no stack);
   `ci/workloads.sh` extended.

## Alternatives rejected

- **Probe the workbench at gate time.** A verdict that depends on a probe
  no contract records; the same commit could run in two places.
- **A fourth form that builds the repository's `Containerfile`.** `compose:`
  already builds it, with the build context rule ADR 0037 §2 fixed.
- **Tool-specific gates (`gates/stryker.sh`).** `gates/` would reopen per
  stack, which ADR 0030 rejected for bandit.
- **semgrep in the workbench** (plan §16's example). An LGPL native engine
  with a networked rule registry (ADR 0015's reason); the SAST command
  already runs in the workbench through `uv`/`npm`.
- **Runner templates as strings inside `core/init.py`.** Not shellchecked,
  not comparable byte for byte with minimal-js's copy; `stacks/` is a
  directory of files, not an abstraction. Copied from `workloads/` instead:
  `app-free` forbids naming it.
- **Keep scanning the attached tree with an allowlist for `.venv` and
  `node_modules`.** An allowlist on a user's tree is the exception ADR 0037
  §3 refused; the subject is what was committed.
- **Default the range as an exported make variable.** `ci/contract.sh`'s
  cold run would judge HEAD~1 on every `./go ci`.
- **Draft the plane half too.** `compose.yaml`, `/healthz`, `/metrics` and a
  kill switch cannot be inferred from a lockfile; a guessed one is a hidden
  mock.

## Consequences

- **The one that will hurt: 300 s holds for a small suite only.** Init
  measures the whole scope, and so do the gates on a single-commit
  repository. The app's whole-package run took 792 s (`app/workload.env`,
  2026-09-15): init on a codebase that size fails the budget on the
  baseline alone. `demo-init` proves 300 s on two tiny repositories, not
  on "a repository"; the README must say so.
- In the `workbench` form a human's `./go gates` runs the code under
  judgment in the workbench, in-process; only the tower isolates it
  (Resolved, 1). A `./go gates` on a repository you would not run on this
  machine is the same risk as attaching it (ADR 0037 Consequences).
- `deps` on the empty-tree base judges every lockfile whole: an advisory
  already in a fresh repository is a NO-GO on its first `./go gates` (ADR
  0015's time bomb, now on the user's first run).
- `stacks/` is a new top-level platform directory: plan §6 and
  `RISK_HIGH_PLATFORM` gain it, `ci/shell.sh` checks its scripts.
- docs/use-on-your-repo.md loses "the worker's toolchain is fixed" and
  "seven keys"; the README gains `./go init` and the forms.
- A `compose:` workload's worker stays on the workbench, not on its own image:
  plan §16 says "the same image"; that holds for the image form only.

## Golden-rule check

`./go demo` runs the app on `compose:backend-test`: the same compose calls
through `core/toolchain.sh`, the same `verdict.env`, the same worker image
(base = workbench). Gate lines gain `— in compose:backend-test`, which
`make gates` accepts. `./go ci` gains two cold checks, no engine, no key.
`./go demo-init` needs the socket and the network (PyPI, npm, OSV, Docker
Hub), no key, no account. Passes.

## Verified while writing (2026-11-03)

- **Stryker 10.1.1** (stryker-mutator.io, the npm registry, and a run):
  `mutate` takes `file:start-end` ranges, one per entry; the command
  runner runs the whole suite per mutant; the JSON report is `files` →
  `mutants[]` with `status` in Killed, Survived, NoCoverage, Timeout,
  RuntimeError, CompileError, Ignored and `location.start.line`, as
  `score.mjs` reads it; score = (Killed + Timeout) / (those + Survived +
  NoCoverage). Its command runner ends each run with tree-kill, which
  needs `ps`: `node:*-slim` and the workbench (debian slim) ship none, so
  the workbench gains `procps` and the example names `node:24`, not slim.
- **ESLint 10.14.0 with eslint-plugin-security 4.2.1**: the plugin's
  recommended flat config loads on 10 and reports
  `detect-eval-with-expression` and `detect-object-injection`; with
  `--config` outside the repository the plugin import resolves from the
  config file's directory, so `sast.sh` copies the config into the tools'
  prefix.
- **uv 0.12.13**: `uv sync --locked --project <runners dir>` with its own
  `UV_PROJECT_ENVIRONMENT` installs cosmic-ray 8.7.0 and bandit 1.9.4 from
  the stack's lockfile beside the repository's environment; `--with` is not
  used (it would override a project pin).
- **gitleaks 8.30.1**: `--config` wins over the target's `.gitleaks.toml`
  (its four sources, in that order), `-i` names the `.gitleaksignore`
  directory (default `.`, this checkout), `--ignore-gitleaks-allow` exists.
- **osv-scanner 2.6.0** on a lockfile with no package exits 128, "No
  package sources found": `gates/deps.sh` reads that case as clean.
- **gemini-cli 0.61.0-preview.0**'s launcher is `bundle/gemini.js`
  (`node_modules/.bin/gemini` resolves to it); the harness image's wrapper
  takes the resolved path. The image built on
  `docker.io/library/python:3.13-slim-trixie` runs `gemini --version`,
  `python3 --version` (3.13.18), git and jq, with no `node` on PATH.
- **The suite outside its image**: `workloads/minimal`'s metrics test
  relied on `OTEL_SEMCONV_STABILITY_OPT_IN=http` from its Containerfile;
  its conftest now sets it, the first thing `./go init` on a real
  repository will find more of.

## Resolved while planning (2026-11-03)

The architect's draft left twelve open questions; the planning session
settled them, and the Decision above is written with these in force.

1. **The judged code never runs with the checkout mounted.** The two
   engine forms bind-mount nothing: they build a throwaway image (`FROM
   <ref>` or `FROM $WORKBENCH_IMAGE`, `COPY . /work`, `WORKDIR /work`)
   through compose, whose client sends the build context over the socket,
   so a worktree in the workbench's `/tmp` (demo-gates, the tower's CI
   step) works too, and the command cannot write into the clone. Named
   volumes keep the uv and npm caches across runs. The `workbench` form
   runs in-process by default (your machine, your HEAD) and through the
   engine, in the throwaway workbench image, under `TOOLCHAIN_ISOLATE=1`,
   which `core/steps/ci.sh` sets: the tower never runs a PR's tests inside
   its own container. The line says `in workbench` or `in workbench
   (isolated)`.
2. **`$` and `#` in a `*_CMD`** are refused by `ci/workloads.sh`: make's
   `include` would expand the one and cut at the other. The only other
   reader, `core/policy.py`, matches `KEY=VALUE`, so spaces and quotes
   pass. A runner takes its scope and its test command as arguments;
   `MUTATION_LINES` is the one variable the gate puts in its environment.
3. The Makefile `-include`s the contract when the goal is `init`.
4. Init on a repository with a committed contract keeps the plane keys
   (`CANARY_VERSION`, `WORKLOAD_KILL_SWITCH`, `WORKLOAD_SAMPLE_PATH`),
   "kept from the committed contract"; without one they are empty, "plane
   half not drafted": empty is what `make up` refuses, and
   `ci/workloads.sh` checks them only with the plane half.
5. The workbench and image forms run from `<root>/$WORKLOAD_TEST_ROOT`;
   `compose:` runs where the image's working directory is, as today.
6. The socket is present iff `DOCKER_HOST` is set and names an existing
   socket: `./go` sets it only when it mounts one, and `demo-init` points
   it at a path that does not exist.
7. **Pinning.** Each stack ships a lockfile: `stacks/javascript/runners/
   {package.json,package-lock.json}` installed with `npm ci --prefix` into
   a temporary prefix; `stacks/python/runners/{pyproject.toml,uv.lock}`
   synced with `uv sync --locked` into an environment of its own
   (cosmic-ray parses the files it mutates and needs nothing from the
   repository's environment; the test command stays the repository's).
   The form check accepts `@sha256:` digests; `workloads/minimal-js`
   commits `node:24` by the manifest-list digest docker.io served on
   2026-11-03 (not slim: Stryker's command runner ends each run with
   tree-kill, which needs `ps`).
8. A runtime the workbench does not admit gets `ghcr.io/astral-sh/uv:
   python<X.Y>-trixie-slim` (tags for 3.12 and 3.13 verified 2026-11-03)
   or `docker.io/library/node:<major>` (not slim: Stryker needs `ps`); no `requires-python` or
   `engines.node` admits the workbench, the comment saying so. The
   javascript scorer and finding counter are node scripts: `node:24-slim`
   has no python3 and no jq.
9. `./go attach` refuses while `.attached/.lights-out/` has uncommitted
   changes (a draft not yet copied), naming `./go detach`.
10. gitleaks 8.30.1 reads `<target>/.gitleaks.toml` as its fourth config
    source and `.gitleaksignore` from `-i` (default `.`): the attached
    range's history scan passes `-c ci/gitleaks-attached.toml` (`[extend]
    useDefault = true`, no allowlist) and `--ignore-gitleaks-allow`, so a
    PR cannot allowlist itself.
11. gemini-cli's launcher is `#!/usr/bin/env node`: the harness image
    installs a `gemini` wrapper that calls `/opt/harness/node/bin/node`.
12. ADR 0039 is taken by the Gitea runner, so the arc's numbers shifted:
    plan §16 now gives M18 0040, M19 0041 and M20 this one, 0042 (main,
    commit 1d5098f, 2026-11-03).

## Amendment 2026-11-11 (M24 phase C): a test command cosmic-ray cannot start is a failed run

Found on tenacity, the first attached repository whose tickets changed production code. Its
`MUTATION_CMD` carried `"SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 uv run …"` as the runner's quoted test
command. The runner's own clean run uses `sh -c` and passed; cosmic-ray 8.7.0 runs the command as
`Popen(shlex.split(command))`, with no shell, so every mutant came back `incompetent` with the worker
outcome `normal` (`cosmic_ray/testing.py`, `run_tests`). `cosmic-ray baseline` fails only on `killed`
(`cosmic_ray/cli.py`), so it passed too. The scorer left every mutant out of the population, printed
`{"total": 0}`, and the gate answered GO by absence of subject on a real production change.

- The python scorer (`stacks/python/runners/score.py` and its three copies) prints no score and exits
  1 when any result is `incompetent` with the worker outcome `normal`: no test ran, so the tool failed
  (§1, "non-zero only if the tool failed"). The gate reads that as a failed run, NO-GO. `incompetent`
  with the worker outcome `exception` (`cosmic_ray/mutating.py`: applying the mutation raised) stays
  out of the population, as before. `stacks/python/tests/test_score.py`, run by `ci/core-tests.sh`
  (not under `core/tests/`: core/ never names the contract directory or a bundled workload, `ci/app-free.sh`).
- A variable the test command needs goes before the runner (`VAR=value sh .lights-out/runners/
  mutation.sh …`), where the `sh -c` line exports it to cosmic-ray and every test run;
  `docs/use-on-your-repo.md` says so.
- Rejected: a shell in the runner's test command (`sh -c` around it). It would change what cosmic-ray
  times and kills (its process group holds the shell), for a case the variable's place already covers.
- Not changed: a repository whose contract was drafted before this keeps its copy of the scorer until
  it copies the new one; a PR is judged by its base's runners (ADR 0033).
