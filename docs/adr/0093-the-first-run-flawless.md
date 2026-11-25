# ADR 0093 — The first run, flawless: a preflight that moves a busy port, a demo that counts its steps, the repository's checks beside the plane, and the run's own console at the end

- Status: accepted (M60)
- Date: 2026-11-16
- Milestone: M60 (docs/PLAN.md, "Sprint 2026-11-16 (M58–M63)", "M60 — Il
  primo avvio senza intoppi (ADR 0093)")
- Amends: ADR 0003 and ADR 0012 §5 (the `ci` stage runs beside the plane,
  not before it; its verdict is still awaited before `GO: demo`); ADR 0013
  §7 (the stranger workflow also judges a regression budget); ADR 0045 §4
  (`./go doctor` reads the ports the preflight moved); ADR 0089 §4
  (`core/freeze.py --hash`).

## Context

The owner wants a person who tries lights-out for the first time to get
there with no hassle, and to see something worth showing at the end. The
friction below was measured on 2026-11-16, before any change, on two
first runs from a fresh clone.

### The friction log, before

**A. The `stranger` workflow** (a clean `ubuntu-latest` runner, podman
from apt, the README's Linux path followed literally). The last five
runs where its `demo` job ran, all green:

| run (job) | `./go demo` | rollback reached at |
|---|---|---|
| 43657565581 (132639432115) | 751 s | 531 s |
| 43652712278 (132624513950) | 805 s | 573 s |
| 43650348153 (132617211995) | 733 s | 510 s |
| 43648941631 (132612887476) | 802 s | 577 s |
| 43627237505 (132545791809) | 700 s | 491 s |

Install podman took 7–15 s, the clone 1 s, `./go doctor` 1–2 s. Where
the 751 s of job 132639432115 went, from its timestamped log:

| seconds | stage |
|---|---|
| 0–50 | the workbench image built (first run) |
| 50–131 | `./go up` on the host (engine-compat): 9 images pulled or built, 7 adapters ready |
| 131–134 | `./go smoke` |
| 134–452 | **`make ci`, the repository's 52 self-checks: 318 s** |
| 452–468 | `make up` again, inside the demo: nothing to start, the stable image re-published |
| 468–531 | `demo-rollback`: a canary at 5%, judged NO-GO on error rate, rolled back |
| 531–749 | `e2e`: ticket 0001 requested, spec, PR, the eight gates (120 s, mutation included), merge, a canary at 5% (75 s), rolled back |

The run printed 3,260 lines. The line that matters, the canary's
`NO-GO: launchpad — 5%: error_rate canary=1.000 … → rolled back`, is
line ~2,000 of them. The run ends by printing the app's, Gitea's and
Prometheus's URLs; the console, where the rollback is explained, is not
named.

**B. This Mac** (podman 6.1.4, rootful machine, libkrun, 4 CPU, 4.656
GiB; the images already in the engine's store from other work, so this is
a warm first run, never a cold one). `git clone` from GitHub: 3 s.

| step | time | what happened |
|---|---|---|
| `./go doctor` | 1 s | `NO-GO: doctor — ports`: 8080 in use (held by `gvproxy`: another container of the owner's publishes it) |
| `./go demo`, no `.env` | 20 s | `Error response from daemon: cannot bind tcp port :8080: address already in use`, then the hint `APP_PORT=<port> in .env`; `NO-GO: engine-compat`. The plane is left half up. |
| `./go down` | 14 s | a manual step the error did not mention |
| write `APP_PORT=9000` in `.env` | — | a manual step |
| `./go demo` | 1,106 s | `NO-GO: demo — rollback reached, but 1103s > 900s budget`. Up and smoke on the host 46 s; `make ci` 504 s (47–551); `make up` again 65 s; `demo-rollback` 68 s; `e2e` 419 s, of which the PR's gates 211 s and its release 107 s. 2,048 lines printed. |

**C. Seen elsewhere, from the brief of this milestone:** a host port
3000 held by a local Node server made `./go up` fail with `gitea not
ready after …` instead of naming the port; on macOS the podman machine
has to be created and started by hand (`./go` prints the two commands);
trying lights-out on one's own repository takes `./go init <path>`, then
`./go gates`, and the plane half more; and after the demo a person has to
know `./go console` exists.

## Decision

### 1. A preflight, on the host, before the plane

`core/preflight.sh` runs on the host, in shell (no Python there, ADR 0052
§1; it joins `core/doctor.sh` and `core/attach.sh` on
`ci/shell-boundary.sh`'s host list). `./go` runs it for `demo` and `up`,
after the engine answers and before anything is built. It prints one
stderr line per item, `preflight: <item> <what>`, and the gate contract's
one stdout line: `GO: preflight` or `NO-GO: preflight — <what to do, in
one line>`.

a. **The machine.** On macOS and Windows, when podman has a machine and
   it is stopped, `./go` (any command that needs the engine) starts it (`podman machine start <name>`) and
   says so in one line, instead of dying with the command to type. With
   no machine at all it still prints the two commands that create one:
   the size of a VM on someone's laptop is theirs to choose.

b. **Memory.** The engine's `MemTotal` below 3,800 MiB prints one
   warning line with the command that grows a podman machine; it does not
   stop the run. Provenance: the README's floor is a 4 GiB machine
   ("runs one demo at a time"); this Mac's 4.656 GiB machine reports
   4,758,765,568 bytes, 95% of its nominal size, so a 4 GiB machine
   reports about 3,890 MiB; 3,800 is that, rounded down (a decision).

c. **Disk.** Free space in the engine's store (podman: `GraphRootAllocated
   − GraphRootUsed`; docker: `df` of `DockerRootDir` when the host can
   read it) below 8,000 MB is a NO-GO naming the free space, the need and
   `podman system prune` / `docker system prune`. Provenance: `podman
   system df` after a whole demo on a clean runner (stranger run
   43837534798, 2026-11-16): images 3.299 GB, containers 3.513 GB, local
   volumes 0.057 GB, 6.87 GB in all; 8,000 MB is that plus about 15% (a
   decision). A first draft said 6,000 MB from a sum of image sizes on
   this Mac; the measure replaced it. The workflow keeps printing `podman
   system df`, so a growing footprint shows.
   When the engine does not say, the line says "not checked".

d. **Ports.** The plane publishes six host ports. Five can move, each
   with its variable: the app's `APP_PORT` (8080), `CODE_HOST_PORT` (3000,
   Gitea at L0), `METRICS_PORT` (9090, Prometheus), `DELIVERY_ADMIN_PORT`
   (9901, Envoy's admin) and `FLAGS_PORT` (8016, flagd). The variables are
   named for the port, not the adapter, because `core/preflight.sh` reads
   them and `core/` names no vendor (ADR 0024 §1, `ci/vendor-free.sh`,
   which caught the first draft's `GITEA_PORT`). The registry's 5050 cannot: it is part of every image's name,
   `localhost:5050/…`, and of the engine's insecure-registry entry (ADR
   0025). For each port, in this order:
   - held by this checkout's own plane (a container of compose project
     `lights-out` whose `working_dir` label is this checkout): fine;
   - held by a `lights-out` plane started from another directory:
     NO-GO, naming that directory and `./go down` there;
   - set in `.env` and held by anything else: NO-GO, naming the port, the
     holder when the host can tell (`lsof`), and the two ways out (another
     value in `.env`, or no line and `./go` picks);
   - otherwise, free: fine; held by something else: the first free port
     after it, up to 99 above, is written to `.crew/ports.env` and one
     line names the holder and the new port. 5050 held by something else
     is a NO-GO naming the holder, since it cannot move.

   `.crew/ports.env` holds only moved ports, one `NAME=port` per line. A
   port it holds is kept on the next run while it is free or held by this
   plane, so the URLs a person has open stay valid. The Makefile includes
   it before `.env`, so a value there wins, and exports the five variables, so compose, the
   adapters and the printed URLs read the same ports. `./go doctor` reads
   it too, and says a moved port is moved, not busy.

   The emulators' 8085 and 8086 are named when busy, as `./go doctor`
   does, and never moved: no profile in the demo uses them (ADR 0060 §3).

e. **A ready check names its port.** When `adapters/gitea/ready.sh` times
   out, it says whether something answered on its host port and with
   which HTTP status, so a busy port that the preflight could not see
   (bound on another address) reads `port 3000 answers HTTP 404, not as
   Gitea: another server holds it`, not only `gitea not ready`.

### 2. A demo a person can follow

By default `./go demo` sends everything the tools print to
`.crew/demo/run.log` and shows, on the terminal, only:

- one header line: what the demo will do and how long it should take
  here;
- one line per step, `[n/N] <what it does> … about <t>` when it starts,
  and its measured seconds when it ends;
- the lines the demo addresses to a person (§4);
- on a failure: the `NO-GO` line, the last 40 lines of the log, and the
  log's path;
- the demo's last line, `GO: demo …` or `NO-GO: demo — …`, on stdout as
  today.

`LIGHTS_OUT_VERBOSE=1` streams everything, as before; the ci workflow's
`engine-compat` job sets it. The filter is host shell
(`core/progress.sh`, on the host list): it sees the host's stages before
any image exists. It reads two markers on its input, `[step] <id>` and
`[say] <text>`, printed by `./go` and by `make demo`; it calls `date` only
on a marker. The steps:

| id | what it does |
|---|---|
| `preflight` | engine, memory, disk, ports (§1) |
| `toolchain` | the workbench image: built once per Containerfile revision |
| `plane` | Gitea, Envoy, Prometheus, flagd, Zot and the app, ready, then the smoke test |
| `rollback` | a canary with a failing feature at 5%, judged, rolled back |
| `ticket` | ticket 0001 from the request to its canary's rollback (e2e) |
| `checks` | the repository's own checks, run beside the plane (§3): waiting for their verdict |
| `console` | the run's own console, frozen into one file (§4) |

The ETA of each step is this machine's last measured time for it, from
`.crew/demo/steps.tsv`, which the filter rewrites at the end of every run
that reaches `GO: demo`; on a machine with no run yet, the defaults are
the `stranger` workflow's measured times after this change (§6). The
header's total is their sum. A step that overruns its ETA says nothing:
the line ends with its real time.

### 3. The repository's checks beside the plane

`make ci` needs no engine (`./go ci` runs it without a socket, and no
check calls one: read on 2026-11-16, `ci/*.sh`), and none of its checks
reads what the plane's stages write. In `./go demo` it no longer runs
before the plane: `./go` starts it in a second workbench container, with
no socket, right after the image exists, its stdout to
`.crew/demo/ci.out`, its stderr to `.crew/demo/ci.err` and its exit code
last to `.crew/demo/ci.rc`. `make demo` runs the plane's stages and, at
the `checks` step, waits for `ci.rc`, prints `ci.out` on stdout as `make
ci` did, and ends `NO-GO: demo — ci: <the first NO-GO line>` when the
checks failed. `make demo` run on its own, with no `LIGHTS_OUT_CI_DIR`
from `./go`, starts the checks itself at its start, beside the rest. The
demo proves what it proved, the same checks, and the same verdict is
required for `GO: demo`; only the order changes.

`make demo` no longer runs `make up` a second time when `./go` has just
brought the plane up and smoke-tested it on the host (engine-compat,
ADR 0012 §5): `./go` passes `LIGHTS_OUT_PLANE_UP=1`. `e2e` still runs its
own `up`, as it does alone.

### 4. The end: the run's own console, on the ticket that rolled back

After `e2e`, `make demo` writes this run's logbook to
`.crew/demo/logbook.json` (`core/logbook.sh`) and freezes the console over
it into `.crew/demo/console.html` (`core/freeze.py`, ADR 0089), labelled
`your ./go demo, <commit>`, with `--hash ticket/0001`: a page opened with
no `#` goes to ticket 0001's timeline, where the flow strip (ADR 0085)
shows each step, its verdict and why. Then three `[say]` lines:

```
The canary of ticket 0001 rolled back by itself: <the launchpad's NO-GO line, from the release record>
See why: file://<this checkout>/.crew/demo/console.html#ticket/0001
Next: ./go console for the live console, or ./go try <path> on a repository of yours
```

The checkout is mounted at its own path, so the link is valid on the
host. When stdout is a terminal and `LIGHTS_OUT_OPEN` is not `0`, `./go`
opens it (`open` on macOS, `xdg-open` with a display on Linux). A logbook
or a freeze that fails does not fail the demo, whose criterion is the
rollback: the line says `no console snapshot: <why>` instead of a link.

`core/freeze.py --hash <h>` (amends ADR 0089 §4) adds, inside the
existing frozen block, one statement that sets `location.hash` to `h`
when the page was opened with none.

### 5. Your own repository, in one command

`./go try <path>` attaches a clone of the repository's HEAD (as `./go
init <path>` does: the repository itself is only read), then runs, in
workbench, `init` (the contract drafted in the clone, baseline measured)
and `gates` on the last commit judged by that draft, and prints one line:

```
GO: try — <stack>, the eight gates on your last commit GO; the draft is in .attached/.lights-out
NO-GO: try — <the gates' NO-GO reason>; the draft is in .attached/.lights-out
NO-GO: try — init: <init's reason>
```

then, on stderr, the commands that keep the draft (`cp -R`, `git add`,
`git commit` in the user's repository, with its real path). The draft is
copied to `.crew/try/<name>/.lights-out` and the clone is detached, so
`./go demo` works right after; `./go try` refuses to start while another
repository is attached. Like `init`, `try` may start without a contract
(the Makefile's include). Nothing new judges: it is `init` then `gates`,
each unchanged.

### 6. The budget a stranger is held to

The plan's budget stays: 900 s to the rollback, `DEMO_BUDGET_SECONDS`
(plan §2, ADR 0013 §7). The `stranger` workflow adds its own, tighter
one: `./go demo` on its clean runner must end within 600 s, or the job
fails naming the time. Provenance: the slowest green run after this
change, 480 s (run 43841491822), plus 20% for runner variance, rounded
up (a decision; the two green runs after spread 30%, 370 s to 480 s, and
the five before 15%). A first draft said 540 s from the slowest run then
known, 446 s; the second green run was within 12% of it, too tight for
runner variance. The runs before this change, 700–805 s, fail it.

The `stranger` job also plants two busy ports before `./go demo`: a
server on 3000 (as a Node server would be) and one on 8080. The demo
must reach `GO: demo` anyway, and the log must name both moves. The
ci workflow's `engine-compat` job keeps the default ports, so both paths
run on every push.

*Amended 2026-11-25:* the work behind 1.0.0 grew the unit tests in `make
ci` from 96 s to 386 s on a runner (`core-tests`, run 45649324464; the
same check was 96 s in run 44847816834), and `./go demo` waits for `make
ci`: the stranger run took 1,120 s and the engine-compat run 1,020 s, both
over budget. Six end-to-end cases in one module, `ci/tests/test_ux_glue_e2e.py`,
took 927 s of the 1,547 s the suite took on this Mac's VM, one module per
process. `ci/core-tests.sh` now deals every test case of `core/tests` and
`ci/tests` round-robin into eight batches per CPU (eight, a decision: enough
batches that the slow cases land apart) and runs as many batches at once as
there are CPUs. The budgets stay as they are. Rejected: taking the checks out
of the demo (it would no longer prove what it proved, §3), and moving the
slow end-to-end cases to a nightly run (they guard the wrapper and the
entrypoint on every push).

## Tests, from this ADR

`ci/first-run.sh`, a `./go ci` check, runs `core/preflight.sh` and
`core/progress.sh` on a temporary tree with a fake engine on `PATH` and
real listening sockets, and proves each can fail:

- a free set of ports: `GO: preflight`, no `.crew/ports.env`;
- a planted listener on the app port and on Gitea's: both moved to the
  next free port, written to `.crew/ports.env`, each move named on
  stderr with the port; a second run keeps them;
- a listener on a port `.env` sets: NO-GO naming the port and `.env`;
- a listener on 5050: NO-GO naming 5050 and that it cannot move;
- a port held by this checkout's plane (the fake engine reports it):
  fine, not moved; held by a `lights-out` plane of another directory:
  NO-GO naming that directory;
- free disk under the need: NO-GO naming both numbers; `MemTotal` under
  3,800 MiB: a warning line, still GO;
- a stopped podman machine on macOS: `machine start` called once;
- the filter: on a scripted input, only the header, the step lines, the
  `[say]` lines and the demo's own verdict reach the terminal (a stage's
  `GO: demo-rollback` or `NO-GO: demo-gates` does not), every input line
  reaches the log, `steps.tsv` is written on GO and not on NO-GO, and on
  a failure the log's last lines and its path are printed;
- `adapters/gitea/ready.sh` against a listener that answers 404: the
  timeout line names the port and the status.

`core/tests/test_freeze.py` gains the `--hash` case. The Makefile's
`try` and the demo's new order are proven by `./go demo` itself and by
the stranger workflow.

## Alternatives rejected

- **Drop `make ci` from the demo.** Cuts 318 s at once, and the checks
  run in their own workflow on every push. But the demo would prove less
  on the stranger's machine than it did, and the brief says never cut
  what the demo proves.
- **Move every port, 5050 too.** The registry's host port is in every
  image reference the workloads' compose files name, and in the engine's
  insecure-registry entry written once per machine; moving it means
  rewriting contracts. A busy 5050 is rare and named.
- **Run `./go doctor` at the start of the demo.** It diagnoses, and a
  NO-GO from it would still leave the person to edit `.env`. The
  preflight acts where acting is safe, and stops with one line where it
  is not.
- **A progress bar redrawn in place.** Needs terminal control that a
  pipe, a CI log and a screen reader all handle differently; a line per
  step reads the same everywhere.
- **Open the live console at the end (`./go console` in the
  background).** Leaves a server running that the person did not start,
  on a port that may be busy. The frozen file needs nothing behind it,
  and `./go console` stays one command away.
- **ETAs from fixed constants only.** A Mac with a warm store and a cold
  runner differ by minutes; the last run on this machine is the better
  guess, and the constants are only the first one's.

## Consequences

- `./go demo` prints about a dozen lines instead of about 3,000; the
  rest is in `.crew/demo/run.log`, which a bug report now has to attach.
- Two workbench containers run at once during the demo; on a 4 GiB
  machine that is measured below (§ After), not assumed.
- `.crew/ports.env` is a second place a port can come from; `./go
  doctor` and every printed URL read it, and the README says so.
- The demo's stdout carries the same lines in a different order: the
  checks' lines come after the plane's.

## Golden-rule check

`git clone && ./go demo` still reaches the automatic rollback at L0, with
no key and no account; it now also gets there with a busy port, sooner,
and ends on a page that shows why. Passes.

## After

Measured on 2026-11-16, after the change.

**The `stranger` workflow**, a clean runner, cold (the toolchain image
built in the run), with 3000 and 8080 held by planted servers:

| run (job) | `./go demo` | rollback reached at | note |
|---|---|---|---|
| 43837534798 (133200533128) | **370 s**, `GO: demo` | 166 s | both ports moved and named; the checks done before the plane's stages |
| 43841491822 (133215492108) | **480 s**, `GO: demo` | 209 s | the same; the toolchain build 49 s, the plane 93 s, ticket 233 s, 37 s waiting for the checks |
| 43844732461 | **394 s**, `GO: demo` | | the same, on bf08e7c, under the 600 s budget |
| 43835418922 | NO-GO at 446 s | 211 s | `ci-vendor-free` caught the first draft's port names |
| 43833357693 | NO-GO at 390 s | 169 s | `ci-shell` caught the first draft's shellcheck findings |

Before, the same workflow: 700–805 s, rollback at 491–577 s. The steps of
the green run, from its `steps.tsv`: preflight 1 s, toolchain 30 s,
plane 71 s, rollback 63 s, ticket 204 s, waiting for the checks 0 s,
console under 1 s. Those are the estimates' defaults (§2). The terminal
showed 17 lines; the log kept 2,527. The second green run's terminal is
the one the README quotes.

**This Mac**, from a fresh clone each time, no `.env`, 8080 still held
by the owner's container (moved to 8081 by the preflight). The VM was
shared with other sessions running `make ci` and tests (load average
8–14 on 4 CPUs, recorded at the start and end of each run), so these
times are an upper bound, not a measure of the change alone:

| run | `./go demo` | note |
|---|---|---|
| before, 1st | NO-GO after 20 s | port 8080; `.env` by hand; `./go down` by hand |
| before, 2nd | 1,106 s, NO-GO over budget | |
| after2 (678d085) | NO-GO at 636 s | plane's stages done; `ci-vendor-free` |
| after3 (cc90936) | 1,335 s, NO-GO over budget | every stage GO; rollback at 197 s, ticket 0001 done at 819 s, then 514 s waiting for the checks; load 6.4 at the start, 10 during |
| after4 (df603a1) | **759 s, `GO: demo`** | rollback at 176 s, ticket 0001 done at 511 s, then 247 s waiting for the checks; load 2.6 at the start, 6.7 at the end |

`./go ci` from a full clone at cc90936: GO, 693 s, on the same loaded VM.

What after3 shows: the checks beside the plane help only where there is
CPU to spare. On the clean runner they finished before the plane's
stages; on this 4-CPU VM with two other sessions' `make ci` beside it,
the same checks took 1,330 s, twice their 693 s alone, and became the
critical path. The plan's 900 s holds on a machine the demo has to
itself, which is what the README asks for ("A 4 GiB podman machine runs
one demo at a time"), and the preflight does not measure the load.
