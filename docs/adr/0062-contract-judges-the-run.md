# ADR 0062 — `contract` judges the run: each `./go ci` check writes its stdout and exit code to a per-run record under `.crew/ci/`, and `contract` judges those records instead of running the checks a second time

- Status: accepted (amends ADR 0060 §7 [the `ci` stage runs each check once; where the time is]; ADR 0011 §4 [a `ci/` check this `./go ci` already ran is judged from its record, not run again; the scan is unchanged])
- Date: 2026-11-09
- Milestone: M34

## Context

`./go demo` must reach a rollback from a clean clone within
`DEMO_BUDGET_SECONDS=900` (plan §2, an owner's decision; ADR 0013 §7).
ADR 0060 §7 found the demo's `ci` stage to be about 43 % of the run
because every check runs twice: once as `ci-<name>`, then again inside
`ci/contract.sh`, which runs every `ci/*.sh` but `lib.sh` and itself, every
`gates/*.sh` and `verify/*.sh`, with stdout captured, and judges the shape.
Measures on 8e968aa:

- Actions, stranger (podman), from the demo log: run 42530031109, demo
  820 s, the 40 checks before `contract` 203 s, `contract` 186 s; run
  42533480868, demo 732 s, checks 145 s, `contract` 142 s. engine-compat
  (docker), from the job log's timestamps: 42530031181, demo 828 s, checks
  229 s, `contract` 229 s; 42533480920, demo 815 s, checks 224 s,
  `contract` 225 s.
- Local, workbench on podman machine, macOS host, `./go ci-contract` timed
  per gate: 179.3 s, of which 179.0 s re-running the 40 checks `ci` had
  just run. The rest is 0.3 s: `ci/engine-compat.sh`, the 14 `gates/*.sh`,
  the 2 `verify/*.sh`, the light entry point three times, the `nogo` probes.
  `./go ci` whole, from a clean clone: 360 s, the 40 checks 186 s,
  `contract` 174 s.

So the cost is the second run, not the judging.

## Decision

1. **Records.** `ci/lib.sh` gains `record_check SCRIPT NAME`, which runs
   `sh SCRIPT`. With `CI_RECORDS` empty or unset, that is all: stdout goes
   where it went before. With it set, the function makes the directory,
   sends stdout to `$CI_RECORDS/NAME.out` (the bytes exactly), then copies
   that file to its own stdout, then writes the exit code as one decimal
   line to `$CI_RECORDS/NAME.rc`, and returns that code. `.rc` is written
   last, so a record cut short has none. The Makefile's `ci-%` recipe
   becomes `. ci/lib.sh; record_check ci/$*.sh $*`. Stderr still streams.
2. **Freshness by run id, not by cleaning.** Only the `ci` target sets
   `CI_RECORDS`, as a target-specific `export CI_RECORDS :=
   .crew/ci/<run id>`, the id 16 hex characters from `/dev/urandom`, drawn
   once per make invocation. Its prerequisites inherit it, so every
   `ci-<name>` of this run, `contract` included, sees one directory that
   no other run can have written. When every prerequisite has passed,
   `ci`'s recipe removes it (its `.out` and `.rc` files and the directory,
   and only under `.crew/ci/`: one named by hand elsewhere is left alone)
   and prints `GO: ci`; after a NO-GO it stays
   under `.crew/ci/` for study and is never read again. `./go ci-contract`
   alone and `./go ci-<name>` alone have no `CI_RECORDS`: nothing is
   written or read, stdout reaches the terminal as today, and `contract`
   runs everything cold. A `CI_RECORDS` given by hand (`./go ci-contract
   CI_RECORDS=<dir>`) is trusted as given: that is how a test plants one.
3. **What `contract` judges.** The scan of `ci/`, `gates/`, `verify/` is
   unchanged and goes by files: a record with no `ci/*.sh` is ignored. For
   each `ci/*.sh` but `lib.sh` and `contract.sh`, if the record is usable,
   `NAME.out` is judged with `NAME.rc` as exit code by the same check a
   cold run gets (one line counted from the file, ticket 0010; no ANSI
   escape; the gate's own name; GO with 0, NO-GO with 1). Usable:
   `NAME.rc` is a readable regular file holding exactly one line of one to
   three digits, value 0 to 255 (the exit status range, an external
   constraint), and `NAME.out` is a readable regular file. Anything else,
   missing included, and the check runs cold as today, with one stderr
   line saying so. With no record, cold: `ci/engine-compat.sh` (host-side,
   not in `CI_CHECKS`), a `ci/*.sh` left out of `CI_CHECKS`, every
   `gates/*.sh` and `verify/*.sh`. The light entry point (ADR 0061 §5)
   and the `nogo` probes (ADR 0038 §5) are unchanged.
4. **Stronger and weaker.** It now judges the line the run printed, in
   the order and state it ran in; the cold re-run judged a second run,
   after everything else, which could differ. It now trusts files a check
   can write; a check can equally edit `ci/contract.sh` or `ci/lib.sh`, and
   the `ci` job runs the PR's own `contract.sh`, so no new trust is
   granted (the tower judges a PR with `make gates` and the base's judges,
   `core/steps/ci.sh`, never with `ci`). At a terminal, under `./go ci` a
   check's stdout is a file, never a TTY, as on Actions; `NO_COLOR` was
   already set by the lib, and the line was already the check's last
   output.
5. **The proof it can fail lives in `ci/contract.sh`**, beside the `nogo`
   probes, so every `./go ci` and every cold `./go ci-contract` runs it.
   In a temporary directory it plants a tree: a copy of `ci/lib.sh` and
   of `ci/contract.sh`, per case a gate `ci/<case>.sh` that sources
   `ci/lib.sh` with `name` set to the case name, and the record below in
   a planted `CI_RECORDS`. It judges the cases by the path a real check
   takes: it runs the planted tree's `ci/contract.sh` there and reads its
   one line, which names each rejected gate as `<case>(<why>)`. A run on
   a planted tree does not prove itself again (`CONTRACT_PLANTED=1`, set
   by the proof alone). Where the table leaves the cold run open, the
   planted gate prints the opposite of the expected verdict when run, one
   good line for a rejected case and two lines for an accepted one, so the
   record alone decides. A "rejected" case that is accepted (or the
   reverse) makes `contract` print `NO-GO: contract — gate(s) violate the
   output contract: contract(record <case> accepted)` (or `rejected`),
   exit 1. Planted cases never join the real gates' list.

   | case | `.out` | `.rc` | cold run prints | expected |
   |---|---|---|---|---|
   | two-lines | `GO: two-lines` twice | `0` | — | rejected, `lines=2` |
   | ansi | `GO: ansi — `, byte 0x1b, `[31mred` | `0` | — | rejected, `ansi` |
   | go-exit-1 | `GO: go-exit-1` | `1` | — | rejected, `GO,exit=1` |
   | nogo-exit-0 | `NO-GO: nogo-exit-0 — planted` | `0` | — | rejected, `NO-GO,exit=0` |
   | wrong-name | `GO: someone-else` | `0` | — | rejected, `format` |
   | blank-after | `GO: blank-after`, an empty line | `0` | — | rejected, `lines=2` |
   | no-rc | `GO: no-rc` | absent | two lines, exit 0 | rejected, run cold, `lines=2` |
   | bad-rc | `GO: bad-rc` | `zero` | two lines, exit 0 | rejected, run cold, `lines=2` |
   | never-run | absent | absent | two lines, exit 0 | rejected, run cold, `lines=2` |
   | good | `GO: good — planted` | `0` | — | accepted |
   | good-nogo | `NO-GO: good-nogo — planted` | `1` | — | accepted |

   One more case calls `record_check` on a planted gate that prints two
   lines and exits 1: it must print both lines and return 1, its `.out`
   hold both, its `.rc` be `1`, and the record be rejected; with
   `CI_RECORDS` unset or empty it prints the same and writes nothing. A
   case rejected for another reason than the table's is `contract(record
   <case> wrong-why)`, a flaw of the capture `contract(record_check:
   <what>)`. Black-box form: with `CI_RECORDS=<dir>` holding `NAME.out` = `GO: NAME` and
   `NAME.rc` = `0` for every `ci/*.sh` but `lib.sh` and `contract.sh`,
   `contract` is GO in about a second; with `help.out` = `GO: help` and an
   empty line it prints `NO-GO: contract — gate(s) violate the output
   contract: help(lines=2)`, exit 1; with `help.rc` removed, `help` runs
   cold and `contract` is GO.
6. **Expected gain** (derived, not measured): `contract`'s re-run less the
   second that stays cold, 141 to 185 s on the two stranger runs above,
   about 19 to 23 % of them, which would take about 591 and 635 s: margin
   to 900 s from 168 and 80 s to about 309 and 265 s. On engine-compat the
   same re-run is 225 and 229 s; ADR 0060 §7's 162 and 176 s are of the
   same order. What remains: the 40 checks once, 145 to 203 s on Actions
   (largest locally: shell 42 s, evals 33 s, logbook 19 s, core-tests
   13 s); the workbench build, about 15 %, and the PR gates in `e2e`,
   about 15 % (ADR 0060 §7).

   After, measured. Locally, same machine and day as the 360 s above:
   `./go ci` from a clean clone 183 s, the 40 checks 181 s, `contract`
   1 s, `ci/engine-compat.sh` the one check it ran cold. On Actions:

   | commit | engine-compat (docker) | stranger (podman) | `contract`, engine-compat and stranger |
   |---|---|---|---|
   | 8e968aa, before | 828 s (42530031181), 815 s (42533480920) | 820 s (42530031109), 732 s (42533480868) | 229 and 225 s; 186 and 142 s |
   | 877e7a2, this branch | 632 s (42588292173) | 678 s (42588292176, second attempt) | 0.9 s; 1 s |

   The gain is the re-run and nothing else: engine-compat 183 to 196 s
   less, the stranger 54 to 142 s less on a runner whose 40 checks took
   211 s against 145 and 203 s before. Margin to 900 s: 268 s and 222 s.
   The stranger's first attempt on 877e7a2 ended `NO-GO: e2e — ticket 0001
   ended at held` at 813 s, with `ci` GO at 374 s and `contract` in 2 s:
   in `e2e`, on podman, the launchpad judged the canary GO at 5 % and 25 %
   with `error_rate canary=no data`. That stage is not this ADR's; the
   handoff says what was found.

7. **Done when:** from a clean clone `./go ci` GO with each check run
   once, shown by `contract`'s time in the stranger job's demo log, written
   into §6's table: of the order of the 0.3 s measured cold plus the
   proof, not of a second run's 142 to 186 s; `./go ci-contract` alone
   GO, cold; `./go ci-contract CI_RECORDS=.crew/planted` behaving as §5's
   black-box form; `./go demo` GO on Actions in engine-compat and
   stranger, §6's table filled; the six workflows green: ci, stranger,
   self, site, package, podman.

## Alternatives rejected

- **(b) A sample of the checks.** A check outside the sample breaks the
  contract unseen; "every check" is the contract.
- **(c) A smaller `CI_CHECKS` in the demo.** `contract` scans `ci/*.sh`,
  not `CI_CHECKS`, so the re-run stays; and the demo stops proving the
  `ci` the `ci` job runs.
- **Drop `ci` from the demo, the `ci` job runs it.** The stranger job is
  the one clean-clone podman run of `./go ci`; what the demo proves is
  plan §2's, the owner's.
- **`contract.sh` as the runner of every check.** `ci` would print one
  line for forty checks, and one gate would judge verdicts and shape.
- **One fixed `.crew/ci/`, cleared by a first prerequisite.** Relies on
  prerequisite order, which `./go ci -j4` breaks: a stale record judged.
- **A tree hash as the freshness key.** A hash per check, and blind to the
  state under `.crew/` that checks read.
- **The capture inline in `ci-%`.** The proof could not run it on a
  planted gate without writing into `ci/`.

## Consequences

- **The one that will hurt: no independent second run any more.** A check
  whose line depends on what ran before it is judged only in that order;
  the cold `./go ci-contract` is the one independent run, and nothing on a
  merge's path runs it. And `-j`, if it ever comes, gives no stale verdict
  but quietly gives the time back: an unfinished record means a cold run.
- A failed `./go ci` leaves its directory under `.crew/ci/`, about 80
  small files, never read again.
- CLAUDE.md's contract section says `contract` runs every gate; under
  `./go ci` it judges the record of the run that ran it. The wording is
  the owner's to change.

## Golden-rule check

`./go demo` gets shorter by the re-run and adds nothing: no image, no
dependency (`od` and `/dev/urandom` are in the workbench, GNU Make 4.4.1
hands a target-specific `export` to prerequisites and a command-line
`CI_RECORDS=` to recipes: checked there on 2026-11-09), no key, no
account. It must stay GO from a clean clone under 900 s.
