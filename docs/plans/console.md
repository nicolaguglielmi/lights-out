# The console (M11): five screens over the logbook, and what stays out

- Date: 2026-11-04
- Status: design, docs only. Decided by [ADR 0046](../adr/0046-console-reads-one-versioned-logbook-and-writes-only-go-for-launch.md) (proposed), which amends [ADR 0023](../adr/0023-console-at-m11.md).
- Built: after M22 merges (plan §7 M11, §16). Preconditions P1–P3 are in ADR 0046 §2.
- Audience: a business client with one lights-out instance, who reads what the agents did and presses go-for-launch. The DevOps engineer keeps `./go`; every screen mirrors `./go logbook`, and the one button mirrors `./go promote`.

## How to read this document

The console reads one document, the logbook (`lights-out/logbook/1.0`),
which `core/logbook.sh` assembles. The console never reads the tower,
`.crew/` or an adapter. So each field below has two names:

- **Logbook field**: the path in the logbook, the only name the console uses.
- **Source**: where the reader takes it from today. When M19, M21 or M22
  change a source, this column changes and the console does not.

Fields marked **P1** exist only after the tower copies them from `run.json`
(ADR 0046 §2, P1). Fields marked **P2** exist only after the release log
`.crew/releases.jsonl` exists (P2). Fields marked **M22** come from the post
mortem of [ADR 0044](../adr/0044-incident-in-postmortem-out.md) (proposed,
on the M22 branch at the time of writing).

A source the reader cannot read is `{ok: false, error}` in the logbook, and
the screen shows that error where the value would be. No screen shows a
default in place of a missing fact.

The logbook has no clock (ADR 0046 §2). "This week" is the viewer's clock
applied to the `at` fields; the logbook does not change with it.

## The logbook at the top

| Logbook field | Source |
|---|---|
| `schema` | constant `lights-out/logbook/1.0` |
| `source.commit` | `git rev-parse HEAD` of the checkout the reader runs in |
| `source.level` | the profile's `LEVEL` |
| `source.workload` | `WORKLOAD` (the attached repository's name, or `app`) |
| `source.code_host` | the profile's `CODE_HOST` (`gitea` at L0, `github` after M21) |

Every screen shows `source.level`, `source.workload` and, in snapshot mode,
`source.commit` with the words "snapshot, no plane behind it".

## Screen 1 — This week's tickets

One row per ticket the tower touched in the window, newest first. The
question the row answers: did the change get through, and if not, which
judge stopped it and with what line.

| Shown | Logbook field | Source |
|---|---|---|
| Ticket id, title | `tickets[].id`, `.title` | file name of `tickets/<id>.md`; tower `spec.title` (first line of the ticket) |
| Where it came from | `tickets[].origin` | the ticket's `- Origin:` line, `null` when absent (post mortem tickets carry one, M22) |
| State | `tickets[].state` | tower `state` |
| Outcome | `tickets[].outcome.verdict` ∈ `GO`, `NO-GO`, `pending`, `needs-human`, `stranded` | derived by the reader, rule below |
| Decided by | `tickets[].outcome.judge` ∈ `gates`, `review`, `verify`, `human`, `step` | derived, rule below |
| Why, in one line | `tickets[].outcome.line` | the deciding judge's own line, verbatim (rule below) |
| Attempts | `tickets[].attempt` | tower `attempt` (ADR 0038) |
| Last activity | `tickets[].updated_at` | tower `history[-1].at` |
| Opened | `tickets[].opened_at` | tower `history[0].at` |
| Cost | `tickets[].task.tokens.total`, `.duration_s` | **P1**: tower `task.*`, from `run.json` |

**Outcome rule**, in the reader, first match wins:

1. `history[-1].error` is set → `stranded`, judge `step`, line = that error
   (ADR 0034).
2. `state == needs-human` → `needs-human`, judge and line of `rework[-1]`.
3. `state == rolled-back` → `NO-GO`, judge `verify`, line = the first stage
   of `release.numbers` whose verdict is not `GO`.
4. `state == held` → `GO`, judge `verify`; once the release log has a GO
   answer for that version (**P2**), judge `human`, line
   `promoted by <by> via <via>`.
5. `ci.verdict == NO-GO` → `NO-GO`, judge `gates`, line `ci.line`.
6. `review.verdict == changes-requested` → `NO-GO`, judge `review`, line
   the first line of `review.reason`.
7. otherwise `pending`, judge and line empty.

The rule lives in the reader, so the console does not interpret states, and
a new tower state changes the reader and its golden file, not the screen.

**Filter**: the window (default 7 days, the viewer's choice; nothing is
computed from it). **Empty**: "no ticket touched in this window". **Before
ADR 0031 §6 exists**: a banner, "the tower is in memory at L0; tickets from
before the last `./go down` are not here" (ADR 0046 §2).

## Screen 2 — One ticket's chain, from ticket to rollback

The audit chain as a vertical sequence of stations, each with its verdict
and the facts that decided it. It mirrors `./go audit` without audit's
check against the code host; the screen says "check against the code host:
`./go audit TICKET=<id>`".

| Station | Shown | Logbook field | Source |
|---|---|---|---|
| Spec | title, hash, perimeter (the declared globs) | `tickets[].spec.{title, sha256}`, `tickets[].perimeter[]` | tower `spec.*`; `core/perimeter.sh --list` on `tickets/<id>.md` |
| Task | role, harness, provider, model served, thinking, turns, tokens, duration | `tickets[].task.{role, harness, provider, model, thinking, turns, tokens, duration_s}` | tower `task.{role, harness, model}` today; the rest **P1** from `run.json` (M19 adds harnesses, not keys) |
| Replay | "replayed, recorded by `<provider>` `<model>`" when L0 | `tickets[].task.replayed` | **P1**: `run.json` has `.recorded.*` |
| Attempts | each rejected attempt: judge, line, details, time | `tickets[].rework[]` | tower `rework[]` (ADR 0038) |
| PR | number, link, base and head | `tickets[].pr.{number, url, base_sha, head_sha}` | tower `pr.*`; `url` passes through as the code host wrote it (Gitea at L0, GitHub after M21) |
| Gates | verdict, the failing gate, risk level, merge decision, the stdout line, details | `tickets[].ci.{verdict, failed, risk_level, decision, line, details[]}` | tower `ci.*` |
| Review | role, verdict, reason, model | `tickets[].review.{role, verdict, reason, model}` | tower `review.*` (ADR 0021); absent when risk did not ask for it |
| Merge | merge sha, head it merged | `tickets[].merge.{sha, head_sha}` | tower `merge.*` |
| Release | version, image, each stage's percentage, verdict and `slo:` numbers | `tickets[].release.{version, image, verdict}`, `releases[i].stages[]` | tower `release.*`; stages from the release log entry whose `ticket` is this id (**P2**) |
| Verify / rollback | final verdict: rolled back at stage N, or held at N % | `releases[i].{verdict, held_at}` | **P2**, from launchpad's final record |
| Go-for-launch | GO or NO-GO, by whom, via what, when | `releases[i].answer.{answer, by, via, at}` | **P2**, appended by `core/promote.sh` |
| History | every step with time, attempt, error | `tickets[].history[]` | tower `history[]` |

A station the ticket never reached is drawn empty, not hidden, so a NO-GO
at gates shows how far it got. The release stations show nothing before P2
for a ticket whose release is not the latest one; for the latest, the
reader falls back to `.crew/release.json` and says so (`releases[i].from:
"release.json"`).

## Screen 3 — Incidents and post mortems, with their fix tickets

One card per post mortem (**M22**). An incident without a post mortem is
not in v1.0: ADR 0044 writes the incident to `.crew/incidents/<name>.json`,
and listing those is a 1.1 candidate once someone needs it.

| Shown | Logbook field | Source (ADR 0044 §5(e)) |
|---|---|---|
| Name, title | `postmortems[].{name, title}` | `postmortem.json` `.incident`, `.title` |
| Window | `postmortems[].window.{from, to}` | `.window` |
| Release: version, stable, verdict, image | `postmortems[].release.{version, stable, verdict, image}` | `.release` |
| Chain: ticket, PR, merge; or "not released by the tower" | `postmortems[].chain` | `.chain` (`null` for a direct release such as `demo-rollback`) |
| Timeline | each event, its anchor, and the evidence lines printed under it | `postmortems[].timeline[].{event, anchor, evidence[]}` | `.timeline` |
| Contributing causes | cause and evidence | `postmortems[].causes[]` | `.causes` |
| Action items | number, title, fix ticket id | `postmortems[].actions[].{n, title, ticket}` | `.actions` |
| Fix ticket state | the fix ticket's outcome, from screen 1's rule | `postmortems[].actions[].ticket` joined to `tickets[]` by id | the console joins on id; the ticket's `origin` is `postmortem <name> action <n>` |
| Who wrote it | provider and model, "replayed" at L0 | `postmortems[].run.{provider, model, replayed}` | `.run` |

The anchor links: `ticket:<id>` and `pr:<n>` open screen 2, `release:<v>`
opens the release on screen 2 or 4. A fix ticket without a perimeter is
shown as "waiting for a human to declare the perimeter" (ADR 0044 §6: the
role saw no code), which is the state `core/perimeter.sh` enforces.

**Empty at L0**: until the owner records ADR 0044's post mortem, "no post
mortem recorded; `./go demo-postmortem` names the recording it needs".

## Screen 4 — Go-for-launch: the brief and one button

The only screen with an input. It mirrors `./go promote`.

| Shown | Logbook field | Source |
|---|---|---|
| Version, stable, image | `launch.record.{version, stable, image}` | `.crew/release.json` |
| Stages and their numbers | `launch.record.stages[].{pct, verdict, numbers}` | `.crew/release.json` |
| Held at | `launch.record.held_at` | `.crew/release.json` |
| What the plane says now | `launch.plane.{canary, stable, weight}` | DELIVERY `status` |
| The brief, and its hash | `launch.brief.{text, sha256}` | `$PROMOTE_OUT/brief.md` (default `.crew/promote/brief.md`) |
| Who wrote the brief | `launch.brief.author` | promote's own line (the release role at L1, rendered from the record under replay) |
| Already answered | `launch.record.promoted.{at, by, via}` or `launch.record.rejected.{at, by, via}` | `.crew/release.json`, written by promote (ADR 0046 §3) |
| Can it be pressed | `launch.pressable`, `launch.disabled_reason` | reader: a `held` record that agrees with the plane and has a brief; the console adds its own reasons (snapshot mode, identity not in `CONSOLE_LAUNCHERS`) |

**The button**: two choices, GO and NO-GO, then a confirmation that repeats
the version and the brief's first line. The POST carries `{answer, version,
held_at, brief_sha256}`; the console runs `core/promote.sh` with
`GO_FOR_LAUNCH`, `GO_FOR_LAUNCH_BY` (the proxy's identity),
`GO_FOR_LAUNCH_VIA=console` and `GO_FOR_LAUNCH_BRIEF_SHA256`. The screen
then shows promote's one stdout line verbatim, `GO: go-for-launch` or
`NO-GO: go-for-launch — <reason>`, and the new state from the next logbook.

**Disabled**, always with its reason: snapshot mode; no held record;
promote's own record-and-plane check; the brief changed since it was shown;
identity not allowed. "Prepare the brief" is offered when a held record has
no brief yet; it runs promote with no answer, which moves nothing.

## Screen 5 — Thresholds, with their provenance

Every number that can stop a change, where it is set, and why it has that
value. A client reads this to know what "GO" meant.

| Shown | Logbook field | Source |
|---|---|---|
| Key, value | `thresholds[].{key, value}` | `policy/merge.env` merged with the workload's `policy.env` by `core/policy.py`; `policy/rework.env`; the workload's `MUTATION_BASELINE` |
| Set where | `thresholds[].file`, `.overridden` | which file the merged value came from; `true` when the workload overrides the platform default |
| Provenance kind | `thresholds[].provenance.kind` ∈ `decision`, `external`, `measurement`, or `null` | the `# Provenance, <kind>: …` comment above the key |
| Provenance text | `thresholds[].provenance.text` | the rest of that comment |
| What it judges | `thresholds[].judge` ∈ `gates`, `verify`, `rework` | which file group it came from: merge policy keys → `gates`, SLO deltas → `verify`, `REWORK_MAX_ATTEMPTS` → `rework` |

A key without the comment form shows **undeclared** in place of a reason
(`provenance: null`). Nothing fills it in. M11 rewrites today's other
provenance forms into the parsed one, and a ci check NO-GOes a numeric
policy key without it (ADR 0046, Consequences). The SLO query itself,
`verify/slo.promql`, is linked, not reproduced: its thresholds are keys in
`policy/merge.env`.

## What every screen shares

- **Header**: `source.workload`, `source.level`, mode (LIVE or snapshot),
  and the identity the proxy sent (or "loopback").
- **Freshness**: LIVE reloads the logbook every 2 s (ADR 0046 §4); the
  screen shows the time of the last good read, and a stale banner when the
  reader failed.
- **No charts.** The numbers that decided are the `slo:` lines and the gate
  lines, shown as text (ADR 0046 §7).
- **Design**: Stitch designs the five screens into `design/` (ADR 0023 §6,
  kept); this document is the brief for that design session. The fields
  above are the data contract the design must use; a field the design wants
  and the logbook lacks goes to the reader as a 1.x minor, never into the
  console.

## What stays out of lights-out (plan §14's boundary rule)

The rule: "does a client with one instance need it?" → lights-out; "does it
only make sense to someone running many engagements?" → a separate private
consumer project that uses lights-out only through public interfaces.

Out, in the consumer:

- **Portfolio across clients.** Many instances, many logbooks, one view. A
  client with one instance has nothing to compare; lights-out has no
  tenant field and keeps one client per instance (ADR 0046 §5).
- **Value report.** Hours and money saved, cost per merged ticket per
  provider, trend across engagements. The logbook carries tokens, duration,
  provider and model (**P1**) and never a currency (ADR 0031 §5: the only
  price with a provenance would be a list price, and a replay spent
  nothing); pricing them is the consumer's.
- **Proposal.** Scope, estimate and price for the next engagement, built
  from past logbooks. Sales material has no place in an open harness.

In lights-out, though a consultant may use them: the five screens above,
the post mortem, the thresholds with provenance. A client with one instance
needs each.

**The public interface the consumer needs**, and the only one (ADR 0046 §6):

1. **The logbook**, read-only, two ways:
   - `./go logbook` writes `LOGBOOK_FILE` (default `.crew/logbook.json`)
     and prints `GO: logbook — <file>, <n> tickets`;
   - `GET /v1/logbook.json` on a running console, behind the same auth as
     the screens.
2. **The schema files**, `core/logbook/<major>.<minor>.schema.json`, so the
   consumer validates what it collects.
3. **The versioning promise**: an added optional field is a minor; a
   removed, renamed or retyped field, or one whose meaning changes, is a
   new major served next to the old one for one tagged release (ADR 0046
   §2, the owner decides the length).

What the consumer must not do: read the tower, `.crew/`, an adapter or the
code host behind lights-out's back; press go-for-launch (the logbook is
read-only and the POST is not part of the interface). What the consumer
adds on its side: which client an instance belongs to (the logbook has
`source.workload`, not a client name), when it collected each logbook, and
prices. If the consumer needs a fact the logbook lacks, the fact enters
the logbook as a minor, open, with its own ADR when it is structural.

## Open questions this plan leaves to M11

- The exact JSON Schema text of 1.0, written from the tables above; the
  golden file and `ci/logbook.sh` come from it (ADR 0046 §2).
- Whether the post mortem's `run` carries `replayed`, or the reader derives
  it from the level; settled when M22 merges.
- Stitch's export format (unverified on 2026-11-04, ADR 0046 §1).
- IAP's and oauth2-proxy's identity headers (unverified, ADR 0046 §5).
