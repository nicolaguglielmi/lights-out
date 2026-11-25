# Paperclip + `lights-out` Approval Gate Recipe

Paperclip coordinates agent tasks with approval stages and token/cost budgets, but leaves numeric code verification to external hooks. This recipe attaches `lights-out gates` and `lights-out-gates --light` as deterministic verification steps before a Paperclip task leaves an approval gate.

## 1. Prerequisites

- Target repository initialized with `.lights-out/workload.env` (`lights-out init --apply`).
- For host-level verification with Podman or Docker: `lights-out` wrapper on `PATH`.
- For lightweight agent-worker preflight without a container engine: `lights-out-gates` installed via `uv tool install lights-out-gates` (or `uvx lights-out-gates --light`).

## 2. Paperclip Pre-Approval Light Check (`lights-out-gates --light`)

Run the fast, zero-container gate inside the worker checkout before Paperclip submits work to its approval stage, writing machine-readable verdict state into `GATES_OUT`:

```sh
set -eu
base=$(git merge-base origin/main HEAD)
head=$(git rev-parse HEAD)
gates_out="${GATES_OUT:-.gates-out}"

rc=0
line=$(lights-out-gates --light BASE="$base" HEAD="$head" TEST_ON_HOST=1 GATES_OUT="$gates_out") || rc=$?
printf '%s\n' "$line"

# Inspect verdict.env (GATES, FAILED, RISK_LEVEL, RISK_DECISION)
cat "$gates_out/verdict.env"
exit "$rc"
```

If a Paperclip ticket markdown file carries `## Acceptance criteria` and `## Perimeter`, pass `TICKET=<path-to-ticket.md>` to enforce `perimeter` and `spec` alongside `risk`, `test-weakening`, `test`, and `test-proof`.

## 3. Full 8-Gate Verification at Paperclip Approval Stage (`lights-out gates`)

Configure Paperclip's verification hook on PR/branch handoff to execute the full containerized 8-gate judge:

```sh
set -eu
base=$(git merge-base origin/main HEAD)
head=$(git rev-parse HEAD)

line=$(lights-out gates BASE="$base" HEAD="$head")
printf '%s\n' "$line"
case "$line" in
  "GO: gates"*) exit 0 ;;
  *) exit 1 ;;
esac
```

## 4. Risk-Aware Approval & Cross-Provider Jury Routing

Use `RISK_LEVEL` and `RISK_DECISION` from `verdict.env` together with [core/jury.py](../../core/jury.py) to drive Paperclip's approval escalation:

```sh
# Require a distinct reviewer vendor on medium/high risk PRs when 2+ provider keys exist:
JURY_CROSS_PROVIDER=auto RISK_LEVEL=medium AUTHOR_PROVIDER=gemini \
  sh core/jury.sh select-env
```

- When `RISK_LEVEL=low` and `lights-out gates` returns `GO: gates — risk low, auto-merge`, Paperclip can advance through automated approval.
- When `RISK_LEVEL=medium` or `RISK_LEVEL=high`, require both `GO: gates` and a cross-provider review (or human sign-off for `RISK_HIGH_PLATFORM` / `high` risk changes).
- Enforce per-ticket and daily USD caps consistently using `SPEND_BUDGET_TICKET_USD` and `SPEND_BUDGET_DAY_USD` (`policy/rework.env`, `core/spend.py`).
