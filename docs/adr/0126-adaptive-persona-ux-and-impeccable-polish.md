# ADR 0126 — Adaptive Junior Guided vs. Senior Fleet Cockpit UX and Operate-Mode UI Refinement

- Status: accepted
- Date: 2026-11-24
- Milestone: M86 (`adaptive-persona-ux`)
- Relates to:
  - [ADR 0047](0047-console-reads-one-versioned-logbook-and-writes-only-go-for-launch.md) & [ADR 0059](0059-asking-the-person-through-the-console.md) (logbook truth, POST writes, and header controls)
  - [ADR 0063](0063-the-console-says-whose-turn-it-is.md) & [ADR 0068](0068-the-kanban-that-is-looked-at.md) (kanbans, drawer, viewport breakpoints, and contrast)
  - [ADR 0103](0103-guided-end-to-end-experience.md) & [ADR 0111](0111-hive-guided-onboarding-ui-and-proxy-recipes.md) (`MODE=junior|expert` and guided Hive walkthrough)
  - [ADR 0124](0124-modular-console-build-and-sse-streaming.md) & [ADR 0125](0125-control-plane-web-workstation.md) (modular `console/src/` assembly, SSE `/v1/events`, `Cmd+K` workstation)
  - [ADR 0127](0127-cryptographic-oidc-jwt-and-console-rbac.md) (`role` surfaced on `/v1/console.json`)

## Context

Prior console milestones delivered all core telemetry, `Cmd+K` navigation, interactive `#hive` walkthroughs, and operator write controls (`ADR 0124`, `ADR 0125`), yet usability and accessibility audits across the assembled interface exposed three concrete gaps:

1. **Unadapted cognitive density across skill levels.** All twelve screens presented the same dense operational chrome regardless of whether a newcomer needed plain-language workflow guardrails or a senior platform engineer needed immediate DORA, burn-rate, canary, and policy override telemetry. Furthermore, `core/start.py` resolved `START_MODE` (`junior` or `expert`) during onboarding without forwarding an initial `CONSOLE_PERSONA` default to `console/serve.py`.
2. **Dark-mode button contrast and coarse touch-target failures.** Filled primary and danger buttons (`button.go`, `button.nogo`) paired `#fff` ink against `#4cc38a` and `#ff7b72` in dark mode, falling well below the WCAG 1.4.3 `4.5:1` minimum contrast floor (`2.17:1` and `2.56:1`), while several filter controls, selects, and modal headers fell below `3:1` boundary contrast (WCAG 1.4.11) or coarse-pointer touch comfort (`44` px, WCAG 2.5.5).
3. **Inconsistent top-layer dialog and navigation ergonomics.** Several overlays lacked declarative light-dismiss and coordinate fallback (`light-dismiss-a-dialog`), `<head>` omitted `<meta name="color-scheme" content="light dark">` (`dark-mode`), horizontally scrollable table containers were not keyboard-focusable (`responsive-table`), and wrapping `<nav>` links risked breaking single-row navigation invariants on mobile viewports (`content-first-noise-reduction`).

## Decision

### 1. Adaptive Persona Mode (`Guided` vs. `Cockpit`) and Core Propagation

- **`core/start.py`**: When writing `.crew/start-plan.env`, include `CONSOLE_PERSONA=junior` when `mode == "junior"` or `CONSOLE_PERSONA=senior` when `mode == "expert"`.
- **`console/serve.py`**: Expose `default_persona` (`"junior"` or `"senior"`, normalized from `CONSOLE_PERSONA`, defaulting cleanly to `"junior"`) on `GET /v1/console.json` without introducing any extra route or subprocess.
- **Browser Persistence & Sync (`console/src/*`)**:
  - Persist explicit operator selection under `localStorage["lights-out.persona"]` (`"junior"` or `"senior"`), applied before first paint in `<head>` with an in-memory fallback when storage is refused.
  - When unchosen in `localStorage`, adopt `con.default_persona` from `/v1/console.json`.
  - Provide a two-button segmented control (`#persona-switch` containing `#persona-junior` labeled `Guided` and `#persona-senior` labeled `Cockpit`) in the header alongside compact SSE live-stream (`#sse-pill`) and RBAC role (`#role-pill`) status indicators, plus corresponding Command Palette actions.
  - Keep all twelve `<nav id="nav">` links in one horizontally scrollable row (`flex-wrap: nowrap; overflow-x: auto`) at all viewports while visually de-emphasizing secondary views (`data-tier="advanced"`) in `Guided` mode and keeping primary views prominent.
  - Synchronize `Guided` (`junior`) and `Cockpit` (`senior`) transitions cleanly with the `#hive` walkthrough detail selector (`junior` vs. `expert`) when invoked from the global persona control.

### 2. Persona-Specific Overview Deck & Threshold Override Signalling

- On `#overview`, render `section#persona-deck` strictly after `#your-turn` and before `#roles` so `#your-turn` remains the first overview panel (`ADR 0063` §4, unless an in-progress canary sits at the top per `ADR 0080` §5):
  - **`Guided` (`junior`) deck**: Explains the recommended next operator action in plain language derived exclusively from the logbook and summarizes the three safety invariants (scoped file perimeters, eight deterministic PR gates, progressive `5% → 25%` canary with automatic rollback) alongside direct links to `#request`, `#hive`, and `#onboard-wizard`.
  - **`Cockpit` (`senior`) deck**: Displays a dense six-cell operational summary strip (human-turn queue, active canary state, DORA snapshot availability, daily spend/projection status, overridden threshold count, and session RBAC role) without introducing computed verdicts of its own.
- On `#thresholds`, mark rows with `t.overridden` using `data-overridden="true"` and an explicit `override` badge (never relying on color alone per WCAG 1.4.1).
- Extend `HIVE_STACKS` in `console/src/06-hive-wizard.js` to surface `dotnet`, `kotlin`, `swift`, `php`, and `ruby` alongside the original five stacks (`ADR 0129`).

### 3. Operate-Mode Visual Refinement & Modern Web Guidance Compliance

Apply surgical refinements across `console/src/00-head.html`, `console/src/01-styles.css`, `console/src/02-shell.html`, `console/src/03-state.js`, `console/src/04-screens.js`, and `console/src/05-kanban-network.js` adhering to `modern-web-guidance`:

- **`dark-mode`**: Declare `<meta name="color-scheme" content="light dark">` in `<head>`, keep `color-scheme` on `:root`, theme scrollbars (`scrollbar-color` with `@supports not (scrollbar-color: auto)` WebKit rules), `::selection`, `caret-color`, and `accent-color`.
- **`color`**: Add dedicated solid CTA surface pairs (`--go-solid`/`--go-on-solid`, `--nogo-solid`/`--nogo-on-solid`, `--accent-solid`/`--accent-on-solid`) meeting WCAG 1.4.3 `>= 4.5:1` in both themes, add `--control-border` meeting `>= 3.0:1` WCAG 1.4.11 non-text contrast against both `--panel` and `--bg`, ensure status chips/pills carry text/glyph markers alongside color, and add `@media (forced-colors: active)` system-keyword boundaries.
- **`form-factor-adaptation`**: Set `--target-size` to `24px` (`44px` on `@media (pointer: coarse)`), preserve compact `40px` empty/collapsed kanban columns (`.col-toggle` at `24px`) so desktop boards stay inside `1440px`, enforce `body { overflow-wrap: break-word; }` and `:where(a, code) { overflow-wrap: anywhere; }` without hiding root horizontal overflow, and balance headings (`text-wrap: balance`) while keeping readable prose measure (`70ch`).
- **`dialog` & `light-dismiss-a-dialog`**: Keep bare `dialog` free of unconditional `display` rules (style `dialog[open]`), omit redundant `role="dialog"` / `aria-modal` attributes, mark all six modal dialogs (`#card-back`, `#why`, `#launch-confirm`, `#onboard-wizard`, `#cmd-palette`, `#ops-dialog`) with `closedby="any"`, install a shared bounding-rect backdrop light-dismiss fallback (`bindDialogLightDismiss`) plus `dialog.returnValue = ""` reset on `close`, add sticky headers inside scrollable dialogs, keep `overscroll-behavior: contain` scroll lock, and gate entry animations behind `prefers-reduced-motion`.
- **`responsive-table` & `content-first-noise-reduction`**: Add `tabindex="0"`, `role="region"`, and accessible labels on horizontally scrollable table containers, use container-aware table styling (excluding `#ticket-now` where `.flow-why` tooltips extend vertically), apply `font-variant-numeric: tabular-nums` to numeric columns and metrics, and keep the `<header>` compact (`<= 12%` viewport height on desktop).

## Alternatives considered

- **Separate HTML entrypoints (`junior.html` vs. `senior.html`):** Rejected because duplicating console templates splits static security checks (`ci/logbook.sh`, `core/freeze.py`) and prevents instantaneous in-session switching when helping teammates or triaging incidents.
- **Collapsing advanced `<nav>` links into a `popover` menu on desktop/mobile:** Rejected because existing automated UI contracts (`ci/console-ui.py` and `ci/tests/test_hive_ui_contract.py`) assert all `nav#nav a` anchors remain visible on one horizontal row without wrapping. Keeping a single horizontally scrollable row preserves 100% contract compatibility while cleanly framing primary vs. advanced destinations.

## Consequences

- Operators can fluidly toggle between `Guided` (`junior`) onboarding clarity and `Cockpit` (`senior`) telemetry density while preserving exact logbook truth (`#who`, `#fresh`, gate verdicts, `ADR 0047`).
- Dark-mode filled buttons (`GO`, `NO-GO`, primary links), control borders, touch targets, tabular metrics, and all six modal dialogs satisfy WCAG 2.1 AA and `modern-web-guidance` requirements across desktop and phone form factors without external assets or CSP changes.

## Provenance

| Threshold / Bound | Value | Provenance |
| --- | --- | --- |
| Minimum body/button text contrast ratio | `4.5:1` (`24` px fine / `18.66` px bold large threshold `3.0:1`) | Derived: WCAG 2.1 Level AA SC 1.4.3 (`color`), verified across light (`5.37:1`–`6.70:1`) and dark (`7.35:1`–`8.00:1`) solid button pairs. |
| Minimum non-text border & focus indicator contrast | `3.0:1` | Derived: WCAG 2.1 Level AA SC 1.4.11 (`color`), satisfied by `--control-border` (`3.61:1`–`3.98:1`) and `--focus`. |
| Minimum interactive target size (fine / coarse pointer) | `24` px fine / `44` px coarse | Derived: WCAG 2.1 SC 2.5.8 (`24` px) and SC 2.5.5 (`44` px) from `form-factor-adaptation`, keeping `.col-toggle` at `24` px inside `40` px empty kanban columns (`ADR 0063`). |
| Readable prose line measure | `70ch` | Decision: midpoint of `65`–`75` character typographic measure guidelines (`craft-floor.md`) for `.lede` and guided explanations. |
| Shell container width & responsive stack breakpoint | `1180` px shell / `720` px mobile | Derived: inherited from existing console layout and `ADR 0063` §1 (`1440` px desktop board fit and `390` px phone stacking). |
| Compact persistent header height cap | `<= 12%` of viewport (`4.5rem` scroll padding) | Derived: `content-first-noise-reduction` guidance keeping persistent headers compact and focus clear (`WCAG 2.4.11`). |
| Modal dialog entry animation duration | `160` ms (`0.16s`, `0` under reduced motion) | Derived: preserved from existing `dialog#card-back` transition in `console/src/01-styles.css` (`ADR 0068`). |
