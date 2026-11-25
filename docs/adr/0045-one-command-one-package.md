# ADR 0045 — One command, one package: `ghcr.io/nicolaguglielmi/lights-out:<version>` is the workbench plus the committed tree; a POSIX wrapper `lights-out` runs `init`, `gates`, `triage` and `postmortem` on any repository, mounted read-only, with one output directory and never the engine socket, judging a range by its base's contract; a composite Action posts the gates line as a check; agent hooks call the wrapper before a commit; `./go doctor` says what the host lacks, one line per item

- Status: accepted (amends ADR 0042 §4 [through the wrapper, `gates` and `init` never get the socket]; ADR 0037 §1 [the source may be a read-only mount, the draft goes to an output directory]; plan §16 M23 [the Action's acceptance is in lights-out's own workflow, §8]); amended by ADR 0057 [a gates image beside the full one, the Action at the root, keyless signing]; amended by ADR 0064 [no contract at the head: a removal and a first run are two lines; `init HEAD=`]
- Date: 2026-11-04
- Milestone: M23

## Context

Plan §16 M23: the four entry commands on any repository without cloning
lights-out, an Action that posts the gates line on a PR, a hook recipe for
three agent CLIs, `./go doctor`. Facts this ADR builds on: `./go` builds the
workbench from the checkout, mounts the checkout at its own path and, for
`gates` and `init`, the engine socket when one exists (ADR 0042 §4), which
is full control of the user's engine. `core/toolchain.sh` already NO-GOes
naming the socket for the `compose:` and image forms. `core/attach.sh` only
reads its source. `core/triage.sh` and `core/postmortem.sh` take
`TRIAGE_OUT`, `POSTMORTEM_OUT`, `TICKETS_DIR`. The tower judges an attached
PR with `workload.env` and `policy.env` restored from the base
(`core/steps/ci.sh`, ADR 0037 §4); a bare `make gates` uses the clone's
own. `ci/model-free.sh` refuses `_api_key` anywhere in `core/`. The L0
Gitea runner reads `.github/workflows/` too (ADR 0039).

## Decision

1. **The image.** `ghcr.io/nicolaguglielmi/lights-out:<version>` is the
   workbench (`workbench/Containerfile` unchanged) plus the tree at
   `/opt/lights-out`, `ENTRYPOINT package/entrypoint.sh`. One host script,
   `package/build.sh <engine> <tag> [build flags]`, assembles a throwaway
   context: `fetch.sh`, `pyproject.toml`, `uv.lock` at its root, `git
   archive HEAD` under `lights-out/`, and a Containerfile that is the
   workbench's followed by `package/Containerfile.tail` (COPY the tree, OCI
   labels with version and revision, ENTRYPOINT). One path for `podman
   build`, `docker build` and `docker buildx build --platform
   linux/amd64,linux/arm64`; only committed files enter, so `.env`,
   `.crew/cosign.key` and `.attached/` cannot. It runs as the host user
   under docker (`--user uid:gid`, `HOME=/tmp`, as `./go`) and as root
   mapped to the user under rootless podman; writes go to `/tmp` and
   `/out`; in the tree only `.crew/` and `.gates-out/` are writable
   (world-writable in the image).
2. **The wrapper.** `lights-out`, POSIX sh beside `go`; host needs an engine
   (podman, then docker, `CONTAINER_ENGINE` to force) and git. Subcommands
   `init`, `gates`, `triage`, `postmortem`, `doctor`, `version`; anything
   else refused naming the six. Arguments `KEY=VALUE` from one allowlist
   (`BASE HEAD STAGED ALERT INCIDENT PROFILE MODEL_PROVIDER MODEL`), the
   entrypoint holding the same list. It `cd`s to `git rev-parse
   --show-toplevel`; file arguments are paths under the current directory.
   Mounts: the repository read-only at `/repo`; one directory read-write
   at `/out`, default `${XDG_STATE_HOME:-$HOME/.local/state}/lights-out/<repo
   basename>`, `LIGHTS_OUT_OUT` to move it, created by the wrapper before
   the mount (an engine creates a missing one as root). SELinux: `--security-opt
   label=disable`, never `:z`, which would relabel the user's tree. Never
   `.env`: a key is passed by name (`-e GEMINI_API_KEY`) only when set.
   Image: `LIGHTS_OUT_IMAGE`, default `…/lights-out:$version`, `version=`
   a line of the wrapper.
3. **No socket, ever; what runs where.** Plan §16 principle 1: a published
   image asking for control of your engine is what a user should refuse.
   `gates` and `init` work for `TOOLCHAIN_IMAGE=workbench` only; the other
   forms keep `core/toolchain.sh`'s NO-GO, plus the hint "use ./go gates
   from a clone". The plane stays the clone path. `gates`: attach clones
   `/repo` HEAD into `/tmp`; no committed `.lights-out/workload.env` is a
   NO-GO naming `lights-out init`; `make gates`, range as given or the last
   commit (ADR 0042 §5), `GATES_OUT=/out/gates`. **A range is judged by its
   base** (ADR 0033, 0037 §4): when BASE holds `.lights-out/workload.env`,
   `workload.env`, `policy.env` and `runners/` are restored from it,
   no-overlay, before the gates run; with no contract at BASE, HEAD's
   judges itself, said on stderr. `STAGED=1` applies `git -C /repo diff
   --cached --binary` to the clone as one commit and judges it, base the
   previous HEAD: a hook would otherwise judge the last commit, a check
   that looks like it gates the commit and does not. `init` attaches,
   runs `make init`, copies the draft to `/out/.lights-out/`, prints the
   host path. `triage`/`postmortem` run the ADK role in-process, outputs
   and tickets under `/out`; at L0 the recordings are lights-out's own, so
   a user's alert NO-GOes naming `PROFILE=l1` and the key variable.
   `doctor` runs on the host, in the wrapper: engine and version, image
   present and its version label, the provider's key set or not.
4. **`./go doctor`**, host-side like `engine-compat`: `core/doctor.sh`
   sourcing `ci/lib.sh`, one stderr line per item: engine (name, version,
   answers, podman machine and API socket where relevant), git version,
   workbench image built, the plane's host ports free or held by the
   `lights-out` compose project (3000 5050 8016 8085 8086 9090 9901 and
   `APP_PORT`, default 8080: measurement, read from `compose.yaml`
   2026-11-04; by number, never by service, for `ci/vendor-free.sh`), the
   key for the provider `PROFILE`/`.env` choose (L0: not needed; L1: set
   or missing, never the value). The variable's name comes from
   `adapters/<harness>/conformance.env` (`KEY_VAR` where `PROVIDER`
   matches), so `core/` names no key. Ports probed with `nc -z`, else
   `lsof`, else `ss`, else "not checked". Then one stdout line.
5. **The Action.** Composite, `action/action.yml`, used as
   `nicolaguglielmi/lights-out/action@<tag>`, Linux only. It runs
   `$GITHUB_ACTION_PATH/../lights-out gates`, so action and image version
   cannot drift (input `image` overrides, for tests), with
   `CONTAINER_ENGINE=docker` as `ci.yml`. `BASE` is
   `github.event.pull_request.base.sha`, `HEAD` the checked-out commit
   (under `pull_request`, the merge commit `actions/checkout` makes: the
   tree tested is the commit judged); the caller checks out with
   `fetch-depth: 0`, and a missing base is a NO-GO naming it. One check run
   `lights-out gates` on the PR head sha (input `sha`, default that or
   `github.sha`), conclusion success/failure, the line as title, REST with
   `github.token`, in a step after the container has exited, the token
   never passed in. The line also goes to the step summary; the exit code
   is the verdict. Permissions: `contents: read`, `checks: write`. A fork
   PR's token cannot write checks: the post is skipped with a warning. A
   private package needs `docker login ghcr.io` first, documented.
6. **Hook recipes**, in `docs/use-on-your-repo.md`, not code: Claude Code
   (`.claude/settings.json`, `PreToolUse`, matcher `Bash`), Codex
   (`.codex/hooks.json` or `~/.codex/hooks.json`, `PreToolUse`, matcher
   `*`: the shell tool's name was not verified, and M19 saw PreToolUse fire
   for code mode's nested calls), Gemini CLI (`.gemini/settings.json`,
   `BeforeTool`, `run_shell_command`). Each is one `sh -c`: pass unless
   stdin contains `git commit`, else `lights-out gates STAGED=1 >&2 ||
   exit 2` (the deny all three read, ADR 0041). Fails closed: no engine is
   exit 2 too. Timeout 300 s, explicit, in each CLI's unit (decision: the
   budget the gates are held to, plan §16).
7. **Versioning and publishing.** Semver from `0.1.0` (proposed; the owner
   decides); the wrapper's `version=` is the one source. Workflow
   `package.yml`, every job `if: github.server_url == 'https://github.com'`
   (ADR 0039): on every push, `./go package`, `./go demo-package` and the
   Action's test job; on a `v*` tag or `workflow_dispatch` only, `publish`
   (`packages: write` on that job only) refuses a tag other than
   `v$version`, builds amd64 and arm64 with QEMU and buildx through
   `package/build.sh`, pushes `:<version>` or `:sha-<commit>`. No
   `latest`. Private until the owner publishes the first one.
8. **Acceptance: `./go demo-package`**, host-side, outside `./go demo`
   (ADR 0003): build, copy `workloads/minimal` into a fresh repository,
   `lights-out init`, commit the draft, `lights-out gates` → GO, init plus
   gates refused above 300 s (owner decision 2026-11-03, plan §16;
   measured 40 s for `./go init` + `./go gates` there at M20). Proven on
   GitHub Actions with docker. The test job runs `uses: ./action` on that
   repository; the plan's "green on a PR of the example repository" with
   `@<tag>` needs the first publish, the owner's act. `./go ci` gains
   `ci/package.sh`, engine-free: wrapper shellchecked, refuses unknown
   subcommands and keys, one clear line with no engine on PATH, its
   allowlist equals the entrypoint's, `version=` is semver, `action.yml`
   asks for no other permission, `core/doctor.sh` keeps the one-line
   contract with no engine.

## Alternatives rejected

- **Mount the socket for the engine forms.** Principle 1; and it is the
  grant a stranger should refuse from a published image.
- **Judge with HEAD's contract** (the draft before this ADR). A PR setting
  `TEST_CMD=true` or rewriting a scorer gets a green check.
- **Mount the repository read-write and write the draft into it.** ADR 0037 §1.
- **`--env-file .env`.** The file may hold more than the key.
- **`FROM` a locally built workbench, or `docker buildx bake`.** A
  multi-platform builder cannot see local images; bake is docker-only.
- **A `.dockerignore`.** podman and docker read different ignore files; one
  slip puts `cosign.key` in a public image.
- **A slim package image.** A second set of pins to keep in step with the
  workbench, before the size is even measured.
- **A docker or JavaScript action.** The first pulls an image named in
  `action.yml`, not the wrapper's; the second needs `node_modules`
  committed. Composite runs what a human runs.
- **Shipping hooks as files or `lights-out hooks install`.** Writes into the
  user's repository and three vendor formats that move fast.
- **A git pre-commit hook.** `--no-verify` skips it, and a model can type it.
- **A `latest` tag.** The verdict would change under the user.
- **`doctor` in the container.** It cannot report an engine that does not answer.
- **A compiled wrapper.** A release per OS; the host has sh, git, an engine.

## Consequences

- **The one that will hurt: the package is the whole workbench** (gcloud,
  terraform, litellm, …) for four commands; its size was not measured
  writing this. The first pull is outside the 300 s, which counts init
  plus gates only; `demo-package` prints the size.
- Every run is cold (`--rm`: `.crew/cache` dies), so each hook run
  reinstalls the repository's dependencies and the stack runners. M20's
  40 s was cold; a cache under `/out` is the first fix if hooks drag.
- The hook matches text: `git -C x commit`, an alias, a script that
  commits are not seen; `--amend` judges the staged diff only. What a CLI
  does when a hook times out was not verified. The Action is the gate;
  the hook is a convenience.
- Tags are mutable and the package is unsigned (ADR 0025 signs workload
  releases); the docs show pinning by digest.
- A `compose:` or image-form contract gets a NO-GO through the wrapper;
  `postmortem` needs ADR 0044's incident document, which only `./go
  incident` builds; tickets are numbered per output directory.
- `./go package` packages HEAD: uncommitted edits are not in the image
  (said on stderr).
- `core/steps/ci.sh` restores `workload.env` and `policy.env` but not
  `runners/` (ADR 0042 came after ADR 0037 §4): the tower has the gap §3
  closes here; the risk gate's `.lights-out/*` → high still stops an
  auto-merge. Its own fix: closed by ADR 0033's amendment of 2026-11-04.

## Golden-rule check

`./go demo` is untouched: no new stage, no new image on its path. `./go
ci` gains `ci/package.sh`, no engine, no network. `./go demo-package` and
`./go doctor` need no key and no account; the first needs the engine and
the network. Passes.

## Verified while writing (2026-11-04)

docs.github.com, developers.openai.com and geminicli.com did not resolve
from the writing session; for those, what is below comes from search
excerpts of the official pages, not the pages, and is re-checked at
implementation.

- **Claude Code hooks** (code.claude.com/docs/en/hooks, read): `hooks` →
  `PreToolUse` → `[{matcher: "Bash", hooks: [{type: "command", command}]}]`
  in `.claude/settings.json` (project), `.claude/settings.local.json`,
  `~/.claude/settings.json`; stdin `tool_input.command`; exit 2 blocks the
  call and stderr goes to the model; `timeout` in seconds, default 600 for
  `command`.
- **Codex hooks** (developers.openai.com/codex/hooks, excerpts):
  `<repo>/.codex/hooks.json` or `[hooks]` in `.codex/config.toml`, plus
  user and managed; project hooks run only when the project's `.codex/`
  layer is trusted, per hook hash, reviewed with `/hooks`; `timeout` in
  seconds, default 600. **Not verified:** the shell tool's matcher name
  (a third-party source says `Bash`) and exit 2 for a project hook; M19
  verified exit 2 denies with `--dangerously-bypass-hook-trust` (ADR 0041).
- **Gemini CLI hooks** (geminicli.com/docs/hooks/reference, excerpts):
  `hooks.BeforeTool[]` in `settings.json`, matcher a regex on the tool
  name; `timeout` in milliseconds, default 60000, so the recipe must set
  it. **Not verified:** whether project settings in an untrusted folder
  load hooks.
- **Check runs** (docs.github.com/en/rest/checks/runs, excerpts): write is
  GitHub Apps only, `GITHUB_TOKEN` is an app installation token, the
  permission is Checks write. A fork's `pull_request` run gets a read-only
  token unless the repository sends write tokens (Actions token page).
  The path `POST /repos/{owner}/{repo}/check-runs` was not re-read.
- **Composite actions**: `github.action_path` gives "the path where an
  action is located… to access files located in the same repository";
  the runner fetches the ref's repository archive (actions/runner#646).
  **Not verified** by a source: `../` above the action directory. The
  test job proves `./action`; `@<tag>` is proven at the first publish.
