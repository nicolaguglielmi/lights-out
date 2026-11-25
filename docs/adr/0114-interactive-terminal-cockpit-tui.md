# ADR 0114 — Interactive Terminal Cockpit TUI (`core/tui.py` and `core/tui.sh`)

- Status: proposed
- Date: 2026-11-24
- Milestone: M74
- Amends: ADR 0047 §6 (adds a read-only terminal consumer of the versioned logbook alongside `console/` and `core/network.py`)

## Context

`lights-out` exposes farm state through two existing read-only surfaces over the versioned logbook (`lights-out/logbook/1.x`, ADR 0047):

1. `console/` (`console/serve.py` + `console/index.html`), a browser workstation polling `GET /v1/logbook.json` every `2` seconds with `ETag` / `If-None-Match`.
2. `core/network.py`, a single-ticket plain-text dump of one attempt's governed network (`--logbook <file> --ticket <id>`).

Operators working over SSH, inside terminal multiplexers, or in headless CI/incident sessions lack a zero-dependency interactive terminal cockpit (`top` / `tui`) that surfaces human-turn actions, ticket queues and flow strips, active worker token burn and daily spend projections, gate/rework attempt diagnostics, and canary SLO stage deltas without opening a browser or installing third-party Python packages.

Any terminal consumer inside `core/` must obey the hexagonal purity and shell-boundary gates:
- Pure Python standard library only (`curses`, `json`, `urllib.request`, `argparse`, `dataclasses`) — zero third-party TUI dependencies in `workbench/` or the light package.
- Strict compliance with `ci/vendor-free.sh`, `ci/app-free.sh`, `ci/model-free.sh`, and `ci/shell-boundary.sh` (`<= 30` code lines in `core/tui.sh`).
- Backward compatibility across committed logbook schemas `1.2` through `1.8`, including absent minor-version sections (`agents`, `dora`, `spend`, `roles`, `flow`, `live`, `intakes`, `screen`) and `{ok: false, error}` unread source markers.
- Deterministic testability without a physical TTY, safe degradation under `NO_COLOR=1` or non-TTY pipes, and bounds-safe rendering down to standard `80x24` terminals.

## Decision

### 1. Read-Only Logbook Consumer & Conditional HTTP Polling (`core/tui.py`)

`core/tui.py` reads only the versioned `lights-out/logbook/1.*` document — never `.crew/` internals, `adapters/`, or policy files directly — from one of two sources:

- **Local file (`--logbook <path>`)**: defaults to `LOGBOOK_FILE` or `.crew/logbook.json` when `--url` and `LOGBOOK_URL` are unset. Tracks `(mtime_ns, size, sha256)` so unchanged files trigger zero re-renders.
- **Live HTTP endpoint (`--url <url>`)**: defaults to `LOGBOOK_URL` when set (normalizing bare `http(s)://host:port/` origins to `/v1/logbook.json` and rejecting non-`http(s)` schemes). Sends `Accept: application/json` and `If-None-Match: <etag>` on subsequent polls:
  - `200 OK`: validates `lights-out/logbook/1.*`, stores `ETag`, and marks state changed.
  - `304 Not Modified`: reuses the cached logbook with zero JSON parsing and zero screen redraw.
  - Transient poll errors after an initial valid load preserve the last good logbook and surface the error in the status bar; initial load failures exit `1` with one diagnostic line on stderr.

### 2. Three-Layer Pure Architecture (Model / Reducer / Renderer vs `curses` Loop)

To make rendering and keyboard navigation deterministically testable in headless unit tests without a pseudo-terminal:

1. **Normalized View-Model & State (`TuiState`)**: pure dataclasses holding the current logbook, active view index (`0..4`), selected ticket ID/index, scroll offset, live `/` filter string and editing mode, `ETag`, and poll status.
2. **Pure Key Reducer (`reduce_key(state, key) -> TuiState`)**: maps normalized key tokens (`"1"`–`"5"`, `"TAB"`, `"BTAB"`, `"j"`, `"k"`, `"UP"`, `"DOWN"`, `"PAGE_UP"`, `"PAGE_DOWN"`, `"g"`, `"G"`, `"/"`, `"ENTER"`, `"ESCAPE"`, `"BACKSPACE"`, `"r"`, `"q"`) to next `TuiState` values without side effects.
3. **Pure Line-Buffer Renderer (`render_frame(state, width, height) -> Frame`)**: formats an exact `height`-row ASCII-safe frame (`FrameRow` list with semantic style tags `header`, `tab_active`, `go`, `nogo`, `wait`, `muted`, `body`, `status`, `footer`) clipped strictly to `width` columns (`width - 1` on row `height - 1` so `curses` `addnstr` never writes the bottom-right auto-scroll cell).
4. **Thin Terminal Driver (`run_curses`)**: runs `curses.wrapper`, configures `curses.halfdelay` / non-blocking `getch` timed to `--poll-s`, disables color pairs when `NO_COLOR` is set, catches `KEY_RESIZE`, and blits `Frame` rows via a bounds-clamped helper.

### 3. Five Switchable Cockpit Views (`1`–`5`, `Tab` / `Shift+Tab`)

1. **`1` — Overview & Waiting-for-You**:
   - **Waiting for You** first (ADR 0063): unanswered `held` releases (`go-for-launch`), tickets at `questions` (open question), tickets at `needs-human` (rework cap or spend stop), `stranded` tickets (`resume`), and open `intakes` (`intake-questions`, `proposed`, `stranded`).
   - **Flow Bands**: ticket counts across `Spec`, `Build`, `Test and review`, `Release`, and `Other` plus per-state breakdown.
   - **DORA & Spend Summary**: 4 DORA measures (`dora`), total/projected USD spend (`spend`), active in-flight worker alert, and unread/schema-version notices when absent or `{ok: false}`.
2. **`2` — Tickets & Flow Strip**:
   - Keyboard-navigable ticket list (`j`/`k`/Up/Down, `g`/`G`) with live `/` substring/glob filter across ticket `id`, `title`, `state`, `feature`, `priority`, and `perimeter`.
   - Selected ticket detail pane: `state`, band, `outcome`, `wait` (`perimeters_held`), governing `adrs`, `perimeter` globs / touched `files`, the 14-node `flow` strip (`1.6`–`1.8`, or a derived stage strip on `1.2`–`1.5`), and recent `timeline` / `history` rows.
3. **`3` — Workers & Token Burn**:
   - Six-role matrix (`roles` in `1.6`–`1.8`, or derived from ticket run records on `1.2`–`1.5`): configured harness/provider/thinking, state (`running`, `ran`, `idle`), and token meter against `TOKEN_BUDGET` (`WARN` at `>= 80%` or `warn: true`).
   - Active runs in flight (`tickets[].live` / `roles[].running`) vs `TOKEN_BUDGET`.
   - Daily spend & burn projection (`spend.burn`): `spent_usd`, `per_hour_usd`, `projected_usd` vs `SPEND_BUDGET_DAY_USD`, `SPEND_BUDGET_TICKET_USD`, and a 24-hour ASCII sparkline.
4. **`4` — Gates & Rework**:
   - Ticket selector (`j`/`k`/arrows) with visual rework pips out of `REWORK_MAX_ATTEMPTS` (from `thresholds`, fallback `4`).
   - Attempt history (`rework`, `ci`, `review`): per-gate verdicts and durations across the 8 PR gates, `NO-GO` lines, `why.details`, and `sast` findings (preserving `{ok: false, error: "withheld: ..."}` redaction when `secrets` did not pass).
5. **`5` — Canary SLO & Ops**:
   - Live delivery plane (`launch.plane`: `stable`, `canary`, `weight`%) and human gate (`launch.pressable`, `preparable`, `disabled_reason`).
   - Canary stage percentages (`5%`, `25%`, `held`, `100%`) and `error_rate` / `latency_p95` deltas vs `ERROR_RATE_MAX_DELTA` and `LATENCY_P95_MAX_DELTA` (from structured `stage.slo` on `1.4`+ or `stage.numbers` on `1.2`–`1.3`).
   - Automatic rollbacks, incidents, and `postmortems` with causes and linked fix action tickets.

### 4. Non-Interactive, Non-TTY, `NO_COLOR=1`, and `--json` Modes

- `--once` (and automatic fallback when stdin/stdout is not a TTY): renders a deterministic plain-text snapshot of the requested `--view` with zero ANSI escape sequences and exits `0`.
- `--json`: emits a deterministic JSON summary (`schema: "lights-out/tui-summary/1.0"`) covering `waiting_for_you`, `bands`, `tickets`, `workers`, `gates_rework`, `canary_ops`, and `unread_sections`.
- `NO_COLOR` (any non-empty value, `CLAUDE.md`): suppresses terminal color initialization in `curses` and guarantees zero ANSI sequences on stdout.
- `core/tui.sh`: thin POSIX wrapper (`<= 30` code lines) unpacking optional `LIGHTS_OUT_CLI_ARGS` (`\037`-separated, ADR 0103) and executing `python3 core/tui.py "$@"`.

## Provenance

| Parameter / Constant | Value | Kind | Provenance |
|---|---|---|---|
| `DEFAULT_POLL_S` (`--poll-s`) | `2.0` s | decision | Inherited from `CONSOLE_POLL_S = 2` in ADR 0047 §4 (`console/serve.py`) and ADR 0035 §3 (`board` 2 s beat) so canary stage transitions and human presses appear within one beat while `304 Not Modified` keeps idle bandwidth near zero. |
| `MIN_POLL_S` | `0.1` s | decision | Floor preventing busy-wait CPU spin loops when an operator passes a sub-100 ms `--poll-s` value; `curses` input slice uses the matching `100` ms (`timeout(100)`) so `q` quit remains responsive between polls. |
| `MIN_COLS` x `MIN_ROWS` | `80` x `24` | external | ECMA-48 / DEC VT100 standard 80-column by 24-row terminal grid; `render_frame` and `draw_curses_frame` also clamp safely below `80x24` during live window resizes and stop at `width - 1` on row `height - 1` to avoid ncurses bottom-right auto-scroll errors. |
| `DEFAULT_HTTP_TIMEOUT_S` (floor `0.5` s) | `5.0` s | decision | Half of `console/serve.py`'s `10` s interactive check budget (`CHECK_TIMEOUT_S = 10`, ADR 0059 §4) so stalled endpoints surface a status-bar warning within ~2.5 poll beats; `0.5` s minimum prevents immediate loopback timeouts. |
| `MAX_LOGBOOK_BYTES` | `16,777,216` B (`16 MiB`) | decision | Memory ceiling (~150x `fixtures/logbook/golden-1.8.json`, measured at `107,953` bytes) bounding local/HTTP reads against malformed payloads; file digest slice uses 16 hex chars (`64` bits) for compact status-bar display only. |
| `TOKEN_WARN_RATIO` | `0.80` (`80%`) | decision | Mirrors ADR 0087 (`live.warn` at 80% of `TOKEN_BUDGET`), used strictly as a display fallback when `warn` is omitted. |
| `DEFAULT_REWORK_MAX` | `4` | decision | Mirrors `REWORK_MAX_ATTEMPTS=4` in `policy/rework.env` (ADR 0038 §3), used strictly as a display fallback when `thresholds` is unread. |
| Six governed roles / eight PR gates / five views / 24h sparkline | `6` / `8` / `5` / `24` | decision / external | `6` roles from `ci/roles.sh`; `8` PR gates from `core/network.py` `TABLE["gates"]`; `5` cockpit views (`1`–`5`) decided for M74; `24` UTC hours per day (`spend.burn.hourly`, ADR 0087); `10`-cell meter bar and `10`/`50`-row page/end scroll steps are display-only UI geometry. |
| `core/tui.sh` code-line ceiling | `<= 30` lines | decision | Enforced by `ci/shell-boundary.sh` (ADR 0052 §4); `core/tui.sh` is `11` code lines. |

## Alternatives rejected

- **Third-party Python TUI frameworks (`textual`, `rich`, `urwid`, `blessed`):** Adds external dependencies to `workbench/uv.lock` and breaks zero-install stdlib portability. Python's built-in `curses` plus a pure line-buffer renderer requires zero external packages.
- **Reading `.crew/`, `adapters/`, or `policy/*.env` directly from `core/tui.py`:** Duplicates `core/logbook.py`'s normalization rules and breaks remote HTTP cockpit monitoring (`--url`). Consuming only `lights-out/logbook/1.*` keeps one source of truth (ADR 0047 §2, §6).
- **Coupling rendering directly to `curses` window calls:** Makes unit testing require fragile PTY emulators. Separating `TuiState`, `reduce_key`, and `render_frame` tests every view, keystroke, filter, and `80x24` boundary deterministically in milliseconds.

## Consequences

- Operators can monitor live or snapshot logbooks from any terminal (`sh core/tui.sh` / `python3 core/tui.py`), inspect `--once` plain-text snapshots in logs, or pipe `--json` summaries into scripts.
- All 7 historical minor schemas (`1.2`–`1.8`) and `{ok: false}` unread sections render cleanly without exceptions.
- Read-only by construction: `core/tui.py` never mutates tower state or executes promotion writes, preserving the single audited promotion path (`core/promote.sh`).

## Golden-rule check

`./go demo` gains no container, no network call, and no required secret. `core/tui.py` uses Python stdlib only, runs offline against local logbooks and L0 snapshots, and keeps `core/` vendor-free, app-free, model-free, and within the 30-line shell boundary.

## Tests

- `core/tests/test_tui.py`: spec-derived unit tests covering `TuiState` initialization, all 5 views, `Tab`/`1`–`5` view switching, `j`/`k`/arrow navigation, `/` search filter & `Esc` reset, `80x24` and sub-`80x24` viewport clamping, curses bottom-right cell safety, `NO_COLOR=1` and `--once` / `--json` modes, `{ok: false}` unread handling, withheld secret details, and `ETag` / `If-None-Match` `200` + `304` + transient error HTTP polling.
- `ci/tests/test_tui_golden.py`: golden compatibility and integration tests replaying every committed `fixtures/logbook/golden-1.2.json` through `golden-1.8.json` across all 5 views at `80x24`, `--once`, `--json`, `core/tui.sh` invocation, and a live local `ThreadingHTTPServer` `ETag`/`304` loop.
