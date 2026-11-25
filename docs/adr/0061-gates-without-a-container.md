# ADR 0061 — Gates without a container: a standard-library package, `lights-out` on PyPI, runs the gates that need only git and Python as `gates --light`, names the ones that need the image, and never prints `GO: gates`

- Status: accepted (amends ADR 0052 §3 [modules import their siblings relatively; one more excused process call]; ADR 0045 §6 [the hook recipes gain a `--light` variant])
- Date: 2026-11-08
- Milestone: M35

## Context

Today the first "it works on my repository" needs an engine and the 1.8 GB
image (PLAN §16 M35), and the hook recipes carry a 300 s timeout (ADR 0045
§6). Half the judgement needs neither: `core/risk.py`, `core/testdiff.py`,
`core/spec.py` and `core/perimeter.sh` read git and text, and ADR 0052 put
that logic in standard-library Python under `core/`. Verified on 2026-11-08:

- `uv build` (uv 0.12.17, setuptools>=77) of a root `pyproject.toml`
  mapping `lights_out` to `core` and `lights_out.platform_policy` to
  `policy`, with `perimeter.sh` and `merge.env` as package data, gives a
  wheel with all 19 `core/*.py`, `perimeter.sh`, `platform_policy/merge.env`
  and no `core/tests`; the sdist builds the same wheel (scratch copy).
- Every `core/*.py` compiles on CPython 3.11.16 and 3.12.14; the workbench
  runs 3.13, the host 3.14.
- `lights-out` and `lights_out` are 404 on `pypi.org/pypi/<name>/json`: free
  today, not reserved.
- Eight modules (`policy`, `spec`, `assign`, `audit`, `incident`, `logbook`,
  `mainline`, `map`) import siblings as `from core.x`, with a script-mode
  fallback (`core/policy.py:21-24`). Installed as `lights_out`, both fail.

## Decision

1. **A Python package, `lights-out` on PyPI, import name `lights_out`.**
   Metadata in a root `pyproject.toml`; `workbench/pyproject.toml` stays the
   workbench's pin. setuptools, `dependencies = []`, `requires-python =
   ">=3.11"`, console script `lights-out = "lights_out.__main__:main"`.
   `core/` maps onto `lights_out` by `package-dir`, no file moves;
   `core/tests` is not shipped. Package data: `core/perimeter.sh`, and
   `policy/merge.env` as `lights_out.platform_policy` (`lights_out.policy` is
   `core/policy.py`). Version `0.3.0.dev0` until the owner tags a release;
   publishing is the owner's act (`docs/launch/checklist.md`).
2. **`core/` importable under any package name.** The first branch of each
   try/except import becomes relative (`from .x import y`); the script
   fallback stays. It works as `core.x` (`python3 -m unittest discover -s
   core/tests -t .`), as `lights_out.x` and as `python3 core/x.py`. A check
   in `./go ci` refuses a new `from core.` in `core/*.py`.
3. **Entry points, not copies.** The light tier is `core/light.py`, the CLI
   `core/__main__.py`: `python3 -m core gates --light` from a clone,
   `lights-out gates --light` installed, `uvx lights-out gates --light`. It
   calls `risk.classify` with `policy.effective`, `testdiff.weakening` and
   `testdiff.subject`, `spec.check`, and `sh core/perimeter.sh <ticket>`
   with the changed paths on stdin, unchanged. `gates/*.sh` and `make gates`
   are untouched. New code is glue only: the range, git with the wrappers'
   flags, the host run of `TEST_CMD`. Its process calls (argument lists; the
   one shell line is `TEST_CMD`, the contract's own command) go through one
   function in `light.py`, excused for bandit on that line with its reason,
   as `core/map.py`'s `run()` is (ADR 0054 §6). It is the fifth such site
   (`core/tower.py`, `core/map.py`, `core/mainline.py`, `core/logbook.py`),
   each justified by its ADR: ADR 0052 §3's "a second process call elsewhere
   is a finding again" holds for an unexcused call, and this one is
   excused here.
4. **What runs, said plainly.** The full judgement is ten: the eight of
   `make gates` (risk, secrets, test-weakening, deps, sast, test, mutation,
   test-proof) and the two the tower runs around them, perimeter after the
   worker and spec before assign. (Perimeter is not a gate in ADR 0021 §5's
   sense; it counts because the tower posts its line where a gate's goes,
   ADR 0033 §2, and here it runs only with a ticket, that ADR's reason.) The
   light tier can run six: risk, perimeter, test-weakening, test,
   test-proof, spec. secrets, deps, sast and mutation need the image
   (gitleaks, osv-scanner, bandit, cosmic-ray) and are never run, faked or
   skipped silently. test-proof proves something only after test passed on
   the head (ADR 0053 §2, §4): both or neither.
   - Default: risk and test-weakening. No code of the repository runs: safe
     and fast for a hook.
   - `TICKET=<file>`: perimeter and spec too; test-weakening's `## Removes
     tests` excuse reads that ticket.
   - `TEST_ON_HOST=1`: test, then test-proof. `TEST_CMD` runs on this host,
     whatever `TOOLCHAIN_IMAGE` says (the host is the toolchain), with
     `NO_COLOR=1`, from `<workload>/<WORKLOAD_TEST_ROOT>` of a throwaway
     checkout outside the repository: test on the head (`git clone --shared
     --no-checkout` into a temporary directory, the head checked out, and
     under `STAGED=1` HEAD with the staged patch applied), so unstaged edits
     never reach the suite and what the suite writes never lands in the
     repository; test-proof on a second such checkout of the base, with the
     changed test files (`testdiff.subject`) copied from the head's, where
     `TEST_CMD` must fail.
   - Order: risk, perimeter, test-weakening, test, test-proof, spec; the
     first NO-GO stops the rest, as in `make gates`.

   This replaces the PLAN's "5 of 9": test was missing from the count, and
   perimeter and spec need a ticket. On a repository without lights-out
   tickets: two gates by default, four with `TICKET`, six with both.
5. **The line.** One stdout line, exit 0 or 1, no colour, details on stderr.
   - `GO: gates-light — risk <level>, <decision>; <n> of 10 gates ran;
     secrets, deps, sast, mutation need the image`, then, for what did not
     run, `; test, test-proof need TEST_ON_HOST=1` and `; perimeter, spec
     need TICKET=`.
   - `NO-GO: gates-light — <gate>: <reason>`.
   - Never `GO: gates`. From the package, `lights-out gates` without
     `--light` is `NO-GO: gates — this package runs the light tier only:
     lights-out gates --light; the eight gates run from the image (the
     lights-out wrapper) or a clone (./go gates)`. An unknown argument is
     `NO-GO: gates-light — '<arg>' is not an argument ...`; an unknown
     command `NO-GO: lights-out — unknown command '<cmd>'; one of: gates
     --light, version`. `ci/contract.sh` runs the entry point cold and checks
     the three shapes.
6. **Arguments in the wrapper's `KEY=VALUE` vocabulary.** `BASE=` `HEAD=`
   (merge-base to HEAD), default the last commit (the empty tree for a root
   commit, ADR 0042 §5). `STAGED=1` judges HEAD against the index with `git
   diff --cached`, read-only: no commit, worktree or object is written to
   the repository or its `.git`, unlike the image, which commits the staged
   change in its own clone (ADR 0045 §3). `WORKLOAD=<dir>`, default the
   repository's top; its contract is `<dir>/.lights-out/`, or
   `<dir>/workload.env` for the bundled app (ADR 0030): with no make to
   compute `WORKLOAD_CONTRACT`, `core/light.py` spells `.lights-out` as
   attach and init do, and `ci/app-free.sh` lists it beside them (ADR 0037
   §7). `TICKET=<file>`.
   `TEST_ON_HOST=1`. `GATES_OUT=<dir>` writes `verdict.env` in `make gates`'
   shape (`GATES`, `FAILED`, `RISK_LEVEL`, `RISK_DECISION`) plus
   `RAN=<gates>`; without it nothing is written anywhere.
7. **The base's contract judges (ADR 0033).** `workload.env` and
   `policy.env` are read from the base commit with git, the head's only when
   the base has none, as `package/entrypoint.sh` does; under `STAGED=1`, the
   committed HEAD's. `runners/` is not read: the gates that call it do not
   run. The platform policy is the package's own `merge.env`, so a
   repository cannot loosen it beyond what `core/policy.py` allows.
8. **Hook recipes.** `docs/use-on-your-repo.md` gains a `--light` variant
   of each: `lights-out gates --light STAGED=1` (or `uvx lights-out ...`) in
   the same `sh -c`, exit 2 on NO-GO, timeout 30 s (Gemini CLI: 30000 ms).
   Provenance: a decision, three times PLAN M35's 10 s acceptance budget;
   with `TEST_ON_HOST=1` the suite's time adds and the recipe says to raise
   it. Each recipe is run once against the pinned CLI (claude-code 2.1.311,
   codex 0.166.0, gemini-cli 0.61.0-preview.0) with the scripted model
   `gates/harness-conformance/stub.py`, beside the adapters, not through
   them: every adapter installs its own guard, and claude-code runs with
   `--setting-sources user` (`adapters/claude-code/entrypoint.sh:66`), so a
   workspace hook cannot be added without editing the adapter.
9. **Proof in `./go ci`.** `core/tests/test_light.py`, unit, from this ADR;
   and `ci/gates-light.sh`: the nine `app/fixtures/gates` fixtures of `./go
   demo-gates`, each applied in a throwaway worktree and judged by the
   light tier against the committed `expect`, which `demo-gates` proves
   equal to the full verdict. `RISK_LEVEL` and `RISK_DECISION` always match;
   where the full verdict failed at a gate the light tier ran, the light
   tier fails at that gate; otherwise it is GO. test and test-proof do not
   run on these fixtures: the app's `TEST_CMD` needs the compose Postgres
   (`TOOLCHAIN_IMAGE=compose:backend-test`). Their host run is proven by the
   unit tests on scratch repositories only. The same check times the light
   tier on this repository's last commit against 10 s (provenance: PLAN
   M35's acceptance), checks the mapping in `pyproject.toml` with
   `tomllib`, and first proves it can fail.
10. **M39's read-only MCP server** will be another subcommand of
    `core/__main__.py` in this package. M35 does not write it.

## Alternatives rejected

- **Ship `gates/*.sh` in the package.** They need the clone's layout, write
  `.gates-out` into the cwd, and `test-proof.sh` says "in workbench" where
  the host ran it.
- **Rewrite `core/perimeter.sh` in Python.** A second implementation of
  ticket 0003's rule to keep in step.
- **Install `core` as a top-level package.** The name collides anywhere.
- **Move `core/` to `src/lights_out/`.** The Makefile, `./go`, the tower and
  `ci/` all name `core/x.py`.
- **Run `TEST_CMD` by default when `TOOLCHAIN_IMAGE=workbench`.** Here it is
  the thirty self-checks, which need shellcheck, actionlint and gitleaks
  and take minutes: the hook fails or times out. And a hook that runs by
  default the code it was asked to judge has the wrong default.
- **A separate script, `lights-out-light`.** The PLAN's command is `uvx
  lights-out gates --light`.
- **hatchling's `force-include`.** It works; setuptools' `package-dir` is
  the more common mapping and needs no exclude list for `core/tests`.

## Consequences

- **The one that will hurt: `lights-out` names two programs**, the image
  wrapper (ADR 0045) and the package's script (light tier and `version`).
  `uvx lights-out` never touches PATH; with both on PATH the first wins, and
  a `--light` recipe that reaches the wrapper gets its refusal: a NO-GO on
  every commit. A later milestone may make the package's script hand
  everything but `--light` to the image.
- Until the owner's first publish the PyPI name is free, not ours: a recipe
  saying `uvx lights-out` runs whatever holds it.
- The light tier is not the judge: on a PR, the Action and the image stay
  the gate.
- `core/` now runs on hosts lights-out does not build, Python 3.11 to 3.14,
  standard library only.
- The package's version is its own field in `pyproject.toml`
  (`0.3.0.dev0`); the wrapper's `version=` stays the one source for the
  image and the Action (ADR 0045 §7). A release that ships both sets both to
  the tag (`docs/launch/checklist.md`); nothing here unifies them.
- Measured time on this repository, 2026-11-08, macOS host, CPython 3.14,
  a bare clone at 94cfc37 (its last commit judged, `risk high, review`, 2
  of 10 gates): `python3 -m core gates --light` 0.14 s wall; `uvx --from
  git+file://<clone> lights-out gates --light` 2.4 s on the first run (the
  build included) and 0.23 s cached. The clone's `git status` stayed empty.

## Verified

The `--light` hook recipes, 2026-11-08, once per CLI: each pinned image
built on the workbench (`localhost/lights-out-workbench:222597916`), run
with its own headless command, not the adapter's entrypoint; the recipe's
settings file in the place the guide names (Claude Code: the project's
`.claude/settings.json`; Codex: `$CODEX_HOME/hooks.json`; Gemini CLI:
`~/.gemini/settings.json`); the wheel from `uv build` installed with `uv
tool install --offline`; `stub.py` on a private podman network as the
model, whose script is one `git commit -m change` shell call and a final
message. A scratch repository with a `.lights-out/` contract, two runs per
CLI:

| CLI | staged assertion removal | clean staged change |
|---|---|---|
| claude-code 2.1.311 | blocked: `PreToolUse:Bash hook error: … NO-GO: gates-light — test-weakening: …` (`is_error`), no commit | commit landed |
| codex 0.166.0 | blocked: `Command blocked by PreToolUse hook: … NO-GO: gates-light — …`, no commit | commit landed |
| gemini-cli 0.61.0-preview.0 | blocked: `Tool execution blocked: … NO-GO: gates-light — …`, no commit | commit landed |

Not covered: Codex's trust step (`--dangerously-bypass-hook-trust`, as the
adapter runs it) and its project file; Gemini CLI's project file in an
untrusted folder (`--skip-trust`); a vendor's real wire; what a CLI does on
a hook timeout; the full (image) variant. The run's script is not
committed: it is a one-off, and this table is its record.

## Golden-rule check

`./go demo` does not change: no stage, image or service on its path, and the
gate wrappers it runs are untouched. `./go ci` gains a unit file and
`ci/gates-light.sh`, cold, no engine, no network. No key, no account.

## Open points

- **The acceptance command** `uvx lights-out gates --light`
  (`docs/PLAN.md:821`) resolves from PyPI; before the first publish it is
  measured as `uvx --from <clone> lights-out gates --light` and as `python3
  -m core gates --light` in a bare clone.

## Amendment 2026-11-09: `lights-out-gates` on PyPI

The first upload, on 2026-11-09, was refused: "400 The name 'lights-out'
is too similar to an existing project". PyPI compares names without their
separators, and `lightsout` (a tool that turns radio playlists into
Spotify ones, 1.2.0) holds that spelling. §1's name is not available, so
§1 and §3 change; nothing else does.

1. **The distribution is `lights-out-gates`**, the name of the gates image
   (ADR 0057), free on 2026-11-09. The import package stays `lights_out`,
   `core/` mapped onto it; the version stays the wrapper's.
2. **Its one console script is `lights-out-gates`**, with the `gates`
   subcommand implied: `lights-out-gates --light [KEY=VALUE ...]` prints
   the same line, with the same exit code, as `lights-out gates --light`
   did, and `uvx lights-out-gates --light` is the ten-second try.
   `lights-out-gates version` and `lights-out-gates help` stay. Without
   `--light` it is one NO-GO line naming the image or a clone, as before.
   The package ships no `lights-out` script any more: the collision of
   §Consequences, two programs called `lights-out`, is gone.
3. **From a clone nothing changes**: `python3 -m core gates --light`.
4. The PLAN's acceptance command for M35, `uvx lights-out gates --light`,
   reads `uvx lights-out-gates --light` from here on.

Rejected: another spelling of the same words (`lights_out`, `lights.out`):
PyPI folds them into `lightsout`, the name it refused; another suffix
(`-cli`, `-ai`), because the image is `lights-out-gates` and one name for
both is one name to remember; a name that does not say lights-out, because
the hook recipes and the site send people to it.
