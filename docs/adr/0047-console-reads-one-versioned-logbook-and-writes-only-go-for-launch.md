# ADR 0047 — The console for a business client: a new `console/` beside the board reads one versioned document, the logbook, which `./go logbook` assembles in core from the tower, a release log, the post mortems and the policy files; its one write is go-for-launch through `core/promote.sh`, recording who pressed; loopback without auth, anywhere else a trusted proxy's identity and an allowlist; no `Charts` port; built after M22

- Status: proposed. Amends ADR 0023 §2(a) [dropped: the console does not answer the product role's question], §4 [no `Charts` port, no Grafana container], §5 [five other screens], §7 [acceptance through `demo-console`]; ADR 0020 §4 [`promote.sh` records who answered, records a NO-GO, and can answer a brief already shown]; ADR 0031 §3 [its cost fields become M11's precondition, nothing else of it] and §5 [`./go report`, if built, renders the logbook]; plan §4 [the `Charts` row goes] and §7 M11 [screens, writes, done-when].
- Date: 2026-11-04
- Milestone: M11 (built after M22 merges)

## Context

ADR 0023 fixed what the console may know and do. The following was read in this checkout on 2026-11-04, except where noted.

- **The board is built for the opposite job.** It is read-only, built with the standard library, needs no build, and its explainer is hosted publicly with `BOARD_STATIC=1` (ADR 0035). `ci/board.sh` proves it names no write verb.
- **A client screen's facts are scattered, and some do not last.**
  - The tower document holds `task.{role,harness,model}` only (`core/steps/assign.sh`). Provider, tokens and turns stay in `.crew/<t>/out/run.json`, which every assign wipes.
  - `.crew/release.json` holds the latest release only. It has no clock, and `core/steps/release.sh` deletes and rewrites it.
  - `core/promote.sh` writes `promoted.by: "go-for-launch"` and records nothing on a NO-GO.
  - At L0 the Firestore emulator is in memory, so `./go down` loses the documents. `.crew/` is a host bind mount (the Makefile's `up` comment) and survives.
- **Three source shapes are moving.** M19 changes `run.json` (same keys, more harnesses), M21 changes PR URLs, and M22 brings `postmortem.json` (ADR 0044 §5(e), proposed). ADR 0044 was read from the `nostalgic-engelbart-1820af` worktree.
- **ADR 0023 §2(a) has no seam.** `ask_product_owner` (ADR 0020 §3) only echoes its question to the eval. No tower step runs the product role, and no `./go` command answers it, so a console answer would mirror no CLI command (ADR 0023 §3).
- **`core/promote.sh` writes the brief and takes the answer in the same run.** A console has to show the brief before the button is pressed.
- **Provenance comments have more than one form.** Some keys have `# Provenance, <kind>: …` on the line above (`policy/merge.env` lines 52 and 73). Others use a different form (`merge.env` 79, `policy/rework.env` 5).

## Decision

1. **A new component, `console/`; the board is not grown into it.**
   - The board's guarantees are the console's opposites. The board is read-only, has no auth, and is hosted as a public explainer. The console has one write, needs auth outside loopback, and shows a client's data. Merging them would drop `ci/board.sh`'s write check and put a button behind the public explainer.
   - **What they share** is how they are built: a standard-library Python server in the workbench, no build, no asset from the network (ADR 0035 §1, §5). After M11 they also share the logbook as data.
   - **No shared module.** The roughly 15 duplicated lines (`run()`) cost less than a library.
   - **ADR 0023 §6 stands.** Stitch designs the five screens into `design/`. A frontend worker builds `console/` from them. A cloner without Stitch gets the console that is committed.
   - Stitch's export format is unverified on 2026-11-04. If it needs a build or a CDN asset, the worker translates it to plain HTML/CSS and `design/CONTRACT.md` says so. If Stitch no longer exists as plan §5 describes it, M11 stops and asks.

2. **One versioned document: the logbook.**
   - **The reader.** `core/logbook.sh` (`./go logbook`; the name `record` is taken) is the only reader.
   - **What it reads:**
     - for each `tickets/*.md`, the tower document through `adapters/$STATE_STORE/get.sh` (an absent document means the ticket was never opened, as on the board);
     - the release log (P2 below);
     - `.crew/postmortem/*/postmortem.json`, plus the tickets' `- Origin: postmortem <name> action <n>` lines;
     - the thresholds: the merge that `core/policy.py` does, `policy/rework.env`, and the workload's `MUTATION_BASELINE`, each with the provenance comment above it;
     - for the launch screen: `.crew/release.json`, `$PROMOTE_OUT/brief.md` and DELIVERY `status`.
   - **Output.** The file is `LOGBOOK_FILE ?= .crew/logbook.json`. Stdout is `GO: logbook — <file>, <n> tickets`.
   - **Determinism and honesty.** The logbook has no clock of its own, so equal sources give equal bytes (ADR 0031 §5). A source it cannot read becomes `{ok: false, error: <the adapter's last stderr line>}`, never a default (ADR 0035 §3). A threshold without the comment form gets `provenance: null`, and the console shows it as "undeclared". Nothing fills it in.
   - **The only place a shape change lands.** When M19, M21 or M22 change a shape, only this reader changes. `pr.url` passes through next to `code_host`; the console never builds a URL.
   - **Shape.** v1.0 at the top level is `{schema: "lights-out/logbook/1.0", source: {commit, level, workload, code_host}, tickets, releases, postmortems, thresholds, launch}`. The schema is kept one file per minor: `core/logbook/1.0.schema.json`, `1.1…`.
   - **Versioning.**
     - Adding an optional field is a minor version. Readers ignore fields they do not know.
     - Removing, renaming or retyping a field, or changing what it means, is a new major. The new major is served next to the previous one (`/v1/…`, `/v2/…`) for one tagged lights-out release. That length is my decision, and the owner decides (nothing consumes the logbook yet).
   - **`ci/logbook.sh`**, written by the gate-writer from this ADR, checks four things:
     - with stub sources (the `stub-state` precedent of `ci/tower.sh`), the reader runs twice and is byte-equal to a committed golden file;
     - the golden validates against every committed schema of its major;
     - the committed snapshot (§4) validates;
     - `console/` names no `adapters/`, `.crew/` or store path, only the served logbook and `core/promote.sh`.

     Validation uses `jsonschema` 4.26.0. It is already in `workbench/uv.lock` as a transitive dependency, and M11 pins it directly, so no new bytes are downloaded.
   - **Preconditions of building M11:**
     - **P1:** assign, rework and review copy `provider, model, thinking, turns, tokens, duration_s, input_sha256` from `run.json` into the document. Under replay they read `.recorded.*`, the rule assign already uses for `model`. This is the cost half of ADR 0031 §3 and nothing more of it.
     - **P2:** a release log, `.crew/releases.jsonl`. Launchpad's `record()` appends every final record (`held` or `rolled-back`) with `at` and `ticket` (the tower's `TICKET`, or `null` for direct releases). `promote.sh` appends every answer. `.crew/release.json` does not change, so ADR 0044's incident still copies it verbatim.
     - **P3:** M22 is merged.
   - **Needed before the console is client-ready, but not for M11's done-when:** ADR 0031 §6, which mirrors the emulator to `.crew/state/`. Showing "the week's tickets" from a store that `down` empties is the lie ADR 0023 §1 forbids. Until the mirror exists, that screen says the tower is in memory.

3. **One write: go-for-launch, only through `core/promote.sh`.**
   - **ADR 0023 §2(a) is dropped** (see Context). It reopens only when the product role runs in the tower and a `./go` command answers its question.
   - **GO and NO-GO both stay** (ADR 0023 §2(b)).
   - **Three changes to `promote.sh`, and the CLI gets each of them too:**
     - `GO_FOR_LAUNCH_BY` (default `unauthenticated`) and `GO_FOR_LAUNCH_VIA` (default `cli`) are written into `promoted: {weight, at, by, via}`. A new `rejected: {at, by, via}` is written on NO-GO. Each answer also gets one line in the release log.
     - `GO_FOR_LAUNCH_BRIEF_SHA256`, when set, makes promote answer on the brief already on disk instead of writing a new one, if that brief's hash matches and the existing checks pass. If not, promote says `NO-GO: go-for-launch — the brief on disk is not the one answered; nothing moved`. The human answers what they read, even when an L1 release role would write the brief differently a second time.
     - "Prepare the brief" is `promote.sh` with no answer, as it is today: it writes the brief and moves nothing.
   - **The one POST is accepted only when:**
     - it has `Content-Type: application/json`;
     - its `Origin` equals the console's own origin, and its `Host` is in the console's own list (against DNS rebinding);
     - its body `{answer, version, held_at, brief_sha256}` matches the current record;
     - no other press is running (a second one gets 409).

     On any mismatch it refuses without calling anything.

4. **L0 with no key: two modes, and the button never fakes a result.**
   - **LIVE (`./go console`, the done-when).**
     - It needs the plane up and the engine socket, like `./go board`.
     - The reader runs in one loop every 2 s (ADR 0035 §3's decision, at the same cost).
     - It is published on `127.0.0.1:8098`. That port is my decision: it sits beside the board's 8099 and outside the ports `up` publishes. `CONSOLE_PORT` moves it.
     - After `./go demo`, it shows ticket 0001's chain to the rollback.
   - **SNAPSHOT (`CONSOLE_SNAPSHOT=<file>`).** It serves a logbook file and never runs the reader.
     - The committed file is `console/snapshot/logbook.json`. It is produced by `./go logbook` after a real `./go demo`, and after `./go demo-postmortem` once ADR 0044's recording exists.
     - `console/snapshot/README.md` gives the commit, date, engine and commands.
     - The ci check requires its `spec.sha256` values to equal the committed tickets', so it cannot drift silently from `tickets/`. It is never hand-written.
   - **When the button is disabled,** it shows the reason:
     - snapshot mode: "snapshot of <commit>, no plane behind it";
     - no record is held;
     - the record and the plane disagree (promote's own check, shown verbatim);
     - the identity is not allowed (§5).
   - **Acceptance: `./go demo-console`.** It runs demo-launch's sequence, but the press is an HTTP POST to the running console instead of `GO_FOR_LAUNCH=GO make promote`. Then it runs demo-launch's own checks: Envoy at weight 100, 10 of 10 `/healthz` answers, and the un-promote. It also checks that the record says `via: console`.
   - **Not in `./go demo` (ADR 0003).** Measured demo runs take 525–765 s (plan §7, the M12 and M13 notes), and the budget is plan §2's 900 s.

5. **Auth: declared, or the console does not start.**
   - **`CONSOLE_AUTH` has three values:**
     - `loopback` is the default. The console refuses to start if it is published beyond 127.0.0.1.
     - `proxy` takes the identity from the header named by `CONSOLE_IDENTITY_HEADER`. IAP sets it on Cloud Run; oauth2-proxy sets it elsewhere. A request without the header gets 401. Go-for-launch is enabled only for identities in `CONSOLE_LAUNCHERS`, a list kept in `.env` or the profile; it is not a secret.
     - `public` is accepted only together with `CONSOLE_SNAPSHOT` pointing at the committed snapshot, which holds the demo's data and is already public.
   - Any other combination refuses to start, with one line naming the key.
   - The identity is what reaches `by`. The console has no login, no session and no password.
   - One client per instance (plan §14), and no tenant field.
   - **Unverified.** Which header names IAP and oauth2-proxy set today, and whether IAP's signed assertion should be checked. M11 verifies both before it writes its deployment notes.
   - **Where a client runs it.** LIVE on Cloud Run needs M8/M10's adapters, which are not built. The client deployment M11 documents is therefore an L1 host behind oauth2-proxy.

6. **The logbook is the only public interface for a consumer.**
   - A consumer gets it from `./go logbook` or from `GET /v1/logbook.json` (the console's, with the same auth).
   - It is read-only, so a consumer cannot press go-for-launch.
   - A portfolio across clients, a value report and a proposal belong to the consumer (plan §14), built from the logbooks it collects.
   - Any other hook enters lights-out as an open port with its own ADR. The consumer never reads the tower, `.crew/` or an adapter.

7. **No `Charts` port.**
   - **The five screens:**
     1. The week's tickets, each with GO/NO-GO and the line that decided it.
     2. One ticket's chain: spec → task (harness, provider, model) → PR → gates → review → merge → release stages → verify → rollback, held or promoted. It mirrors `./go audit` without the cross-check against the code host, which stays audit's (ADR 0031 §5).
     3. Incidents and post mortems, with their fix tickets and those tickets' states.
     4. Go-for-launch: the record, the brief, and the button.
     5. The thresholds, with their provenance.
   - **They replace ADR 0023 §5's screens.** The ADR navigator goes, because a client reads ADRs on the code host. The ticket detail folds into the chain.
   - **Nothing to chart.** No screen draws a time series. The numbers that decided are the record's `slo:` lines. So the `Charts` port, its `grafana` and `cloud-monitoring` adapters and the Grafana container leave plan §4. ADR 0035 had already rejected Grafana for the board.
   - Every screen mirrors `./go logbook`, so ADR 0023 §3 holds by construction.

8. **When it is built.**
   - After M22 merges (P3).
   - P1 relies on M19's `run.json`, and M19 keeps its keys. If M19 has not merged, the reader covers the harnesses that exist.
   - M21 changes only `pr.url` and `code_host`, which pass through the reader unchanged.
   - The plan places M11 after the experiment (§14, §16 "Dopo"). This ADR does not move it.

## Alternatives rejected

- **Grow the board:** it loses its read-only proof, and the write ends up behind a public explainer.
- **The console reads the tower, `.crew/` and the adapters directly:** every M19/M21/M22 change becomes a UI change, and §6 is left with no interface.
- **The logbook assembled in the console's Python:** `./go report` and a consumer would each need a second reader.
- **An unversioned document:** the first rename breaks a consumer silently.
- **Release history in the tower document:** promote does not know the ticket, and `demo-launch`/`demo-hold` releases have no ticket at all.
- **A `releases` collection in the StateStore:** it is in memory at L0 and lost on `down`, while `.crew/` survives.
- **Login built into the console:** a password store and sessions in a component whose rule is to keep no state of its own (ADR 0023 §1).
- **A CSRF token:** it adds server state to stop what Origin, Host and the JSON content type already stop in a browser.
- **Snapshot only:** it cannot press the button the done-when needs.
- **LIVE only:** a client sees nothing before a 15-minute run, and a talk has no backup.
- **Keep `Charts` for later:** a port with no screen to serve.
- **Keep §2(a):** a write that exists in the console first inverts ADR 0023 §3.

## Consequences

- **The one that hurts first: `proxy` trusts a header.** The console cannot tell whether a request came through the proxy. If a deployment leaves its port reachable, anyone can claim any identity, including a launcher's. The deployment notes must make the console reachable from the proxy only. When M11 verifies IAP's signed assertion, it also decides whether checking it is worth a JWT dependency.
- The release log lives in the operator's `.crew/`, as `release.json` does today. At L2 it is only as durable as that checkout, until an ADR puts it behind a port.
- There are two readers of the tower until the board's ticket probe moves to the logbook. That is the first ticket after M11, not part of it.
- A shape change in any source means a reader change and a regenerated golden. A major bump also means a regenerated snapshot, which takes a real `./go demo`.
- M11 rewrites today's other provenance forms (`merge.env` 79, `rework.env` 5) into the form the reader parses. A ci check NO-GOes any numeric policy key without it.
- At L0 the post mortem screen is empty until the owner records ADR 0044's post mortem, and the screen says so.
- `RISK_HIGH_PLATFORM` gains `console/*`, because it holds the write path and the auth check.

## Golden-rule check

`./go demo` gains no stage and no image. `./go console` runs in the existing workbench on loopback with no key. After `./go demo` it shows the chain to the rollback. `demo-console` presses go-for-launch at L0 through the real `promote.sh`. Nobody who clones needs Stitch, a proxy or an account. `jsonschema` is already in the lock. Passes.

## Open for the owner

Each with the recommendation this ADR is written on; a different answer amends the section named.

1. Drop ADR 0023 §2(a), answering the product role's question (§3). Recommended: yes, until a `./go` command answers it.
2. Fold M17's "dashboard" into M11, so there is one human surface and the board stays the explainer. Recommended: yes; this ADR does not decide it.
3. Build ADR 0031 §6, the emulator mirror, inside M11, so "the week's tickets" survive `down` (§2). Recommended: yes, §6 alone.
4. How long a superseded major is served (§2). Recommended: one tagged release; a decision, not a measurement.
5. Host the snapshot console publicly with `CONSOLE_AUTH=public` (§5). Recommended: yes, deployed by the owner, like the board.
6. Who builds the console (ADR 0023 §6): a frontend worker through the tower at L1, with the owner's key. Recommended: keep it; if a screen does not pass the gates within `REWORK_MAX_ATTEMPTS`, the session builds it and the README says so.
7. The default `by` for a CLI answer (§3). Recommended: `unauthenticated`; a container user name would look like an identity it is not.
8. Whether `proxy` mode verifies IAP's signed assertion (§5). Recommended: decide at M11, after reading IAP's current headers.
9. Timing (§8). Recommended: after M22 and M21, since a client wants links to its own GitHub; the plan's place after the experiment holds unless a design partner asks sooner.
10. The command name `./go logbook` (§2). Recommended: yes; `record` is taken.
