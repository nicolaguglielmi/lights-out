# OpenHands + `lights-out` Verification Recipe

OpenHands manages multi-step coding sessions inside sandboxes and opens pull requests for completed tasks. This recipe wires `lights-out gates` (or `lights-out-gates --light` inside nested agent sandboxes without a container engine) as the deterministic verification gate before OpenHands marks a task ready to merge.

## 1. Install the Gate CLI in the OpenHands Environment

In a host runner or CI job with Podman or Docker (full 8-gate container judge):

```sh
curl -fsSLo /usr/local/bin/lights-out https://raw.githubusercontent.com/nicolaguglielmi/lights-out/v0.9.0/lights-out
chmod +x /usr/local/bin/lights-out
```

Inside an engine-free OpenHands sandbox (zero-container light tier via `lights-out-gates`):

```sh
uv tool install lights-out-gates
```

Ensure the repository contains `.lights-out/workload.env` (generated once via `lights-out init --apply` or `./go init`).

## 2. Pre-Commit / Sandbox Check (`lights-out-gates --light`)

Before OpenHands commits or pushes a task branch from inside its sandbox, run the zero-container light tier on staged or committed changes:

```sh
set -eu
line=$(lights-out-gates --light STAGED=1 TEST_ON_HOST=1)
case "$line" in
  "GO: gates-light"*)
    printf '%s\n' "$line"
    ;;
  *)
    printf 'Verification blocked: %s\n' "$line" >&2
    exit 1
    ;;
esac
```

To verify a committed branch diff against `origin/main` instead of `STAGED=1`:

```sh
base=$(git merge-base origin/main HEAD)
lights-out-gates --light BASE="$base" HEAD=HEAD TEST_ON_HOST=1
```

## 3. Full 8-Gate PR Verification (`lights-out gates`)

When OpenHands hands the task branch to your host runner or PR pipeline, run the full 8-gate container suite (`risk`, `secrets`, `test-weakening`, `deps`, `sast`, `test`, `mutation`, `test-proof`):

```sh
set -eu
base=$(git merge-base origin/main HEAD)
head=$(git rev-parse HEAD)
export LIGHTS_OUT_OUT="${LIGHTS_OUT_OUT:-$PWD/.crew/openhands-gates}"
line=$(lights-out gates BASE="$base" HEAD="$head")
printf '%s\n' "$line"
case "$line" in
  "GO: gates"*) exit 0 ;;
  *) exit 1 ;;
esac
```

## 4. Feeding Failures Back & Cross-Provider Review

- **Rework on `NO-GO`:** Forward stderr diagnostics (plus `$LIGHTS_OUT_OUT` gate logs) to OpenHands as the next task feedback turn so the agent fixes failing tests, SAST findings, or surviving mutants before re-running `lights-out gates`.
- **Cross-provider jury ([core/jury.py](../../core/jury.py)):** On `medium` or `high` risk changes, resolve a reviewer provider distinct from the OpenHands coding provider via `sh core/jury.sh select-env` (with `JURY_CROSS_PROVIDER=auto` or `JURY_CROSS_PROVIDER=strict`) before approving the PR.
