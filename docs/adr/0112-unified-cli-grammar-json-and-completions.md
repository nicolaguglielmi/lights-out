# ADR 0112 — Unified power-user CLI: noun-verb grammar, `--json`/`--jsonl`, exit codes and shell completions

- Status: accepted
- Date: 2026-11-24
- Milestone: M72 (Unified Power-User CLI)

## Context

Today a power user meets lights-out through three surfaces that grew separately:

- `./go <target> KEY=VALUE ...`, a flat list of about eighty make targets (clone only);
- `lights-out <command> KEY=VALUE ...`, the wrapper over the published image (ADR 0045, 0105), twenty-four flat commands and a closed allowlist of 29 `KEY=VALUE` names;
- `core/start.py`, which already owns the POSIX flags of `start` and `next` (ADR 0103).

The gaps this ADR closes:

1. **No grammar.** Flat names (`intake-confirm`, `alert-webhook`, `auto-incident`) do not group by what they act on, and there is no `--flag value` form outside `start`/`next`.
2. **No machine-readable output.** Only `start --json` and `next --json` emit JSON; every other command prints a gate line that scripts must scrape.
3. **No exit-code vocabulary.** Every non-zero exit is `1`, so a script cannot tell a NO-GO from a typo, a ticket waiting for a human, or a budget stop.
4. **No completions.**
5. **Wrapper parity.** `try`, `watch`, `alert-webhook`, `auto-incident`, `incident`, `audit`, `resume`, `request`, `map`, `network`, `production-readiness` and `scoreboard` exist in `./go` but not in `lights-out` or `package/entrypoint.sh`.

Constraints inherited and kept:

- The gate output contract (CLAUDE.md, `ci/lib.sh`, `ci/contract.sh`): a gate emits exactly one stdout line, `GO: ...` or `NO-GO: ...`; details on stderr.
- `core/` stays vendor-free, app-free and model-free (`ci/vendor-free.sh`, `ci/app-free.sh`, `ci/model-free.sh`), and shell in `core/` stays at most 30 code lines (`ci/shell-boundary.sh`).
- The wrapper's and the entrypoint's `KEY=*` allowlists stay identical and are the 29 keys of ADR 0105 (`ci/package.sh`, `core/tests/test_wrapper_plane.py`).
- The host has a container engine, git and `sh`, nothing else: the wrapper cannot depend on Python.

## Decision

### 1. Grammar: `lights-out <domain> <verb> [args]`

Six domains. Each verb maps to exactly one flat command (the flat name is what `./go`, the wrapper's tiers and the entrypoint already use).

| domain | verbs (flat command in parentheses when it differs) |
|---|---|
| `repo` | `init`, `try`, `gates`, `test-proof`, `test-integrity`, `doctor`, `map`, `corpus` |
| `ticket` | `intake`, `request`, `answer`, `intake-confirm`, `assign`, `resume`, `network`, `audit` |
| `release` | `up`, `down`, `smoke`, `deploy` (`release`), `promote`, `evidence`, `production-readiness` |
| `ops` | `watch`, `alert-webhook`, `triage`, `auto-incident`, `incident`, `postmortem`, `dora`, `spend` |
| `config` | `start`, `next`, `cloud-setup`, `scoreboard`, `agents` |
| `completion` | `bash`, `zsh`, `fish` |

Rules:

- **Flat commands keep working, unchanged.** `lights-out gates BASE=a HEAD=b` and `lights-out repo gates --base a --head b` are the same invocation. `console` and `version` stay flat only.
- **`release` is both a domain and a flat command.** `lights-out release <word>` is the domain when `<word>` is one of its verbs; any other arguments (`CANARY_VERSION=...`, `--json`, nothing) are the flat deploy command. No flat `release` invocation ever starts with a bare word, so the two cannot collide.
- A domain without a verb, or with an unknown verb, is a usage error (exit `2`) that lists the verbs of that domain. `lights-out <domain> --help` lists them and exits `0`.
- `corpus` and `agents` need a clone (bundled fixtures, the contract of an attached repository); the published image does not offer them. `core/cli.py` dispatches them to `./go`; the wrapper refuses them as a usage error naming `./go`.

### 2. Flags: POSIX `--flag value` and `--flag=value`

- A flag is translated to the `KEY=VALUE` the existing commands already take: strip `--`, upper-case, `-` becomes `_`. `--ticket 0001` and `--ticket=0001` are `TICKET=0001`.
- Two boolean shorthands: `--staged` is `STAGED=1`, `--once` is `ONCE=1`.
- **Native flags are not translated.** `start` and `next` own `--non-interactive`, `--preset`, `--provider`, `--harness`, `--delivery`, `--profile`, `--ticket-budget-usd`, `--day-budget-usd` and `--mode` (ADR 0103); `--engine` belongs to the wrapper's gate tier (ADR 0108). They pass through verbatim. `doctor` passes `--level <l0|l1|l2|l3>` to `./go` (ADR 0057 §8).
- `KEY=VALUE` positional arguments keep their meaning and may be mixed with flags.
- A translated key must be in the 29-key allowlist of ADR 0105 or in the **per-command extension keys** below; anything else is a usage error that names the allowed keys. The extensions are carried by a per-command function in the wrapper and the entrypoint, not by new `KEY=*` case arms, so the 29-key allowlist equality of ADR 0105 is untouched.

| command | extension keys |
|---|---|
| `request` | `REQUEST_BY`, `REQUEST_VIA` |
| `network` | `ATTEMPT` |
| `watch` | `ONCE`, `POLL_S` |
| `alert-webhook` | `ALERT_FILE` |
| `auto-incident` | `RELEASE_RECORD` |
| `incident` | `NAME`, `FROM`, `TO`, `LOGS` |
| `map` | `MAP_FILE` |
| `cloud-setup` | `SLO_MODE` |

- **Secrets are never arguments.** A `KEY=VALUE` whose key ends in `_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD` or `_HEADER` is a usage error: credentials are passed by environment variable name only (ADR 0045 §2).

### 3. Exit-code taxonomy

| code | meaning | rule |
|---|---|---|
| `0` | GO | the last stdout line is `GO: ...` and the command exited `0` |
| `1` | NO-GO | the last stdout line is `NO-GO: ...`, or the command failed without a more specific reason |
| `2` | usage or contract error | the command line is refused before anything runs; stdout stays empty |
| `3` | waiting on a human or a lock | a `NO-GO:` line naming `needs-human`, `awaiting`, `waiting for`, `waiting on`, `held at`, `lockfile` or `locked` |
| `4` | budget stop | a `NO-GO:` line naming `budget` or `SPEND_STOP` (checked before `3`) |

Provenance (the codes are a vocabulary, not a measured threshold): `0` and `1` are the gate contract of CLAUDE.md. `2` is the usage-error convention of POSIX tools and of `make`; inside `make` it means a failed recipe, so a child that exits `2` is classified from its line like any other failure and a usage error is only ever raised by the CLI itself. `3` and `4` are new and chosen so they cannot be confused with `1` or `2`; the marker words are the ones the repository already prints: `needs-human` and the budget lines come from `core/tower.py` (ADR 0083 §4, 0087 §1), `waiting for` from `core/start.py` (ADR 0103), `holds lockfile` from `core/runner.py` (ADR 0109).

`3` and `4` are derived only for commands that are **not gate-shaped** (§5). The eight PR gates and the commands that wrap them keep the two-valued contract: GO is `0`, everything else is `1`.

The wrapper applies `2` to its own usage errors (unknown command, unknown or missing argument, a domain without a verb). Environment failures (no engine, no socket) stay `1`, as today, with exactly one stderr line (ADR 0105). Inside the image, `package/entrypoint.sh` keeps the one-line gate contract for its own refusals (`NO-GO: ...`, exit `1`), because the wrapper has already refused every usage error before it starts the container.

### 4. Structured output: `--json` and `--jsonl`

`--json` and `--jsonl` are universal for non-gate-shaped commands; they are consumed by the CLI and never forwarded to `make`. Exactly one of them may be given.

`--json` prints exactly one stdout line, the **envelope**, and the exit code follows §3:

```json
{"schema": "lights-out.cli/1", "command": "ticket assign", "flat": "assign",
 "verdict": "GO", "exit_code": 0, "line": "GO: assign",
 "args": {"TICKET": "0001"}, "secrets": {"DEMO_SERVICE_TOKEN": true}}
```

- `verdict` is `GO`, `NO-GO`, `USAGE`, `WAITING` or `BUDGET`; `line` is the last stdout line of the command (empty if none).
- `args` holds the `KEY=VALUE` pairs the command ran with (secret-named keys cannot be there, §2).
- `secrets` maps the **name** of every environment variable whose name ends in `_KEY`, `_TOKEN`, `_SECRET` or `_PASSWORD`, plus every name in the space-separated `LIGHTS_OUT_SECRET_NAMES`, to a boolean: set and non-empty, or not. Values are never read into the output. As a second line of defence, any secret value of four or more characters that appears verbatim in `line` or `args` is replaced by `<redacted>`. Provenance of 4: decision; a shorter value (`1`, `on`) would mangle ordinary text for no protection.
- A usage error under `--json` still prints an envelope (`verdict: USAGE`, plus an `error` field), then exits `2`.

`--jsonl` prints one JSON object per line: an `{"event": "stdout", "line": "..."}` for each stdout line of the command as it arrives, then a final `{"event": "verdict", ...}` carrying the envelope fields above. stderr is never wrapped in either mode.

`start --json` and `next --json` keep their native documents (ADR 0103); `--jsonl` is not offered for them.

### 5. Gate-shaped commands never take `--json`/`--jsonl`

The gate output contract is exactly one `GO:`/`NO-GO:` stdout line (ADR 0042 §3, `ci/contract.sh`). To keep every gate command byte-identical for the Action and for scripts, **gate-shaped commands refuse `--json` and `--jsonl`** with a usage error (exit `2`, empty stdout): the whole `repo` domain (`init`, `try`, `gates`, `test-proof`, `test-integrity`, `doctor`, `map`, `corpus`) and `production-readiness`. Scripts that want structure for a gate read its single line and its exit code (`0`/`1`). Opting into `--json` is explicit and only exists on the non-gate workflows (`ticket`, `ops`, the rest of `release` and `config`).

### 6. Surfaces

- **`core/cli.py`** (stdlib only) is the single source of truth for the grammar: the command table, flag translation, the plan, the exit-code classification, the envelopes and the completion scripts. From a clone, `python3 core/cli.py ...` (or `sh core/cli.sh ...`, a thin launcher) dispatches the plan to `./go <flat> ...`; `--dry-run` prints the plan as JSON instead of running it. The internal verb `envelope` wraps a command's stdout, read from stdin, for the entrypoint.
- **`lights-out`** (the wrapper) re-implements the grammar's front half in POSIX `sh`, because the host has no Python: domain-verb resolution, flag translation, the per-command key extensions and `completion`. It then picks the socket tier of the flat command:

| tier | commands | engine socket |
|---|---|---|
| readonly (`/repo:ro`, `/out`) | `init`, `gates`, `test-proof`, `test-integrity`, `triage`, `postmortem`, `doctor` | never (unless `--engine`, ADR 0108) |
| state (`.crew` only) | `next`, `dora`, `spend`, `cloud-setup`, `map`, `network`, `scoreboard`, `auto-incident`, `incident`, `alert-webhook` | never |
| plane (mirrored platform dir) | `start`, `intake`, `answer`, `intake-confirm`, `assign`, `up`, `down`, `smoke`, `release`, `promote`, `console`, `evidence`, `try`, `watch`, `request`, `resume`, `audit`, `production-readiness` | mounted, with the one-line stderr notice (ADR 0105) |

  `incident` moves to the plane tier when `TICKET=` is given: that is the option that needs the state store (Makefile `incident`). `alert-webhook` takes `ALERT_FILE=` (a path in the repository) and normalizes it; serving the webhook needs a published port and stays a clone command (`./go alert-webhook`). `try` runs at the plane tier because the gates it runs on a drafted contract may need the toolchain image.
- **`package/entrypoint.sh`** accepts the same domains, flags and extension keys (so `podman run <image> ticket assign --ticket 0001` works), runs the new commands (§6 table), and wraps stdout in the envelope by re-running itself under `python3 core/cli.py envelope` when `--json`/`--jsonl` is present.
- A parity test (`ci/tests/test_cli_wrapper_parity.py`) fails when the table in `core/cli.py` and the wrapper or the entrypoint disagree about domains, verbs, flat names, extension keys, translation or completion output.

### 7. Completions

`lights-out completion bash|zsh|fish` prints a completion script and exits `0`. It needs no engine and no Python: the wrapper generates it in `sh`; `core/cli.py completion <shell>` generates the same bytes (enforced by the parity test). The scripts complete, in order: the domains and flat commands as the first word; the verbs of a domain as the second; the flags (§2 and the key flags `--ticket`, `--base`, ...) afterwards; `bash|zsh|fish` after `completion`. Installation is the user's one line (`eval "$(lights-out completion bash)"`, `lights-out completion fish | source`); lights-out never writes outside its state directory.

## Provenance

Every number in this ADR, and where it comes from. None is a measured threshold; they are inherited constants or vocabulary decisions.

| number | what | provenance |
|---|---|---|
| `29` | keys in the wrapper's and entrypoint's allowlist | inherited, ADR 0105 §4; enforced by `ci/package.sh` and `core/tests/test_wrapper_plane.py`; this ADR adds none to it |
| `0`, `1` | GO, NO-GO exit codes | inherited, the gate output contract (CLAUDE.md, `ci/lib.sh`) |
| `2` | usage or contract error | decision; the POSIX/`make` convention for a refused command line, chosen because no gate emits it (`make` reports a failed recipe as `2`, which the CLI never takes as usage: §3) |
| `3`, `4` | waiting on a human or a lock; budget stop | decision; the first free codes after `2`, so a caller can branch with `-ge 3`; the marker words are inherited from `core/tower.py`, `core/start.py` and `core/runner.py` (§3) |
| `4` characters | shortest secret value redacted from output | decision; below it a value (`1`, `on`) would mangle ordinary text for no protection (§4) |
| `1` in `lights-out.cli/1` | envelope schema version | decision; first version, bumped only by an incompatible change to a field of §4 |
| `30` | code lines allowed in `core/cli.sh` | inherited, ADR 0052 §4, enforced by `ci/shell-boundary.sh`; the launcher is a handful of lines |

## Alternatives rejected

- **A Python `lights-out` on the host.** Rejected: the host has no Python (ADR 0045 §1), and the wrapper must stay a single file installable by `curl` or `uv tool install` (ADR 0099).
- **Renaming flat commands.** Rejected: ADR 0045/0105 users, the Action and `./go` scripts depend on them; the domains are an additive spelling.
- **New `KEY=*` allowlist arms for the extension keys.** Rejected: ADR 0105's equality of the wrapper's and the entrypoint's allowlists is enforced by `ci/package.sh` and `core/tests/test_wrapper_plane.py`; per-command functions extend it without changing it.
- **`--json` on the eight gates.** Rejected: it would break the one-line contract that `ci/contract.sh` and the Action match on; a gate's structure is its line and its exit code.
- **Distinct exit codes for every NO-GO reason.** Rejected: five classes are what a caller branches on (done, failed, wrong call, wait, stop); the reason is in `line`.

## Consequences

- Scripts can branch on `0`/`1`/`2`/`3`/`4` and parse one envelope line per non-gate command.
- The wrapper gains about twelve commands, the domain grammar and completions with no new dependency on the host.
- Wave B wires `./go`, `Makefile` targets and the roadmap to this CLI; this ADR changes none of them.
- Known limit: the wrapper's `--jsonl` is emitted when the command ends (the entrypoint captures stdout), not as it streams; `core/cli.py` streams.
