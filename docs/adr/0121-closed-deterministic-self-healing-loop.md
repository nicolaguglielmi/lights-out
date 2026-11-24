# ADR 0121 — Closed deterministic self-healing loop

- Status: accepted
- Date: 2026-11-24
- Milestone: M81 (Closed Deterministic Self-Healing Loop)
- Builds on: ADR 0020 §3 (triage proposes, nothing acts), ADR 0044 (incident and post mortem), ADR 0080 (stage `slo` entries), ADR 0109 (continuous ops loop)

## Context

ADR 0109 closed the loop from a rolled-back release to an incident document, from an external alert to `core/triage.sh`, and from an incident to an opt-in post mortem. Three seams still need a person:

1. **Evidence is thin.** `on_rollback()` copies a log excerpt only when `LOGS` or `.crew/<ticket>/release.log` exists, so most automatic incidents hold `logs: []`. The per-stage numbers sit as text in `release.stages[].numbers`, which the `ops-triage` role has to re-read. A log that is supplied is copied verbatim, so a leaked `*_TOKEN=` line goes straight into incident JSON, and from there into a model prompt.
2. **Post-mortem tickets carry no perimeter.** `core/postmortem.sh` files every action item without a `## Perimeter` ("a human adds one before it is assigned"). The rolled-back merge commit already says which files were involved, and `core/map.py` already knows which tests cover them. Nothing connects the two.
3. **A triage proposal of `rollback` is never executed.** ADR 0020 §3 keeps the role without a tool that acts, and that stays true. But a deterministic policy gate could execute a proposal that is checked against the release record, using only the ports that already roll back (`adapters/$DELIVERY/rollback.sh`) or flip the kill switch (`adapters/$FLAGS/set.sh`).

Every behaviour below is opt-in and off by default. With no new variable set, every existing test, `./go demo`, `./go demo-rollback`, `./go e2e` and every existing artefact byte stays as it is.

## Decision

### 1. Bounded log and exemplar capture (`core/ops_loop.py`)

**Log source order** in `on_rollback()`, first hit wins:

1. `LOGS` (explicit file, as today);
2. `.crew/<ticket>/release.log` (as today);
3. `ROLLBACK_LOG_SOURCE` (a file path named by the operator);
4. `.crew/canary.log`.

Sources 1 and 2 keep their current contract (the file's lines, in order, no tail, no truncation, because the operator or the launchpad chose that excerpt). Sources 3 and 4 are *collected*: the last `MAX_LOG_LINES` lines, each cut to `MAX_LOG_LINE_CHARS` characters (the cut is marked with a trailing `...` that counts inside the cap). A missing file of source 3 or 4 is not an error: `logs` stays `[]` exactly as before, nothing is fabricated.

**Redaction applies to every source** before the lines are written into the incident document. A line is rewritten, never dropped, so line anchors (`log:<n>`) in a post mortem keep their numbering. The patterns, and nothing broader:

| Shape | Rewritten to |
| --- | --- |
| `<NAME>_KEY=<value>`, `<NAME>_TOKEN=<value>`, `<NAME>_SECRET=<value>` (name case-insensitive, value is a quoted string or a run of non-space characters) | `<NAME>_KEY=[REDACTED]` |
| JSON member `"<name>_key": "<value>"` (same three suffixes) | `"<name>_key": "[REDACTED]"` |
| `Authorization: Bearer <value>` and `Authorization: Basic <value>` (case-insensitive) | `Authorization: Bearer [REDACTED]` |

Redaction is a defence in depth for log excerpts, not a secret scanner: it names three suffixes and one header because those are the shapes this repository's own adapters and webhook use (`ALERT_WEBHOOK_TOKEN`, `Authorization: Bearer`, the provider key variables). A bare high-entropy string is not guessed at.

**Exemplars.** The incident document gains one key, `exemplars`, derived from `record["stages"]` with no model and no clock:

```json
{
  "failing_stage_pct": 25,
  "stages": [
    {"pct": 5,  "verdict": "GO", "error_rate": null, "p95": null, "traffic": {...}},
    {"pct": 25, "verdict": "NO-GO: ...",
     "error_rate": {"canary": 0.5, "stable": 0.0, "delta": 0.5, "max_delta": 0.01},
     "p95": null}
  ],
  "breach": {"pct": 25, "query": "error_rate", "delta": 0.5, "max_delta": 0.01}
}
```

- `failing_stage_pct` is the `pct` of the first stage whose verdict does not start with `GO`, else `null`.
- A stage's numbers come from its `slo` list (ADR 0080 §1); a stage recorded before ADR 0080 has only `numbers` text, which is parsed with the same reader `core/releases.py` uses (`slo_of`), so the two never disagree. `latency_p95` is exposed under the key `p95`.
- `breach` is the first `slo` entry, in stage order, whose `delta` is above a known `max_delta`; `null` when no threshold is known. No threshold is invented here: `max_delta` is whatever the record carries.
- Nothing is removed from or rewritten in the existing keys: `kind`, `name`, `window`, `release`, `alerts`, `logs`, `chain` are exactly `core/incident.py`'s output, so `core/postmortem.py validate` (which ignores unknown keys) and every existing assertion hold. The document stays deterministic: same record and logs, same bytes.

### 2. Post-mortem action-ticket perimeters (`core/ops_loop.py`)

`derive_action_perimeters(ticket_paths, chain, repo_dir, map_file=None, env=None)` returns one result per ticket path, in input order:
`{"ticket": <path>, "status": "injected" | "kept" | "needs-perimeter", "perimeter": [<glob>...], "readiness": None | "spec-ready" | "needs-perimeter" | "needs-spec", "reason": <text or None>}`.

- **Changed files**: for the rolled-back merge `chain["merge"]` (a hex sha, validated before it reaches git), `git diff-tree --no-commit-id --name-only -r --no-renames <first parent> <merge>` (a root commit uses `--root`). Argv list, no shell. Paths under `tickets/` and `.crew/` are the tracker's and the run's own state and are dropped.
- **Test edges**: when `map_file` names a readable map database (`core/map.py`'s sqlite, read-only), the tests joined to the changed files by an `edge` of kind `tests`. Without a map: the conventional neighbours of each changed file (`tests/test_<name>.py`, `<dir>/tests/test_<name>.py`, `<dir>/test_<name>.py`) that exist in `repo_dir`. Either way the result is sorted and de-duplicated, so the same commit gives the same perimeter.
- **Injection**: only into a ticket with no `## Perimeter` section (`core/spec.py`'s `section()` is the judge). The section is inserted before `## Notes` when the ticket has one, else appended. The new text is checked (the section must hold at least one `- ` path, every path relative, no `..`, no backtick or newline) *before* anything is written, then replaced atomically (temp file, `os.replace`). A ticket that already has a perimeter is `kept`, byte for byte. No chain, no merge, or no derivable path is `needs-perimeter` and the file is not touched: a guess is worse than the human step the ticket text already asks for.
- **Readiness**, only when `AUTO_PROMOTE_POSTMORTEM_TICKETS=on` (default `off`, then `readiness` is `None`): `core/spec.check()` runs on the resulting text. `None` from it is `spec-ready` (eligible for assignment); a perimeter that is missing or empty is `needs-perimeter`; any other spec flaw (for example an acceptance criterion without Given/When/Then) is `needs-spec`, with the flaw in `reason`. This function only reports. Moving a ticket to `spec-ready` in a tracker, and assigning it, stay with the tower and the spec gate; the readiness is an input to them, never a bypass of `core/spec.py`.
- `on_rollback()` calls it, when `AUTO_POSTMORTEM_ON_ROLLBACK=on` and the post mortem filed tickets that exist under `TICKETS_DIR` (default `tickets`), and reports the results under `postmortem["perimeters"]`. The repository is `REPO_DIR` (default `.`) and the optional map database `MAP_FILE`. When no local ticket file exists (a remote Tracker) the key is absent.

### 3. Policy-gated alert to rollback mitigation (`core/triage.py`, `core/webhook.py`)

`triage.mitigate(triage_result, release_record_path, env)`; `triage_result` is the `incident.json` document `triage.check` writes (or its path).

**Off by default.** Unless `AUTO_MITIGATE_ON_TRIAGE=on` it returns `{"status": "disabled"}` before touching anything: no read, no process, no audit line.

**Checks, in this order; the first failure refuses and is audited:**

1. the proposal's `action` is `rollback` (a `hold` proposal never mitigates);
2. the proposed `version` is the alert's version (already enforced by `judge`, re-checked here because this function may be called with any document);
3. the version is the canary: the release record at `release_record_path` names it as `version`, with verdict `held` or `in-progress`; or, with no such record, `CANARY_VERSION` in `env` names it. A version equal to the record's `stable` is refused whatever else is true: mitigation only ever moves traffic *to* stable.
4. a record already `rolled-back` for that version is `already-rolled-back`: nothing runs (idempotent re-delivery of an alert).

**Actions.** `MITIGATE_ACTIONS` is a comma list over `{rollback, kill-switch}`, default `rollback`. The kill switch is a larger blast radius (it turns the workload's feature off for every version), so it is opt-in:

- `rollback` runs `sh adapters/$DELIVERY/rollback.sh`;
- `kill-switch` runs `sh adapters/$FLAGS/set.sh $WORKLOAD_KILL_SWITCH off`.

Both through `subprocess.run` with an explicit argv list, `shell=False`, stdin closed, a timeout, output captured and never stored. `DELIVERY`, `FLAGS` and `WORKLOAD_KILL_SWITCH` must be plain names (letters, digits, `.`, `_`, `-`, not starting with `-` or `.`), and the script must exist under `adapters/`; anything else is a refusal. No agent command, no string from the alert or the role reaches an argv. The role still has no tool that acts (ADR 0020 §3): the proposal is data that a policy, which a person switched on, judges.

**Audit.** Every decision appends one JSON line to `MITIGATIONS_LOG` (default `.crew/mitigations.jsonl`): `{"ts", "alert", "version", "action", "verdict", "rc"}`. `alert` is the alert's headline, else its title, else its version; `action` is `rollback`, `kill-switch` or `none` (a refusal); `verdict` is `applied`, `failed`, `refused` or `skipped`; `rc` is the exit status or `null` when nothing ran. The file is opened append-only, one `write` per line, and never rewritten or truncated by this code. No environment value and no adapter output is written to it, so it cannot carry a secret.

**Verdict update and the loop.** After a successful `rollback` on a record whose verdict is `held` or `in-progress`, the record is rewritten atomically as `rolled-back` with `reason` set to `mitigated on triage of <alert>` and the line appended to `RELEASES_LOG` in `core/releases.py`'s shape (`{"kind": "release", "at", "ticket", "record"}`). If `AUTO_INCIDENT_ON_ROLLBACK=on`, `ops_loop.on_rollback()` then runs, so the incident (and, if opted in, the post mortem) follows without a person, and its failure never undoes the mitigation. A successful `kill-switch` alone changes no verdict.

**Webhook.** `core/webhook.py`'s dispatch, after `core/triage.sh` exits 0 for an alert, reads `<TRIAGE_OUT>/<alert file stem>/incident.json` and, only when `AUTO_MITIGATE_ON_TRIAGE=on`, calls `mitigate`, adding `mitigation` to that alert's dispatch result. With the variable unset the dispatch result is byte-identical to ADR 0109's. `python3 core/triage.py mitigate <incident.json> [record]` is the same call for a person or a later `./go` target.

### Thresholds and provenance

| Name | Value | Provenance |
| --- | --- | --- |
| `MAX_LOG_LINES` | 200 | Decision of this ADR (a judgement, not a measurement): a bounded tail of a few screens, enough context around a canary rollback, and small enough that the whole tail fits the 64 KiB webhook payload cap of ADR 0109 (`PAYLOAD_MAX_BYTES = 65536`) at the per-line cap below. |
| `MAX_LOG_LINE_CHARS` | 320 | Derived: 65536 bytes / 200 lines = 327 bytes per line, rounded down to 320 characters so that a full tail of single-byte lines is under the cap. Characters, not bytes: a multi-byte line may exceed it, the cap bounds the shape, not the encoding. |
| `MITIGATE_TIMEOUT_S` (default) | 120 | Derived: the delivery adapters wait up to `READY_TIMEOUT` (default 60 in each `adapters/*/env.sh`) for the router to report weight 0, and `make kill-switch` polls the same value; the timeout is that wait plus an equal margin for process start and the adapter's own preamble. Overridable per run through `MITIGATE_TIMEOUT_S`. |
| Revision shape | 7 to 64 hex digits | git's own range: 7 is the default `core.abbrev` (shortest name git prints), 64 is a full SHA-256 object name (a full SHA-1 name is 40). A merge reference outside it never reaches git. |

No other numeric threshold is introduced. Perimeter size is deliberately unbounded: a very large merge gives a very large perimeter, which `core/spec.py` and the human who reads the ticket judge, rather than a cut-off picking arbitrary files out of it.

## Consequences

- An automatic incident now carries the tail of the canary log (when one exists) and the failing stage's numbers in structured form, with secrets rewritten. The post-mortem role cites `log:<n>` anchors into a log that cannot leak a key.
- A post-mortem ticket arrives with a derived perimeter and a readiness line instead of "a human adds one", yet it still goes through `core/spec.py` before anything assigns it.
- A person can switch on one variable and have a triage `rollback` proposal executed through the same two ports a human would use, only for the live canary, never for stable, with an append-only audit line per decision. The default is that nothing changes.
- `core/` stays vendor-free, app-free and model-free; no shell file grows; `core/triage.sh`, `core/postmortem.sh`, `core/launchpad.sh` and `core/releases.py` are untouched.

## Verification

- `core/tests/test_self_healing.py` — specification tests for the three sections: redaction shapes, tail bounds, exemplar shape, perimeter derivation against a temporary git repository, readiness values, mitigation refusals, the adapter calls, the audit lines and the disabled default.
- `core/tests/test_ops_loop.py` — the existing ADR 0109 assertions plus the compatibility cases for logs, exemplars and the webhook dispatch with the variables unset.
- `ci/tests/test_self_healing_e2e.py` — a subprocess rollback with a canary log carrying secrets, a real git merge giving a perimeter, and a mitigation that runs stub port adapters.
- The meta-gates `ci/vendor-free.sh`, `ci/app-free.sh`, `ci/model-free.sh` and `ci/shell-boundary.sh`.
