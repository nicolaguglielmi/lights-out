# ADR 0044 — Ops without the plane: an incident document in (`./go incident`), a post mortem out (`./go postmortem`) through a fourth ops-triage tool `file_postmortem`; every timeline entry anchored to an id the document holds and printed beside that evidence; every action item a ticket through the Tracker's new `put` verb; the Metrics port gains `promql` on `METRICS_URL`, and the plane's `prometheus` adapter calls it with its own URL

- Status: accepted (shipped M22; amendments listed under "Amends")
- Date: 2026-11-04
- Milestone: M22

## Context

Plan §16 M22: ops-triage takes an incident (alerts, the release record, a log excerpt, the time window) besides an alert, files a post mortem whose timeline is anchored to the audit chain, and turns each action item into a ticket that re-enters the tower. Separately, `./go verify` judges a user's own metrics without the plane. What the code gives us (Verified): the audit chain (`core/audit.sh`) needs the state store and the code host, so it needs the plane. The release record has no clock: `{version, stable, stages[{pct, verdict, numbers}], verdict, held_at, image}`. `demo-rollback` releases `CANARY_VERSION` directly, with no ticket and no PR. After a full `./go demo` the record on disk belongs to e2e's tower release, not to demo-rollback, because `core/steps/release.sh` deletes the record and rewrites it. Replay keys on the canonical JSON of the first user message (ADR 0020 §2). A live record's numbers change on every run (ADR 0020 §4 note). The Tracker has one verb, `get.sh`. `./go verify` runs `verify/metric-shape.sh` only. `verify/slo.sh` runs `sh $METRICS_QUERY '<promql>'` and expects raw `/api/v1/query` JSON.

## Decision

1. **The incident document** is one JSON file with:
   - `kind: "incident"`;
   - `name`, matching `^[a-z0-9][a-z0-9-]*$`; it names `.crew/postmortem/<name>/` and the ticket origins;
   - `window: {from, to}` in the tower's `YYYY-MM-DDTHH:MM:SSZ` format (`core/tower.sh` `now()`), with `from ≤ to` compared as strings;
   - `release`: the release record verbatim. `verdict` must be `held` or `rolled-back`; an `in-progress` record is not a verdict (ticket 0008);
   - `alerts`: an array, possibly empty, of today's alert shape without `chain`;
   - `logs`: an array of strings, possibly empty, one line each, verbatim;
   - `chain`: `null` or `{ticket, pr, merge}`, the three keys alerts already carry.

   Alerts stay as they are; no `kind` is added to them, so their replay keys do not move.
2. **`./go incident NAME= FROM= TO= [LOGS=<file>] [TICKET=<id>]`** (`core/incident.sh`, no socket) writes `.crew/incidents/<name>.json`.
   - The release comes from `RELEASE_RECORD` (default `.crew/release.json`).
   - It derives one alert per stage whose verdict is not `GO`: `{headline: <the stage verdict>, version, stable, stage}`.
   - `LOGS` lines are copied verbatim.
   - `TICKET` reads the document through `adapters/$STATE_STORE/get.sh`, so the plane is needed only for this option. It sets `chain` from the tower's own `.pr.number` and `.merge.sha`, and it is a NO-GO when the document's `.release.version` is not the record's `version`.
   - Without `TICKET`, `chain` is `null`. The same inputs always give the same bytes.
   - Stdout is `GO: incident — <path>`, or `NO-GO: incident — <reason>` for a bad `NAME` or window, a missing or `in-progress` record, a missing `LOGS` file, or a store document that is missing or disagrees.
3. **Anchors.** Each timeline entry carries exactly one `anchor` string from this grammar:
   - `release:<version>`: equals `release.version`;
   - `release:<version>@<pct>`: also, `pct` is one of `release.stages[].pct`;
   - `pr:<n>`, `merge:<40-hex sha>`, `ticket:<id>`: equal to `chain.pr`, `chain.merge`, `chain.ticket`. When `chain` is `null`, these are NO-GO;
   - `log:<n>` and `alert:<n>`: 1-based indexes that must exist.

   Offline, "real" means "present in the document core gave the role". The role's only input is that document, so the model cannot add a release or a PR that passes; at most it can cite real ones wrongly. Whoever builds the document is trusted the way `ALERT`'s author is today, and `./go incident` builds it from launchpad's record and the store. **The model writes no times.** The record has no clock, and a time the model invents is the one thing nothing could check. Instead, core prints each anchor's evidence verbatim under the model's sentence:
   - a stage: its verdict and `slo:` lines;
   - the release: version, verdict and image;
   - a log line or alert: its text;
   - a PR, merge or ticket: its id.
4. **One role, one prompt, four tools.**
   - `adapters/adk/agents/ops_triage/agent.py` gains `file_postmortem(title, timeline:[{event, anchor}], causes:[{cause, evidence}], actions:[{title, problem, acceptance}])`. It echoes its arguments, like the other three tools (ADR 0020 §3).
   - `roles/ops-triage/prompt.md` gets two input sections: an alert as today, and a document with `kind: "incident"`, answered by exactly one `file_postmortem` call, with the timeline oldest first and every action item a fix for one cause. The prompt must pass `ci/prompts.sh`, so it names neither `./go` nor `gate`.
   - Core tells the two inputs apart by the command (`triage` versus `postmortem`); the model tells them apart by `kind`.
   - `core/triage.sh` is unchanged, and its exact two sequences already NO-GO a `file_postmortem`.
   - In a post mortem a proposal is a NO-GO. The release has already been decided, and a change made afterwards goes through a ticket.
5. **`core/postmortem.sh` (`./go postmortem INCIDENT=<file>`, no socket).** It runs in this order, and each failure prints `NO-GO: postmortem — <reason>`:
   - **(a) Input checks, before the harness is called:**
     - P1: `no incident at <path>`;
     - P2: `<path> is not an incident document: <what>`, which covers the JSON, `kind`, `name`, `window` and the shape of every field;
     - P3: `release <v> is in-progress`.
   - **(b) The harness run:**
     - P4: `the ops-triage role did not finish (<harness>)`;
     - P5: `expected exactly file_postmortem, got: <sequence>`.
   - **(c) Content checks, before any ticket is written:**
     - P6: `timeline entry <n>: anchor "<a>" <names no release|names no stage of <v>|names no PR|names no merge|names no ticket|names no log line|names no alert> in the incident`, or `… is not a known anchor`;
     - P7: `no timeline entry is anchored to release <v>`;
     - P8: `the chain has PR #<n> and no timeline entry is anchored to it`;
     - P9: `a post mortem needs at least one contributing cause` / `at least one action item` / `action <n> has no <field>`. "At least one" is part of the definition: a post mortem that files nothing does not close the loop. It is not a threshold.
   - **(d) Ticket filing.** Each action is filed through `adapters/$TRACKER/put.sh`, with origin `postmortem <name> action <n>`. A failure is P10: `action <n> has no ticket: <adapter line>`. Tickets already filed stay, and a re-run picks them up.
   - **(e) Output, written only on GO**, to `.crew/postmortem/<name>/`:
     - `out/` (the harness's `calls.json`, `final.md`, `run.json`);
     - `postmortem.json` = `{incident, title, window, release:{version, stable, verdict, image}, chain, timeline:[{event, anchor, evidence:[…]}], causes, actions:[{n, title, ticket}], run}`;
     - `postmortem.md`: `# Post mortem <name>: <title>`, a header with window, release and chain (or "none: not released by the tower"), and the model line from `run.json`. At L0 that line reads "replayed, recorded by <provider> <model>". Then `## Timeline` (each event plus its evidence lines), `## Contributing causes`, and `## Action items`, one line per item with its ticket id. With a chain, the post mortem names `./go audit TICKET=<id>` as the check against the code host.
   - Stdout: `GO: postmortem — <k> action item(s): tickets <id> …`.
6. **The Tracker's second verb: `adapters/file/put.sh <title> <origin>`**, with the ticket body on stdin. It honours `TICKETS_DIR` as `get.sh` does.
   - If a `$TICKETS_DIR/*.md` file already has the line `- Origin: <origin>`, it prints that file's id and writes nothing. It never rewrites a ticket: once filed, a ticket belongs to the Tracker.
   - Otherwise the id is the highest numeric basename plus one, zero-padded to four digits (the existing tickets' format, `0001`–`0013`). It writes `# <id> — <title>`, a blank line, `- Status: open`, `- Origin: <origin>`, a blank line, then stdin.
   - Stdout is the id alone. A failure is one stderr line and exit 1 (ADR 0012 §6).
   - The body core sends has three sections. `## Problem` holds the action's `problem` and the line "Found by the post mortem of incident `<name>` (release `<v>`, `<verdict>`)". `## Acceptance criteria` holds `acceptance`. `## Notes` says the ticket was written by the ops-triage role, and that it has **no `## Perimeter`**: the role saw the incident, not the code, so a human declares the perimeter before `./go assign`, and until then `core/perimeter.sh` refuses the ticket.
   - "Re-enters the tower like any ticket" means it is a valid `tickets/<id>.md`. `assign` reads its first line as `spec.title`. Nothing else is built.
7. **Replay, capture and recording; who commits what.**
   - **The session** runs `./go up` and then `./go demo-rollback` on its own, at L0 with no key. Each side of the run is stamped with `date -u +%FT%TZ`. The session then runs `./go incident NAME=demo-rollback FROM=… TO=… LOGS=<that run's stderr lines matching ^\[(launchpad|demo-rollback)\] |^slo: >` and commits the result as `fixtures/incidents/demo-rollback.json`, with `fixtures/incidents/README.md` stating the provenance: date, commit, engine, the commands, and that the numbers are that run's. The file is the single source of the demo's input.
   - **`./go record-postmortem PROFILE=l1`** (L1 only, as `record-evals`): writes `adapters/adk/agents/ops_triage/evals/postmortem.evalset.json` from `fixtures/incidents/*.json` (one case per file, the incident as the user text, expected trajectory `[file_postmortem]` with `ignore_args`, as for alerts), runs `ROLES=ops_triage` with `ADK_RECORD=1`, and keeps the evalset and the new fixtures only if the eval passes; otherwise it removes both and keeps the recordings under `.crew/adk/`. After that `./go record-evals` re-records the alert cases and this one uniformly.
   - **The owner** runs it with his key. No model answer is ever written by hand. Until the recording exists the evalset does not exist either, so `./go ci` stays green (the `evals` check judges what is recorded), and `demo-postmortem` prints `NO-GO: demo-postmortem — no recording of the ops-triage role for fixtures/incidents/demo-rollback.json; at L0 it replays fixtures/adk/ops-triage, record it with ./go record-postmortem PROFILE=l1`.
   - **`demo-postmortem`** (no socket, `PLANE_HOST=nowhere.invalid`, not a `./go demo` stage, ADR 0003) runs these steps:
     1. Takes the incident from `fixtures/incidents/demo-rollback.json`.
     2. Seeds `TICKETS_DIR=.crew/demo-postmortem/tickets` from `tickets/*.md`.
     3. Runs `core/postmortem.sh` and expects GO (a NO-GO whose reason is a missing recording is reported as above).
     4. Checks that every action has a ticket file carrying its `- Origin:` line, and that a `release:` anchor is present.
     5. Runs `core/postmortem.sh` again and expects GO, the same ids and no new file.
     6. Prints `GO: demo-postmortem`, with the ticket paths on stderr.
     The demo writes into a copy because a run into `tickets/` would leave an untracked file that takes the owner's next ticket number; `./go postmortem` itself writes to `tickets/`.
8. **Metrics.**
   - **The `promql` adapter.** `adapters/promql/query.sh '<promql>'` runs `curl -fsS --connect-timeout 1 --max-time 3 -G "$METRICS_URL/api/v1/query" --data-urlencode query=…`. The timeouts are the plane adapter's own today. `METRICS_URL` is the base that `/api/v1/query` is appended to. An empty `METRICS_URL` is one stderr line naming it, then exit 1, with no network call. An optional `METRICS_AUTH_HEADER` (one header line, e.g. `Authorization: Bearer …`) goes to curl on stdin as `-H @-`, never in argv, and is never printed. It is set in `.env` only: `.env.example` gets both keys empty, and `ci/gitleaks-worktree.toml`'s `.env` entry gains `^METRICS_AUTH_HEADER=`.
   - **The `prometheus` adapter.** `adapters/prometheus/query.sh` becomes `env -u METRICS_AUTH_HEADER METRICS_URL="${PROMETHEUS_URL:-http://$PLANE_HOST:9090}" sh <promql>/query.sh "$1"`. It **ignores an ambient `METRICS_URL`**, so a user's `.env` never moves launchpad's judge. Its `Containerfile`, `ready.sh` and the `METRICS=prometheus` profile value stay as they are. The plan's `http://prometheus:9090` is a compose name that the workbench cannot resolve; the case is `http://$PLANE_HOST:9090`.
   - **`./go verify`.** With a non-empty `METRICS_URL`, it runs `METRICS=promql sh verify/slo.sh`: `slo.promql` unchanged, the three-query contract unchanged, and `CANARY_VERSION`/`STABLE_VERSION` from the command line. A missing version is slo.sh's existing NO-GO. With an empty `METRICS_URL`, it runs today's metric-shape. `./go demo` calls `verify METRICS_URL=`, so `.env` cannot change the demo.
9. **Seams for the gate-writer (cold, no plane, no model).**
   - **`ci/postmortem.sh`** follows `ci/promote.sh`'s shape: a sandbox root with symlinks to `core/` and `ci/`, a `roles/ops-triage/role.env` with `HARNESS=stub-harness` whose `run.sh` copies `world/calls.json`, the real `adapters/file` under a sandbox `TICKETS_DIR` seeded with `0001.md` and `0007.md` (so the next id is `0008`), and a `stub-tracker/put.sh` that exits 1. It covers:
     - P1–P10, each with the harness log or the tickets dir proving what did not happen;
     - GO, with the md headings, the `postmortem.json` keys and the ticket files' first line and Origin line;
     - a re-run, which files nothing;
     - `put.sh` alone: allocation, padding, idempotency, stdout being only the id;
     - `core/incident.sh` on canned records: alert derivation, an `in-progress` refusal, `TICKET` through a stub store agreeing and disagreeing, and byte-identical output across two runs.
   - **`ci/metrics.sh`** uses an inline python3 HTTP server on 127.0.0.1, ephemeral port, that records path, query and headers. It covers:
     - `promql/query.sh` passes the body through and encodes the query;
     - an empty `METRICS_URL` is a stderr line and exit 1;
     - the header reaches the server and appears in neither stdout nor stderr;
     - `prometheus/query.sh` with `PROMETHEUS_URL` set to the server and `METRICS_URL` set to a dead URL hits the server;
     - `verify/slo.sh` with `METRICS=promql` on canned responses gives the expected `GO: slo` and `NO-GO: slo — error_rate …` lines.
   - **`ci/evals.sh`**: the `ops_triage` set gains `file_postmortem`.
   - **The plane comparison** is the session's and the owner's acceptance, not a ci check. Run `./go verify METRICS_URL=http://host.containers.internal:9090 CANARY_VERSION=<v> STABLE_VERSION=<s>` immediately after `./go demo-rollback`; its line must agree with the record's `stages[-1].verdict`. The window in which it can agree is `slo.promql`'s own `[1m]`. `./go demo` itself proves the wrapper, because launchpad now reaches the plane through `promql`. HANDOFF records both lines.

## Alternatives rejected

- **A seventh role, or a second ADK agent for ops-triage**: ruled out by `ci/roles.sh` and plan §16; a second agent would be a seventh role in disguise.
- **`file_incident` before `file_postmortem`, or a proposal allowed**: two calls where one is enough, and a proposal after the verdict is an action item without a ticket.
- **Model-written times in the timeline**: the record has no clock, and invented times cannot be checked. Printing the evidence verbatim says more.
- **Checking anchors against the plane inside the post mortem (`core/audit.sh`)**: the same incident would get GO on one machine and NO-GO on another (ADR 0042's "probe at gate time"). The store is read once, when the document is built, and `./go audit` stays the plane check.
- **The alerts' hand-written `chain` as the anchor source, or the whole ticket document in the incident**: the first is hand-written. The second (history, rework, review reasons) makes the input bigger and gives the model more to misread, and no check needs it.
- **A perimeter written by the model**: it saw no code, so the paths would be invented, which is the fabrication this ADR stops.
- **An origin keyed on a hash of the action's content**: a re-run at L1 would file the same intent twice.
- **A demo keyed on a live `.crew/release.json`**: the numbers vary on every run (ADR 0020 §4 note). **A hand-written answer until the owner records**: a hidden mock (plan §16, risks).
- **A documented jq recipe instead of `./go incident`**: no ci check can test it, and it cannot call the StateStore port.
- **The evalset committed before it can replay**: `adk eval` fails on a missing recording, so `./go ci` would be red until the owner records. **The demo's tickets in the checkout's `tickets/`**: every run leaves an untracked `0014.md` in the checkout, which takes the owner's next ticket number.
- **`METRICS_URL` honoured by the `prometheus` adapter, or the adapter renamed or removed**: the first moves the canary's judge from `.env`; the second touches the plane's image, `PLANE_ADAPTERS` and the profiles for nothing.
- **Basic-auth flags or a gcloud token**: one header line covers bearer, basic and tenant headers. Managed Prometheus's OAuth belongs to L2.

## Consequences

- **The one that hurts: an anchored timeline is not a true one.** The check stops invented releases, PRs and log lines. It does not stop a wrong story about real ones. Core puts the evidence under every claim so a reader sees the gap; offline, nothing more mechanical exists.
- "Any Prometheus-compatible endpoint" does not mean "any metrics". `slo.promql` reads the OTel HTTP semconv histogram joined to `target_info{service_version}`. On other metrics, `./go verify` is a NO-GO saying "no data". The README must say so.
- The prompt change makes the three alert recordings replay with a "recorded under a different prompt" line on stderr until `./go record-evals PROFILE=l1 ROLES=ops_triage` re-records them. If one alert case fails under the new prompt, `record-evals` discards the recordings of that role.
- Until the owner runs `./go record-postmortem PROFILE=l1`, `demo-postmortem` says NO-GO and names the command; the branch's `./go ci` and `./go demo` are green without it.
- Tickets written by a model enter the Tracker unreviewed. The missing perimeter is the deliberate stop.
- `put.sh` takes no lock: two post mortems running at once can allocate the same id. There is one human and one run at a time.
- A ticket links to `.crew/postmortem/…`, which is local and gitignored.
- A remote endpoint slower than 3 s is a NO-GO. A knob comes only with a measurement.
- New pieces: the `incident`, `postmortem` and `demo-postmortem` targets, two ci checks, a gitleaks allowlist line, and `METRICS_URL`/`METRICS_AUTH_HEADER` added to `ci/attach.sh`'s `env -u` list.

## Golden-rule check

`./go demo` gains no stage. `verify` is called with `METRICS_URL=`, and launchpad reaches the plane through one more `sh`, which `demo-rollback` proves on every run. `demo-postmortem` and `postmortem` need no socket, no plane and no key at L0. Until the owner's recording exists, they NO-GO and name the command, and they never pass on hand-written output. Passes: neither joins `./go demo`, and `./go ci` does not need the recording.

## Verified (2026-11-04, by reading this checkout)

- `core/launchpad.sh` `record()`: no timestamp field. `core/steps/release.sh` runs `rm -f "$RELEASE_RECORD"` before every tower release. `./go demo` runs `e2e` after `demo-rollback`.
- `core/triage.sh` accepts exactly `file_incident propose_rollback|propose_hold`. `ci/evals.sh` globs `evals/*.evalset.json` per agent and checks toolsets by name. `record-evals ROLES=` runs every evalset of the role, with `ADK_RECORD=1`.
- `replay.canonical()` is `json.dumps(sort_keys=True, indent=2)`. A different `instruction_sha256` is only a stderr warning.
- `core/perimeter.sh` refuses a ticket with no `## Perimeter`. `core/steps/assign.sh` takes `spec.title` from the first line without `# `.
- `adapters/prometheus/query.sh` defaults to `http://$PLANE_HOST:9090` with `--connect-timeout 1 --max-time 3`. No other `query.sh` exists (no `managed-prometheus` adapter yet).
- curl's manual (curl.se, the page for 8.23.1): `-H @filename` adds one header per line, and `@-` reads stdin. The version that introduced it is not stated there; check the workbench's curl at build.
- Not verified: the `/api/v1/query` paths of Mimir, VictoriaMetrics and GMP. The README names Prometheus's, and `ci/metrics.sh` proves that shape.

## Amends

ADR 0020 §3: the ops-triage toolset becomes four tools, and `ci/evals.sh` asserts the four; the triage sequence and the incident artefact are unchanged; a second core driver is added. ADR 0017 §4 and plan §4's `Tracker` row: `file` gains `put`. Plan §4's `Metrics` row: `promql` is added, and `prometheus` becomes `promql` at `$PLANE_HOST:9090`. Plan §16 M22: `prometheus:9090` becomes `$PLANE_HOST:9090`, and the done-when's "in `tickets/`" holds for `./go postmortem`, not for the demo. ADR 0013 §4: Metrics `query.sh` now has two implementations. CLAUDE.md is not touched.

## Decided with the owner's veto open (2026-11-04)

1. The evalset lands with the recording (`./go record-postmortem`), not before: `./go ci` stays green.
2. The demo's tickets go to a copy seeded from `tickets/`; the done-when's "in `tickets/`" holds for `./go postmortem`, not for the demo.
