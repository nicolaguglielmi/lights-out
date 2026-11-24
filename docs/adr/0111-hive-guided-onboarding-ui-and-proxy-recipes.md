# ADR 0111 — Animated Hive Onboarding UI, Interactive Repo Wizard, and Production OIDC Proxy Recipes

- Status: proposed
- Date: 2026-11-24
- Milestone: Track 5 — Junior E2E Onboarding & Animated Hive UI (`agy/p2-hive-ui`)
- Amends: ADR 0047 (adds `#hive` read-only visualization & local wizard alongside the existing screens; adds concrete `deploy/proxy/` OIDC reverse-proxy templates for `CONSOLE_AUTH=proxy`); ADR 0059 (keeps `nav#nav` single-line at `390x844` and `1440x900`, preserves all 6 POST routes and zero extra network requests); ADR 0103 (adds an interactive animated entrypoint `"Open Animated Hive Guide →"` inside `section#journey` while preserving `#your-turn` as the first `.panel` in `#overview`)

## Context

`lights-out` already ships an end-to-end guided CLI (`./go start`, `./go next`, and `lights-out start`/`next`) and a static 6-stage lifecycle strip (`section#journey`) inside `#overview` of `console/index.html`. However, two gaps limit junior developer onboarding and production console deployment:

1. **No visual mental model of the Hive & 7-phase loop.** Junior operators struggle to see how the central `Tower` coordinates the six roles (`product`, `architect`, `dev-worker`, `test-reviewer`, `release`, `ops-triage`), how a PR flows through the 8 quality gates with bounded rework, how canary traffic shifts (`5% → 25% → 100%`) against SLO thresholds, and how an automatic rollback loops back to `ops-triage` and postmortem action items — all while observing real live/snapshot logbook state across schemas `1.2`–`1.8`.
2. **No zero-install interactive setup command generator in the Console, and no copy-ready `CONSOLE_AUTH=proxy` OIDC proxy configs.** Beginners want to pick their Stack, AI Provider/Harness, CodeHost, Cloud/Delivery target, and USD budgets in a guided modal and receive exact copy-paste `lights-out` wrapper commands without touching `.env` secrets or leaking internal repository filenames. Meanwhile, teams deploying `console/serve.py` behind `CONSOLE_AUTH=proxy` need concrete templates for `oauth2-proxy`, `Caddy`, and Cloudflare Access (`deploy/proxy/`) with zero committed secrets and explicit header-stripping / loopback-binding rules.

All Console enhancements must preserve every strict static and runtime perimeter enforced by `ci/logbook.sh` and `ci/console-ui.py`:
- Single-file `console/index.html` with zero external URLs/fonts/CDNs/scripts/images.
- Zero occurrences (even in comments or JS strings) of `adapters/`, `.crew/`, `firestore`, or `STATE_STORE`.
- Zero `<form`, `XMLHttpRequest`, or `sendBeacon` usage; zero `fetch` outside `/v1/`; zero new `POST` routes; zero `POST` requests during page load, walkthrough playback, wizard opening, or snapshot mode.
- Zero new `draggable` attributes or drag/drop handlers/listeners outside the existing held-launch card logic.
- `section#your-turn` remains the first `.panel` in `#overview`, `section#journey` remains last, `nav#nav` stays on a single line at `390x844`, and `localStorage` refusal never throws.

## Decision

### 1. Navigation & `#overview` Entrypoint (`console/index.html`)

- Add `<a href="#hive" data-s="hive">Hive</a>` to `nav#nav`. Because the label `Hive` is 4 characters and `@media (max-width: 720px)` enforces `flex-wrap: nowrap; overflow-x: auto;` with `white-space: nowrap` on `nav a`, all navigation links stay on one horizontal row at `390x844` without horizontal document overflow, and fit within the `1180px` desktop container at `1440x900`.
- Inside `journeyPanel()` (inside `section#journey`, the last section of `#overview`), add a prominent `"Open Animated Hive Guide →"` link (`href="#hive"`). `section#your-turn` remains the first `.panel` on `#overview`, and `#overview` text remains identical across light, dark, system themes, and `localStorage`-refused contexts.

### 2. Animated Hive Control Center (`#hive`, `screenHive()`)

`screenHive()` renders:
- **Inline SVG + CSS Control Center (`#hive-svg`)**:
  - Central `Tower` hexagon (`[data-role="tower"]`) orbited by six role hexes (`product`, `architect`, `dev-worker`, `test-reviewer`, `release`, `ops-triage`), connected by radial spokes (`line.hive-spoke`, `path.hive-signal`).
  - **7-phase E2E pipeline strip** integrated visually in the diagram and as an accessible native `<button type="button">` stepper (`.hive-phase-btn[data-phase="1..7"]`):
    1. Connect & Contract
    2. Setup & AI Provider
    3. Intake & Spec Gate
    4. Agentic Hive Build + 8 Gates + Rework
    5. Canary Deploy (5% → 25%) & SLO Verify
    6. Human Go-for-Launch (100%)
    7. Observe, Rollback & Postmortem
  - **8-gate LED status nodes** (`.hive-gate-led[data-gate="<name>"]`) mapped dynamically from `NET.gates` (`risk`, `secrets`, `test-weakening`, `deps`, `sast`, `test`, `mutation`, `test-proof`).
  - **Canary traffic arc** (`.hive-canary-arc`, `5% → 25% → 100%`) and **Rollback return arc** (`.hive-rollback-arc`, curving back from Canary/Verify to `ops-triage` and `Tower`).
- **Motion & Reduced Motion**:
  - CSS keyframe animations (`stroke-dashoffset` flow pulses, hex beacon halos, LED pulse) are gated inside `@media (prefers-reduced-motion: no-preference)` and explicitly disabled (`animation: none; transition: none;`) inside `@media (prefers-reduced-motion: reduce)`.
  - Walkthrough playback provides `Play Hive Walkthrough` / `Pause`, `Step`, and `Reset` controls (`#hive-play`, `#hive-step`, `#hive-reset`) using pure client-side timers (zero network calls) and respecting `still()` (`prefers-reduced-motion: reduce`).
- **Live State Binding across Logbook Schemas `1.2`–`1.8`**:
  - Gracefully reads `book.source`, `book.roles`, `book.intakes`, `book.tickets`, `book.releases`, `book.launch`, `book.dora`, `book.spend`, and `book.postmortems`, handling absent fields (`1.2`–`1.5`) or `{ok: false, error}` unread objects without throwing.
- **`Junior` vs `Expert` Mode Toggle**:
  - Accessible mode toggle (`#hive-mode-junior`, `#hive-mode-expert`) persisted in `localStorage` under `lights-out.hiveMode` wrapped in `try/catch` so `localStorage` refusal falls back cleanly to `"junior"`.

### 3. Interactive Repo Wizard (`<dialog id="onboard-wizard">`)

- Opened via `#open-wizard` (`"Interactive Repo Wizard"`) using native `dialog.showModal()`.
- Selects:
  - **Stack**: `python`, `node-ts`, `go`, `rust`, `java`
  - **AI Provider / Harness**: `replay` (`l0`), `gemini` (`gemini-cli`), `anthropic` (`claude-code`), `openai` (`codex`), `zai` (`claude-code`)
  - **CodeHost**: `github`, `gitlab`, `gitea`
  - **Cloud / Delivery target**: `envoy-canary`, `argo-rollouts`, `flagger`, `gcp-cloud-deploy`, `aws-ecs-codedeploy`, `azure-container-apps`, `hetzner-k3s`
  - **Budgets**: per-ticket USD (`SPEND_BUDGET_TICKET_USD`) and daily USD (`SPEND_BUDGET_DAY_USD`)
- Generates exact copy-paste `lights-out` wrapper commands (`lights-out doctor`, `lights-out init`, `lights-out cloud-setup` when cloud target selected, `lights-out start --non-interactive ...`, `lights-out gates`, `lights-out next`, `lights-out intake`, `lights-out release`, `lights-out promote`, `lights-out console`) without ever spelling banned strings (`adapters/`, `.crew/`, `firestore`, `STATE_STORE`) or persisting any secrets.
- Uses `<button type="button">` controls (never `<form`, which `ci/logbook.sh` forbids), `closedby="any"` with pointer bounding-box fallback when `'closedBy' in HTMLDialogElement.prototype` is false, native `Esc` dismissal + focus restoration to the opener, and `overscroll-behavior: contain` on `dialog:modal` and `dialog::backdrop`.

### 4. Modern Web Guidance Compliance

Consulted `modern-web-guidance` via `search` and `retrieve`:
- **`dialog`**: `<dialog id="onboard-wizard">` opened with `showModal()` and closed with `dialog.close()` without duplicating `role="dialog"`, `aria-modal`, custom focus traps, or unconditional `display` rules; browser handles inert background and focus return to opener.
- **`light-dismiss-a-dialog`**: `<dialog closedby="any">` paired with a `if (!('closedBy' in HTMLDialogElement.prototype))` bounding-box click fallback and a visible `<button type="button" class="close">Close (Esc)</button>`.
- **`prevent-page-scroll-under-modal-dialog`**: `dialog:modal { overscroll-behavior: contain; }` and `dialog::backdrop { overflow: hidden; overscroll-behavior: contain; }` prevent background scroll chaining without mutating `html`/`body` overflow.
- **`motion`**: Continuous SVG dash-flow/beacon animations are scoped to `@media (prefers-reduced-motion: no-preference)` and disabled under `@media (prefers-reduced-motion: reduce)`; Play/Pause toggle changes its visible label without combining `aria-pressed`.
- **`accessibility`**: Inline SVG uses `role="img"` and `aria-label` + `<title>`, interactive controls use native `<button type="button">` with `>= 24x24` px targets and `:focus-visible` `outline: 2px solid var(--focus)` (`>= 3:1` contrast), and manual step announcements reuse the single centralized `#announce` (`aria-live="polite"`) region.
- **`tabs` & `color`**: Mode tabs use `aria-selected` with border/underline shape indicators (never color alone) and existing WCAG 2.1 AA custom properties (`--bg`, `--panel`, `--ink`, `--muted`, `--line`, `--go`, `--nogo`, `--wait`, `--accent`, `--focus`) across both light and dark themes.
- **`content-first-noise-reduction` & `html`**: Keeps `nav#nav` compact in one row without opening modal dialogs on load, and pairs all wizard controls with explicit `<label for="...">` and `aria-describedby` hints.

### 5. Production OIDC Proxy Recipes (`deploy/proxy/`)

Add copy-ready, secret-free configuration templates under `deploy/proxy/`:
- `deploy/proxy/README.md`: architecture, upstream loopback trust boundary (`CONSOLE_PUBLISH=127.0.0.1`, `CONSOLE_AUTH=proxy`, `CONSOLE_IDENTITY_HEADER`, `CONSOLE_LAUNCHERS`, `CONSOLE_HOSTS`), header-stripping requirements, and `./go console` / `./go production-readiness` verification steps.
- `deploy/proxy/oauth2-proxy.cfg`: OIDC configuration forwarding `X-Auth-Request-Email` to `http://127.0.0.1:8098` with explicit env/placeholder secret markers (`REPLACE_WITH_OIDC_CLIENT_SECRET`, `REPLACE_WITH_32_BYTE_COOKIE_SECRET`).
- `deploy/proxy/Caddyfile`: TLS reverse proxy stripping client-forged identity headers and delegating `forward_auth` to `oauth2-proxy` before proxying to `127.0.0.1:8098`.
- `deploy/proxy/cloudflare-access.md`: Zero Trust setup using `Cf-Access-Authenticated-User-Email` as `CONSOLE_IDENTITY_HEADER`, Cloudflare Access JWT verification / tunnel loopback binding guidance, and `CONSOLE_LAUNCHERS` configuration.

## Provenance

- **Console single-file, route, drag, and forbidden-string perimeter:** `ci/logbook.sh` (`cmd_names`, `cmd_serve`, `cmd_index`).
- **Mobile `390x844` single-line nav, `#your-turn` first panel, `localStorage` refusal, and WCAG AA contrast:** `ci/console-ui.py` (`c_themes`, `g_fit`, `i_overview`, `q_contrast`, `s_nodrag`).
- **`CONSOLE_AUTH=proxy` contract:** ADR 0047 §5 and `console/serve.py`.

## Alternatives rejected

- **Adding external JS/CSS dependencies or SVG icon libraries:** Violates the zero-network single-file console rule (`console/README.md`, `ci/console-ui.py`).
- **Using `<form method="dialog">` inside `#onboard-wizard`:** Fails `ci/logbook.sh` (`cmd_index` rejects `<form` anywhere in `console/index.html`). Native `<button type="button">` controls with `.close()` preserve full dialog semantics cleanly.
- **Adding a new `/v1/` POST endpoint for wizard setup execution:** Violates `ci/logbook.sh` (`cmd_serve` & `cmd_index` allow exactly the 6 existing POST routes) and risks executing commands on read-only snapshot consoles. Emitting copy-paste `lights-out` commands keeps snapshot and live modes 100% safe.

## Consequences

- Junior developers can visually trace the entire 7-phase Hive workflow, watch live/snapshot state bound across schemas `1.2`–`1.8`, and generate ready-to-run `lights-out` setup commands from `#hive`.
- Operators deploying `console/serve.py` behind OIDC have hardened, zero-secret recipes for `oauth2-proxy`, `Caddy`, and Cloudflare Access.
- Existing screens, tests, `ci/logbook.sh`, and `ci/console-ui.py` contracts remain unaffected.

## Golden-rule check

`./go demo`, `./go ci`, and snapshot mode require zero network calls and zero secrets; `#hive` and `#onboard-wizard` execute purely in the browser without mutating server or plane state.

## Tests

- `core/tests/test_hive_onboarding.py`: vendor-free, app-free, and model-free unit tests verifying `#hive` contract, zero forbidden literals, `<form`/POST/drag hygiene, `localStorage` disabled safety, CSS reduced-motion & scroll containment, and Node 24 VM/DOM validation across `golden-1.2`/`1.4`/`1.5`/`1.8`.
- `ci/tests/test_hive_ui_contract.py`: full integration tests covering `#overview` -> `#hive` linking, all stack/provider/codehost/cloud wizard outputs, walkthrough phase transitions, WCAG contrast ratios for light and dark tokens, `390px` single-line nav checks, `console/serve.py` HTTP serving checks, and `deploy/proxy/` secret-free + non-drift template checks.
