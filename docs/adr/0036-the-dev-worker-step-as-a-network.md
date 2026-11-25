# ADR 0036 — The dev-worker step as a network: a `network` AgentHarness adapter fans one ticket out to members with disjoint perimeters, and the harness governs what they produce together as one PR

- Status: accepted (reconciled at 0.9.0: `adapters/network/run.sh` shipped, L0 ticket 0012 fixtures remain hand-written, opt-in autonomous `## Network` spec-split + parallel execution added by ADR 0120, and per-member spend accounting added by ADR 0135; adds `network` to plan §4's `AgentHarness` row, which lists `replay`, `gemini-cli`, `antigravity-sdk` and `adk` — see decision 1; amends ADR 0017 §4 [crew picks its harness from `CREW_HARNESS` first]; consistent with ADR 0021 §5 [the perimeter], ADR 0029 [six roles, the harness is the product] and ADR 0035 [no tower state is added])
- Date: 2026-11-03
- Milestone: none yet; a branch for the owner's review (`docs/plans/agentic-network.md`)

## Context

The owner asked for "an agentic network for software development that
this harness governs". Three facts of the repo bound what that can mean:

- Six roles is a deliberate scope limit (plan §12, `ci/roles.sh`); a
  coordinating seventh role is out.
- Plan §12 forbids a tree of agents as the control plane of a flow that
  lasts days, and ADK in the path of whoever writes code. The tower stays
  the control plane.
- Plan §4: an adapter may not bring a new concept into the core. The
  `AgentHarness` port's contract is ADR 0017's: a run on a workspace that
  leaves a commit over `BASE`, `OUT/patch`, `OUT/pr.json` with a title,
  and `OUT/run.json`; `core/harness-check.sh` and `core/perimeter.sh`
  judge the result whatever produced it.

So a network that sits behind the port, inside the one step that writes
code, is governed by everything downstream without the core learning a
new word.

## Decision

1. **A composite adapter.** `adapters/network/run.sh` is an
   `AgentHarness` adapter whose members are runs of the profile's own
   harness (`AGENT_HARNESS`: `replay` at L0, `gemini-cli` at L1). It adds
   no vendor. It is outside plan §4's table, which is why this ADR exists;
   accepting it adds `network` to that row.
2. **The ticket declares the members.** A `## Network` section holds one
   `### <id>` per member: prose lines are the member's brief, `- ` items
   are its perimeter globs, in the `## Perimeter` syntax. The ticket's own
   `## Perimeter` still declares the whole change. Member ids are
   `[a-z0-9-]+`.
3. **Each member works alone.** A member gets its own clone of the
   workspace at `BASE` (prepared by the workload's `crew-prepare.sh` when
   it has one, as crew does) and an input document: the ticket without
   its `## Perimeter` and `## Network` sections, then "your part" (its id,
   the other ids, its brief) and its own `## Perimeter`. Members run in
   parallel and share nothing while they run; they cooperate through the
   ticket. Its `TICKET` is `<ticket>/<id>`, so `replay` reads
   `fixtures/<ticket>/<id>/<role>/`.
4. **Each member is judged on its own, then together.** Before any member
   runs, the network refuses: more than four members, two perimeters of
   which one glob matches the other's text, and `AGENT_HARNESS=network`.
   After the members run it refuses: a member that failed; a member that
   fails `core/harness-check.sh` (exit 0 is not success, ADR 0017 §1); a
   member whose diff leaves its own perimeter, by `core/perimeter.sh` on
   its own document — the per-member rule catches a test-writer who edits
   the code, which the ticket's perimeter alone would let through; a path
   changed by two members. A refusal integrates nothing. Otherwise the
   members' commits are applied to the ticket's branch with `git am`, in
   the declared order, and the network writes `OUT/patch`
   (`format-patch BASE..HEAD`), `OUT/pr.json` (the ticket's title; the
   body lists each member, its harness and model or its fixture source,
   and its own PR text), `OUT/final.md` and `OUT/run.json`
   (`harness: "network"`, `model`: the members' model when they agree,
   else `null`, and `members`: id, perimeter, the member's own
   `run.json`).
5. **Crew picks the network on request.** `core/crew.sh` reads
   `CREW_HARNESS` before the role's `HARNESS` and the profile's
   `AGENT_HARNESS`. Only crew reads it, so `./go e2e CREW_HARNESS=network`
   reaches the assign step and nothing else: the review step keeps its
   own harness. A ticket without `## Network` is handed to the member
   harness unchanged (a network of one is a single worker), so enabling
   the network never breaks an ordinary ticket.
6. **The first topology is the spec split.** Ticket 0012 declares `impl`
   (application code and its doc line) and `tests` (a new test file,
   written from the ticket alone). The repo's rule that tests come from
   the spec, never from the implementation, holds by construction, and
   the mutation gate on the composite proves the tests kill mutants of
   code their author never saw.
7. **At L0 ticket 0012's members are hand-written, and say so.** No L1
   recording of a network exists. The member fixtures under
   `fixtures/0012/` are patches written by hand, like the gate fixtures
   under `<workload>/fixtures/gates/` (ADR 0015 §6); their `run.json`
   says `hand_written: true`, `model: null`, so the tower records
   `model: null`, the audit prints `model none`, and the PR body names the
   fixture source. A recorded network replaces them when the owner
   decides to spend the quota (plan step 5).

**The number.** At most four members. Provenance: the Gemini API free
tier's ~250 requests a day (external constraint, plan §5) divided by the
dev-worker's `MAX_TURNS=60` (a decision, `roles/dev-worker/role.env`) is
4.2 full member runs a day; four members is the largest network one day
of free-tier quota can run once. Each member keeps the role's unchanged
budgets; the cap is what bounds a network's total.

## Alternatives rejected

- **A seventh, coordinating role.** Against the six-role limit, and the
  coordination it would do (splitting a ticket) is the architect's job
  description; plan step 6 gives the split to the architect later.
- **Members that talk to each other while they run** (shared scratchpad,
  agent-to-agent messages). A conversation between models is neither
  replayable at L0 nor one auditable artefact, and it breaks the isolation
  that makes the spec split mean anything.
- **The ADK roles in an agent-to-agent mesh.** Plan §12: ADK stays out of
  the path that writes code, and an agent tree is not the control plane.
- **A fleet: many tickets in parallel through the tower.** A different
  problem (the launchpad is one canary at a time, so releases would need a
  lock), and no new agent cooperation: each ticket is still one worker.
  Worth its own ADR when the §14 experiment's batch needs it.
- **The network as tower states** (`split`, `integrate`). The core would
  learn the concept the adapter rule forbids, the board would follow, and
  a failed member would become a tower step to resume instead of one
  failed assign.
- **The per-member checks in `core/crew.sh`.** Same reason; crew keeps
  checking the composite, the adapter checks its members with the same
  core scripts crew uses.
- **Selecting the network with `HARNESS` or a profile's `AGENT_HARNESS`.**
  Both reach the review step too: the reviewer would be fanned out over
  its own input document.
- **Recording the members tonight at L1.** It spends the owner's quota
  without the owner, and the dev-worker prompt still names the first
  workload's layout (ADR 0031, proposed).
- **Splitting an existing recorded run (ticket 0001 or 0005) into
  members.** It would present one agent's work as two agents' — a hidden
  mock. Tickets 0001 and 0005 also touch the same files.
- **The gates packaged for external agents** (plan §15, phase 3). A
  different launch, with its own ADR.

## Consequences

- `ci/network.sh` proves decisions 3–5 cold, on throwaway repositories
  with a stub member written inside the test; `./go demo-network` proves
  the governed path at L0 on ticket 0012 through the tower, from ticket to
  rollback and audit. Neither is in `./go demo`'s fixed list (ADR 0003).
- `adapters/network/` calls `core/harness-check.sh` and `core/perimeter.sh`
  (`adapters/inproc/` already calls `core/tower.sh`).
- At L1, parallel `gemini-cli` members are unverified: two `docker-compose
  run --build` of the same service at once, and the free tier's
  per-minute limits. The first recording measures both.
- `record` cannot save a network's member fixtures yet (plan step 5).
- `RISK_HIGH_PLATFORM` already covers `adapters/*` and `fixtures/*`.

## Golden-rule check

`./go demo` does not run the network. The one core change is a parameter
expansion whose default is the previous expression, exercised by
`./go demo`'s own e2e on ticket 0001. `ci/network.sh` adds a cold check to
`./go ci`; its time is measured in the report. Passes if `./go demo`
stays `GO` inside 900 s on the branch.
