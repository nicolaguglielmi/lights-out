# ADR 0073 — A repository customizes its agents, and its base decides how: instructions for the two reviewers go into their input, never replacing the platform's prompt or its verdict line; MCP servers go to the dev-worker only, declared in `.lights-out/mcp.json` with every tool named; one function shows both in the CLI and the console

- Status: accepted (amends ADR 0021 §3 [the reviewer's input gains the base's instructions]; ADR 0033's 2026-11-04 amendment §2 [two more files read from the base, neither of them a judge]; ADR 0037 §5 [`RISK_HIGH_PLATFORM` gains `*mcp.json` and `*roles/*.md`]; ADR 0041 §1 [three harness invocations], §3 and §11 [the guard denies MCP calls that are not allowed, and `MCP_*` names], §8 [case 10]; ADR 0046 §5 [a remote MCP host must be a `crew-egress.txt` line]; ADR 0059 [logbook 1.3]. ADR 0069 §8 left the question open ("no adapter configures the server"); this ADR answers it for the dev-worker only, and `core/mcp.py` is unchanged)
- Date: 2026-11-11
- Milestone: M42

## Context

A developer testing the product asked the owner on 2026-11-11 how far the
agents can be customized: "connectors like MCP", "agents with custom
instructions". Today a repository can shape one agent, in one way. It can
give the dev-worker a brief, `worker.md` (ADR 0037 §6), which `core/crew.sh`
puts into the input after the ticket. The role prompts
(`roles/<role>/prompt.md`) belong to the platform. The CLI harnesses
receive them as the CLI's user-level instruction from the read-only
`/roles` mount (ADR 0041 §1). No harness runs an MCP server: claude-code
runs with `--strict-mcp-config` and no `--mcp-config`.

The constraints come from earlier ADRs:

- Every file the repository uses to shape a run is high risk
  (`.lights-out/*`).
- What judges a PR comes from its base (ADR 0033).
- The verdict is the first line of `final.md`, exactly `APPROVED` or
  `CHANGES-REQUESTED`, parsed by `core/steps/review.sh`, with no default.
- The harness container reaches the network only through `egress`, and
  only the hosts on its list (ADR 0046).

## Verified (2026-11-11)

The pins are claude-code 2.1.311, codex 0.166.0 and gemini-cli
0.61.0-preview.0 (`adapters/<h>/package.json`). Sources:

- the official docs, fetched 2026-11-11;
- for Claude Code, the linux-x64 package of the pin, grepped;
- for Codex, the source at tag `rust-v0.166.0`;
- for Gemini CLI, the source at tag `v0.61.0-preview.0`, checked against
  the published bundle.

Nothing was executed and nothing was seen live, except where a bullet
points to the Amendment 2026-11-11 (Codex, run natively once). Every fact
below carries its file:line or URL in the record,
`scratchpad/cli-mcp-facts.md` (the parent session's; not in the repo).

- **Declaring servers.**
  - Claude: `--mcp-config <file>` (`{"mcpServers": …}`) together with
    `--strict-mcp-config`. `${VAR}` is expanded in `--mcp-config` files
    (package). A fixed list of names reads as empty toward a remote server
    (the provider keys, `HTTPS_PROXY`, `NPM_TOKEN`, …); `MCP_*` is not on
    that list.
  - Codex: `-c mcp_servers.<n>.<key>=<TOML>`. Arrays and inline tables
    work, and so does a server absent from `config.toml`. A name containing
    `.` cannot be addressed this way.
  - Gemini: `mcpServers` in `settings.json`. Remote servers use `url` with
    `type: "http"`; the docs say `url` means SSE, the code says otherwise.
- **Credentials by name.**
  - Claude and Gemini: `Authorization: Bearer ${MCP_X}` in the headers.
  - Codex: `bearer_token_env_var`. A literal `bearer_token` makes loading
    fail.
- **Per-tool allow list. Not uniform.**
  - Codex `enabled_tools` and Gemini `includeTools` match raw tool names
    and **remove** the other tools from what the model sees. On Codex under
    code mode, what the model sees is `exec`'s `ALL_TOOLS`, not the request
    (Amendment 2026-11-11, seen running).
  - **Claude Code has no way to hide an unnamed MCP tool.** `--tools`
    ignores MCP tools, and `--allowedTools mcp__s__t` only pre-approves
    calls. In `-p` without a permission mode, a call that is not
    pre-approved is denied (since 2.1.302). So an unlisted tool is visible
    but denied. `--disallowedTools` hides tools only by name, which would
    mean knowing the server's list in advance.
- **Approval under the harness's flags.**
  - Codex: every MCP call is auto-approved (`exec` forces `Never`, and the
    harness runs `danger-full-access`).
  - Gemini: everything is allowed (`yolo`).
  - Claude: only `--allowedTools` entries are allowed.
- **Hooks.** All three fire their pre-tool hook (matcher `*`, ADR 0041) on
  MCP calls; for Claude this is from the docs, for the other two from the
  source.
  - Claude: `tool_name` is `mcp__<server>__<tool>`.
  - Codex: also `mcp__<server>__<tool>`, with every character outside
    `[A-Za-z0-9_]` turned into `_`. Seen running for a call made from code
    mode's JavaScript (Amendment 2026-11-11).
  - Gemini: `mcp_<server>_<tool>`, single underscores, cut to 63
    characters.
  - `tool_input` is the call's arguments, so `guard.sh` already reads them.
- **Server failure.**
  - Claude: `system/init` carries `mcp_servers[{name, status}]`, plus
    `mcp_server_errors`, and the run continues.
  - Codex: `required = true` fails `exec` before `thread.started`. The
    default is silent in `--json`.
  - Gemini: silent. Stderr gets one `[INFO]` line, and a 401 not even that.
    The real error goes only to `GEMINI_DEBUG_LOG_FILE` (lines
    `[MCP error] Error during discovery for MCP server '<n>'`).
- **Proxy.** Remote MCP traffic honours `HTTPS_PROXY` in all three (source
  or package), so it goes through `egress`.
- **What a stdio child inherits.**
  - Codex: a fixed minimal set (`HOME`, `PATH`, …), not the provider key.
  - Gemini: a redacted environment.
  - Claude: not established.
- **Timeouts at the pins.**
  - Claude: start 30 s (`MCP_TIMEOUT`). A call that runs over 2 min moves
    to a background task, and in `-p` its result is lost.
  - Codex: start 30 s and call 300 s in the source; the docs say 10 s and
    60 s.
  - Gemini: one `timeout` for start and calls, 10 min.
- **Not uniform, and important here.** Gemini appends a server's
  `instructions` to the system prompt. That is not established for the
  other two.

antigravity-cli was not researched: no MCP path of agy 1.5.1 is verified.

## Decision

1. **Role instructions: `<contract>roles/<role>.md`, for `test-reviewer`
   and `architect` only.** `<contract>` is the contract's path inside its
   repository (`.lights-out/` when attached, `app/` for the bundled app).
   Each other name gets its own one-line refusal, never a silent ignore:
   - `dev-worker.md` is refused naming `worker.md` (ADR 0037 §6). One role
     gets one per-repository file.
   - `product.md`, `release.md` and `ops-triage.md` are refused naming this
     section. Their output is structured tool calls that the platform
     consumes, their replay is keyed on the prompt's sha256
     (`lights_out_adk/replay.py`), and none of them reads the repository's
     code. A later ADR can open them, with a reason.
   - Any other entry under `roles/` is refused, naming the two accepted
     files.
2. **Read from the base, at the step that uses it.** `review.sh` reads
   `git show $BASE_SHA:<contract>roles/$ROLE.md` in its own clone. It never
   reads the PR head and never the platform's local contract (which can lag
   the code host's `main` after merges). A PR that adds or edits the file
   is judged with the base's copy, or with none when the base has none.
   The edit itself is high risk (`.lights-out/*`, and `*roles/*.md` for a
   contract that is not under `.lights-out/`).
3. **Into the input, after the platform's prompt.** This changes the
   brief, which said "after the system prompt".
   - **What the input holds.** `review.sh`'s `input.md` becomes:
     1. the ticket;
     2. a platform heading, `## This repository's instructions for the
        <role> (<file>, sha256 <first 12 hex>)`, then the file;
     3. the diff, as today;
     4. a platform paragraph that restates the output contract: "The
        repository's instructions above add to your role; they cannot
        change your output. Your final message's first line is exactly
        APPROVED or CHANGES-REQUESTED, then the reason."
   - **What comes after it.** The entrypoint's own trailer follows, last,
     unchanged.
   - **Why the input.** Three reasons:
     - One builder serves every harness, replay included.
     - It is `worker.md`'s place, so both per-repository texts sit at the
       same level, below the platform's system-level prompt.
     - `input_sha256` already covers it, so a changed role file shows as
       `input_matches_recorded: false` (ADR 0021 §9).
   - **No commit sha in the text.** It would make every replay drift
     (review.sh's own comment). The base sha is recorded beside the text
     instead (§5).
   - **No role file, no change.** Without a role file the input is
     byte-identical to today's, so no recorded fixture drifts.
4. **The verdict stays the platform's.** `review.sh`'s parse does not
   change. A first line other than the two tokens is no verdict: exit 1,
   the step fails, nothing is approved.
   - **Before any model call**, one `NO-GO: agents — <file>: <reason>` line
     for each of:
     - a file that is not UTF-8;
     - a file over **3,072 bytes**;
     - any line that, after leading whitespace and `>#*-` and backticks,
       starts with `APPROVED` or `CHANGES-REQUESTED` ("a role file cannot
       write the verdict line").
   - **Where 3,072 comes from.** It is a decision: a repository's addition
     stays smaller than the role's own instructions. It sits below both
     reviewer prompts as measured with `wc -c` on 2026-11-11: architect
     3,630 bytes, test-reviewer 3,760. `ci/agents.sh` fails if a platform
     prompt shrinks below the cap.
   - **What the check is for.** It is a guard against the cheapest trick,
     not the guarantee. The guarantee is the base read (§2) and the parse.
5. **The record.** The review step's document entry (`review`, beside
   `input_sha256`) gains `instructions: {file, sha256, base}`, or `null`.
   It goes there and not in `run.json`, which five harnesses write: the
   instructions are the step's doing.
6. **`<contract>mcp.json`: MCP servers for the dev-worker, and no other
   role.** The schema, with `deny_unknown_fields`-style strictness:

   ```json
   {"servers": {
     "notes": {"command": ["python3", ".lights-out/mcp/notes.py"], "tools": ["search_notes"]},
     "docs":  {"url": "https://mcp.example.com:443/mcp", "tools": ["search_docs"], "token_env": "MCP_DOCS_TOKEN"}}}
   ```

   - **Names.** A server name matches `^[a-z][a-z0-9]{0,9}$`. A tool name
     matches `^[A-Za-z0-9_-]{1,48}$` and is listed once.
     - Lower-case alphanumerics only: the three CLIs build qualified names
       with `_`, `__` and `.` rules of their own, and Gemini splits at the
       first `_`.
     - Where 10 and 48 come from: Gemini's 63-character cap on a function
       name (external), since `mcp_` + 10 + `_` + 48 = 63.
     - Two tools that Codex's sanitising would make equal are refused.
   - **Limits: 4 servers and 16 tools in all.** This is a decision:
     small enough that every tool the worker can call is named in one diff
     a human reads; raised by amending this ADR.
   - **stdio.**
     - `command` is a non-empty argv list. It runs inside the harness
       container, with the workspace as its working directory.
     - There is no `env` key and no `cwd` key, so the declaration hands it
       no secret. It runs with the model's own privileges and nothing more:
       the same container, the same guard on its calls' arguments, the same
       egress.
     - What the CLI passes it from its own environment differs per harness
       (Verified). A stdio server is for local reads; a server that needs
       the network or a secret is a remote one.
   - **remote.**
     - `url` must be `https`, with an explicit port.
     - Its `host:port` must be a line of the **base's** `crew-egress.txt`,
       else one line: `NO-GO: agents — mcp.json server <n>: <host:port> is
       not in crew-egress.txt (ADR 0046 §5)`. The connection then goes
       through `egress` like everything else.
     - The credential is given by name only. `token_env` matches
       `^MCP_[A-Z0-9_]+$`, so a provider key can never be forwarded to a
       third party.
     - The value comes from the environment, which `.env` feeds as it feeds
       provider keys. Before the container starts, `run.sh` refuses when the
       value is unset or `replace-me`.
     - The variable reaches the container by `-e <name>`. After the run,
       `run.sh` greps `OUT` for the value, as it does for the provider key.
     - There is no `headers` key and no literal value.
   - **`tools` is mandatory and non-empty, with exact names and no
     wildcard.** "Read-only by default" means only this: nothing is
     offered unless it is named. The platform cannot see what a tool does.
     A server's `readOnlyHint` is its own unverified word (MCP spec, ADR
     0069). The docs say to name read tools only, and the human who
     approves the high-risk `mcp.json` is the check.
   - **Timeouts.**
     - Start: 30 s. The source is external: the start default of Claude
       Code and Codex at the pins.
     - Each call: 30 s as well. This is a decision: one number, far under
       Claude's 2-minute background cut, which loses a result in `-p`.
   - **Read from the base.** `crew.sh` runs `git show
     $base:<contract>mcp.json` and the base's `crew-egress.txt` in the
     worker's clone, at the commit the attempt forks from. It never reads
     the working tree, which `crew-prepare.sh` and a rework's previous
     attempt can change.
7. **One module, `core/agents.py`, for everything that names no vendor.**
   It is stdlib-only, engine-free, unit-tested, and imported by the
   logbook as `policy.py` is.
   - **Why not `core/mcp.py`.** That file is the server lights-out exposes
     (ADR 0069 §7). This module is about how a repository customizes its
     agents, role files included. Merging the two would put a client-side
     declaration into a server's mutation scope.
   - **One reader** over either a directory or a commit. It reads commits
     through `map.git`, so there is no new process-call site (ADR 0052 §3).
   - **Verbs, each refusing with one line:**
     - `check` validates the whole of `roles/` and `mcp.json`.
     - `instructions --role` prints §3's block, or nothing.
     - `mcp --harness <h> --out <dir>` validates, then writes `decl.json`,
       the normalised declaration, with its sha256.
     - `calls --allow <file> --hooks <hooks.log>` gives `run.json`'s calls.
     - `lines` gives §10's text.
   - **Who calls it.** `crew.sh` and `review.sh` call `check` at the base
     before anything starts. `crew.sh` passes `CREW_MCP=<dir>`, a directory
     in the run directory, to `run.sh`, and only when servers are declared.
     The name is not `MCP_*`, which belongs to tokens.
   - **The translations live in the adapters.** CLI flags and settings
     keys are vendor concepts (the architect's rule; ADR 0024), so each is
     an adapter verb, `adapters/<h>/mcp.sh <decl.json> <dir> <workspace>`
     (sh and jq, ADR 0052 §1).
     - It writes that harness's configuration into `<dir>`, plus `allow`:
       one line `<qualified name> <server> <tool>` per named tool, in that
       CLI's naming. The entrypoint and `calls` read `allow`.
     - **A harness honours `mcp.json` if and only if it has `mcp.sh`.** The
       harnesses that do are discovered, never listed in `core/`, as the
       conformance suite discovers CLI harnesses.
8. **One declaration, three translations**, one `mcp.sh` each. `run.sh`
   calls it, and the entrypoint reads the result.
   - **claude-code.**
     - `--mcp-config <dir>/claude.json`, keeping `--strict-mcp-config`.
       stdio servers become `{command, args, timeout: 30000}`. Remote
       servers become `{type: "http", url, headers: {Authorization:
       "Bearer ${<token_env>}"}, timeout: 30000}`.
     - `--allowedTools` gains `mcp__<s>__<t>` for each named tool.
       `--tools` is unchanged, since it does not touch MCP.
     - `MCP_TIMEOUT=30000`.
     - **The unnamed tools of a declared server are visible to the model
       and denied** (Verified). `run.json` says so (§9).
   - **codex.**
     - Per server: `-c mcp_servers.<s>.command=…`, `args=[…]`,
       `cwd="<workspace>"`, or `url=…` with `bearer_token_env_var=…`.
     - Then `enabled_tools=[…]`, `required=true`, `startup_timeout_sec=30`,
       `tool_timeout_sec=30`.
   - **gemini-cli.**
     - `mcpServers.<s> = {command, args, cwd, timeout: 30000, includeTools}`
       and `mcp.allowed = [<names>]` in the generated `settings.json`.
     - `GEMINI_DEBUG_LOG_FILE` is set to a file in the container's `/tmp`.
       The entrypoint copies only its `[MCP error]` lines into
       `OUT/mcp-errors.log`.
     - **Remote servers are refused on gemini-cli.** A remote server that
       answers 401 is silent at this pin (Verified), so a missing token
       would become a silently missing tool. stdio failures do reach the
       debug log.
     - A tool named like an excluded built-in (`web_fetch`,
       `google_web_search`, from `tools.exclude`) is refused. Gemini would
       hide it.
   - **A declared server that did not come up fails the run.** Each
     harness reports it in one stderr line naming the server and this
     section, exit 1, with no `pr.json`:
     - claude-code: any `init.mcp_servers` status other than `connected`,
       or any `mcp_server_errors`;
     - codex: `required` makes `exec` fail;
     - gemini-cli: any line in `mcp-errors.log`.
   - **Every other harness refuses.** When servers are declared, `agents.py
     mcp` refuses a harness with no `adapters/<h>/mcp.sh`, in one line.
     `crew.sh` runs it before the clone is handed over, with the network's
     member harness (`AGENT_HARNESS`) when `CREW_HARNESS=network`. The line
     names the harnesses found: `NO-GO: agents — <harness> cannot give the
     dev-worker MCP servers (ADR 0073 §8); use one of: <harnesses with
     mcp.sh>`.
     - antigravity-cli gets no `mcp.sh`: no MCP path of it is verified.
     - adk and inproc get none either.
     - **replay** gets an `mcp.sh` of its own that translates nothing. It
       compares `decl.json`'s sha256 with the fixture's `run.json`
       `.mcp.sha256` (both absent counts as equal) and refuses when they
       differ, naming both. That is the instruction-sha precedent of
       `lights_out_adk/replay.py`.
9. **The guard enforces the list as well, on every harness, and the calls
   are counted where they are guarded.** `guard.sh` stays byte-identical
   across adapters and gains two rules:
   - a `tool_name` that starts with `mcp_` (which covers `mcp__`) and is
     not a word of `CREW_MCP_ALLOW` is denied. The entrypoint sets
     `CREW_MCP_ALLOW` in the CLI's environment, where the model cannot
     change it. It is empty when nothing is declared, so then every MCP
     call is denied.
   - `MCP_[A-Z0-9_]+` joins the deny-list.

   This is a second layer: the CLI's own filter (§8) is the first.
   `run.json` gains `mcp`, which is `null` when nothing is declared:
   - `sha256`;
   - `servers: [{name, transport, endpoint (remote only), tools}]`;
   - `unlisted: "hidden" | "visible-denied"` (claude-code is the second);
   - `calls: [{server, tool, count}]`, from the `ALLOW` lines of `hooks.log`
     whose name is a declared tool's qualified name for that harness;
   - `denied: [{tool_name, count}]`, from its MCP `DENY` lines.

   `hooks.log` is the one record that all three write in one shape. The
   transcripts differ: a hook-denied Codex call has no item, and a Gemini
   `tool_result` has no name. A network's `run.json` keeps each member's
   `mcp` inside its `members` entry.
10. **One wording, CLI and console.** `agents.py lines --contract <dir>`
    returns the lines for the platform's local contract, for example:
    - `test-reviewer: +1,204 bytes from .lights-out/roles/test-reviewer.md`
    - `dev-worker MCP: notes (stdio: python3 .lights-out/mcp/notes.py) —
      tools search_notes`
    - `dev-worker MCP: none`

    Where those lines appear:
    - **`./go agents`.** It prints one line on stdout under the gate
      contract, `GO: agents — <n> role files, <n> MCP servers, <n> tools`
      or `NO-GO: agents — <first refusal>`, and the lines go on stderr. It
      is `make agents` in the workbench.
    - **`ci/workloads.sh` and `make attached`** run `check` on every
      contract they already check.
    - **The logbook** goes to `lights-out/logbook/1.3` with `agents: {lines}`
      (unread, as `thresholds`, when `check` fails).
    - **The console.** The Thresholds screen gains an "Agents" panel that
      prints the lines verbatim. The ticket's back (ADR 0068) shows the
      review's `instructions` and `run.json`'s `mcp.calls`.

    The CLI and the panel show what the contract declares now. A run
    records what it used, at its base.
11. **`lights-out init` drafts neither file.** Its stderr summary gains
    one line: `agents: no role instructions, no MCP servers; see
    docs/use-on-your-repo.md#agents`. Nothing about a repository's review
    preferences or connectors can be inferred. An `.example` file would be
    an entry under `roles/` that §1 must refuse, or a JSON file nothing
    reads, sitting in a high-risk directory. The page carries the examples.

## Tests (for the gate-writer)

- **`core/tests/test_agents.py`.**
  - **Role-file refusals.** One case per refusal, each asserting the exact
    line. Inputs:
    - `dev-worker.md`, `product.md`, `foo.md`, and a subdirectory;
    - a non-UTF-8 file;
    - a file of 3,073 bytes, against one of 3,072 that passes;
    - `APPROVED` alone on a line, `  > APPROVED`, and `` `CHANGES-REQUESTED` ``
      on a line;
    - `Approved changes are…` inside a sentence, which passes.
  - **`mcp.json` refusals.** One case per refusal, each asserting the exact
    line:
    - unknown keys (`env`, `cwd`, `headers`);
    - an empty or missing `tools`, a duplicate tool, a `*` in a tool name;
    - names over the lengths;
    - five servers, 17 tools;
    - an `http` url, and an `https` url without a port;
    - a host missing from `crew-egress.txt`;
    - `token_env` set to `OPENAI_API_KEY`, and to `mcp_x`;
    - two tools that collide under Codex's sanitising.
  - **Read from the commit.** Given a git fixture whose working tree's
    `mcp.json` and `roles/` differ from the commit's, `--rev` reads the
    commit's.
  - **Harnesses without `mcp.sh`.** In a scratch adapters tree,
    antigravity-cli and adk are refused, and the line lists the harnesses
    that have one.
  - **`calls`.** Given an `allow` file and a `hooks.log` holding `ALLOW`
    and `DENY` lines in each harness's naming, plus built-ins, it returns
    `calls` and `denied`.
- **Translation goldens, in `ci/agents.sh`.** Each `adapters/<h>/mcp.sh`
  runs on one stdio and one remote declaration and is compared with a
  golden:
  - claude-code: the `--mcp-config` JSON with `${MCP_X}` literal, and the
    `allow` lines;
  - codex: the `-c` list;
  - gemini-cli: the `settings.json` fragment, and the remote refusal;
  - replay: the digest refusal.
  - **`lines`.** Exact wording.
- **`ci/review.sh`, new cases** on its stub harness, which records the
  input it was given and whether it ran:
  - (i) the PR adds `<contract>roles/test-reviewer.md` with a marker, and
    the base has none. The input has no heading and no marker, and is
    byte-equal to the input without the file.
  - (j) the base holds the file with marker A, and the PR edits it to
    marker B. The input carries A, never B. The heading names the file and
    A's sha256. The closing paragraph comes after the diff. The document's
    `review.instructions.base` equals `BASE_SHA`.
  - (k) the base file makes the stub answer `LGTM` on its first line. The
    step exits 1, and no status is posted as a verdict.
  - (l) the base file has a line `APPROVED`. The step exits 1 with §4's
    line, and the stub harness never ran.
  - (m) the base has `roles/dev-worker.md`. The step refuses, naming
    `worker.md`.
- **`ci/agents.sh`** (new, in `CI_CHECKS`, gate contract). These cases run
  the real `core/crew.sh` with ADR 0033's stub CodeHost and Tracker and a
  stub harness that records `CREW_MCP`:
  - `crew-prepare.sh` rewrites the workspace's `mcp.json` to add a server.
    The stub receives the base's declaration.
  - A remote host missing from the base's `crew-egress.txt` gives exactly
    §6's one line, before the harness runs.
  - With `AGENT_HARNESS=antigravity-cli`, and with
    `CREW_HARNESS=network AGENT_HARNESS=antigravity-cli`, §8's line.
  - Replay with a fixture whose `.mcp.sha256` differs: the refusal naming
    both.
  - `./go agents` on `workloads/minimal`, and on a planted bad contract:
    one stdout line each.
  - The cap stays below both reviewer prompts.
- **`ci/hooks.sh`.** In each vendor's shape:
  - an allowed qualified name passes;
  - an unlisted one, and an `mcp_` name with `CREW_MCP_ALLOW` empty, are
    denied;
  - `echo $MCP_DOCS_TOKEN` through the shell tool is denied.
- **`gates/harness-conformance.sh` case 10, "agents"** (engine; the
  `harness-conformance` job on Actions, docker, every push, no key). The
  fixture is `$WORKLOAD_CONTRACT/mcp/notes.py` in `workloads/minimal`
  (reached through the variable, never named, per `ci/app-free.sh`).
  - **The fixture server.** It is stdio and stdlib Python. It serves both
    MCP eras as `core/mcp.py` does (Claude 2.1.311 spoke 2026-07-28 in ADR
    0069's proof; the other two are not established). It has two read-only
    tools: `search_notes(q)`, which answers `notes-hit:<q>:<cwd>`, and
    `count_notes()`, which answers `notes-count-canary`. It writes nothing
    to stderr.
  - **The declaration.** `workloads/minimal/.lights-out/mcp.json` declares
    it with `tools: ["search_notes"]`. The suite commits that contract into
    the case's BASE and builds `CREW_MCP` with `agents.py mcp` at BASE, as
    `crew.sh` does.
  - **The stub.** It gains an `mcp` op, `{server, tool, args}`, rendered
    in each vendor's naming. Codex's MCP surface under code mode is not
    established; the op is rendered as whatever the request declares, and
    the build finds out which.
  - **(a) Role marker.** `ROLE=test-reviewer`, with the input built by
    `agents.py instructions` from a base role file holding a marker, then a
    final `APPROVED`. Expect:
    - the first request holds `prompt.md`'s first line, then the marker,
      then the restated paragraph, in that order;
    - `final.md`'s first line is `APPROVED`.
  - **(b) Calls, dev-worker.** The steps are `mcp count_notes`, `mcp
    search_notes q=<random>`, then case 1's write and final. Expect:
    - codex and gemini-cli: the first request declares the allowed
      qualified name and not `count_notes`;
    - claude-code: both are declared, and `hooks.log` holds a `DENY` for
      `mcp__notes__count_notes`;
    - all three: the next request's tool result carries
      `notes-hit:<random>:` and the workspace path;
    - `notes-count-canary` is in no request body;
    - `run.json` `.mcp.calls` is `[{server: "notes", tool: "search_notes",
      count: 1}]` and `.mcp.unlisted` is as §9 says;
    - the PR is valid, as in case 1.
  - **(c) A server that does not start.** The declaration's command is
    `python3 -c "raise SystemExit(1)"`. Exit 1, the line names `notes` and
    §8, and there is no `pr.json`.
  - **antigravity-cli.** Case 10 expects §8's refusal from `agents.py mcp`,
    with no container started.
  - **Case 8.** It now also covers case 10's artefacts.

## Alternatives rejected

- **MCP tools for the reviewers.** A judge whose inputs include a server
  the repository chose is a judge the repository partly controls at run
  time. A remote server's answers change without a commit, so "judged by
  the base" would pin the declaration, not the behaviour. The dev-worker's
  tools are bounded by the judges that come after it; a reviewer's are
  not.
- **MCP servers for every role.** The ADK roles consume structured calls
  and replay on a hash (§1). The reviewers are the bullet above.
- **Replacing the platform prompt, or putting the repository's text at
  system level.** That gives repository text the platform's authority and
  needs five per-harness changes. The output contract would no longer be
  the platform's last word.
- **Reading from the PR head, or from the platform's local contract.** The
  head lets a PR choose its own judge's instructions. The local copy lags
  `main` and is not what the run forks from.
- **A `readOnlyHint` preflight** (`tools/list` before the run). It takes
  the server's own unverifiable word, needs a client for both eras plus
  streamable HTTP through the proxy, and Codex and Gemini already filter
  by name.
- **A filtering MCP proxy of our own, so Claude also hides unnamed
  tools.** The same client and server code as above, for a difference the
  guard and Claude's permission check already turn into a denial.
- **Secrets in `mcp.json`** (literal headers, `env`). The file is
  committed and read in the clear; `token_env` names suffice.
- **Opening the ADK roles now.** No consumer asks, and their replay keys
  would need a second hash.
- **`.example` files from `init`, an empty `mcp.json`, a role-file
  directory README.** See §11.
- **Remote servers on gemini-cli with a "no calls" heuristic.** Zero calls
  is also a legitimate run; a silent 401 is a dropped tool.
- **Counting calls from transcripts.** That means three shapes, one of
  which loses denied calls (Codex) and one of which loses names (Gemini).

## Consequences

- **The one that will hurt: a repository can tell its own reviewer to be
  lenient.** The platform fixes who chooses the instructions (the base, a
  high-risk change a human approved) and the verdict's form. It does not
  fix the judgement. Not touched: the gates, the risk policy and the human
  gate before 100%. The page says so, and §10 shows the instructions on
  every review.
- **The tool list is not the same on every harness.** On claude-code the
  model sees a declared server's unnamed tools, and every call to them is
  denied, twice (Claude's permission check and the guard). `run.json` says
  `visible-denied`.
- **A typo'd name in `tools` is offered nowhere and called never.** Without
  a client the platform cannot see the server's list. `calls` shows 0, and
  nothing fails.
- **Remote servers are proven only by unit translation.** The suite's stub
  is plain http, and a test-only `http` exception would be a product knob
  for a test (ADR 0033's rejected switch). The first live remote run is
  the owner's, and the page says so. gemini-cli takes stdio servers only
  until a bump reports a failed remote server.
- **An `MCP_*` token is exposed as the provider key is** (ADR 0046's first
  consequence). It is in the container and reachable by the model despite
  the guard's word list, and can be sent only to allowed hosts.
- **A stdio server is code with the model's privileges.** It runs the
  workspace's bytes at start: the base on attempt 1, the judged head on a
  rework. Its edits are ordinary PR content, high risk when they are under
  `.lights-out/`. On Gemini its `instructions` reach the system prompt.
- **Two readers of `crew-egress.txt`.** The check reads the base's copy;
  the proxy keeps reading the local contract (ADR 0046 §4, unchanged).
  When they differ, the connection is denied, and §8 fails the run in one
  line. That is loud, not silent, and it costs a run.
- **More cost in the ledger.**
  - The logbook goes to 1.3, with a new golden.
  - `RISK_HIGH_PLATFORM` gains `*mcp.json` and `*roles/*.md`. The second
    also flags a user repository's `docs/roles/x.md` as high, which is
    conservative and visible in `risk.json` (ADR 0037's precedent).
  - `guard.sh` grows, in every copy.
- **Codex's MCP surface under code mode is unknown.** Case 10 finds it. If
  MCP tools reach the model only through code mode's JS, the stub follows
  and §8 gains a line.

## End-to-end proof (2026-11-11, once, live)

The real `adapters/claude-code/run.sh`, as `core/crew.sh` runs it, in its
image (Claude Code 2.1.311), on the owner's podman machine, against the
real Anthropic API (`claude-sonnet-5-5`, the owner's key). The workspace
was `workloads/minimal` as a repository of its own, with its committed
`mcp.json` (the `notes` fixture, `search_notes` named). `CREW_MCP` came
from `agents.py mcp` at the base. The ticket asked for one
`search_notes q=canary` call and its answer in `NOTES.md`.

- `system/init`: `notes` `connected`; the model was offered
  `mcp__notes__search_notes` and `mcp__notes__count_notes`, as §8 expects
  (`visible-denied`).
- `hooks.log`: `ALLOW mcp__notes__search_notes`, then `Write`, then `Bash`.
- `NOTES.md` held `notes-hit:canary:/crew/live-m42/workspace`, so the
  server ran in the workspace, inside the container.
- `run.json` recorded `mcp.calls` `[{server: notes, tool: search_notes,
  count: 1}]` and `denied` `[]`. The run took 4 turns, 33,396 tokens and
  145 s, and exited 0.

The scripted-model proof of all three CLIs is case 10 of the suite, on
Actions.

## Amendment 2026-11-11: Codex's MCP surface

Case 10b found it on Actions (run 43007583308): codex's first request named
no MCP tool. The facts below come from two sources. One is the source at
`rust-v0.166.0` (paths under `codex-rs/`). The other is one native run of
the pinned binary on the owner's Mac (the darwin-arm64 package, integrity
equal to `package-lock.json`'s). It ran against the suite's stub on
127.0.0.1, with a fake key, and is marked "seen".

- **Why.** `gpt-6.1-sol`'s bundled metadata sets `tool_mode:
  code_mode_only` (`models-manager/models.json:193`). Eleven of the twelve
  bundled models do. That metadata wins over the `code_mode` and
  `code_mode_only` features (`core/src/tools/mod.rs:66-76`), so `--disable
  code_mode_only --disable code_mode` changed nothing in the request
  (seen).
- **Where the tools are.** Under code mode only, for a model with
  `supports_search_tool`, an MCP tool is *deferred*
  (`core/src/tools/spec_plan.rs:267-287`). It is not a direct tool. The
  `exec` description never renders deferred tools
  (`code-mode-protocol/src/description.rs:279-281`, where
  `_deferred_tools` is unused). The model reaches the tool only from
  `exec`'s JavaScript, as `tools.mcp__notes__search_notes(…)`, and finds
  it in `ALL_TOOLS`, which carries its name and TypeScript declaration.
  Seen: `ALL_TOOLS` listed `mcp__notes__search_notes` and no `count_notes`.
- **`enabled_tools` still hides.** `tools.mcp__notes__count_notes` is not
  a function in the isolate (seen: `TypeError`). `unlisted` stays
  `hidden`.
- **The hook fires.** The nested call runs PreToolUse with `tool_name`
  `mcp__notes__search_notes` and `tool_input` `{"q": …}` (seen; the
  dispatch is `core/src/tools/code_mode/mod.rs:399-407`). `guard.sh`
  logged `ALLOW mcp__notes__search_notes`, and `agents.py calls` counted
  1. With `CREW_MCP_ALLOW` empty, the guard denied the call, the script
  failed with "Tool call blocked by PreToolUse hook", and no
  `mcp_tool_call` item was emitted (seen). So on Codex the second layer of
  §9 holds, and so does counting from `hooks.log`. The `exec` call itself
  fires no hook, as ADR 0041's case 3 already says.
- **No documented way to make the tools direct.** The per-server
  `omit_tools_from` would do it (`protocol/src/config_types.rs:400`).
  Seen: `["deferred", "code_mode"]` put `mcp__notes.search_notes` in the
  request. It was rejected for three reasons:
  - Codex's MCP docs (fetched 2026-11-11) do not mention it.
  - It shows a direct function to a model whose metadata asks for code
    mode only.
  - `code_mode_only_strict_3p_tools` ignores it (`spec_plan.rs:243-252`).

What changes:

- **§8, codex, gains a line.** Under code mode, the named tools reach the
  model only through `exec`. They are deferred, named in no request,
  listed in `ALL_TOOLS` and called as `tools.mcp__<s>__<t>`. The hook sees
  every call. `mcp.sh` and its translation are unchanged.
- **The stub.** Its `mcp` op, on a request that offers code mode's `exec`,
  becomes an `exec`. Its JavaScript prints `ALL_TOOLS: <names>`, then
  calls `tools.mcp__<s>__<t>(args)`.
- **Case 10b.** On such a request it checks that list instead of the first
  request: the named tool must be in it, and `count_notes` must not. This
  replaces the stub bullet and the codex line of Tests (b), and it closes
  the Consequences bullet on Codex's surface.
- **Unchanged.** ADR 0041's case 3 still holds: `jsread` reads with no
  hook, under the same configuration. So do `run.json`'s `unlisted:
  hidden` and the `calls` from `hooks.log`.

A new consequence: on Codex, the model sees a declared MCP tool only when it
runs JavaScript that looks in `ALL_TOOLS`. A model that never looks never
calls the tool, and `calls` shows 0. The live proof against the real
OpenAI API has not run.

## Amendment 2026-11-11: logbook 1.3 is shared with ADR 0071

ADR 0071 (the governed network) also took the logbook to 1.3, on its own
branch. Neither 1.3 was released, so there is one 1.3, not a 1.4. It
carries both slices' fields, all optional: ADR 0071's (each attempt's
`members`, `patch` and `files`; `ci.gates` and each rework entry's
`gates`; each ticket's `files`) and this ADR's (top-level `agents:
{lines}`; each ticket's `task.mcp` and `review.instructions`). There is one
schema, `core/logbook/1.3.schema.json`, and one golden,
`fixtures/logbook/golden-1.3.json`, written by the reader over the stub
sources that now hold both slices' facts. `golden-1.2.json` stays as the
old-logbook fixture, as ADR 0071 says.

## Golden-rule check

`./go demo` is unchanged. At L0 the reviewers and the worker replay, and
the bundled app declares no role file and no `mcp.json`, so every input
and every fixture stays byte-identical. `./go ci` gains a unit file,
`ci/agents.sh` and cases in `ci/review.sh` and `ci/hooks.sh`: local git,
no engine, no network, no key. Case 10 runs where the suite already runs.
Nothing vendor-specific enters `core/`. MCP is a protocol (ADR 0069), and
each CLI's flags and keys stay in that CLI's `mcp.sh`. Passes.
