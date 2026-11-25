# ADR 0052 — Where shell is allowed: the host, the contracts and thin entry points; logic in Python under `core/`, held by a ratchet

- Status: accepted
- Date: 2026-11-06

## Context

Measured on 2026-11-06 over tracked files: 16,817 lines of `.sh`, about
6.1k of `.py`, 1.1k of `Makefile`. By directory, `ci/` 7.1k (the
repository's self-checks), `adapters/` 3.2k, `gates/` 2.7k, `core/` 2.1k,
`verify/` 0.3k. `core/` held 23 shell files, 2,101 lines of which 1,357
were code lines (non-blank, not a comment).

Some of that shell is right where it is: `./go` runs on a host that
guarantees podman, git and a POSIX shell and nothing else (CLAUDE.md,
"Toolchain nel container"); an adapter verb is a shell file by contract
(ADR 0024: one folder plus one line); a gate is the file that prints one
`GO`/`NO-GO` line through `ci/lib.sh`. But `core/` also held a state
machine (`tower.sh`, 117 code lines), JSON schemas and record checks
(`audit.sh` 75, `incident.sh` 54, `triage.sh` 53), all written as `jq`
filters inside `sh`. That logic had no unit test of its own, only the
end-to-end `ci/*.sh` suites, and the self gate (ADR 0049) mutation-tests
`core/` with `unittest`, so it saw only the Python half of the nucleus.
Nothing stopped the shell half from growing.

## Decision

1. **Shell stays where it is the contract or the host.**
   - The host side: `./go`, `lights-out`, `package/entrypoint.sh`, and the
     `core/` files `./go` runs on the host before any image exists:
     `core/doctor.sh` (`go` `doctor`) and `core/attach.sh` (`go` `attach`).
   - Adapter verbs, `adapters/<name>/<verb>.sh` (ADR 0024).
   - The gate wrapper: the file that sources `ci/lib.sh` and prints the one
     line (CLAUDE.md, "Contratto di output dei gate").
   - Thin entry points in `core/` whose paths callers name (`Makefile`,
     `./go`, the tower, adapters), and glue whose job is running other
     processes in order (git, `docker-compose`, an adapter verb).
2. **Logic goes to Python under `core/`**: parsing, decisions, state
   machines, JSON handling, anything with branches beyond a few lines.
   Standard library only. It runs in the workbench, where `python3` is
   already a dependency of `core/` (`ci/lib.sh` calls `core/policy.py`);
   the package image and the Gitea runner are built from the workbench, and
   no `core/` file runs in a harness image. Unit tests go in `core/tests/`
   with `unittest` (what `ci/core-tests.sh` and the self gate's
   `MUTATION_CMD` run; the workbench has no pytest), written from the ADRs
   and the file headers that state the rule, not by translating the shell.
3. **The entry-point pattern** is the one `effective_policy` in `ci/lib.sh`
   already uses. The `.sh` keeps its path, arguments and environment;
   `python3 core/<x>.py` prints a reason or a detail on stdout, details on
   stderr, and exits 0 or 1; the `.sh` turns that into `go`, `go_with` or
   `nogo`. An entry point that is not a gate is `exec python3 core/<x>.py
   "$@"`. Each module runs as a script and is imported by its tests as
   `core.<x>`; one may import another's function, as `policy.py` imports
   `risk.py`'s parser. Adapters are still called as `sh
   adapters/<name>/<verb>.sh`, through one function, `sh()` in
   `core/tower.py`, which the other modules import: an argument list, never
   a shell line. The self gate's bandit flags every process call (B404,
   B603, B607) and any finding is a NO-GO; those findings are excused on
   that one line and on the imports, each with its reason, as
   `core/egress.py` excuses B104 (ADR 0046 §2), so a second process call
   elsewhere is a finding again. No other shared helper and no dependency.
4. **The ratchet, `ci/shell-boundary.sh`, in `./go ci`.** A `core/` shell
   file has at most **30 code lines**. Provenance: the owner's decision
   (2026-11-06), checked against a measurement the same day: the glue in
   `core/` that needs no Python is 15–17 code lines (`steps/merge.sh`,
   `steps/assign.sh`, `history-findings.sh`), the entry points this slice
   leaves are 1–20, and the smallest file that held a JSON schema or a
   state machine was 53 (`triage.sh`). Comments do not count, so the
   repository's dense headers are not penalised. Every file over the limit
   today is named in the check with its exact count: debt to pay down, not
   grandfathered. NO-GO on an unlisted file over 30; on a listed file over
   its count (move logic out) or under it (lower the entry); on an entry
   whose file is at most 30 or gone (remove it). So the list always states
   the real debt, and this ADR's numbers can be checked against it. The
   host files are a second list, exempt, and an entry for a file that is
   gone is a NO-GO too. The check proves it can fail on a temp tree before
   it judges this one. The count is a tripwire, not the rule:
   `perimeter.sh`, 27 code lines, is a glob matcher, and review still
   judges it as logic.
5. **The first slice**, this ADR's: the state machine and the record
   readers, the four files that were `jq` throughout.
   - `core/tower.sh` → `exec python3 core/tower.py "$@"`. The transition
     table is `PRECONDITION` and `NEXT` in `core/tower.py`; `ci/board.sh`
     reads them from there instead of parsing the shell's `case` tables.
   - `core/audit.sh` and `core/incident.sh` → wrappers over `core/audit.py`
     and `core/incident.py`.
   - `core/triage.sh` keeps the role run, as `core/postmortem.sh` does;
     `core/triage.py` judges `calls.json` and writes `incident.json`.

   Callers do not change: same paths, arguments, environment, stdout line
   and stderr lines (`ci/tower.sh` greps `already applied` and `not
   stranded`). Each module was compared with the script it replaces on the
   same inputs: identical stdout, stderr and files, except that a release
   record's non-canonical number literal (`1.5E+2`) is now written in
   Python's form (`150.0`); `core/launchpad.sh` writes none. 103 unit tests
   (`test_tower.py` 39, `test_audit.py` 24, `test_incident.py` 23,
   `test_triage.py` 17); `triage.sh` had no cold test before.
6. **Later slices, recorded.** The rest of `core/` is the debt list:
   `toolchain.sh` 126, `crew.sh` 87, `promote.sh` 87, `steps/ci.sh` 85,
   `launchpad.sh` 77, `steps/review.sh` 75, `steps/release.sh` 56,
   `steps/rework.sh` 52, `postmortem.sh` 49, `egress.sh` 42, `init.sh` 40,
   `mutation-baseline.sh` 32. `toolchain.sh` is sourced by three gates, so
   its slice changes them. Then `ci/` and `gates/` suites: their logic
   moves to Python tests, the wrapper stays, and the check's scope grows
   to each directory as it is paid down. `adapters/` change only behind
   their verbs, never the verbs. *Amended 2026-11-13:* `gates/` is under
   this rule since ADR 0084, its logic in `core/gates/`.

## Alternatives rejected

- **All Python, `./go` included.** The host has no Python by contract; a
  Python `./go` would add a host prerequisite and break the golden rule
  on a machine that has only podman and git. Adapter verbs are a shell
  contract that contributors implement in a folder (ADR 0024). And a
  16.8k-line rewrite in one change would put `./go demo` at risk for no
  test gained per line moved.
- **Go.** A compiler and a second toolchain in the workbench, and
  per-platform binaries if the host side were to use it; nothing in the
  repository to reuse. Python is already in the workbench, in `core/`, in
  the self gate's SAST and mutation commands, and in the board.
- **Leave it as is.** Nothing stopped `jq` inside `sh` from growing, it
  stayed outside the mutation score, and every new rule had to be proven
  end to end or not at all.
- **A threshold on total lines.** It counts comments, and this repository
  documents each rule where it is enforced; a long header is not logic.
- **Debt as a list in this ADR only, or grandfathered silently.** A list
  nobody checks drifts; a ceiling the check holds, which must also be
  lowered when a file shrinks, cannot.

## Consequences

- `core/` shell: 2,101 → 1,764 lines, 1,357 → 1,074 code lines (−283),
  23 files, 12 of them on the debt list. Tracked `.sh` in the repository:
  16,817 → 16,552 (the check itself is 76 lines).
- The self gate's mutation and SAST commands now see the tower, the audit,
  the incident builder and the triage judge. Measured on 2026-11-06 with
  its own runner, scoped to the four modules: 701 mutants, 607 killed,
  score 0.866, against the contract's `MUTATION_BASELINE` of 0.3934; bandit
  1.9.4 over `core/`: no finding.
- A change to a debt file must keep or lower its count and update the
  entry when it lowers it.
- `ci/board.sh` imports `core/tower.py`; renaming its tables is a change to
  that check too.

## Golden-rule check

`./go demo` runs the tower (`e2e`'s steps and the audit) through the same
`core/tower.sh` and `core/audit.sh` paths; `python3` is in the workbench
image the demo already builds. No key, no account, no new image layer.
`./go ci` gains one cold check, `shell-boundary`: `find` and `awk`, no
engine.
