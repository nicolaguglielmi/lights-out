# ADR 0049 — lights-out judges its own changes: a hand-written contract at the root, the Action on every push and pull request with a judge built from the base, and the gates that cover only part of the repository said so

- Status: accepted (amended by ADR 0074 §1 [a push is judged from its last green first-parent ancestor, not HEAD^1]; amends ADR 0042 §4 and resolved 10, ADR 0037 §3 [an attached repository's history finding is excused only by a fingerprint in the base's `.lights-out/.gitleaksignore`]; amends ADR 0042 §6 [init lists history findings; the python runners skip tests inside the package])
- Date: 2026-11-05

## Context

lights-out's own CI ran `./go ci`: about thirty self-checks (shellcheck,
the gate output contract, the unit tests, roles=6). The six gates it sells
— risk, secrets, deps, sast, test, mutation — judged the bundled app, the
example workloads and, through `./action`, a scratch repository in
`package.yml`; never a change to lights-out. A tool that governs other
people's agents should take its own verdict.

`lights-out init` on this repository NO-GOes: "no stack found: looked for
pyproject.toml (python) and package.json (javascript) at the root" (run
42033682939 on Actions, 2026-11-05). The repository is mostly POSIX shell;
its Python sits in `core/`, `adapters/`, `board/` and `app/`, with no
`pyproject.toml` at the root. Init infers a stack only from root files,
one stack per repository, and has no shell stack.

## Decision

1. **A hand-written contract at `.lights-out/`**: the eleven keys, each
   under its provenance; `worker.md`; a `policy.env` with no override;
   `runners/`, the python stack's runners byte for byte (`ci/init.sh`
   checks) plus `self-checks.sh`.
   - `TOOLCHAIN_IMAGE=workbench`: the image refuses any other form.
   - `TEST_CMD` runs `make ci` through `runners/self-checks.sh`, in an
     emptied environment (`make gates` exports its variables, and the
     self-checks must see a cold checkout, as `./go ci` gives them). The
     test gate is the thirty self-checks, shell included.
   - `SAST_CMD` is bandit over `core/`; `MUTATION_CMD` is cosmic-ray over
     `core/` against `python3 -m unittest discover -s core/tests -t . -f`.
   - `MUTATION_BASELINE=0.3934`, measured: 1327 mutants, 522 killed, 344 s
     (run 42035898400, the committed runner). It is low because
     `postmortem.py` has no unit test and `init.py`'s version parsers are
     tested on few cases; it is the number the suite earns, not one chosen
     to pass.
   - `WORKLOAD_LOCKFILES` names the eleven tracked lockfiles.
   - The plane keys are empty: lights-out's plane runs `app/`, not itself.
2. **`.github/workflows/self.yml`**, GitHub-only (ADR 0039): on every push
   the range is the commit against its parent; on a pull request, GitHub's
   merge commit against its first parent, the PR's base. The Action in
   `./action` judges it and posts the `lights-out gates` check run.
3. **The judge is built from the base, in the same run.** The job adds a
   worktree at the base and runs its `package/build.sh`; the image's
   entrypoint already takes `.lights-out/` from the base (ADR 0045 §3).
   Base gates with the base contract is the pair ADR 0033 gives the
   tower: a change that rewrites `gates/` or `core/` is judged by the
   gates it replaces, and a contract key added with the gate that reads
   it never meets an image that does not.
4. **A history finding is excused only by the base, by fingerprint.**
   Owner's decision, 2026-11-05 ("ok, procedi con b"). On an attached
   repository `ci/secrets.sh` still judges the whole history reachable
   from HEAD, with the platform's config and inline `gitleaks:allow`
   ignored (ADR 0042 resolved 10): a key in history is live until it is
   rotated. A finding a human has judged a false positive is excused by
   its fingerprint in `.lights-out/.gitleaksignore`, gitleaks' own format
   and mechanism (`-i`), read from the BASE commit, never from HEAD, so a
   change cannot excuse its own finding; `.lights-out/*` is in
   `RISK_HIGH_PLATFORM`, so adding a line goes to review. Each excused
   finding is still printed on stderr, `secrets: <fingerprint> excused by
   base <sha>`. gitleaks 8.30.1 also loads a `.gitleaksignore` from the
   root of the directory it scans (`cmd/root.go`): the gate scans the git
   directory, which has no working tree, so a HEAD copy at the root, which
   a pull request could add, is never read either. `./go init` lists the
   history findings with their fingerprints on stderr
   (`core/history-findings.sh`) and never drafts the file: whether a
   finding is a false positive is a human's call. `ci/secrets-history.sh`
   checks the spec on a throwaway repository: a new leak in the range is
   NO-GO; a finding excused only at HEAD is NO-GO; one excused at the base
   is GO with the stderr line; init lists and drafts nothing.
   Found here: ADR 0025 quotes a PEM header line, excused inline in
   3fb56bf, and every judgement of lights-out was NO-GO on it (run
   42035898458). Its fingerprint is committed alone, so that commit is the
   base that excuses it.
5. **The python runners skip tests inside the package** (`tests/`,
   `test_*.py`). `sast.sh` already said tests are not scanned and scanned
   `core/tests/` (bandit B101 on `assert`); `mutation.sh` mutated the
   suite itself: `core/` scored 0.5496 with its tests mutated (1947
   mutants), 0.3934 without. A user's package laid out the same way got
   the same inflated baseline.

## What is not judged, and why

- **Mutation on shell.** No mutation tool for POSIX sh is in the image,
  and none is pinned anywhere in the repository; a score lights-out
  cannot measure is not reported. Shell is 17,343 lines (the `*.sh`
  files, `go`, the wrapper, the Makefile) against 5,305 of Python, 1,122
  of them in `core/` outside its tests (`git ls-files`, 2026-11-05):
  mutation covers about 5% of the code, and the shell's test is `make
  ci`, never a mutation score.
- **Mutation and SAST outside `core/`.** `WORKLOAD_MUTATION_SCOPE` holds
  one path. bandit finds 1 issue in `adapters/` and 3 in `board/` today
  (run 42033682939): widening the scope is a change of its own, after
  those are triaged.
- **The workflow and the Action are the head's.** A pull request can edit
  `self.yml` or `action/`; GitHub runs the head's workflow. `.github/*` is
  in `RISK_HIGH_PLATFORM`, so such a change is high risk and goes to
  review; `action/`, `package/`, `stacks/` and the `lights-out` wrapper
  are not in that list today.
- **Risk is reported, not gating.** Almost every change here touches
  `RISK_HIGH_PLATFORM` paths, so the line says `risk high, review`.

## Alternatives rejected

- **The image built from the head**, as `package.yml` does for its scratch
  repository. The judged code would be the judge: a change to
  `gates/mutation.sh` would grade itself, and a new contract key would
  meet the base's contract.
- **Teach init a shell stack.** No shell mutation tool to pin, and
  `make ci` is a repository's own convention, not something to infer.
- **Judge the range only, report the history as inherited** (deps'
  rule, ADR 0032). A key in history stays live until it is rotated:
  range-only would give a stranger GO on a repository with a leaked key
  in its past. A vulnerable dependency is fixed by the next change; a
  leaked key is not fixed by any change.
- **Leave the gate as it is**, the self-judgement red or
  `continue-on-error`. A gate that is NO-GO on every change, with no
  remedy but rewriting history, is a gate people switch off; and a red
  job nobody reads is a fake verdict of its own.
- **Excuse findings from HEAD's file, or from a `.gitleaks.toml`** in the
  repository. A pull request would excuse its own leak (ADR 0042
  resolved 10's reason).
- **Rewrite history to drop 3fb56bf's line.** The repository is public;
  a rewrite of `main` breaks every clone.
- **`./go ci` as the only judge.** It never runs deps, mutation or the
  base's policy on the change.

## Consequences

- Every push and pull request runs a second image build and `make ci` a
  second time (about two more minutes), on top of `ci.yml`'s `ci` job.
- An attached repository with a real key in its history stays NO-GO
  until the key is rotated and a human commits its fingerprint; `init`
  says so up front.
- A change to the mutation suite or to `core/` should re-measure
  `MUTATION_BASELINE` in a dedicated commit, as for any workload.

## Golden-rule check

`./go demo` is unchanged: the demo judges the bundled app, whose
toolchain is `compose:backend-test` and whose runners are its own, and
the checkout's own secrets check is unchanged. `./go ci` gains one cold
check, `secrets-history`: gitleaks on a throwaway repository, no engine,
no key.

## Amendment 2026-11-11 (M24 phase C): `ci/init.sh` judges the runners committed at HEAD

The first change to the python runners since this ADR (the scorer fix of ADR 0042's amendment of the same
day) was NO-GO in `self` by construction: the image's entrypoint puts the base's `.lights-out/runners/` back
into the working tree (§3, ADR 0033), and `ci/init.sh` case (g) compared that copy, byte for byte, with the
head's `stacks/python/runners/`. With ADR 0074's base, the last green ancestor, every later push would have
been judged against the same old copy, so `self` would have stayed red on the branch and, after a merge, on
`main` (run 43087538182).

- §1's "byte for byte" is a property of commits: `ci/init.sh` reads both copies from HEAD (`git archive`)
  and requires them equal, `self-checks.sh` aside. In the working tree, `stacks/python/runners/` must be
  HEAD's, or equal to the working tree's `.lights-out/runners/` (an uncommitted edit made to both). A base's
  contract in the working tree is then not a difference. A commit that changes one copy and not the other is
  still NO-GO, and so is an uncommitted edit to the stack alone. `ci/init.sh` case (k), five throwaway
  repositories; NO-GO on the base's contract before the change, GO after.
- Rejected: leaving the push red until the owner merges. With ADR 0074 the red does not end on its own.
- Rejected: taking `stacks/` from the base too. The check would then compare two base copies and never see the
  change it is there to see.
