# ADR 0031 — Ready for the experiment: a workload tells the dev-worker its layout in `<workload>/worker.md`, delivered inside the worker's input document; the tower document becomes the one record `./go report` renders; the emulator's documents are mirrored to `.crew/state/` and reloaded on `up`

- Status: superseded in part (reconciled at v1.0.0: §1-§2 adopted by ADR 0037, §4 adopted by ADR 0034, §6 emulator shipped alongside ADR 0060 default sqlite; §3/§5 `report` superseded by ADR 0047 logbook and ADR 0083 spend; originally: amends ADR 0030 §3 [the port gains `worker.md`], §4 [`*/worker.md` is platform-risk], §5 [`app-free` scans `roles/`; `workloads` checks `worker.md`] and its Consequences [the prompt no longer "stays"]; ADR 0017 §3 [`run.json` gains `tool_calls`] and §4 [crew hands the harness an input document, not the bare ticket]; ADR 0021 §3, §9 [`input_matches_recorded` extends to the dev-worker]; ADR 0019 §1 [a failed step leaves one history entry; state and queue untouched] and §2 [the document's fields]; ADR 0015 §2 [`gates` keeps every gate's line; `mutation.json` carries its threshold]; ADR 0012 §6 [firestore-emulator's `ready.sh` reloads]; consistent with ADR 0023 §1 [a view, not a source of truth])
- Date: 2026-09-23
- Milestone: M13

## Context

Plan §7 M13 asks for three things before the §14 experiment. Reading the
code on 2026-09-23 turned up four facts that settle more than the plan's
own options do:

- **The worker cannot read `workload.env`.** `adapters/gemini-cli/hooks/
  guard.sh` denies every tool call whose strings contain `.env`. The
  file is also make-included and `.`-sourced, unquoted: a value like
  `uv sync --locked && uv run pytest` would run in every gate that
  sources it. Nothing in the platform runs the worker's install or test
  command. Only the model reads it, as prose.
- **The report's inputs do not last.** `.gates-out/` is wiped by every
  `gates` run, and it keeps no line per gate. `.crew/<ticket>/` is wiped
  by every assign. `.crew/release.json` holds only the latest release.
  `./go audit` asks the code host, and L0 Gitea has no volume. The tower
  document is the only per-ticket record, and it lives in an in-memory
  emulator.
- **The emulator persists only through itself.** `--export-on-exit` is
  reported to lose, or delete, its export when the process is signalled
  (firebase-tools #3092, #3475), and compose `down` is a signal. A REST
  `:export` plus `--import-data` writes an opaque binary format, and
  nobody has verified it on the pinned `584.0.0` image in native mode.
  A named volume on its own holds nothing.
- `roles/dev-worker/prompt.md` names the app at lines 15–16 and 27–37.
  Lines 73–75 ("this ticket touches four files") describe ticket 0001,
  not the role. `roles/product/prompt.md:28` says "meetup".

## Decision

1. **`<workload>/worker.md`, a new file of the port, markdown.** It must
   contain `## Scope` (the paths the worker reads and changes) and `## Tests`
   (the directory to run from, the exact install-and-test command, what
   the workspace already provides, where tests go). Other sections are
   optional (`app/` keeps "no migration, no auth change"). It is prose
   for a model, not keys for a script, so it is a sibling of
   `workload.env`, not an eighth key. Both workloads ship one. `app/`'s
   text is the current prompt's lines 15–16 and 27–37, moved.
   `ci/workloads.sh` adds check (d): the file exists and has both
   headings. `RISK_HIGH_PLATFORM` gains `*/worker.md`, because it
   instructs an agent, the same class as `roles/*`.
2. **The brief is delivered inside the input document, following
   review's precedent** (ADR 0021 §3). `core/crew.sh` writes
   `.crew/<t>/input.md` as the ticket, then `---`, then
   `$WORKLOAD/worker.md`, and passes it as `TICKET_FILE`. It NO-GOes,
   naming the file, if the brief is missing. No harness adapter changes.
   `spec.sha256` is still the ticket's own hash. The run's `input_sha256`
   covers ticket plus brief. `assign.sh` records it and records
   `task.input_matches_recorded`, the same way `review.sh` does. `e2e`
   asserts it is `true`, as `demo-review` does. The rewritten prompt says
   that the input is a ticket followed by the workload's brief, and that
   the worker uses the brief's commands exactly. It keeps its platform
   rules, which name no app, and drops the ticket-0001 sentence.
   `ci/app-free.sh` adds `roles` to its scan.
3. **The tower document is the record.** Each step writes what it already
   holds, at the moment it holds it:
   - assign writes `task.{tokens, tool_calls, turns, duration_s,
     input_sha256, input_matches_recorded}` from `run.json`, reading
     `.recorded.*` under replay. It also writes `spec.perimeter`, the
     declared globs, parsed by `core/perimeter.sh --list` so the parser
     exists once.
   - ci writes `ci.gates`, one stdout line per gate run, in order. It
     reads them from `$GATES_OUT/lines`, which `make gates` now appends to,
     and gates after a NO-GO are absent. It also writes `ci.mutation`
     from `mutation.json`, and `gates/mutation.sh` now adds `baseline` and
     `margin` to that file, so the threshold recorded is the one that
     judged. `verdict.env` is unchanged, so the `expect` files stay valid.
   - review writes the same cost fields as assign.
   - `adapters/gemini-cli/run.sh` adds `tool_calls` to `run.json`.
4. **A failed step leaves a trace.** `core/tower.sh` tees the failing
   step's stderr, following `release.sh`'s pattern. It appends
   `{step, state: <unchanged>, at, error: <last non-empty stderr line>}`
   to `history`, writes the document, publishes nothing and exits 1. The
   precondition reads only `.state`, so idempotency is unchanged. Two
   report fields come from here: attempts (the number of `assign` history
   entries) and the causes of failures that end before a PR (the ticket's
   perimeter, the harness budget, no change).
5. **`./go report TICKETS="<ids>"`** (`TICKETS ?= $(TICKET)`) runs
   `core/report.sh`. It reads one thing: each ticket's document, through
   `adapters/$STATE_STORE/get.sh`. It does not read the code host,
   `.crew/`, `.gates-out/` or the release record. It prints audit's chain
   fields from the document, but not audit's cross-check against the code
   host, which stays `./go audit`'s. The output is markdown written to
   `REPORT_FILE ?= .crew/report.md`, and stdout gets one line,
   `GO: report — <file>, <n> tickets`. Under a TTY `./go` merges stderr
   into stdout, so stdout would be a poor carrier for the report itself.
   The report has no clock of its own and lists tickets in the order
   given, so equal documents give a byte-identical report.
   - Per ticket: outcome, PR, perimeter, gate lines and mutation score
     against its baseline, review verdict and reason, attempts, tokens,
     tool calls, turns and duration (marked "recorded" under replay),
     `input_matches_recorded`, and the canary verdict with its numbers.
   - Header: how many of the n tickets reached `merged`. The §14
     criterion reads this number. "No human touched the code" holds by
     construction, because a branch moved after the PR opened fails
     `ci`, and audit checks `merge.head_sha == pr.head_sha`. The header
     also carries total and per-ticket tokens and seconds, and failures
     grouped by cause: failed gate, review changes-requested, rollback,
     or step error.
   - **No HTML, no currency, no threshold.** Markdown renders on the
     code host and in M11's console. The only price with a provenance is
     an introductory one, and a replay spent nothing. The 2-in-3 threshold
     is one experiment's decision (§14), not a platform default.
6. **Durability: `firestore-emulator` mirrors every write.** `put.sh`
   writes `.crew/state/<collection>/<id>.json` after a successful PATCH,
   and exits 1 if the file write fails. `delete.sh` removes the file.
   Once the emulator answers, `ready.sh` PATCHes every mirrored file back.
   Re-PATCHing on a second `up` is idempotent. Only this adapter knows
   about it: the tower and the report see the port, and real Firestore
   needs nothing. At L0 the files are the durable copy and the emulator
   the served one.

## Alternatives rejected

- **An eighth key, `WORKLOAD_TEST_CMD` and friends.** The guard would hide
  the key from the worker, and `.` would execute its value.
- **Pass `WORKLOAD` to the harness, or use a workspace `GEMINI.md`.** The
  first changes the AgentHarness contract for every adapter. The second is
  a vendor concept inside the workload.
- **The report reads `.crew/` and `.gates-out/`.** The next assign or
  gates run overwrites them, they are not there at L2, and they would
  give the report two sources.
- **The report calls `./go audit`.** After `down`, Gitea no longer has the
  PR, so audit NO-GOes and the report stops being identical.
- **A StateStore `list` verb, so the report can enumerate the batch.** It
  would be a port change for a batch the operator already names.
- **A named volume on the emulator.** It is in-memory, so the volume
  would be empty. Adding `--export-on-exit` loses data on a signal.
  Adding a REST export before `down` plus `--import-data` needs a new
  pre-`down` hook, is unverified, is opaque, and misses every stop that
  is not `./go down`.

## Consequences

- **The one that will hurt: only the documents survive.** Gitea's PRs
  and Pub/Sub's queue are lost on `down`. After `down` and `up`, the
  report regenerates, but audit NO-GOes on the code host and a ticket in
  flight is stranded without its next event. A multi-day L0 batch must
  `down` only between tickets, until an ADR makes Gitea durable. That
  also changes a second `./go demo`'s starting state, which is why it is
  not done here.
- Each `worker.md` edit needs a keyed L1 re-record, or `e2e` NO-GOes.
  M13 re-records `0001/dev-worker`, `0005/dev-worker` and the `0005`
  review fixtures, whose input embeds the new diff. It also re-records
  the product ADK fixtures, because the instruction hash changes. A
  re-record that pushes `./go demo` past plan §2's 900 s is itself
  re-recorded, not accepted.
- `ci/tower.sh` case (e) becomes "state untouched, one history entry".
  A new `ci/report.sh` renders stub documents to a committed golden file,
  twice, and compares the results byte for byte. `workloads/minimal/worker.md`
  is checked for its shape but runs no ticket. A go-for-launch rewrites the
  release record, not the document, so a promoted canary reads `held`.
- Plan §4's `Workload` row gains `worker.md`. README, `docs/ARCHITECTURE.md`
  and `./go`'s header list `./go report`.

## Golden-rule check

`./go demo` runs the same targets. `e2e` adds one assertion that is true
on the re-recorded fixtures. The mirror adds a few file writes, and on a
fresh clone `ready.sh` reloads nothing. `./go report` needs no key and no
socket, and it is not part of the demo. Only the owner's one-off
re-record needs a key. Passes.
