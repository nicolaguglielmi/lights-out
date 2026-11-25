# ADR 0053 — Tests that prove something, and tickets that wait: a test the PR adds must fail on the base, the diff may not weaken tests, two open tickets never hold the same file, and an attached repository's base is green before its first ticket

- Status: accepted (amends ADR 0015 §2 [two gates join `GATES`]; ADR 0021 §5 [one gate reads the ticket when it is given, to excuse a declared removal]; ADR 0030 §2 and ADR 0042 §1 [twelve keys: `WORKLOAD_TEST_PATHS`]; ADR 0019 §1 [the `assign` step has two preconditions of its own])
- Date: 2026-11-07
- Milestone: M27

## Context

Before M24 runs the crew on a repository nobody here knows, a verdict is
worth what the tests under it prove. Today a test that proves nothing is
caught by the mutation gate (ADR 0015 §3), and only where a mutation runner
exists and the changed lines yield mutants: a PR whose code change lies
outside the mutation scope (a contract file, a fake, a template) has no
mutants and passes by absence, whatever its test does. The `test-reviewer`
(ADR 0021) looks for the same thing, and it is a model. Nothing stops a
diff from deleting the assertion that failed. And two tickets whose
perimeters (ADR 0021 §5) share a file can be assigned at once: the second
PR conflicts with the first, and the rework loop (ADR 0038) charges the
conflict to a worker that did nothing wrong. On an attached repository, a
suite that is already red on the base makes every PR's `test` gate NO-GO
before the worker writes a line.

Four deterministic controls answer this: no new role (`ci/roles.sh` keeps
six), no model, logic in Python under `core/` (ADR 0052).

## Decision

1. **The contract declares which files are tests: `WORKLOAD_TEST_PATHS`**,
   the twelfth key of `workload.env`. Colon-separated globs relative to the
   workload's root, `*` crossing `/` (the `RISK_*` and `## Perimeter`
   semantics, `fnmatch`), like `WORKLOAD_LOCKFILES`. `ci/workloads.sh`
   requires it non-empty. The bundled app: `backend/tests/test_*.py:
   backend/tests/conftest.py`, what pytest collects plus its fixtures, not
   the runners beside them under `tests/mutation/` and `tests/sast/`.
   `./go init` drafts it from the stack's default discovery: python
   `test_*.py`, `*_test.py`, `conftest.py` anywhere (pytest's default
   `python_files`, plus its fixture file), javascript `test/*`, `*/test/*`
   and `*.test.*`, `*-test.*`, `*_test.*` anywhere (the default patterns of
   `node --test` in the Node 24 documentation, any extension); the comment
   says so. Only the two gates below read it.

2. **`gates/test-proof.sh`: a test the PR adds or changes must fail on the
   base.** On the range's merge-base, in a throwaway worktree, the gate
   applies the PR's changes to test files only (`git restore --source=<head>`
   of exactly those paths, which also removes a test file the PR deleted),
   and runs `TEST_CMD` there through `core/toolchain.sh`, in the same place
   the `test` gate runs it (ADR 0042 §2), contract and runners the base's
   (ADR 0033). A non-zero exit is GO: the tests describe something the base
   does not do. Exit 0 is NO-GO, naming the test files: they pass without
   the change, so they cannot be what proves it. Exit 125 is a toolchain
   failure, NO-GO as in `test`. Passing on the head is the `test` gate's
   verdict: `test-proof` runs after it in `GATES`, and `make gates` stops at
   the first NO-GO, so it never runs on a head whose suite is red.
   **GO by absence of subject** (ADR 0015 §3), nothing run, the reason on
   the line: no test file changed (mutation's case: a PR that touches no
   test is mutation's to judge); only test files changed (no code for them
   to prove: a PR that adds tests to existing behaviour is welcome); the
   base is the empty tree (a root commit, ADR 0042 §5: nothing to fail on).
   The whole changed set runs at once, never one file at a time: a
   `conftest.py` or a helper changed beside the test that uses it would
   pass alone on the base and read as proving nothing, and N files would
   cost N suite runs. Works for every runner, because it reads only an
   exit code.

3. **`gates/test-weakening.sh`: the diff may not weaken the tests.** A
   check on `git diff -M -U0 <base> <head>` restricted to
   `WORKLOAD_TEST_PATHS`, no engine, milliseconds. Per test file, NO-GO
   when:
   - the file is deleted, or renamed out of the test paths;
   - it loses assertions: lines matching an assertion pattern removed
     outnumber those added;
   - it loses tests: test definitions removed outnumber those added;
   - it gains skips: skip, xfail, todo or only markers added outnumber
     those removed.

   Counts, not lines: an assertion edited or moved removes one line and
   adds one, and a PR that changes what a test expects must stay possible.
   A line that is a comment in its language does not count as added, so
   commenting an assertion out is a loss. The threshold is zero, a
   decision: one silenced assertion is the case this gate exists for. The
   patterns, by extension, are data in `core/testdiff.py`:
   - **Python** (`.py`), from the Python 3.13 documentation of `unittest`
     and `unittest.mock` and the pytest documentation. Assertions: the
     `assert` statement; `self.assert<Name>(` (TestCase's assert methods);
     `.assert_<name>(` (mock's `assert_called*`); `pytest.raises(`,
     `pytest.warns(`. Tests: `def test…(` and `async def test…(` (pytest's
     default `python_functions`, unittest's method prefix), `class Test…`
     (pytest's default `python_classes`). Skips: `pytest.mark.skip`,
     `skipif`, `xfail`; `pytest.skip(`, `pytest.xfail(`,
     `pytest.importorskip(`; `unittest.skip`, `skipIf`, `skipUnless`,
     `expectedFailure`, also imported bare as decorators; `.skipTest(`;
     `SkipTest`.
   - **JavaScript and TypeScript** (`.js .mjs .cjs .jsx .ts .mts .cts
     .tsx`), from the Node 24 documentation of `node:test` and
     `node:assert`, plus the `expect(` and `x`-prefix forms the common
     runners share. Assertions: `assert(` and `assert.<name>(` (also as
     `t.assert.<name>(`), `expect(`. Tests: `it(` and `test(`. Skips:
     `.skip(`, `.todo(`, `.only(` on `describe`, `suite`, `it` or `test`
     (`only` silences every other test); `xit(`, `xdescribe(`, `xtest(`;
     the options `skip:`, `todo:`, `only:`; `t.skip(`, `t.todo(`.
   - Any other language: only the deletion rule applies, and the GO line
     says how many changed test files had no patterns.

   **Unless the ticket declares it**: a `## Removes tests` section, list
   items of globs in the `## Perimeter` form (repository-relative), excuses
   every finding on a file it matches, each reported on stderr. The gate
   reads the ticket from `GATES_TICKET`, which the tower's CI step sets to
   the ticket it already fetched from the Tracker, never from the PR (ADR
   0033 §2). Without a ticket (`./go gates`, `demo-gates`) nothing is
   excused. This amends ADR 0021 §5's "gates know nothing about tickets"
   for this one optional input: the gate still judges a range, and a ticket
   can only relax it, never make it pass a range it would otherwise
   refuse for another reason.

4. **The order.** `GATES := risk secrets test-weakening deps sast test
   mutation test-proof`. `test-weakening` is a diff check, cheap, with the
   other diff checks. `test-proof` goes after `test` (it needs a green
   head, 2) and after `mutation`: ADR 0015's `fake-test` fixture, whose
   test also passes on the base, is the only proof that mutation can say
   NO-GO, and with `test-proof` before it mutation would never be seen
   failing again. The order changes which NO-GO is named, never whether
   there is one.

5. **Overlapping perimeters: `assign` refuses a ticket that would share a
   file with an open one, and the ticket waits.** The tower's `assign` step
   (`core/steps/assign.sh`) first runs `core/assign.py`. It reads the
   ticket from the Tracker and its `## Perimeter` (a ticket without one is
   refused: what it would hold is unknown, ADR 0021 §5), and reads one
   document, `perimeters/held` in the StateStore: `{"tickets": {<id>:
   [<glob>, …]}}`, written by earlier assigns. A ticket listed there holds
   its perimeter while its own document is in `pr-opened`, `ci-failed`,
   `ci-passed`, `review` or `needs-human`: a PR is open and unmerged. Once
   it is `merged` its files are on the base the next worker clones, and
   from `opened` it has not started; an entry whose ticket is not holding
   is dropped at the next write, so nothing has to release a perimeter.
   Two globs overlap when one string could match both: two literals
   when equal; a literal and a glob when the glob matches it; two globs
   when one's literal prefix (up to the first `*`, `?` or `[`) starts the
   other's and one's literal suffix ends the other's. The last test can
   say yes where no path matches both, never no where one does: an error
   costs a wait, never two workers on one file. On an overlap the step
   prints `<id> waits: its perimeter overlaps <holder>'s (<a> ~ <b>),
   held while <holder> is <state>` on stderr and exits 1, before any
   clone. The tower records the failed attempt (ADR 0034 §1); the
   document stays `opened` and is not discarded, and `./go resume
   TICKET=<id>` asks for `assign` again once the holder has merged. No
   automatic wake-up: the tower has no scheduler (ADR 0034). Otherwise
   the ticket's entry is written and the step goes on. `./go assign`
   outside the tower (`core/crew.sh` directly) is a human running one
   worker and is not checked.

6. **Green base before the first ticket on an attached repository.**
   Then `core/base-gates.sh` (only when `WORKLOAD_ATTACHED` is set; the
   bundled app's main is this repository's, already judged by `./go ci`)
   fetches the code host's `HEAD`, the commit the worker will clone, and
   runs `make gates GATES="sast test" BASE=<sha> HEAD=<sha>` on it in a
   throwaway worktree with `TOOLCHAIN_ISOLATE=1`, as the CI step does.
   `sast` and `test` are the gates that judge the tree, not the diff:
   `risk`, `secrets`, `deps` (ADR 0032), `test-weakening`, `mutation` and
   `test-proof` judge only what a PR changes, so nothing of theirs is
   inherited, and an empty range gives them nothing to say. A NO-GO is
   printed as `base <sha> is red at <gate>: <line>` and the step exits 1:
   the ticket waits as in 5, until the base is fixed. A GO is cached as
   `bases/<sha>` in the StateStore and not run again for that commit; a
   NO-GO is never cached, so a flaky base is judged again at the next
   resume. This is also what keeps `test-proof` honest on such a
   repository: on a base whose suite already fails, every test "fails on
   the base".

7. **Where the logic lives (ADR 0052).** `core/testdiff.py` classifies
   changed paths for `test-proof` and judges the diff for `test-weakening`;
   `core/assign.py` holds the perimeter lease. Their process calls go
   through `core/tower.py`'s `sh()`, the one excused call. The gates and
   `core/base-gates.sh` are the shell around them: `git`, the toolchain
   and `make`, each under 30 code lines where `ci/shell-boundary.sh`
   counts. The line contract is `ci/lib.sh`'s.

8. **The proof.** `./go demo-gates` gains three fixtures in
   `app/fixtures/gates/`: `test-passes-on-base` (ticket 0001 "fixed" in the
   contract file with a test that posts the venue the contract already
   answered: mutation has no line to mutate, `test-proof` NO-GO),
   `assertion-removed` (one assertion deleted from an existing test:
   `test-weakening` NO-GO) and `test-edited`, the positive control (a
   contract value corrected and the existing assertions that pin it
   edited to match: edits, not removals, and the edited tests fail on the
   base: every gate GO). `honest-fix` stays GO through both gates with a
   subject. `./go ci` gains `ci/assign.sh`: the real `assign` step, the
   real `core/assign.py` and `core/base-gates.sh` on file-backed stub
   adapters and a stub `make`, no engine, proving disjoint, overlapping,
   released and perimeter-less tickets, the wait and the resume through the
   tower, and the base check refusing, caching and being skipped on the
   bundled app. Unit tests in `core/tests/test_testdiff.py` and
   `core/tests/test_assign.py`, written from this ADR.

## Alternatives rejected

- **Run each changed test file alone on the base.** Per-file is finer
  (one real test cannot carry a fake one in another file), but a shared
  fixture or helper changed beside the test reads as a test that proves
  nothing, and the cost grows with the number of files. Two tests in one
  file have the same problem at any granularity a runner-agnostic gate can
  see; that is mutation's and the reviewer's ground.
- **Select single tests by name** (`pytest -k`, `--test-name-pattern`).
  It needs one adapter per runner, which ADR 0042 refused for the gates.
- **`test-proof` before `mutation`**, cheapest first. It would retire the
  only fixture that shows mutation failing (4).
- **Treat a docs or contract change as "no code"**. The gate cannot tell a
  README from a template or a contract a fake serves; `test-passes-on-base`
  is exactly a contract change. A PR that only adds tests is the one
  shape that is plainly not a change to prove.
- **Weakening as a line rule** (any removed assertion line is a NO-GO).
  Every edited expectation would fail, and a PR that changes tests would
  need a ticket declaration for ordinary work.
- **An AST per language.** Exact, but one parser per language in a
  workbench that holds Python only; the patterns are a tripwire, review
  still reads the diff.
- **A list verb on the StateStore** to find the open tickets. Every
  adapter (the emulator, Firestore) would grow a verb; one document read
  and written with the existing `get` and `put` is enough.
- **A tower state `waiting`.** A new state in the table, the board and the
  audit, for what ADR 0034 already records: a failed attempt that can be
  resumed.
- **Exact glob intersection.** Deciding emptiness of two globs with
  classes needs an automaton; the prefix and suffix test is sound in the
  direction that matters (5) and fits in a few lines.
- **The whole `make gates` on the base**, empty range or last commit. The
  diff gates would judge the last commit someone else made (a commit that
  deleted a test would block every ticket), and mutation on the empty tree
  measures the whole scope (792 s on the app, `app/workload.env`).

## Consequences

- Every contract needs the twelfth key; one without it NO-GOes at both
  gates, naming it. `workloads/minimal`, `workloads/minimal-js`, the app,
  this repository's own `.lights-out/` and `./go init` carry it.
- A PR that changes code and adds a test which passes on the base is a
  NO-GO even when the test is a good one: it does not prove the change.
  The worker splits it or writes the test that does.
- `test-proof` costs one more suite run, and for the compose and image
  forms one more image build, on every PR that changes both tests and code.
- A refactor that deletes tests needs `## Removes tests` in its ticket.
- The lease is read and written by one `assign` at a time: at L0 the
  `inproc` orchestrator runs one step at a time. An orchestrator that runs
  two `assign`s at once (L2) races on `perimeters/held`, and needs a
  conditional write before it does.
- A stranded ticket in a holding state keeps its files until it is
  resumed or its PR closed by hand.

## Golden-rule check

`./go demo` runs ticket 0001 through the tower on the bundled app: `assign`
reads and writes one more document in the Firestore emulator, finds no
other ticket, and skips the base check (not attached). The replayed PR
changes the geocoder client and adds two tests to
`test_geocoder_client.py`: `test-weakening` sees additions only, and
`test-proof` runs the suite once more, on the base with those tests, where
they fail (the client raises on the 404). That run is inside the 900 s
budget, measured at M27 close. No key, no account, no new image. `./go ci`
gains `ci/assign.sh` and two unit test files, cold, no engine.
