# ADR 0060 — The lean plane: a `sqlite` StateStore and a `spool` EventBus on files under `.crew/`, the emulators opt-in, two conformance suites as the proof; the Gitea runner leaves and every workload's merge is built by the release step

- Status: accepted (amends ADR 0039 [superseded: no runner], ADR 0025 §4
  and §5 [no image job; the release step builds the merge for every
  workload], ADR 0043 §4 [the bundled workload takes the attached path],
  ADR 0019 §2 §3 [new default adapters, same verbs], ADR 0002 [the
  one-YAML promise is kept on GitHub]; plan §3 and §4 rows)
- Date: 2026-11-08
- Milestone: M34
- Also touches: ADR 0012 §3 and ADR 0015 §7 (the runner service and its
  socket grant are retired), ADR 0031 §6 (the emulator mirror, proposed
  and never built, is replaced by a file that is durable by itself).

## Context

The L0 plane runs eight default-profile services in `compose.yaml`:
envoy, prometheus, flagd, zot, gitea, runner, pubsub-emulator,
firestore-emulator. Three exist for the shape of an L2/L3 that is
designed and not run (ADR 0022). The emulators cost one 447 MB pull
(amd64; 356 MB arm64, compressed, measured 2026-09-14) and two readiness
probes, for a store whose verbs are `get`, `put`, `delete` and a bus
whose one consumer is `adapters/inproc/run.sh` in the same process. The
runner costs an image build, a registration wait, and `release.sh`'s
`image` wait (`IMAGE_TIMEOUT=300`), the step ADR 0039 and ADR 0025 §5's
2026-11-04 amendment record as the demo's flake. The attached path
already builds a merge in a throwaway worktree (ADR 0043 §4, proven by
`demo-seam`). On c387961 `./go demo` took 834 s (engine-compat) and
828 s (stranger) against plan §2's 900 s ceiling.

Plan §16 M34 named the bus `inproc`. That is the Orchestrator adapter's
directory, and an adapter's name is its directory (ADR 0024), so the
bus is named `spool` here. The plan's 600 s budget was a proposal, not
a measurement, and is not adopted (§7).

## Decision

1. **StateStore `sqlite`.** `adapters/sqlite/{get,put,delete,ready}.sh`,
   each one line, `exec python3 -I "$here/store.py" <verb> "$@"`: verbs
   stay `.sh` (ADR 0052 §1), an adapter's logic stays in its folder (ADR
   0024; precedent `adapters/antigravity-cli/served_model.py`), standard
   library `sqlite3` only. One file, `${STATE_FILE:-.crew/state.sqlite}`
   (an adapter setting like `FIRESTORE_URL`, read by no one else), one
   table keyed `(collection, id)` holding the document as JSON text.
   ADR 0019 §2's contract, unchanged: `get` prints the plain JSON
   document, exits 1 silently when absent, 2 with one stderr line on any
   other failure; `put` reads a JSON object on stdin and replaces the
   whole document, and refuses anything else (not JSON, an array, NaN)
   with exit 1 and one stderr line, the stored document unchanged;
   `delete` of an absent document is success; `ready` creates the file
   and its table. Durable: `.crew/` survives `./go down` and `./go up`,
   with no volume, export or mirror. Exposure: the emulator published
   8086 unauthenticated on every host interface; the file is reachable
   only through the checkout. No compose service mounts `.crew/` itself
   (harnesses get their run directory, ADR 0041 §4; `egress` and
   `conformance-model` one subdirectory each). Concurrency: the tower
   applies one step at a time (ADR 0019 §2); SQLite's lock with a 30 s
   busy timeout (a decision: a wait that long is a stuck process) covers
   the console or board reading while the tower writes.

2. **EventBus `spool`** (the classic name for an on-disk queue).
   `adapters/spool/{publish,pull,ack,ready}.sh` + `spool.py`, one file
   `${SPOOL_FILE:-.crew/spool.sqlite}`. `publish` appends (a message with
   a line break is refused: `pull` prints one line); `pull` leases the
   oldest message not leased or whose lease expired, in one transaction,
   and prints `<ackId> <data>` (ackId without whitespace, data the bytes
   published), or nothing; `ack` deletes the leased message, and an ackId
   that leases nothing (acked, superseded by a redelivery, never issued)
   is success, as a Pub/Sub ack is best-effort. Lease
   `SPOOL_ACK_DEADLINE`, default 600 s: the pubsub-emulator
   subscription's `ackDeadlineSeconds` (`adapters/pubsub-emulator/
   lib.sh`), the API's maximum (external constraint), for the same
   reason: a whole step runs between pull and ack. Order is FIFO by
   publish, stronger than the port, which promises none. Messages survive
   `./go down`: a stale event from a killed run is pulled later and acked
   and dropped by the tower's idempotency rule (ADR 0019 §2). That is the
   at-least-once delivery the port already had.

3. **The emulators stay, opt-in.** Their services move to the compose
   profile `emulators`; the Makefile sets `COMPOSE_PROFILES=emulators`
   only when `EVENT_BUS` or `STATE_STORE` names an `*-emulator` adapter,
   and `down` stops that profile too. Plan §4: EventBus `spool`,
   `pubsub-emulator`, `pubsub`; StateStore `sqlite`, `firestore-emulator`,
   `firestore`. L0 and L1 default to `spool` and `sqlite`; L2/L3 keep
   `pubsub`/`firestore` (not written, M10). Plan §3: "Eventi e stato" at
   L0/L1 becomes files under `.crew/`; "CI" at L0/L1 becomes the tower's
   `ci` step and `./go ci`.

4. **Conformance is the proof** (the ADR 0026/0041/0043 §5 shape).
   `gates/statestore-conformance.sh` and `gates/eventbus-conformance.sh`,
   written from this ADR, drive only `adapters/$STATE_STORE/*.sh` or
   `adapters/$EVENT_BUS/*.sh`, refuse at once without `CONFORMANCE=1` (so
   `ci/contract.sh` can cold-run them), print one GO/NO-GO line. Targets
   `./go statestore-conformance`, `./go eventbus-conformance`.
   - StateStore, writing only collection `conformance-<run id>` and
     deleting what it wrote: (1) `get` of an absent document exits 1,
     stdout empty; (2) a document holding every JSON type (strings with
     a quote, a backslash, a newline and non-ASCII; an integer; a
     non-integer; true, false, null; nested arrays and objects; `[]`;
     `{}`) round-trips equal as a JSON value; (3) `put` replaces, no
     merge; (4) one id in two collections is two documents; (5) `put` of
     non-JSON, and of a JSON array, exits non-zero and leaves the stored
     document unchanged; (6) a 65,536-letter ASCII string round-trips (a
     decision: above any tower document seen, below the 128 KiB Linux
     puts on one argument, `MAX_ARG_STRLEN`, which the emulator adapter's
     `curl -d` meets: external constraint); (7) `delete` then `get` exits
     1; (8) `delete` of an absent document exits 0.
   - EventBus: (0) the first pull prints nothing, else NO-GO without an
     ack (the suite never consumes a real event; a foreign message it
     pulled returns after the adapter's deadline); (1) publish then pull
     prints `<ackId> <data>`, data byte-identical (a JSON line with
     spaces, quotes, a backslash, non-ASCII), ackId non-empty without
     whitespace; (2) while leased, a second pull prints nothing; (3)
     `ack` exits 0 and the next pull prints nothing; (4) three messages
     are pulled and acked as exactly that set, then the bus is empty;
     (5) only when `CONFORMANCE_ACK_DEADLINE` gives the adapter's
     deadline: an unacked message comes back after it, an acked one does
     not; unset, the GO line says case 5 did not run.
   - `ci/store-bus.sh` in `./go ci`: both suites on `sqlite` and `spool`
     against temporary `STATE_FILE`/`SPOOL_FILE`, case 5 at
     `SPOOL_ACK_DEADLINE=2` (a decision: a redelivery `./go ci` can wait
     for), and each suite proved able to fail on a broken copy of its
     adapter in a temporary tree. After its own steps, the `launch` job
     runs `./go up EVENT_BUS=pubsub-emulator STATE_STORE=firestore-emulator`,
     which starts and probes the two emulators on the plane already up,
     and runs both suites there: the suites are shown port-generic, the
     opt-in adapters stay honest. Found writing the suite: the
     firestore-emulator's `put.sh` refused a JSON array only by accident
     (jq fails on a number as an object key); its encoder now refuses
     anything but an object by name.
     `ci/vendor-free.sh` does not change.

5. **The Gitea Actions runner leaves.** `core/steps/release.sh` takes
   ADR 0043 §4's path for every workload, on the commit it already
   releases (`mainline.green`, else `merge.sha`, ADR 0056 §3): fetch
   `HEAD` of `clone-url.sh`, check that commit is an ancestor of
   `FETCH_HEAD`, a detached worktree at it under `mktemp -d`, publish,
   remove the worktree, then the unchanged `make release`, whose
   launchpad verifies the signature (ADR 0025 §6). Attached: `make
   publish TAG=sha-<sha> WORKLOAD=<worktree>`, as today. Bundled: `make
   publish` runs inside the worktree root (`make -C`), `WORKLOAD` the
   workload's path in the repository, `COSIGN_KEY` the checkout's
   `.crew/cosign.key`. Before it, `Makefile`, `compose.yaml`, `profiles/`
   and `adapters/` in the worktree are restored from the checkout's
   `HEAD`, as `core/steps/ci.sh` restores the base's judges: the merge
   supplies the workload, this checkout builds, pushes and signs it. That
   is narrower than the runner, whose `image` job ran the merge's own
   `./go publish` with the engine socket. ADR 0033 is untouched:
   publishing is no verdict; the PR was judged by the base, main after
   the merge (ADR 0056), and the canary by this checkout's launchpad. The `image` wait
   (`await.sh`, `IMAGE_TIMEOUT=300`) leaves the step. Gitea: Actions off
   instance-wide (`GITEA__actions__ENABLED=false`), the registration
   token removed, `ready.sh` polls the API only, `bootstrap.sh` stops
   toggling `has_actions`, `await.sh` drops the Actions-job lookup (ADR
   0025 §5, 2026-11-04) whose job no longer exists. `await` stays a
   CodeHost verb (its conformance case, the `github` adapter) with no
   caller in `core/`; removing it is a separate decision. No compose
   service holds the engine socket: ADR 0015 §7's grant is retired, `./go`
   stops passing `HOST_DOCKER_SOCK`, the L0 trusted-network line loses
   the runner and its public token. `adapters/gitea/runner/` and
   `ci.yml`'s `image` job are deleted.

6. **"The same YAML runs locally and on GitHub"** (plan §3, ADR 0002) is
   kept by `.github/workflows/` on GitHub Actions and by `./go ci` being
   exactly what the `ci` job runs, on a laptop and on GitHub. Nothing
   runs the YAML locally any more, so `ci/gitea-compat.sh` retires with
   the runner. The `github.server_url` guards stay, inert; removing them
   is a later cleanup.

7. **Budget: it stays 900 s, because the measure allows nothing lower.**
   `./go demo` on GitHub Actions, the clock `DEMO_BUDGET_SECONDS` judges
   (from before engine-compat, the workbench build included):

   | commit | engine-compat (docker) | stranger (podman) |
   |---|---|---|
   | c387961, M33, before | 834 s (run 42435760042) | 828 s (42435759957) |
   | e2599c8, this branch | 873 s (42506965618) | 852 s (42506965640) |
   | 8fd756c, this branch | 757 s (42508676734) | 801 s (42508676759) |
   | a0fc792, this branch | 825 s (42510074390) | 719 s (42510074189) |

   Runs of this branch differ by 116 s on the same job and by 154 s
   between jobs, more than anything this ADR changes. Where the stranger's time goes, e2599c8
   against c387961: the workbench build 127 s against 112; the demo's `ci`
   stage 363 s against 336 (`store-bus` 11 s, run again inside
   `contract`'s cold re-run of every check, 176 s against 162; 8fd756c
   runs the spool suite beside the stubs, 13.5 s to 9.4 s locally); `up`
   and `smoke` 24 s both; `demo-rollback` 76 s both; `e2e` 245 s against
   260 (its `up` 8 s against 13; the release step 77 s against 78, of
   which building, pushing and signing the merge is 2 s on cached layers
   and the rest the launchpad's 60 s stage window). On a green run the
   `image` wait cost about a second: what this ADR removes on Actions is
   a failure mode, the 300 s timeouts of ADR 0025 §5's amendment, not
   seconds. The emulator pull costs a few seconds on GitHub's network and
   more on a slow one, not measured here.

   The rule this section was to apply, the slower run plus a headroom,
   gives more than plan §2's 900 s ceiling, which it may not exceed; so
   `DEMO_BUDGET_SECONDS` stays 900 (plan §2, an owner's decision), with 27 s
   of margin on the slowest run measured. The roadmap's 600 s warm is not
   supported: Actions runs are cold, and the measured runs less the
   workbench build give about 600 to 760 s (derived, not measured). Where the
   time is: the `ci` stage, about 43 % of the run, every check run twice;
   the workbench build, about 15 %; the PR gates in `e2e`, about 15 %.

   *Amended 2026-11-09 (ADR 0062):* the second run is gone. Under
   `./go ci` each check leaves its stdout and exit code in a record, and
   `contract` judges the records instead of running the checks again. The
   measures before and after, on Actions and locally, are in ADR 0062 §6.
   The budget stays 900 s (plan §2, an owner's decision).

8. **Done when:** `./go demo` GO from a clean clone, no key, under §7's
   budget; default-profile plane services in `compose.yaml` go from 8 to
   5 (envoy, prometheus, flagd, zot, gitea); in engine-compat, after
   `./go demo` and `./go logbook`, `./go down && ./go up && ./go logbook`
   writes a byte-identical file (`core/logbook.py`: "No clock of its
   own: equal sources give equal bytes") but for `launch.plane`, the one
   field read from the live plane (the Delivery port's `status.sh`):
   `down` removes the rolled-back canary revision the demo leaves running
   at weight 0, so `canary` goes from its version to null (found on the
   first run of this branch, 42506965618, `cmp` at that field); `./go ci` GO; `demo-mainline`,
   `demo-rework` (added to the `mainline` job, which no workflow ran
   before), `demo-seam`, `demo-console` GO.

## Alternatives rejected

- **One `sqlite` directory for both ports.** `EVENT_BUS=pubsub` with
  `STATE_STORE=sqlite` needs two names; one folder per adapter per port
  is ADR 0024's shape.
- **A directory-of-files queue, leases by `rename`.** Atomic lease and
  expiry are one transaction in SQLite and a protocol in rename.
- **The `sqlite3` CLI in shell.** Quoting JSON through a shell is the
  bug class ADR 0052 moves away from; Python's `sqlite3` is already in
  the workbench (`core/map.py`, `core/logbook.py`).
- **Delete the emulator services and adapters.** The §4 table keeps
  them; an adapter that cannot run is a hidden mock (CLAUDE.md).
- **A volume or `--export-on-exit` on the emulator.** ADR 0031's own
  rejection; the pull and the probe stay.
- **No bus at L0, `run.sh` calling the tower directly.** `core/` and
  `demo-mainline` pull and ack through the port; the port would lose its
  local adapter.
- **Keep the runner for the bundled app only.** The attached path
  exists and `demo-seam` proves it. **Behind an opt-in profile until
  asked** (the roadmap's fallback): git history holds it.
- **Bundled publish from the checkout, `WORKLOAD=<worktree>/app`.** The
  Makefile calls a workload attached when its git toplevel is not the
  checkout's (Makefile, `WORKLOAD_REPO`), so it would look for
  `.lights-out/`.
- **600 s as the budget.** A proposal, not a measurement (§7).

## Consequences

- **The one that hurts: the tower's state outlives the plane.** A
  stranded ticket, a red `mainline/main` (refusing `assign`, ADR 0056
  §5) and an open ticket's perimeter hold (ADR 0053) survive `./go down`,
  where the emulator forgot them. The replays already delete their own
  documents first; anything else is `./go resume` or removing
  `.crew/state.sqlite`, and `docs/reference.md`'s plane section says so.
- **The release build has no ceiling** (ADR 0043's consequence, now on
  the demo path): a hung build hangs `e2e` until the CI job's timeout.
  The build is not saved, it moves from the runner into the step.
- L0 and L2 no longer share EventBus and StateStore code (plan §4's
  "same code, another endpoint"); only the suites tie them, and the M10
  adapters must pass them.
- `core/steps/release.sh` is on ADR 0052's debt list at 56 code lines,
  never raised (`ci/shell-boundary.sh`): the one path must fit.
- `up`'s port message names 8085 and 8086 only with the emulators.
  `core/doctor.sh`, which M31 rewrites in parallel, still lists them, as
  do the README lines that describe it; M34 leaves both to that session.
- Nothing proves the workflow YAML on Gitea any more.
- SQLite's locks are as good as the file share's: `.crew/map.sqlite`
  is the precedent; the console reading during a demo is the case to watch.

## Golden-rule check

Removes the 447 MB emulator pull, the runner's image build and
registration wait, and the 300 s `image` wait from `./go demo`. Adds no
image, no account, no key: `python3` and its `sqlite3` are in the
workbench the demo already builds. `./go demo` must stay GO from a
clean clone, under the budget §7 states from its measurement.
