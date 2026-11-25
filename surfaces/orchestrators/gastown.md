# Gas Town + `lights-out` Merge-Queue Gate Recipe

Gas Town coordinates parallel worker branches ("polecats") and a merge queue ("refinery") over git worktrees. This recipe places `lights-out gates` (full container judge) and `lights-out-gates --light` (fast worktree check) directly in front of the merge queue so only verified branches land on `main`.

## 1. Worktree Preflight Before Queue Submission (`lights-out-gates --light`)

Inside a Gas Town worker worktree (with no Docker/Podman socket exposed to the worker), run the light tier before proposing the branch to the merge queue:

```sh
set -eu
base=$(git merge-base origin/main HEAD)
head=$(git rev-parse HEAD)

line=$(lights-out-gates --light BASE="$base" HEAD="$head" TEST_ON_HOST=1)
printf '%s\n' "$line"
case "$line" in
  "GO: gates-light"*) exit 0 ;;
  *) exit 1 ;;
esac
```

When Gas Town tracks a ticket spec file with `## Perimeter`, add `TICKET=<ticket-file>` so `lights-out-gates --light` refuses out-of-perimeter edits before the merge queue sees them.

## 2. Merge-Queue Verification Gate (`lights-out gates`)

In Gas Town's merge-queue verifier ("refinery") on a host with Podman or Docker, judge each candidate branch against the current `origin/main` target using `lights-out gates`:

```sh
set -eu
base=$(git merge-base origin/main HEAD)
head=$(git rev-parse HEAD)
export LIGHTS_OUT_OUT="${LIGHTS_OUT_OUT:-$PWD/.crew/gastown-gates}"

line=$(lights-out gates BASE="$base" HEAD="$head")
printf '%s\n' "$line"
case "$line" in
  "GO: gates"*) exit 0 ;;
  *) exit 1 ;;
esac
```

Only merge to `main` when `lights-out gates` exits `0` and stdout starts with `GO: gates`. On `NO-GO: gates — <gate>: <reason>`, return the branch to the worker queue with the failing gate line.

## 3. Optional Cross-Provider Jury & Provenance Attestation

- **Independent reviewer on `medium` / `high` risk branches:** Invoke `sh core/jury.sh select-env` (with `JURY_CROSS_PROVIDER=auto` or `JURY_CROSS_PROVIDER=strict`) to route high-stakes polecat diffs to a reviewer provider distinct from the author provider ([ADR 0130](../../docs/adr/0130-cross-provider-jury-and-outside-harness.md)).
- **Attest merged commits:** After merging, run `sh core/provenance.sh` ([core/provenance.py](../../core/provenance.py)) to emit an in-toto `Statement/v1` attestation binding the merged `gitCommit`, ADR digests, gate checks, and jury summary.
