# ADR 0050 — `antigravity-cli`: Antigravity CLI as an optional, experimental Gemini harness, pinned by version and sha256 at image build, behind a shim to the one guard

- Status: experimental (reconciled at 0.9.0: optional Gemini CLI harness conformance-tested against a scripted stub; proprietary unredistributed binary, unrecorded at L1, and §7 workspace-hook gap remains open; amends ADR 0041 §2 [a provider may have more than one harness; `AGENT_HARNESS` chooses, `gemini-cli` stays the default for `gemini`], §8 [a CLI harness is discovered by `package-lock.json` or `release.env`; case 0 accepts any harness that fits the other provider; case 4 adds agy's workspace hooks file], §9 [`harness-shape` checks a release pin and no longer requires a provider of its own], §11 [the guard reached through a per-adapter shim]; ADR 0017 §3 [a worker image may hold a binary downloaded at build, never published]). Plan §4's `AgentHarness` row names `antigravity-cli` since 2026-11-07, with the owner's approval, and its status note says what is below.
- Date: 2026-11-06
- Milestone: none; the owner's request, built after ADR 0046's egress proxy reached main, as they asked ("Yes, after egress")

## Context

The owner asked for Antigravity CLI (`agy`) as a fourth coding harness, on two conditions: a spike first, proving it meets ADR 0041's contract, and gemini-cli staying the default until a live agy run on ticket 0001 has passed the gates. The spike ran on GitHub Actions (podman is stopped on the owner's Mac), on a branch never to be merged. It found that agy can meet the contract only with a shim in front of the guard, a hook of its own for the budgets, and a store nobody documents for the served model. It also found conflicts with ADR 0041 as written: one provider per harness, and an npm pin for every CLI harness. And it found one gap this ADR does not close.

## Verified (2026-11-05 and 2026-11-06, GitHub Actions ubuntu-latest, linux x64, branch `spike/agy-harness`, runs 42040265336, 42041151697, 42042147762, 42255348889, 42255879282, 42255995624, 42256387946, 42256676055)

agy 1.5.1, as uid 1001 and as root in `python:3.12-slim`. A scripted Gemini endpoint was reached through `GOOGLE_GEMINI_BASE_URL` with a fake key. One real-key call went through a recording proxy and one went direct. Uploads were text only after run 2, whose artefact held the binary and was deleted.

- **Packaging.** Per-architecture tarballs are published on GitHub releases of `google-antigravity/antigravity-cli`, with sha256 digests (`agy_cli_linux_{x64,arm64}[_musl].tar.gz`). Each holds one file, `antigravity`, a 240 MB dynamically linked glibc binary. The official `install.sh` always takes the latest from a manifest, with no version pin. Releases are near daily: 1.4.10 to 1.5.1 in seven days, and 1.5.2 on 2026-11-06.
- **Licence.** None in the repository (GitHub `licenseInfo` null) and no source. It is governed by the Google Terms of Service and Antigravity's additional terms. Google collects "Interactions", and the telemetry toggle is an account setting, not a `settings.json` key.
- **Headless.** The invocation is `agy -p … --output-format stream-json --dangerously-skip-permissions --model <id> --effort low|medium|high`. It works as root. `--print-timeout` defaults to 0, no limit.
  - Events: `init`, then `step_update` (`step_index`, `state`, `step_type` `user_input|agent_response|tool|system_message|error_message`, `text_delta`, per-call `usage`), then one `result` (`status`, `response`, `error`, `usage`, `num_turns`).
  - `result.response` joins every agent message's text: a run that said "waiting" before its answer reports both.
  - Exit codes: 0 for success; 3 for a print timeout and for a hook's terminate; 1 for a bad flag value (`--effort xhigh` on gemini-3.8-flash); 2 for a usage error. A denied tool call does not fail the run.
- **Thinking.** `--effort low|medium|high` reaches the request as `generationConfig.thinkingConfig.thinkingLevel` `LOW|MEDIUM|HIGH`.
- **Auth and endpoint.** `modelProvider: "gemini"` in `~/.gemini/antigravity-cli/settings.json` plus `GEMINI_API_KEY` is the API-key mode, as documented. `GOOGLE_GEMINI_BASE_URL` is honoured. `trustedWorkspaces` avoids the trust prompt.
- **Instructions.** `~/.gemini/GEMINI.md` reaches the request, and so does `~/.gemini/AGENTS.md`. `~/.gemini/antigravity-cli/GEMINI.md` does not.
- **Tools.** In API-key mode the model is declared twelve tools:
  - command and file tools: `run_command` (`CommandLine`, `toolSummary`, `WaitMsBeforeAsync`), `read_file` (`Path`), `write_file` (`Path`, `Content`), `edit_file`, `manage_task`;
  - the rest: `search_web`, `invoke_subagent`, `define_subagent`, `manage_subagents`, `schedule`, `send_message`, `ask_question`.

  `search_web` works, through the Gemini API on the provider's host. `permissions.deny: ["search_web"]` did not stop it.
- **Long commands.** `run_command` moves a command to the background after `WaitMsBeforeAsync`, at most 10000 ms: 60000 was clamped to it. The model then gets "Tool is running as a background task" and is told to end its turn. `manage_task status` returns only RUNNING and a log path under `~/.gemini/`. When the model ends its turn, agy waits for the task, sends its output as a user `<SYSTEM_MESSAGE>` ("finished with result: … Command output: …") and calls the model again.
- **Hooks.** The configuration is `~/.gemini/config/hooks.json`, keyed by hook name; hooks in `settings.json` are ignored.
  - The `PreToolUse` payload is `{toolCall: {name, args}, stepIdx, conversationId, transcriptPath, modelName, …}`.
  - The only way to deny is stdout `{"decision": "deny", "reason": …}`; the exit code is ignored. So `guard.sh` run unchanged allowed everything: the push went through and `.env` was read. A shim that maps the payload to `{tool_name, tool_input}` and the guard's exit 2 to that JSON denied both, as uid 1001 and as root, and through the egress proxy.
  - `PreInvocation` gets `invocationNum` (0 for the first call). Printing `{"injectSteps": [{"errorMessage": {"userMessage": …}}], "terminationBehavior": "terminate"}` stopped the run after exactly that many calls, exit 3.
  - When the hook runs, the captured stream-json already holds every earlier call's usage: at 1,000 tokens a call it saw 0, 1000, 2000, 3000.
- **The workspace and the hooks** (run 42042147762):
  - A committed `.agents/hooks.json` with `{"guard": {"enabled": false}}` disabled the user hook named `guard` (C1: push and `.env` read went through).
  - A random hook name defeated that (C4).
  - A workspace hook answering `allow` did not override the user hook's deny (C2).
  - A `.agents/hooks.json` written mid-run was not reloaded (C3).
  - **The workspace's own `SessionStart` and `PreToolUse` commands ran** (C4).
- **Served model.** It is in neither json nor stream-json (`init` echoes the requested `--model`), and hooks get `modelName`, the requested id plus the effort (`gemini-3.8-flash-medium`). The response's `modelVersion` is kept only in `~/.gemini/antigravity-cli/conversations/<id>.db`: table `gen_metadata`, one protobuf blob per model call. Decoded by hand, the served id was field 19 of field 1 in every row, and the requested id sat elsewhere. With a real key, gemini-3.8-flash served gemini-3.8-flash. The fake key was in no file under `HOME`.
- **Network.** agy honours `HTTPS_PROXY`. On every run it asks for `generativelanguage.googleapis.com:443` (or the base URL), `play.googleapis.com:443` (telemetry) and `antigravity-cli-auto-updater-974169037036.us-central1.run.app:443`. Every run also calls `gemini-3.1-flash-lite-preview` on the provider. Through `core/egress.py` with only the endpoint allowed, a full run passed and the two other hosts were denied.
- **Self-update.** A writable 1.5.0 binary replaced itself with 1.5.1 during one headless `-p` run, leaving `agy.<ts>.old`. There is no documented switch: `noUpdate` and `AUTO_UPDATER_TEST_MODE` appear only as strings in the binary, so they are not used.

## Decision

1. **Optional, experimental, on Gemini.** `adapters/antigravity-cli/` is a fifth CLI-harness directory in gemini-cli's shape:
   - `Containerfile`, `entrypoint.sh`, `run.sh`, `thinking.env`, `conformance.env`, `hooks/`;
   - `release.env` in place of the npm pin (§2);
   - `served_model.py` (§5).

   It runs `gemini` only and refuses any other provider in one line naming the harness that fits, as every `run.sh` does. ADR 0041 §2 is amended: each harness still runs one provider, but a provider may have more than one harness. `AGENT_HARNESS=antigravity-cli` with `MODEL_PROVIDER=gemini` chooses it; nothing else does. The profiles keep `gemini-cli`, the default, until a live agy run on ticket 0001 has gone through the gates (the owner's condition). `ci/harness-shape.sh` no longer requires a provider unique to each harness, and case 0 of the suite accepts a refusal naming any harness of the other provider.
2. **Pinned by version and sha256, downloaded at image build only.**
   - **`release.env`.** It holds `VERSION`, an exact version (1.5.1, the one the spike verified, not the latest), and per architecture `URL_AMD64`/`URL_ARM64`, https URLs that name the version as a path segment, plus `SHA256_AMD64`/`SHA256_ARM64`, the digests GitHub publishes for those assets.
   - **The build.** A stage on the workbench, which has curl, downloads the tarball for `uname -m`, refuses it unless `sha256sum -c` passes, and copies the one binary into the worker. Nothing else ever fetches it. A musl base stops the build at `agy --version` with a message; only the glibc tarballs are pinned.
   - **The rule.** A CLI harness is now `run.sh`, `Containerfile` and one pin: `package.json`/`package-lock.json` or `release.env`. It is discovered the same way by `ci/harness-shape.sh`, `ci/hooks.sh`, `ci/workloads.sh`, the suite and `record`. `harness-shape` checks the keys, the version, the URLs and the digests, and refuses both pins at once; its self-check plants a defect for each.
   - **Self-update.** The pin holds on two counts. The updater's host is not in `EGRESS`, so the proxy denies it on every run. And every run starts from the image (`run --rm`), so an update that got through would last one run. A bump is its own commit with `./go harness-conformance` as its eval (ADR 0018).
3. **The guard, behind a shim, under a random name.** `hooks/guard.sh` stays byte-identical to every other harness's (ADR 0041 §11). agy's `PreToolUse` runs `hooks/agy-guard.sh` with matcher `*`.
   - **The tools that reach the guard.** The shim passes on only the five tools this harness offers: `run_command`, `read_file`, `write_file`, `edit_file`, and `manage_task`, which reads back a backgrounded command. It hands each to `guard.sh` as `{tool_name, tool_input}` and turns exit 2 into the deny JSON, the guard's stderr as the reason.
   - **The tools it denies.** Every other tool is denied by name, with its own `hooks.log` line `DENY <tool> not-offered`, the way gemini-cli excludes its web tools. `search_web` reads the web and `permissions.deny` does not stop it. The subagent, schedule and message tools were never verified to send their own calls through this hook. A tool a new release adds is denied until it is named here: the opposite failure from the matcher list ADR 0041's amendment removed.
   - **The hook names.** Both hooks get a fresh random suffix every run (8 random bytes, a decision: a workspace cannot guess them), because a workspace can disable a hook by its name (C1).
   - **The checks.** `ci/hooks.sh` renders every case in agy's shape and runs it through the shim, plus the two name denials. Suite case 4 adds a `.agents/hooks.json` that disables `guard` and `budget` and adds a `PreToolUse` hook answering `allow`.
4. **Budgets.**
   - **`TIMEOUT_S`.** Coreutils `timeout`, exit 124.
   - **`MAX_TURNS` and `TOKEN_BUDGET`.** `hooks/budget.sh` on `PreInvocation` stops the run before the next model call when `invocationNum` has reached `MAX_TURNS`, or when the `usage.total_tokens` of the agent responses in the captured stream-json have reached `TOKEN_BUDGET`. It writes the budget's name to `OUT/stop` (ADR 0041 §5) and returns the terminate JSON.
   - **The stop line.** `run.sh` reads the marker first, since agy's exit 3 means a hook stop and a print timeout alike, and prints the stop phrase.
5. **The served model, from the conversation store, or `unknown`.**
   - **The copy.** After the run the entrypoint copies `~/.gemini/antigravity-cli/conversations/` to `.agy-conversations/` in the run directory, beside `OUT`, never in it.
   - **The read.** `run.sh` runs `served_model.py` in the workbench (standard library: `sqlite3` and a twenty-line protobuf reader). It takes the most frequent string at field 1 > field 19 of `gen_metadata`, claude-code's rule (ADR 0041 §6), then deletes the copy. Anything unreadable is `unknown`, and case 1 fails on it.
   - **Turns and tokens.** `run.json`'s `turns` is the number of agent responses, one per model call; its tokens are their summed usage.
   - **The final message.** It is the text of the last agent response that has any, never `result.response` (see Verified).
6. **A proprietary binary, never redistributed.** An image built from this Containerfile is never pushed or published; no CI artefact holds the binary or a `HOME` that could hold a key. Its telemetry host is denied by the proxy. Running it accepts Google's terms; the README says so.
7. **Open gap, recorded, not closed: repository hooks run.** A repository's `.agents/hooks.json` is loaded with the user's. Its `SessionStart` and `PreToolUse` commands ran in the spike (C4): content the crew is asked to work on executes commands inside the container before the model starts and outside the guard. It cannot switch the guard off (§3), and an `allow` from it does not override the guard's deny (C2, suite case 4, which runs such a hook on purpose). The container and the egress proxy are what bound it: no engine socket, no git credential, only the run's directory mounted, and the only way out is the provider's host. The key is in that container's environment, so this is ADR 0046's first consequence reached without a model. No setting to ignore workspace hooks was found. A pre-run refusal of a workspace holding `.agents/hooks.json` was not built: the owner asked to record the gap, and other file names or locations agy reads were not enumerated. The README's harness table and "Not yet" both say it; it is the reason the harness is experimental beyond its youth.
8. **Long commands, as agy designs them.** A command still running after 10 s (agy's maximum, an external fact) comes back to the model as a background task, and its output arrives only after the model ends its turn. The scripted model plays that: on the notice it answers with a short text, a `wait` step the script does not count, and reads the next request's `<SYSTEM_MESSAGE>` as the command's result. Case 9's probe can take longer than 10 s and goes through that path. A real model has to do the same with a long test suite, and the dev-worker prompt does not tell it to. The first live record will show whether it does.
9. **Docs and plan.** `README.md` and `docs/reference.md` list `antigravity-cli` as offered, experimental, with the gap of §7; gemini-cli stays the default. `docs/PLAN.md` §4, the owner's document (Italian), listed `antigravity-sdk` but no CLI harness. With the owner's approval (2026-11-07) its `AgentHarness` row gains `antigravity-cli`, and an amendment note there says: optional and experimental, gemini-cli the default, the repository-hooks gap.

## Alternatives rejected

- **Pinning by npm or `install.sh`.** No npm package exists, and a made-up `package.json` would hide the real pin. `install.sh` always installs the latest.
- **Downloading at run time.** That needs github.com in `EGRESS` on every run, and the pin would be only as good as the network.
- **Replacing gemini-cli.** It is the owner's condition that gemini-cli stays until a live agy run passes the gates.
- **Teaching `guard.sh` agy's payload instead of a shim.** agy ignores the exit code, so a translation to stdout JSON is needed anyway. The guard's one input contract stays the one ADR 0041 §11 tests three ways.
- **`permissions.deny` for `search_web`.** It was verified not to stop it.
- **Fixed hook names.** A workspace disables them by name (C1).
- **The served model from the hook's `modelName` or the `init` event.** Both are the requested id.
- **The musl tarballs.** They mean one more detection in the build for a base no workload uses today.

## Consequences

- **The one that hurts: this harness rests on a proprietary binary that changes almost daily, and on facts nobody documents.** These are the hook payloads, the exit codes, the protobuf field of the served model, the background-task message and the twelve declared tools. Each was read from 1.5.1's behaviour, not from a contract. A bump can move any of them; the suite catches the ones it exercises (case 1 shows `unknown` for the served model). The stub now describes a fourth wire surface.
- Repository hooks run outside the guard (§7) until a verified switch or a refusal closes it.
- A long command's output reaches the model only after it ends its turn (§8). A model that does not do that loses the output.
- The image adds a 240 MB binary. It is built only by `harness-conformance` and L1 runs, and the build downloads about 70 MB from github.com.
- `./go record TICKET=0001 PROFILE=l1 AGENT_HARNESS=antigravity-cli MODEL_PROVIDER=gemini MODEL=gemini-3.8-flash` writes `fixtures/0001/dev-worker/antigravity-cli/` (ADR 0041 §10). It is not run here; the owner launches it. Its `run.json` is the first measurement of this harness on the real wire.
- `RISK_HIGH_PLATFORM` does not yet name `.agents/*`. A PR that adds workspace hooks reconfigures the next agy worker, as `.gemini/*` does for gemini-cli (ADR 0017 §7). Left to the owner with the policy file.

## Golden-rule check

`./go demo` is unchanged: L0 replays, and no harness image is built. `./go ci` gains engine-free checks (a release pin in `harness-shape`, agy's shape in `hooks`). `./go harness-conformance` builds a fourth image, which downloads the pinned tarball from GitHub, and needs no key and no account; a cloner never has to type it.
