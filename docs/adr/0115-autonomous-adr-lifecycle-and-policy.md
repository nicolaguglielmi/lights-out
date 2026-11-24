# ADR 0115 — Autonomous decision-record lifecycle and the RISK_REQUIRE_ADR policy

- Status: proposed
- Date: 2026-11-24
- Milestone: M75 (Autonomous ADR Lifecycle & Policy Enforcement)
- Amends: ADR 0016 (`classify()` gains an `adr_required` key; no existing
  key or value changes), ADR 0037 §5 (one more overridable key), ADR 0021
  (the architect flags a structural change that records no decision)

## Context

The repository's own rule is "an ADR for every structural choice, short,
with the alternatives rejected and why", and "a numeric threshold in an
ADR always states where it comes from" (CLAUDE.md). Both rules are kept by
hand today: nothing numbers a new record, nothing checks that one has its
sections, and nothing notices when a migration, an authentication path, a
new lockfile or a platform surface lands with no record at all. The farm
roadmap (M72-M89) asks the agents to keep this lifecycle on their own, on
this repository and on an attached one, without a seventh role
(`ci/roles.sh`) and without turning every repository that never kept
records red on upgrade.

The risk classifier (ADR 0016) already knows which ranges are high risk,
and the effective policy (ADR 0037 §5) already lets a workload tighten the
platform's defaults. What is missing is a library that drafts and checks a
record, one policy key that says when a record is required, and a gate that
enforces it.

## Decision

### 1. `core/adr_gov.py`, stdlib only

- `next_number(adr_dir)` scans the `NNNN-*.md` files in `adr_dir` and
  returns the next number as a zero-padded four-digit string, `0001` for an
  empty or missing directory. Gaps are not reused: the next number is one
  past the largest, because a number once published may be referenced.
- `scaffold(adr_dir, title, context, decision, alternatives, consequences,
  provenance=None, status="proposed", date=None)` writes
  `<adr_dir>/<NNNN>-<slug>.md` with the sections `# ADR NNNN — <title>`,
  Status, Date (today, UTC, when not given), Context, Decision,
  Alternatives rejected, Consequences and Provenance, and returns its path.
  `alternatives` and `provenance` may be a string or a list (one bullet
  each). It never overwrites a file, and it refuses, writing nothing, a
  record that `validate_adr` would reject: a drafted record is a valid one.
- `validate_adr(text)` returns a list of defects, empty when the record is
  valid:
  - each of `## Context`, `## Decision` and `## Alternatives…` (any heading
    that starts with that word) is present and has content before the next
    heading;
  - every numeric threshold statement carries provenance (§2).
- `check_range_adr(repo, base, head, policy)` judges the git range
  `base..head` of `repo` against the policy's `RISK_REQUIRE_ADR` (§3) and
  returns `Verdict(go, reason, details)`.

### 2. What a numeric threshold statement is

A line outside fenced code and headings is a threshold statement when, once
dates, ADR numbers and paths, `§` references, versions and list ordinals
are set aside, it still holds a number and either a threshold word
(threshold, limit, cap, ceiling, floor, budget, margin, timeout, quota,
baseline, max, maximum, min, minimum, "at most", "at least", "no more
than", "no less than", "no fewer than", "up to", "exceeds") or a
`KEY=<number>` assignment. It carries provenance when its paragraph says
where the number comes from (provenance, because, measured, measurement,
derived, source, rationale, justified, justification, or a reference to
another ADR), when the number also appears in the record's own
`## Provenance` section, or when it is itself inside that section. The
rule reads words, not meaning: it cannot tell a good reason from a bad one,
only that a reason was given, which is the repository rule's own bar.

### 3. `RISK_REQUIRE_ADR` in `policy/merge.env`

One key, three values, in order of strictness:

| Value | A record is required when | What satisfies it |
|---|---|---|
| `off` (default) | never | nothing to satisfy |
| `high` | `classify()` says `level=high` | a new or updated valid record under `docs/adr/` or `adr/` in the range, or a commit message in the range naming `ADR-NNNN` or `ADR NNNN` where every record so named exists at `head` as `docs/adr/NNNN-*.md` or `adr/NNNN-*.md` and is valid |
| `structural` | `level=high`, or the range adds a package manifest, lockfile, container or compose manifest | a new or updated valid record under `docs/adr/` or `adr/` in the range itself |

`core/policy.py` accepts it as an override with exactly those three values;
a value lower in the order than the default is looser, reported on stderr
and applied, like every other key. The platform default may be absent (a
policy file written before this ADR): absent is `off`, so no existing
policy is refused for lacking it.

### 4. `classify()` exposes `adr_required`

`core/risk.py` `classify(numstat_lines, policy, added_paths=())` adds one
boolean key, `adr_required`, true exactly when the first column of §3 holds
for the policy's `RISK_REQUIRE_ADR`. `added_paths` is the range's added
files (`git diff --diff-filter=A --name-only`), used only by `structural`;
callers that do not pass it keep their behaviour. `level`, `decision`,
`signals` and `size` are unchanged. An unknown `RISK_REQUIRE_ADR` value is
a `ValueError`, so a malformed policy never silently disables the rule.
`core/gates/risk.py` passes the added paths and prints `adr_required` in its
stderr summary; risk stays a routing decision, never a verdict.

### 5. `gates/adr-check.sh`

A standard range gate under the shell boundary (ADR 0084):
`require_range`, `effective_policy`, then
`verdict core.gates.adr_check "$base" "$head" "$out_dir/effective-policy.env"`.
One stdout line, `GO: adr-check` or `NO-GO: adr-check — <reason>`, details
on stderr. GO when `RISK_REQUIRE_ADR=off`, when no record is required, or
when the requirement is satisfied. When a record is required, NO-GO when
it is missing, when a record in the range fails `validate_adr` (each defect
on stderr), or when a commit message names a record that does not exist at
`head` or fails `validate_adr`. Records are judged only when one is
required: a repository that has not opted in, or a range that needs none,
is never held up by an old record's shape. NO-GO, too, when the range or
the policy cannot be read. Wiring it into the `GATES` list and
the entrypoints is the integrator's slice, not this one.

### 6. The roles

No seventh role. The architect flags a structural change that arrives with
no decision record, and requests changes when a record in the diff leaves
out its context, decision or rejected alternatives. The dev-worker writes or
updates a record inside the ticket's declared files when the ticket asks
for one. Neither prompt names the gate or its tools (ADR 0020 §6).

## Alternatives rejected

- **A seventh "decision-keeper" role.** Six is a scope constraint
  `ci/roles.sh` enforces; the work fits the architect, who already judges
  structure, and the dev-worker, who already writes the files a ticket
  declares.
- **Required by default.** Every attached repository without a `docs/adr/`
  would turn red on upgrade, for a rule its owner never chose.
- **Folding the check into the risk gate.** Risk is routing, never pass or
  fail (ADR 0016); a missing record is a verdict, so it gets its own gate.
- **A model judging the record's quality.** Not deterministic, not
  reproducible on a cold run, and it would put a model call inside a gate.
- **Every number counts as a threshold.** Dates, ADR numbers, section
  references and versions would drown the real defects; §2 sets them aside.
- **Taking a commit-message reference on trust.** A message naming a
  record that was never written would pass; the reference is resolved
  against the tree at `head` and the record it names must be valid.
- **Commit-message references also satisfy `structural`.** Then
  `structural` would be no stricter than `high` for the paths both cover;
  the reference stays the `high` level's lighter path.

## Consequences

- A workload opts in with one line in its own `policy.env`, under a
  provenance comment like every override.
- `risk.json` grows one boolean; readers that index known keys are
  unaffected.
- The record format is checked by words, so a record can satisfy the check
  and still be thin; the architect's read is the second line.
- `gates/adr-check.sh` is a new basename, so `ci/prompts.sh` now forbids
  the word in role prompts, as it does for every gate.

## Provenance

- Four-digit, zero-padded numbering — measurement: every record in
  `docs/adr/` from `0001` to `0111` uses it.
- `RISK_REQUIRE_ADR=off` as the default — decision: opting in is the
  workload owner's call (§3, alternatives).
- The manifest and lockfile names in §3 — external: the file names each
  ecosystem's own tooling documents (npm, pnpm, yarn, bun, pip, Poetry, uv,
  Pipenv, Go modules, Cargo, Maven, Gradle, Bundler, Composer, Compose).
- The shell gate's size — external to this ADR: the 30 code-line boundary
  of ADR 0052 §4 and ADR 0084 §5.

## Tests, from this ADR

`core/tests/test_adr_gov.py` (the library, the policy key, `adr_required`)
and `ci/tests/test_adr_lifecycle.py` (the gate end to end on a scratch
repository, its output contract, its shell size, and this record validating
under its own rule) were written from this ADR before the code.
