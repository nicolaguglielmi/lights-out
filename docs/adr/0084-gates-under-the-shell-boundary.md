# ADR 0084 — `gates/` under the shell boundary: the wrapper stays shell, the logic moves to `core/gates/`, the ratchet holds both

- Status: accepted (M51); amended 2026-11-16 (M61, the second slice)
- Date: 2026-11-13

## Context

ADR 0052 put `core/` under a rule: a shell file has at most 30 code
lines, logic goes to Python, a ratchet in `./go ci` holds the debt and
only lets it go down. Its §6 named `gates/` as a later slice. The first
outside feedback (2026-11-06) named the shell as a weak spot, and on
2026-11-13 the owner asked for less shell and mostly Python.

Measured on 2026-11-13 over tracked files: 31,175 lines of `.sh`
(`ci/` 17,043, `adapters/` 4,730, `gates/` 4,074, `core/` 2,107) and
33,035 of Python. `gates/` held 18 files and 2,670 code lines of shell
(non-blank, not a comment), 916 lines of Python (the harness suite's
stub and probe, which run inside containers). What the shell held,
besides wrappers: two Python programs inside heredocs (`mutation.sh`,
`risk.sh`), `jq` filters over adapter output, an advisory diff with
`comm`, and four port conformance suites (`statestore`, `eventbus`,
`codehost`, `delivery`) written as `sh` sequences of verb calls and
checks. None of it had a unit test; the self gate (ADR 0049) mutates and
scans `core/` only, so it saw none of it.

## Decision

1. **`gates/` joins `core/` under ADR 0052.** What stays shell is what
   is a contract or the host: the gate wrapper, which sources `ci/lib.sh`
   and prints the one `GO`/`NO-GO` line (CLAUDE.md, "Contratto di output
   dei gate"); the glue that runs other processes in order
   (`require_range`, `effective_policy`, `core/toolchain.sh`); adapter
   verbs (ADR 0024); and the host entry points, `./go` and `lights-out`,
   because the host has only podman, git and a POSIX shell (CLAUDE.md,
   "Toolchain nel container"). Everything else in a gate (parsing,
   decisions, a suite's cases, JSON) is Python.
2. **The logic lives in a package, `core/gates/`**, one module per gate,
   standard library only, run as `python3 -m core.gates.<x>` from the
   repository root, where every gate already runs (it sources `ci/lib.sh`
   by a relative path). Under `core/` because the self gate's
   `MUTATION_CMD` and `SAST_CMD` scope is `core/` (ADR 0049): this is
   what puts the gates' logic under mutation and bandit. A package of its
   own, not `core/*.py`, because `pyproject.toml` ships the top level of
   `core/` as the `lights-out-gates` wheel (ADR 0061), the light tier;
   these modules need an engine, a plane or a code host and stay out of
   it. Tests in `core/tests/test_gates_<x>.py`, `unittest`, written from
   the gate's header and the ADRs it cites, never by translating the
   shell.
   - `core/gates/__init__.py` holds what every module shares, and
     nothing else: `NoGo(reason, *details)` and `main(judge)`, which
     prints the verdict (a GO detail, or the reason and then one detail a
     line) and returns 0 or 1; `say`, a line on stderr; `proc(argv,
     ...)`, the package's one process call, an argument list, never a
     shell line, excused for bandit (B404, B603, B607) on that line and
     the import with its reason, as `core/tower.py`'s `sh()` is (ADR 0052
     §3), so a second process call elsewhere in the package is a finding
     again; `adapter(directory)`, a port's verbs over `proc`; and
     `run_id()`, the suites' date-and-random run id.
   - A suite drives its port through a `verb` callable it is given; the
     default runs `sh adapters/<name>/<verb>.sh` through `proc`. The
     tests hand it a fake port in Python, broken one behaviour at a time,
     so a suite is proved to fail without an engine or a subprocess.
   - A module never names a vendor: `core/` is vendor-free (ADR 0024 §1,
     `ci/vendor-free.sh`). What a suite needs from one host's adapter
     stays in its wrapper, under `gates/`, and reaches the module in the
     environment: the codehost wrapper reads `GITHUB_TOKEN` and the Gitea
     adapter's password and hands them over as `CONFORMANCE_SECRETS`.
3. **The wrapper is `verdict`**, one function added to `ci/lib.sh`:
   `verdict MODULE [ARG...]` runs `python3 -m MODULE ARG...`; exit 0 is
   `go`, or `go_with` the first stdout line when there is one; exit 1 is
   `nogo` with the first line as the reason and each further line as a
   detail (so `$NOGO_DETAILS` still gets them, ADR 0038 §5); any other
   exit, or a 1 with nothing on stdout (an uncaught exception), is a
   NO-GO naming the module. A gate's `.sh` keeps its path, arguments,
   environment, the `CONFORMANCE` guard and `require_range` first (what
   a cold `ci/contract.sh` run sees, ADR 0026 §4, ADR 0015 §2), then
   calls `verdict`. The guards stay shell so that the cold run starts no
   Python.
4. **A gate that runs the workload's command** (`sast`, `mutation`,
   `test-proof`) runs it from its module through `sh
   core/toolchain-run.sh CMD`: an entry point of a few lines that sources
   `core/toolchain.sh` and calls `run_in_toolchain`, its exit CMD's or
   125, a contract error a line on stderr and 125, never a contract line
   on stdout. The toolchain's preflight keeps its place in each gate:
   `sast` and `mutation` run `toolchain_preflight` in the wrapper, after
   their guards; `test-proof` runs `sh core/toolchain-run.sh --preflight`
   from its module (its `$toolchain_where` on stdout, or the preflight's
   reason and exit 1), after a GO by absence of subject, which needs no
   engine. `core/toolchain.sh` stays shell, on ADR 0052's debt list, its
   own slice.
5. **The ratchet, `ci/shell-boundary.sh`, scans `core/` and `gates/`**
   with ADR 0052 §4's rule unchanged: at most **30 code lines** per shell
   file (provenance: the owner's decision of 2026-11-06, ADR 0052 §4;
   checked again 2026-11-13, a measurement: every gate wrapper this
   slice leaves is 4 to 22 code lines), files over it named with their
   exact count, NO-GO when a listed file grows, shrinks without its entry
   lowered, or is gone, and when an unlisted file is over. The check's own proof on a temp tree
   gains a planted `gates/` file over the limit and a `gates/` entry
   raised, so it is shown to fail in the new directory too.
6. **This slice** moves `production-readiness`, `deps`, `sast`,
   `mutation`, `test-proof`, `risk` and the four port suites
   (`statestore`, `eventbus`, `codehost`, `delivery`). Callers do not
   change: same paths, arguments, environment, stdout line, files in
   `$GATES_OUT`, and the stderr lines the `ci/` suites read. `spec`,
   `test` and `test-weakening` were already under 30; `test-weakening`
   now ends in `verdict core.testdiff` instead of splitting the module's
   output itself. The harness suite,
   `gates/harness-conformance.sh` and `gates/harness-conformance/proof.sh`
   (693 and 738 code lines on 2026-11-13, a measurement), stays on the
   debt list with its exact count: it drives compose, a scripted model,
   the egress proxy and a made-up harness, and only `./go
   harness-conformance` on a real engine proves a rewrite of it, a slice
   of its own.

## Alternatives rejected

- **Python in `gates/` beside the wrapper.** Outside the self gate's
  mutation and SAST scope (`core/`), which is half of why ADR 0052 moved
  logic at all; widening that scope to `gates/` would also mutate the
  harness suite's stub and probe, which run in containers and have no
  unit tests.
- **The modules at the top of `core/`.** They would ship in the
  `lights-out-gates` wheel, whose promise is the gates that need only git
  and Python (ADR 0061); a conformance suite for a code host is not one.
- **Each wrapper splitting the module's stdout itself**, as
  `test-weakening.sh` did. Ten copies of the same twelve lines, each a
  place to get the contract wrong; one function in the library every
  gate already sources.
- **Rewriting `core/toolchain.sh` in Python in the same change.** It is
  sourced by `gates/test.sh` and `core/init.sh` too and has its own
  engine paths (compose, throwaway image); a separate slice keeps this
  one provable by `./go ci` and `./go demo`.
- **The harness suite in this slice.** 1,431 code lines whose only proof
  is a full engine run per harness; moved without that run it would be a
  rewrite trusted on faith, the opposite of this ADR.

## Consequences

- The gates' rules have unit tests and are mutated and scanned by the
  self gate on every push (ADR 0049); a rule a test does not reach is a
  surviving mutant on the change that wrote it.
- `ci/lib.sh` gains `verdict`; `core/` gains `core/gates/` and
  `core/toolchain-run.sh`; `core/sastdiff.py` gains `diff()`, which its
  command line calls, unchanged.
- The conformance record's digest (ADR 0057 §9) is written by the
  delivery suite and read by production-readiness through one Python
  function, `core/gates/readiness.py`'s `adapter_hash`. `ci/lib.sh` keeps
  its shell `adapter_hash` for `ci/readiness.sh`, which builds records
  with it; a unit test holds the two equal on every adapter and on a
  tree with subdirectories and `__pycache__`.
- The cold `ci/` suites that build a tree of symlinks to run a gate from
  (`ci/store-bus.sh`) link `core/` too.
- A change to a harness suite file must keep or lower its count and
  update the entry when it lowers it, as for `core/`'s debt.
- The numbers before and after this slice are in the M51 handoff.

## Golden-rule check

`./go demo` runs the gates (`demo-gates` and the tower's CI step) through
the same `gates/*.sh` paths; `python3` is in the workbench and gates
images the demo already builds. No key, no account, no new image layer.

## Amendment 2026-11-16 (M61): the second slice

The boundary does not move: `core/` and `gates/` are under the rule,
`ci/` is not (§A5). What moves is the debt §6 left: the harness pair and
`core/toolchain.sh`. Measured on 2026-11-16 over tracked files, before
this slice: `gates/` 2,515 lines of `.sh`, of which 1,431 code lines in
the harness pair (`harness-conformance.sh` 693, `proof.sh` 738);
`core/` 2,254, of which 126 code lines in `toolchain.sh`.

A1. **The harness suite is `core/gates/harness.py`**, run by
   `gates/harness-conformance.sh` through `verdict` after the guards that
   stay shell (§3): `CONFORMANCE` first, then `WORKBENCH_IMAGE`,
   `COMPOSE_FILE`, `WORKLOAD`, `WORKLOAD_CONTRACT`, the tools on `PATH`
   and case 10's fixture. Same cases, order, messages, files under
   `.crew/conformance/`, stderr lines and GO line as the shell
   (`gates/harness-conformance.sh`'s header is the spec and moves to the
   module's docstring). Compose, `run.sh` and git are process calls
   through `proc`; the stub and the probe stay where they are, they run
   in containers.
   - **The cases are data.** `CASES` is the ordered table of the cases a
     harness runs: each row its id, the workspace it prepares, the steps
     the stub plays, the budgets, and the checks that judge it. A case is
     one row and a check function; a harness is discovered, as before
     (an `adapters/<name>/` with `run.sh`, `Containerfile` and a pin,
     ADR 0050 §2), its contract read from its conformance files
     (`conformance.env` and, since ADR 0092 §2, one
     `conformance.<provider>.env` per further provider) and `thinking.env`,
     never listed. A provider is one row of `PROVIDERS`, in the order case 0
     offers them: a harness is offered the first it runs in none of its
     files, and the refusal names a harness with a file for it or, with none
     discovered, the row's harness (ADR 0041 §2). Why a table and not code
     per case: two sessions changed this suite in the same sprint (M59 added
     `zai`, landed first and ported here as one row), and a row is a
     conflict a reviewer can read.
   - The judging is pure functions over a case's files
     (`requests.jsonl`, `OUT/`, `stderr.log`, the proxy's decisions), each
     returning the NO-GO reason or nothing, so `core/tests/
     test_gates_harness.py` proves each one on planted files, with no
     engine.
A2. **The proof is `core/gates/harness_proof.py`**, run by
   `gates/harness-conformance/proof.sh` through `verdict` after its two
   guards (the tools, and `127.0.0.2` bindable). What the shell held in
   heredocs is now files under `gates/harness-conformance/fake/`, the
   proof's fixtures, copied into its temp tree and run nowhere else, as
   `stub.py` and `probe.py` are the suite's: fake-cli (`run.sh`, a
   one-line `exec` into `run.py`, the adapter contract of ADR 0041 as
   the shell had it; `fake_cli.py`; `guard.sh`; `mcp.sh`), the stand-in
   `docker-compose` (a Python script), the notes server, the `agents.py`
   shim, `egress_wrap.py` and the stand-in network
   (`netshim/sitecustomize.py`). The defects are a table, `DEFECTS`:
   what the fake does wrong, the tree it runs in, the compose project it
   is shown, and the verdict the suite must give. A new way to be wrong is
   one row and, when the fake must learn it, one branch in the fixture.
   The fixtures are outside `core/` and out of the self gate's mutation
   and SAST scope, as the stub and the probe are: they are the test, not
   the rule.
A3. **The toolchain is `core/gates/toolchain.py`.** `core/toolchain.sh`
   keeps the two functions its callers source (`toolchain_preflight`,
   which sets `$toolchain_where` or calls `nogo`, and
   `run_in_toolchain CMD`), each a call to `python3 -P -m
   core.gates.toolchain preflight|run` with `PYTHONPATH` set to the
   checkout the file was sourced from (`$toolchain_root`, the caller's
   directory unless the caller set it: `core/toolchain-run.sh` runs
   from a base worktree, `ci/toolchain.sh` from a temp directory). `-P`
   keeps the caller's directory off `sys.path`, so a base worktree's own
   `core/` is never imported. `core/toolchain-run.sh` calls the module
   directly. The forms, the socket test, `toolchain.where`, the cache
   paths, the throwaway image's Containerfile and compose file, and the
   exit codes (CMD's, or 125 with one `toolchain:` line on stderr) are
   unchanged (ADR 0042 §2-§4). A package module, not `core/
   toolchain.py`: it needs an engine, so it stays out of the
   `lights-out-gates` wheel (§2).
A4. **The ratchet** loses the three entries this slice pays: each file
   is at most 30 code lines, so its entry is removed, not lowered
   (ADR 0052 §4). `ci/toolchain.sh` plants its two flaws (no socket test,
   no form test) in a copy of `core/gates/toolchain.py` instead of a copy
   of the shell, by the same means: a definition appended to the copy
   replaces the original.
A5. **`ci/` stays out of the ratchet.** Before this slice and the sprint's
   merges it was 17,976
   lines of `.sh` over 54 files (13,451 code lines), nearly all over 30;
   four other milestones of this sprint edit it, and an exact-count list
   there would turn each of their edits into a NO-GO until its entry moved.
   Its slice needs a decision this amendment does not make: where a
   `ci/` check's logic goes. `core/` is the product, mutated and scanned
   by the self gate; a `ci/` suite is a test of it, and its Python would
   need a home and a runner of its own (`ci/core-tests.sh` runs
   `core/tests/` only). Measured on 2026-11-16, by code lines and by
   branching lines (`if`, `case`, `&&`, `||`, `jq`, `awk`, `sed`): `logbook`
   2,152 and 620, `tower` 637 and 339, `plugin` 821 and 208, `attach` 357
   and 187, `postmortem` 733 and 181; `contract` (225) is the one whose
   logic is a judge rather than glue around `make` and `git`. ADR 0094 makes it: `ci/`'s Python in `ci/checks/`, its debt listed,
   the ratchet over `ci/` after this sprint lands, and `ci/contract.sh` the
   first `ci/` slice.

### Alternatives rejected (M61)

- **One Python function per case, no table.** It is what the shell was:
  a provider or a case is then an edit in the middle of a 700-line
  function, the conflict M59 and this slice would both have had.
- **The proof's fake-cli kept as shell heredocs, the driver in Python.**
  `proof.sh` would stay over 30 code lines for text that is a fixture,
  and the fake's `run.sh` would still be a 110-line shell program nobody
  can run alone.
- **The proof in `core/tests/`.** It starts the stub and fake-cli about
  sixty times, two and a half minutes; `MUTATION_CMD` runs `core/tests/`
  for every mutant that reaches it.
- **`core/toolchain.sh` deleted, the callers calling Python.** Five
  callers source it (three gates, `core/init.sh`,
  `core/mutation-baseline.sh`); their wrappers would each grow the
  preflight's three lines. Two thin functions keep them as they are.

### Consequences (M61)

- The before and after line counts per directory, and the proof's
  verdicts before and after on the same planted defects, are in the M61
  handoff.
- A change to a harness case is a change to `core/gates/harness.py`
  and its unit tests; a new defect for the proof is a row in
  `core/gates/harness_proof.py` and, when the fake must learn it, a
  branch in `gates/harness-conformance/fake/`.
