# ADR 0124 — Modular Console Source Build and SSE Real-Time Event Streaming (`/v1/events`)

- Status: proposed
- Date: 2026-11-24
- Milestone: M84 — Modular Console Build & SSE Real-Time Event Streaming (`swarm/m84-modular-console-sse`)
- Amends: ADR 0047 §3–§5 (adds read-only `GET /v1/events` SSE notification route alongside `GET /v1/logbook.json` and `GET /v1/console.json`); ADR 0059 §1 (progressively triggers immediate conditional `ETag` fetches on SSE `logbook` events in live mode while preserving the 2 s `If-None-Match` poll fallback and zero SSE activity in snapshot mode)

## Context

`console/` currently maintains its entire 11-screen browser workstation (`#overview`, `#hive`, `#kanban`, `#network`, `#tickets`, `#ticket/<id>`, `#specs`, `#request`, `#adrs`, `#incidents`, `#launch`, `#thresholds`) plus four modal dialogs (`#card-back`, `#why`, `#launch-confirm`, `#onboard-wizard`) inside a single hand-edited 3,338-line `console/index.html` file, paired with `console/serve.py`.

Two structural limitations slow down Wave 5 workstation evolution (`M84`–`M86`) and live operator feedback:

1. **Monolithic 3,338-line inline HTML/CSS/JS source.** Editing styles, Kanban/Network SVG graphs, Hive walkthroughs, and runtime networking inside one 223 KiB file increases merge contention and review risk. Yet `console/index.html` must remain a single, committed, offline-capable, zero-CDN artifact satisfying strict Content-Security-Policy (`default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'`) and every static/DOM invariant in `ci/logbook.sh` and `ci/console-ui.py`.
2. **2-second polling latency in live mode.** Even when `console/serve.py` completes a new `core/logbook.sh` read (either on its loop or immediately after a human `POST` press), connected browser tabs wait up to `2` seconds (`CONSOLE_POLL_S`) to discover that `etag` changed. Operators watching active agent turns, gate verdicts, rework transitions, or canary stages benefit from sub-second push notifications without giving up `ETag`/`304` HTTP caching or resilient fallback polling.

Every change must uphold the existing `ci/logbook.sh` and hexagonal meta-gate boundaries:
- Zero occurrences of forbidden internal names in non-`.md` files under `console/`.
- Zero `<form`, `XMLHttpRequest`, `sendBeacon`, external URLs, or `fetch(...)` calls outside `/v1/`, and `draggable` restricted strictly to the conditional held-release card inside `card()`.
- Zero new `POST` routes, subprocess scripts, or `make` targets in `console/serve.py`.
- `#your-turn` first in `#overview`, `#journey` last, `#tower-machine` and `#network-words` exact, `#hive` and `#onboard-wizard` intact, and WCAG 2.1 AA light/dark tokens unchanged.

## Decision

### 1. Ordered Modular Source (`console/src/`) and Deterministic Stdlib Bundler (`console/build.py`)

Split `console/index.html` into nine ordered, domain-scoped source modules under `console/src/`:

1. `console/src/00-head.html` — `<!doctype html>`, SPDX header, architectural overview comment, and `<head>` metadata.
2. `console/src/01-styles.css` — Light and dark WCAG 2.1 AA custom-property palettes, responsive layout (`720px` and `900px` breakpoints), Kanban, Flow strip, Network SVG, Journey bar, modal scroll containment, Hive Control Center, and Onboarding Wizard styles.
3. `console/src/02-shell.html` — Pre-paint theme initializer script, `<header>`, single-line `<nav id="nav">`, `<main id="main">`, the four modal `<dialog>` containers, `#announce` polite live region, `#tower-machine` JSON table, and `#network-words` JSON table.
4. `console/src/03-state.js` — Strict-mode preamble, runtime state bindings, `h()` text-safe DOM builder, `MACHINE` / `now()` turn logic, `#why` failure dialog, card face badges, and `flowFace` / `flowDetail` strips.
5. `console/src/04-screens.js` — Overview (`rolesPanel`, `doraPanel`, `spendPanel`, `screenOverview`, `journeyPanel`), hash router (`route`, `kanbanView`, `viewHash`, `setView`), weekly tickets, ticket timeline + open-question reply, incidents/postmortems, go-for-launch + live canary + `#launch-confirm`, `screenThresholds` / agents (ADR 0047, ADR 0073), specs, request editor + intake start, and ADR search.
6. `console/src/05-kanban-network.js` — Kanban board (`noteMoves`, `fly`, conditional held-launch `card`, `drawer`, `intakesPanel`, `screenIntake`, `drawBoard`, `showBack`, arrow-key navigation) and Governed Network (`netView`, `netReveal`, `netGraph`, `screenNetwork`, `noteNet`, `startReplay`).
7. `console/src/06-hive-wizard.js` — Animated Hive Control Center (`#hive`, 7-phase walkthrough, Junior/Expert tabs) and Interactive Repo Wizard (`#onboard-wizard`).
8. `console/src/07-runtime-sse.js` — Theme selector persistence, `header()`, focus preservation (`focusMark`/`refocus`), `render()`, conditional `ETag` `load()`, coalesced `tick()`, progressive `EventSource("/v1/events")` client, and boot listeners.
9. `console/src/99-tail.html` — Closing `</body>` and `</html>`.

`console/build.py` is an executable (`+x`) pure Python stdlib script with zero third-party packages:
- Enforces an explicit ordered `MANIFEST` and rejects unlisted/orphan files in `console/src/` so every source file under `console/src/` is deterministically governed.
- Wraps `.css` modules in `<style>...</style>` and concatenates consecutive `.js` modules into the single main `<script>...</script>` block, preserving single-file offline portability and Node VM extraction compatibility (`main_js[:boot_idx]`).
- `--write` (default): writes deterministic UTF-8 output (`\n` line endings, zero timestamps or machine paths) to `console/index.html` (or `--out <path>`).
- `--check`: compares freshly assembled output byte-for-byte against `console/index.html` (or `--out <path>`), exiting `0` when identical and exiting `1` with a unified diff preview and exact `python3 console/build.py --write` remediation hint on drift.

### 2. Server-Sent Events Endpoint (`GET /v1/events` in `console/serve.py`)

Add `GET /v1/events` to `console/serve.py`:
- **Authentication & Security Headers**: Runs through the exact `identity(self.headers)` gate at the top of `do_GET` (returning `401` in `CONSOLE_AUTH=proxy` when `CONSOLE_IDENTITY_HEADER` is missing). Responds `200` with:
  - `Content-Type: text/event-stream; charset=utf-8`
  - `Cache-Control: no-store`
  - `X-Content-Type-Options: nosniff`
  - `Content-Security-Policy` matching all other Console responses.
- **Condition-Synchronized State Transitions**: `state` gains a monotonic `seq` counter guarded by `state_cond = threading.Condition()`. Inside `_read_once(out)`, `state_cond.notify_all()` fires whenever `etag` changes or `read_error` changes (both on new error and on error recovery).
- **Event Frame Schema**:
  - When `state["etag"]` is present:
    `event: logbook\ndata: {"etag": "<quoted-sha256>", "mode": "live"|"snapshot", "read_at": "<iso8601|null>", "read_error": <str|null>}\n\n`
  - When `state["etag"]` is `None` (before the first live read finishes):
    `event: pending\ndata: {"etag": null, "mode": "live", "read_at": null, "read_error": <str|null>}\n\n`
- **Non-Blocking Snapshot & One-Shot Mode (`?once=1`)**: If `CFG.snapshot` is active OR the query string contains `once=1`, `GET /v1/events` writes the single initial frame, flushes, and closes the HTTP response immediately so snapshot servers and CLI/CI probes never block.
- **Live Streaming & Heartbeat**: In live mode without `once=1`, `GET /v1/events` flushes the initial frame and loops waiting on `state_cond` up to `SSE_HEARTBEAT_S` (default `15.0` s, configurable via `SSE_HEARTBEAT_S` or `CONSOLE_SSE_HEARTBEAT_S` down to `MIN_SSE_HEARTBEAT_S = 0.05` s). On state change it flushes the updated `event: logbook` (or `pending`) frame; on timeout it flushes `: keepalive\n\n`. Client disconnects (`BrokenPipeError`, `ConnectionResetError`, `OSError`) terminate the stream handler cleanly without stderr noise, and `ThreadingHTTPServer.daemon_threads = True` ensures clean server shutdown even while SSE clients are connected.

### 3. Progressive Client-Side SSE Enhancement (`console/src/07-runtime-sse.js`)

- **Zero SSE in Snapshot Mode & Replays**: When `con.mode === "snapshot"` or while `#network` replay (`replaying()`) is active, `syncEventStream()` opens zero `EventSource` connections (and closes any open stream). If a simulated live page receives a frame advertising `"mode": "snapshot"`, it closes the stream immediately.
- **Immediate Conditional Fetch on Change**: In live mode (`con.mode !== "snapshot"`), when `typeof EventSource === "function"`, the client opens `new EventSource("/v1/events")` and listens for `"logbook"` (and `"pending"`) events. When `payload.etag` differs from the client's current `etag` (or `payload.read_error` differs from `con.read_error`), it triggers a coalesced `tick()`, immediately fetching `/v1/logbook.json` with `If-None-Match: <etag>` and `/v1/console.json`.
- **2-Second Poll Fallback Preserved**: The existing `2000` ms `setInterval` loop remains active alongside SSE. When SSE already updated `etag`, the periodic poll receives a zero-body `304 Not Modified`; if `EventSource` is unavailable, blocked by an intermediary, or enters fallback on error, the 2 s poll continues updating the UI with zero degradation.

### 4. CI Gate Integration (`ci/logbook.sh`)

- Add `"/v1/events"` to `GETS` in `ci/logbook.sh` (`cmd_serve`) plus positive and negative `cmd_serve` self-tests.
- Add `console/build.py --check` self-tests (proving drift rejection) and enforce `python3 console/build.py --check` in section `d`.
- Verify `GET /v1/events` in section `e` across snapshot mode (immediate single-frame response, `POST /v1/events` refused), live mode (`?once=1` frame matching `/v1/logbook.json` `ETag`), and `CONSOLE_AUTH=proxy` (`401` without identity header, `200` with identity header).

## Provenance

| Parameter / Constant | Value | Kind | Provenance |
|---|---|---|---|
| `SSE_HEARTBEAT_S` (`console/serve.py`) | `15.0` s | decision | One quarter of the standard `60` s reverse-proxy idle stream timeout (`oauth2-proxy`, `Caddy`, Envoy, and Cloudflare Tunnel in `deploy/proxy/`, ADR 0111), keeping idle `/v1/events` streams open through intermediaries at `< 1` B/s overhead. |
| `MIN_SSE_HEARTBEAT_S` (`console/serve.py`) | `0.05` s (`50` ms) | decision | Floor on `SSE_HEARTBEAT_S` / `CONSOLE_SSE_HEARTBEAT_S` preventing zero-timeout CPU busy-waiting while permitting fast (`0.1` s) keepalive assertions in E2E tests. |
| `POLL_S` / client poll interval (`console/serve.py`, `console/src/07-runtime-sse.js`) | `2.0` s (`2000` ms) | decision | Inherited unchanged from `CONSOLE_POLL_S = 2` in ADR 0047 §4 and `board` `2` s beat in ADR 0035 §3 as the server's single reader loop cadence and client's `If-None-Match` fallback poll. |
| `DIFF_CONTEXT_LINES` / `MAX_DIFF_LINES` (`console/build.py`) | `3` / `40` lines | decision | Standard `3`-line unified diff context (`diff -u` convention) truncated at `40` preview lines so `python3 console/build.py --check` pinpoints bundle drift concisely in CI logs. |
| `MANIFEST` module count & prefixes (`console/build.py`) | `9` files (`00`..`07`, `99`) | decision | Partitions the `3,338`-line console into `9` single-purpose HTML/CSS/JS source modules (`00`, `01`, `02`, `03`, `04`, `05`, `06`, `07`, `99`) compiled deterministically into one zero-dependency `console/index.html` artifact. |

## Alternatives rejected

- **External Node/npm bundlers (Vite, Rollup, esbuild, Webpack):** Introduces npm lockfile/supply-chain surface into the Console build path and breaks stdlib-only reproducibility inside minimal Python environments. A 100-line Python stdlib bundler (`console/build.py`) is deterministic, instant, and dependency-free.
- **Loading `<script type="module" src="/src/...">` at runtime without bundling:** Breaks single-file `console/index.html` portability, alters `ci/logbook.sh` `cmd_index` static whole-page verification, and breaks Node VM extraction in existing Console contract tests.
- **Streaming full ~108 KiB logbook JSON payloads inside every SSE `data:` frame:** Wastes bandwidth when multiple tabs are open or when a tab ignores updates during replay, and duplicates conditional HTTP caching. Pushing compact metadata (`etag`, `mode`, `read_at`, `read_error`) and reusing `GET /v1/logbook.json` with `If-None-Match` keeps one canonical payload path.
- **Replacing the 2-second poll entirely with SSE:** Intermediary proxies that buffer `text/event-stream` or environments lacking `EventSource` would stall updates. Keeping the 2 s `If-None-Match` (`304`) poll as a fallback guarantees liveness everywhere.

## Consequences

- Future Wave 5 tracks (`M85` Control-Plane Workstation, `M86` Adaptive Persona UX) can edit isolated CSS and JS modules under `console/src/` and verify bundle sync via `python3 console/build.py --check` in `ci/logbook.sh`.
- Live browser consoles react immediately to `core/logbook.sh` updates and human `POST` actions over `/v1/events` while snapshot consoles remain completely inert on the network after initial load.
- All existing `ci/logbook.sh`, `ci/console-ui.py`, `test_hive_onboarding`, `test_hive_ui_contract`, and `test_guided_ux` invariants remain intact.

## Golden-rule check

`./go demo` gains no container, no external dependency, and no required secret. `console/build.py` and `console/serve.py` use Python stdlib only, `console/index.html` remains committed and offline-ready, and `core/` stays vendor-free, app-free, model-free, and within the 30-line shell boundary.

## Tests

- `core/tests/test_console_modular_sse.py`: vendor-free, app-free, and model-free unit tests covering `console/build.py` determinism, `--write` and `--check` drift detection, orphan-file rejection, `console/` forbidden-name and `cmd_index` hygiene across `console/src/*`, and Node 24 behavioral verification of `syncEventStream()`, `stopEventStream()`, `handleLogbookEvent()`, snapshot-mode zero-SSE guarantee, and `EventSource` error fallback.
- `ci/tests/test_console_sse_e2e.py`: live end-to-end HTTP/SSE integration tests against `console/serve.py` covering snapshot single-frame close, live `?once=1`, live streaming `event: logbook` push on background logbook ETag updates, `: keepalive` heartbeat emission under short `SSE_HEARTBEAT_S`, `event: pending` before first read, `CONSOLE_AUTH=proxy` `401`/`200` enforcement, and clean client-disconnect handling.
