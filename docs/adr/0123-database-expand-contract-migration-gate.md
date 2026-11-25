# ADR 0123 — Deterministic Database Expand/Contract Migration Safety Gate

- Status: proposed
- Date: 2026-11-24
- Milestone: M83
- Relates to:
  - [ADR 0084](0084-gates-under-the-shell-boundary.md) (a gate's `.sh` is a thin wrapper; its judgement is `core/gates/<x>.py`)
  - [ADR 0016](0016-risk-model-and-merge-policy.md) (`RISK_HIGH_MIGRATIONS` already routes a migration PR to a human)
  - [ADR 0008](0008-sql-migrations-expand-contract.md) (the expand/contract discipline this gate enforces mechanically)

## Context

The canary rollout (5% → 25% → 100%) and every rollback run **two revisions of the application against one database schema**. A migration is therefore only safe to ship in the same PR as the code if the *previous* revision keeps working after it ran. Review catches this only when a human reads the SQL: `RISK_HIGH_MIGRATIONS` routes the PR to review, but nothing inspects the DDL, and the dev-worker is an agent that will happily write `DROP COLUMN` in the same PR that stops reading the column.

The discipline (expand, then contract in a *later* deploy) is known; what is missing is a deterministic gate with no model and no database in the loop.

## Decision

### 1. One new gate: `gates/migration-safety.sh` → `core/gates/migration.py`

The wrapper follows the shape of every Python-backed gate: `set -eu`, `name=migration-safety`, `. ci/lib.sh`, `require_range`, `effective_policy "$out_dir/effective-policy.env"`, then `verdict core.gates.migration "$base" "$head" "$out_dir/effective-policy.env"`. It stays under the 30 code-line shell boundary and honours the one-line output contract: `GO: migration-safety[ — detail]` or `NO-GO: migration-safety — <reason>`, details on stderr, exit 0/1.

`python3 -m core.gates.migration <base> <head> <effective-policy.env>` reads `WORKLOAD` (the repository the range belongs to, default `.`) and `TICKET_FILE` (optional) from the environment.

### 2. Which files are judged

Files **added or modified** in `git diff --no-renames --name-status -z base head` whose path matches either:

- a glob of `RISK_HIGH_MIGRATIONS` in the effective policy (colon-separated, `fnmatch`, `*` crosses `/`, as in `core/risk.py`), or
- a default structural glob: `*migrations/*.sql`, `*migrations/*.py`, `*db/migrate/*`, `*prisma/migrations/*.sql`, `*flyway/*.sql`, `*liquibase/*.sql`.

Deleted files are never judged. A matched file whose type has no analyzer (anything but `.sql`, `.py`, `.rb`) is listed on stderr as *not analyzed*; it is never silently "clean" and never a NO-GO by itself.

### 3. What is rollback-unsafe (the rules)

Only the **forward** path is judged: Python `downgrade()` bodies, Django `RunSQL` reverse SQL and Rails `def down` blocks are skipped, because undoing an expand is exactly what a downgrade does.

| Rule id | Unsafe operation | Why the previous revision breaks |
| --- | --- | --- |
| `drop-table` | `DROP TABLE`, `DROP VIEW`, `DROP MATERIALIZED VIEW`, `DROP SCHEMA`, `DROP DATABASE`; `drop_table`, `DeleteModel` | it still reads or writes the object. Exempt: a temporary table (`DROP TEMPORARY TABLE`, a table created `TEMP`/`TEMPORARY` earlier in the same file, or `DROP TABLE IF EXISTS` on a `tmp_`/`temp_`-named table) |
| `drop-column` | `ALTER TABLE … DROP [COLUMN]`; `drop_column`, `RemoveField` | it still selects/inserts the column |
| `rename` | `RENAME TABLE`, `ALTER TABLE … RENAME …` (not `RENAME CONSTRAINT`), MySQL `CHANGE a b`; `rename_table`, `alter_column(new_column_name=…)`, `RenameField`, `RenameModel` | the old name no longer exists |
| `truncate` | `TRUNCATE [TABLE]` (not the `TRUNCATE(x, n)` function) | data the previous revision relies on is gone |
| `alter-type` | `ALTER COLUMN … TYPE`, `SET DATA TYPE`, T-SQL `ALTER COLUMN c <type>`, `MODIFY [COLUMN]`; `alter_column(type_=…)` | narrowing cannot be ruled out without the old type. Exempt: a change **to** unbounded text (`text`, `varchar`/`character varying` with no length, `longtext`, `mediumtext`), the one widening provable from the statement alone |
| `add-not-null` | `ADD COLUMN … NOT NULL` with no `DEFAULT` (and no `SERIAL`/`AUTO_INCREMENT`/`GENERATED`); `add_column(Column(nullable=False))` with no `server_default`; Django `AddField` with neither `null=True` nor `default` | the previous revision's `INSERT` omits the column and fails during canary or after rollback |
| `set-not-null` | `ALTER COLUMN … SET NOT NULL`, `MODIFY … NOT NULL` with no `DEFAULT`; `alter_column(nullable=False)` with no `server_default`; Django `AlterField(null=False)` with no `default` | the previous revision may still write `NULL` |

A table created (without `IF NOT EXISTS`) earlier in the **same file** did not exist for the previous revision, so ALTER/DROP/TRUNCATE rules on it do not fire.

### 4. No false positives from comments or literals

SQL is tokenized before any rule runs: `-- …` and (nested) `/* … */` comments are removed, single-quoted string literals (including `''` escapes and `E'…\'…'` backslash escapes) are blanked, double-quoted/backtick identifiers keep their shape but not their content; newlines are preserved so every finding carries its real line number. Python migrations are parsed with `ast`, so comments and docstrings can never match; calls are recognized by their final name (`op.drop_column`, `batch_op.drop_column`, `migrations.RemoveField`), and the string arguments of `execute`/`RunSQL`/`exec_driver_sql` (including `text("…")`) go through the same SQL scanner. A Python migration that does not parse is a NO-GO (fail closed). Ruby migrations get `#` comments stripped and a small set of Rails operations.

### 5. Stage-2 contract cleanup is allowed only when declared and anchored

A range containing unsafe operations is GO **only** when it declares the contract phase **and** names the expand step that already shipped:

- `TICKET_FILE` or any commit message in `base..head` contains a `Contract-After: <ref>` line, or a `## Contract migration` section whose body names a `<ref>`;
- a `<ref>` is a commit (7–40 hex digits) that is an ancestor of `base`, or a migration path/file name that exists in the tree at `base`. Prior to `base` is the deterministic stand-in for "already deployed": the gate runs on a PR whose base is the mainline.

A declaration with no resolvable `<ref>` is a NO-GO naming what it tried; it never degrades into an allow. When the declaration is accepted the findings are still printed on stderr, marked `allowed (contract after <ref>)`, and the GO detail says so.

### 6. Verdict

- No migration file in the range → `GO: migration-safety — no migration files changed`.
- Only safe operations → `GO: migration-safety — N migration file(s), expand-safe`.
- Unsafe operations without an accepted declaration → NO-GO; the reason names the first finding as `<file>:<line> <rule>`, the details list every finding as `<file>:<line>: <rule>: <statement>` with the fix ("split into expand now, contract in a later PR with `Contract-After: <ref>`").
- An unreadable range, policy or ticket → NO-GO.

### 7. Perimeters

`core/gates/migration.py` and its unit tests name no vendor, no workload path, no model; the unit tests use synthetic `service/db/migrations/…` paths. The real bundled-workload migration pattern is exercised only by `ci/tests/test_migration_gate_e2e.py`, which sits outside `ci/app-free.sh`. No role prompt names the gate (`ci/prompts.sh`). Wiring the gate into `Makefile`, `./go` and the roadmap is the integrator's, not this ADR's.

## Provenance

| Number | Where it comes from |
| --- | --- |
| `7–40` hex digits for a commit reference | Provenance, external: git's default abbreviation length is 7 (`core.abbrev`), and a full SHA-1 object id is 40 hex digits. A shorter token is never taken for a commit. |
| `3`-character minimum for a reference token | Provenance, decision: a noise filter, not a safety threshold. A token is only ever accepted when it names an existing commit or an existing migration file at the base, so a shorter word could not resolve; the minimum merely skips words such as `of` and `a` in a prose section. |
| `30` code lines for `gates/migration-safety.sh` | [ADR 0052](0052-where-shell-is-allowed.md) §4 and `ci/shell-boundary.sh` (`limit=30`), extended to `gates/` by ADR 0084 §5. The wrapper is 14. |
| Canary `5% → 25% → 100%` | [ADR 0013](0013-launchpad-canary-verify-rollback-at-l0.md): the staged weights during which two revisions share one schema. |
| `120` / `200` characters of a statement or declaration on stderr | Provenance, decision: display truncation only, never part of a verdict. |

## Consequences

- A PR that drops, renames, truncates or tightens in the same change as its expand is stopped before review, with the line to fix.
- Expand/contract becomes two PRs with a machine-checked link between them.
- Conservative by design: type changes that cannot be proven widening are NO-GO until declared as a contract step; this costs one trailer line.
- Not covered: data-dependent hazards (long locks, backfills), non-SQL/Python/Ruby migration formats, and schema state that only a live database knows.

## Alternatives considered

- **Run the migration against a scratch database and replay the previous revision's tests.** Sound but needs an engine and a schema per workload; this gate is the cheap, socket-free first line.
- **Let the model reviewer judge DDL.** Non-deterministic; rejected for the same reason as every other gate (ADR 0084).
- **Allow contract steps on a bare `Contract` label.** Rejected: without a reference to the expand it is a bypass.
