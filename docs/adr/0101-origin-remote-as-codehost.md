# 0101 — Repository `origin` remote as CodeHost

- **Date**: 2026-11-24
- **Status**: Accepted
- **Milestone**: M68
- **Amends**: [ADR 0024 §1](0024-ports-and-adapters.md), [ADR 0037 §1](0037-the-workload-is-a-repository.md), [ADR 0043 §1–§2](0043-github-code-host-and-attached-release.md), [ADR 0058 §8](0058-outside-adapters-and-other-cis-as-surfaces.md)

## Context

Attaching a repository (`./go attach <path>`) clones the source repository into `.attached/` and checks its `.lights-out/` contract, yet leaves `CODE_HOST` at the profile default (`gitea`). Users targeting a remote forge must manually set `CODE_HOST` and repository coordinates in `.env` or on the command line even though `git -C <src> remote get-url origin` already carries that identity.

Three constraints shape the design:

1. **Vendor-free `core/` (`ci/vendor-free.sh`, ADR 0024 §1)**: `core/` and `core/tests/` must never name CodeHost vendors (`github`, `gitlab`, `gitea`) or vendor-specific environment keys.
2. **Source vs. clone `origin` (`core/attach.sh`, ADR 0037 §1)**: `core/attach.sh` clones `<src>` into `.attached/`, so inside the clone `origin` points to `<src>` (a local host directory that is not mounted inside the workbench container). Remote detection must inspect `<src>`'s `origin` before or alongside cloning.
3. **Remote GitLab parity (`adapters/gitlab/`, ADR 0058 §8)**: `adapters/gitlab/` previously supported only the local `gitlab-conformance` container (`root/workload` on `$PLANE_HOST:8929` with an embedded token push URL) and had no env-scoped git `credential.sh` helper or remote-project mode.

## Decision

1. **Vendor-free URL parser and dynamic probe runner (`core/remote.py`)**:
   - Standard-library only, strictly vendor-free and app-free.
   - Parses `git@host:owner/repo(.git)`, `ssh://git@host[:port]/path(.git)`, `https://host[:port]/path(.git)`, and `http://host[:port]/path(.git)` into normalized fields (`REMOTE_URL`, `REMOTE_SCHEME`, `REMOTE_HOST`, `REMOTE_PORT`, `REMOTE_PATH`, `REMOTE_OWNER`, `REMOTE_REPO`), requiring at least two path segments (`owner/repo`, supporting nested namespaces).
   - Strips embedded userinfo credentials (removing all userinfo from `http`/`https` URLs and passwords from `ssh`/SCP URLs) and rejects any adapter-emitted secret key (`*_TOKEN`, `*_KEY`, `*_SECRET`, `*_PASSWORD`) so tokens never leak into `.crew/remote.env`.
   - Discovers `adapters/*/detect-remote.sh` in deterministic sorted order, passes the seven `REMOTE_*` environment variables, and accepts the first script exiting `0` with valid `KEY=VALUE` lines.
   - CLI: `python3 core/remote.py detect <repo> [--remote origin] [--adapters adapters] [--out .crew/remote.env]`. When `<repo>` has no matching remote, a local path, an unsupported URL, or no matching adapter, removes any stale `--out` file and exits `0` without output.

2. **Dynamic adapter probes (`adapters/*/detect-remote.sh`)**:
   - `adapters/github/detect-remote.sh`: matches `github.com` or `${GITHUB_HOST:-}` with a strict two-segment `owner/repo` path; emits `CODE_HOST=github` and `GITHUB_REPO=<owner>/<repo>`.
   - `adapters/gitlab/detect-remote.sh`: matches `gitlab.com` or `${GITLAB_HOST:-}` (including nested group paths); emits `CODE_HOST=gitlab`, `GITLAB_URL=https://<host>[:<port>]` (preserving non-default HTTPS ports while ignoring SSH ports), and `GITLAB_PROJECT_PATH=<path>`.
   - `adapters/gitea/detect-remote.sh`: matches local plane Gitea (`$PLANE_HOST:$CODE_HOST_PORT` or `localhost:$CODE_HOST_PORT`); emits `CODE_HOST=gitea`.

3. **Remote GitLab mode & credential helper (`adapters/gitlab/`)**:
   - When `GITLAB_PROJECT_PATH` is set (remote mode), `adapters/gitlab/env.sh` refuses missing, empty, or placeholder (`replace-me`) `GITLAB_TOKEN` without a network call (naming `.env`), derives `GITLAB_API="$GITLAB_URL/api/v4"`, `GITLAB_PROJECT` (URL-encoded project path), `GITLAB_CLONE_URL="$GITLAB_URL/$GITLAB_PROJECT_PATH.git"`, and `GITLAB_PUSH_URL="$GITLAB_CLONE_URL"` (no token in URL).
   - `adapters/gitlab/credential.sh` implements `gitcredentials(7)` for `GITLAB_URL` (default `https://gitlab.com`), answering `username=oauth2` and `password=$GITLAB_TOKEN` only on `get` for the matching protocol and host when `GITLAB_TOKEN` is set and non-placeholder, and printing nothing otherwise.
   - `adapters/gitlab/ready.sh` passes `PRIVATE-TOKEN` on stdin via `api` (never in curl argv) and checks `$GITLAB_PROJECT` in remote mode (`$GITLAB_API/version` in local mode). `bootstrap.sh`, `push.sh`, and `open-pr.sh` enforce attached-repo safety, default-branch HEAD parity, and `ticket/*` / `conformance/*` branch guards in remote mode.
   - When `GITLAB_PROJECT_PATH` is unset, local `gitlab-conformance` behavior (`root/workload`, `$PLANE_HOST:${GITLAB_PORT:-8929}`) stays 100% unchanged.
   - `ci/codehost.sh` adds `gitlab` to its credential helper verification table; `.env.example` and `ci/gitleaks-worktree.toml` declare `GITLAB_TOKEN=`.

4. **Attach & Makefile precedence (`core/remote-env.sh`, `core/attach.sh`, `./go`, `Makefile`)**:
   - `core/remote-env.sh` stays `<= 30` code lines (`ci/shell-boundary.sh`) and delegates to `core/remote.py detect`.
   - `core/attach.sh` detects the `origin` remote of `<src>` into `.crew/remote.env` (when attaching into the checkout root or when `REMOTE_ENV_OUT` is set) and prints a stderr note when a remote code host is detected.
   - `./go detach` deletes `.crew/remote.env` alongside `.attached/`.
   - `Makefile` adds `-include .crew/remote.env` directly after `-include .crew/ports.env` and directly before `-include .env`, guaranteeing precedence: `profiles/$(PROFILE).env` < `.crew/remote.env` < `.env` < command-line overrides.

## Alternatives considered

- **Hardcoding vendor host/repo mapping in `core/`**: Rejected — couples `core/` to code hosts and violates `ci/vendor-free.sh`.
- **Inspecting `.attached/` inside `Makefile` at parse time**: Rejected — `.attached/`'s `origin` is the local host path `<src>`, and `<src>` is outside the container mount.
- **Writing detected variables into `.env`**: Rejected — overwrites user configuration and tokens; `.crew/remote.env` stays gitignored, regenerable, and strictly lower priority than `.env`.

## Consequences

- **Positive**: Attaching a repository cloned from GitHub, GitLab, or the local plane Gitea configures `CODE_HOST` and repository identity automatically while keeping tokens exclusively in `.env` and git credential helpers.
- **Trade-offs**:
  - Detection at `./go attach` time runs `python3` when present on the host; on a host without `python3`, `attach` skips detection cleanly and explicit `.env` or `core/remote-env.sh` inside workbench remains available.
  - Because `.crew/remote.env` includes before `.env`, an explicit `CODE_HOST=...` in `.env` always overrides auto-detection (intended precedence).

## Golden-rule check

`./go demo` runs against the bundled app with no attached repository and `.crew/remote.env` absent (cleared on `./go detach` and whenever a non-matching local repo is attached). `./go ci` remains cold, engine-free, and network-free.

## Provenance

| Parameter / Rule | Value | Provenance |
|---|---|---|
| `core/remote-env.sh` ceiling | `<= 30` code lines | ADR 0052 §4 (`ci/shell-boundary.sh`) |
| GitLab git HTTP username | `oauth2` | Existing `adapters/gitlab/env.sh` convention |
| Precedence order | profile < `remote.env` < `.env` < CLI | Decision preserving `.env` explicit override (ADR 0093 §1d pattern) |
