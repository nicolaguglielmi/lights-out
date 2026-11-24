# Roadmap

Derived from [docs/PLAN.md](docs/PLAN.md), the Italian working plan, which
wins when the two disagree. Decisions behind each step are in
[docs/adr/](docs/adr/).

## Done

| Milestone | What it delivered |
|---|---|
| M0–M2 | Skeleton, the bundled app, the local L0 plane |
| M3 | Canary delivery, verify, automatic rollback, kill switch |
| M4 | Six PR gates: risk, secrets, dependencies, SAST, tests, mutation (eight since M27) |
| M5 | The agent runtime: ticket → PR, recorded at L1, replayed at L0 |
| M6 | The control plane: the whole golden path, audited |
| M7 | ADK roles (product, ops-triage, release) and go-for-launch, the one human gate |
| M8, local part | Release images built, signed and verified before they take traffic |
| M9 | The review step for medium- and high-risk PRs; ticket perimeters |
| M12 | The Workload port: the platform operates on any workload, not only the bundled app |
| M13 | Attach: `./go attach <path>` points a plain clone at your repository; its `.lights-out/` holds the contract, the dev-worker's brief and your thresholds (ADR 0037) |
| M15 | The rework loop: a gates NO-GO or a review asking for changes goes back to the dev-worker with the verdict, up to four attempts, then a human (ADR 0038) |
| M18 | The model is a choice: `MODEL_PROVIDER` (gemini, anthropic, openai) and `MODEL` in the profile or `.env`, a role keeps only its thinking level and budgets, the ADK roles reach Claude and OpenAI through `LiteLlm`, `ci/model-free.sh` keeps model ids out of the core (ADR 0040). The non-Gemini recording is implemented, run once on Claude (2026-11-04, evals GO), not committed |
| M19 | The `claude-code` and `codex` harnesses beside `gemini-cli`, one provider each, and `./go harness-conformance` against a scripted model (ADR 0041). Recording with them is implemented; claude-code ran once live (2026-11-04), stopped by the mutation gate; codex not yet |
| M20 | Gates anywhere: `./go init <repo>` drafts `.lights-out/` from the repository itself, and `test`, `sast` and `mutation` run the commands the contract declares, in the workbench, a compose service or an image; init and gates in 40 s on `workloads/minimal`, 8 s on `workloads/minimal-js`, under the 300 s rule; JavaScript with Stryker as the second stack (ADR 0042, *proposed*) |
| M21 | GitHub as code host: `adapters/github/`, the eight CodeHost verbs on the REST API, the token never in argv or a `.git/config`; an attached repository's merge is built, signed and released; `./go codehost-conformance` against Gitea on every push and against GitHub in a nightly (ADR 0043). The end-to-end run on GitHub is the owner's, not yet run |
| M22 | Ops without the plane: `./go incident` and `./go postmortem`, a post mortem whose timeline is anchored to the audit chain and whose action items are tickets; the `Metrics` port reads any Prometheus-compatible endpoint (`METRICS_URL`); `./go demo-postmortem` replays a recorded answer, still six roles (ADR 0044, *proposed*) |
| M23 | One command, one package: the `lights-out` wrapper (`init`, `gates`, `triage`, `postmortem`, `doctor`) on any repository from one image, mounted read-only and never given the engine socket; `./go doctor`; a GitHub Action that posts the gates' line as a check run (ADR 0045, *proposed*). Built and tested in Actions, published as `v0.1.0` on 2026-11-05 |
| M27 | Tests that prove something: `test-weakening` refuses a diff that drops assertions or tests or adds skips, `test-proof` runs the tests a change adds on the base, where they must fail; the tower's `assign` waits while an open ticket holds a file of its perimeter, and on an attached repository until the base is green (ADR 0053, *proposed*) |
| M28 | The project map: `./go map` builds a SQLite file of one commit's tickets, files, tests, ADRs and pages, rebuilt at every merge, which files a docs ticket when a merge changes a path a page cites; `ci/docs-drift.sh` fails on a page citing a path, command or key that no longer exists; the map's slice in the worker's brief stays off (`MAP_BRIEF=off`) until M24 measures it (ADR 0054, *proposed*) |
| M29 | The dev phase visible: the console recovered (M11's five screens, ADR 0047), then `requested` → `questions` → `spec-ready` → `opened` in the tower, `./go request` and `./go answer`, the `spec` gate, one timeline per ticket from the request to the release, an overview and a kanban whose columns are the tower's states (ADR 0055, *proposed*) |
| M30 | Main stays green without a queue: no second judgment when nothing moved under a PR's perimeter, main judged again after merging in groups of at most four, a red group bisected and the culprit reverted through the code host, `assign`, `rework` and `release` stopped while main is red; `./go demo-mainline` (ADR 0056, *proposed*) |
| M31 | The stepped way in: a gates image (508 MB) beside the full one (1.25 GB, was 1.77), `action.yml` at the root with a `gates` input and a summary per gate in the check run, `test-proof` and `test-integrity` as standalone Actions, `./go doctor --level`, the `production-readiness` gate, the README along five steps with the reuse said as reuse (ADR 0057, *proposed*). Published as `v0.3.0` on 2026-11-09, both images signed; on the probe, first pull included, the three checks in 27 s, 14 s and 25 s. On the GitHub Marketplace since 2026-11-09, the three Actions |
| M31, second slice | The first run drafts the contract: on a repository with no `.lights-out/` the check stays NO-GO and carries the contract drafted from the base and the two commands that commit it; a change that removes the contract is refused. Released in 0.3.1 (2026-11-10) ([ADR 0064](docs/adr/0064-the-first-run-drafts-the-contract.md)) |
| M33 | The console as a workbench: the kanban gains a drawer, search, keys, a view in the link, collapsible columns and a highlight on a moved card, and still moves none; a light and dark theme; a specs screen and a request editor the spec gate checks as you type, saved through `./go request`; ADR search from the project map, schema 2, through logbook 1.2 (ADR 0059, *proposed*) |
| M34 | The lean plane: a `sqlite` StateStore and a `spool` EventBus, files under `.crew/`, the defaults at L0 and L1; the emulators opt-in (compose profile `emulators`); the Gitea runner, its `image` job and the Gitea compatibility check gone, the release step builds every workload's merge; five plane services instead of eight; the tower's documents outlive `./go down`. Measured on Actions, 719 to 873 s, so the demo budget stays 900 s (ADR 0060, *proposed*). `./go ci` then runs each check once, `contract` judging the record of the run instead of a second run: 632 s and 678 s measured (ADR 0062, *proposed*) |
| M35 | Gates without a container: `lights-out-gates --light`, a standard-library Python package (the root `pyproject.toml`, `lights_out` is `core/`), runs the gates that need only git and Python, two by default, four with `TICKET=`, six with `TEST_ON_HOST=1`, in 0.14 s on this repository, and never prints `GO: gates`; the `--light` hook recipes verified once per CLI (ADR 0061, *proposed*). On PyPI as `lights-out-gates` 0.3.0 since 2026-11-09 (PyPI refused the name `lights-out`) |
| M17, first slice | The console says whose turn it is: the kanban's columns under four bands (Spec, Build, Test and review, Release) fit one screen and stack on a phone; every card, drawer and timeline carries a now line, the tower's own table embedded and checked, "Your turn" with the one action for an open question, `needs-human` and a held canary; the timeline opens with a strip of the bands and groups its rows under them; the overview opens with what waits for you; the editor keeps the gate's line in view and cannot file a request twice (ADR 0063, *proposed*) |
| M17, second slice | The kanban that is looked at: Trello's look without the drag; a card face with priority and feature labels, an icon for who has the ball (the tower's table gains `actor`, checked), the time in its state and the rework attempts as pips; the drawer becomes the card's back, a modal over the board with the one action, the open question and a compact timeline; a moved card flies and is announced; "Your turn only" in the link; a band bar on the phone; WCAG AA contrast and visible focus proven in both themes (ADR 0068, *proposed*) |
| M17, third slice | The governed network on screen and in text: the console's `#network` screen draws the tower, the six roles, the dev-worker's workers with harness and model, the gates lighting up as verdicts arrive, the rework, the canary and the human gate, beside each attempt's patch and the files by perimeter; `./go network` prints the same facts in the same words; logbook 1.3, a patch shown only when its attempt's `secrets` gate said GO ([ADR 0071](docs/adr/0071-the-governed-network-on-screen-and-in-text.md), *proposed*) |
| M25 | Delivery on Kubernetes: `DELIVERY=argo-rollouts` gives the launchpad's canary, verify and rollback on a cluster with no Envoy, proven by `./go argo-conformance` on a k3s node beside the plane ([ADR 0051](docs/adr/0051-delivery-on-kubernetes-with-argo-rollouts.md), *proposed*) |
| M26 | The shell boundary: logic goes to Python under `core/`, shell stays where the contract or the host needs it, and `ci/shell-boundary.sh` holds a file of `core/` to 30 lines of code, with a debt list that can only fall; the first slice took `core/` from 2,101 to 1,764 shell lines ([ADR 0052](docs/adr/0052-where-shell-is-allowed.md), *proposed*) |
| M39 | The agent's toolbelt: the Claude Code plugin, the Codex plugin and the Gemini CLI extension with `/gates`, `/spec` and a pre-commit hook, and `lights-out-gates mcp`, a read-only MCP server over the project map; each proven once in its pinned CLI, nothing listed in a marketplace ([ADR 0069](docs/adr/0069-the-agents-own-toolbelt.md), *proposed*) |
| M42 | A repository customizes its agents: `roles/<role>.md` instructions for the two reviewers and one `mcp.json` for the dev-worker, both read from the base and translated per harness; `./go agents` and the console's Agents panel ([ADR 0073](docs/adr/0073-a-repository-customizes-its-agents.md), *proposed*) |
| M43 | Security scanning: `sast` judged on the diff, an SPDX SBOM attested per released image, passive DAST at staging, off by default (`./go demo-dast`) ([ADR 0072](docs/adr/0072-security-scanning-sast-sca-dast-sbom.md), *proposed*) |
| Release machinery | A push is judged from its last green ancestor ([ADR 0074](docs/adr/0074-judge-what-was-never-judged.md)); the tag releases itself ([ADR 0075](docs/adr/0075-the-tag-releases-itself.md)); a mutant runs only the tests that reach it ([ADR 0076](docs/adr/0076-a-mutant-runs-the-tests-that-reach-it.md)); the wiki is rendered at the tag ([ADR 0077](docs/adr/0077-the-wiki-is-rendered-at-the-tag.md)); all *proposed* |
| M24, phases A–D | The public legacy experiment, on jd/tenacity (sqlparse was dropped: it ended its maintenance): ten tickets, five tests-only and five changing production code. Anthropic (2026-11-11): 10 of 10 merged, two through mutation's rework. Gemini (2026-11-14): 7 of 10 merged, each at its first attempt with every gate GO, three stopped at the dev-worker's `TOKEN_BUDGET` with no PR. The criterion of plan §14 holds on both. Numbers and sources in [docs/scoreboard/m24-tenacity.json](docs/scoreboard/m24-tenacity.json). Found on the way: `mutation` passed by absence when cosmic-ray could not start the test command, fixed ([ADR 0042](docs/adr/0042-gates-anywhere-init-and-the-toolchain-contract.md) amendment) |
| M45 | The human gate on the card: the held release's card is the one that drags, onto "Promote to 100%" or "Rollback", through the launch screen's confirmation panel ([ADR 0079](docs/adr/0079-the-human-gate-on-the-card.md), *proposed*) |
| M46 | The live canary: each stage records when it started, when it was judged and its numbers; the console shows the stage in course and a rollback's reason ([ADR 0080](docs/adr/0080-the-live-canary.md), *proposed*) |
| M47 | The why of a NO-GO, one click away: each rejected attempt carries its gate, line and details, and one panel shows the attempts left and the next move ([ADR 0078](docs/adr/0078-the-why-of-a-no-go-one-click-away.md), *proposed*) |
| M48 | Delivery measures: `./go dora` gives DORA's four keys from the logbook, and prints "not measured" with the reason for a key the record cannot give ([ADR 0082](docs/adr/0082-delivery-measures-from-the-logbook.md), *proposed*) |
| M49 | The evidence pack: `./go evidence VERSION=<v>` writes one signed file per release, each value with its source and every gap named ([ADR 0081](docs/adr/0081-the-evidence-pack-per-release.md), *proposed*) |
| M50 | Cost and time per ticket: `./go spend` prices each run from a dated table, and `SPEND_BUDGET_TICKET_USD` and `SPEND_BUDGET_DAY_USD` stop the next attempt and send the ticket to a person, off by default ([ADR 0083](docs/adr/0083-cost-and-time-per-ticket-with-a-budget.md), *proposed*) |
| M51 | Gates under the shell boundary: the logic of ten gates moved to `core/gates/` with 103 unit tests, shell in `gates/` from 4,074 to 2,489 lines; the harness-conformance pair stays on the debt list ([ADR 0084](docs/adr/0084-gates-under-the-shell-boundary.md), *proposed*) |
| M32 | Outside adapters and other CIs as surfaces: `ADAPTERS_PATH` and `outside/<name>`, admitted by the exact hash of their tree and a conformance GO; GitLab as a CodeHost (`./go gitlab-conformance`), Jenkins as a surface that shows the gates' line and never judges (`./go demo-jenkins`) ([ADR 0058](docs/adr/0058-outside-adapters-and-other-cis-as-surfaces.md), built 2026-11-13) |
| M52–M54 | The flow strip on every card (the request, the perimeter, the eight gates, the PR, the canary, the human gate, 100%), the role matrix, and the spend per hour with the day's projection and the run in flight against `TOKEN_BUDGET`; logbook 1.6 ([ADR 0085](docs/adr/0085-the-flow-strip.md), [0086](docs/adr/0086-the-role-matrix.md), [0087](docs/adr/0087-burn-projection-and-the-run-in-flight.md), *proposed*) |
| M55 | The provider scoreboard: `./go scoreboard` puts the providers side by side from sourced results, seeded with M24's ten tickets and OpenAI shown as not run; ten tickets on one repository, not a benchmark ([ADR 0088](docs/adr/0088-the-provider-scoreboard.md), *proposed*) |
| M56 | A read-only console snapshot: `./go evidence` freezes the console of a real run into one signed, self-contained page, which the site links ([ADR 0089](docs/adr/0089-a-read-only-console-snapshot.md), *proposed*) |
| M57 | The intake interview: `./go intake` takes a request in free text, `product` asks at most five questions and proposes tickets with criteria, feature and priority, `architect` proposes each perimeter, a person confirms each one and the spec gate judges the confirmed; every ADK role has a turn cap, `ADK_MAX_TURNS`; logbook 1.7 ([ADR 0090](docs/adr/0090-the-intake-interview.md), *proposed*) |
| M58 | Closed decisions: a `Decide` port with the Gemini Decisions API or the profile's model, screening every request for instructions aimed at an agent before any agent reads it, and asking file by file which ones a ticket touches ([ADR 0091](docs/adr/0091-closed-decisions-the-perimeter-and-the-injection-screen.md)) |
| M59 | GLM by Z.ai as a fourth provider (`MODEL_PROVIDER=zai`), a token budget per provider, and cached input priced at the vendor's rate ([ADR 0092](docs/adr/0092-glm-by-zai-and-a-budget-per-provider.md)) |
| M60 | The first run, flawless: a preflight that moves busy ports, seven steps with estimates, the run's console opened on the rollback, `./go try`; the clean runner's demo 370–480 s from 700–805 s ([ADR 0093](docs/adr/0093-the-first-run-flawless.md)) |
| M61 | The second Python slice: the harness conformance suites and the toolchain check in Python, off the shell debt list; `ci/`'s debt measured ([ADR 0094](docs/adr/0094-ci-under-the-shell-boundary-later.md)) |
| M62 | `DELIVERY=flagger` with Flagger's own rollback and 100% held for a person, GitLab CI on a real runner, the CircleCI recipe validated ([ADR 0095](docs/adr/0095-flagger-under-the-launchpads-gates-and-two-more-ci-surfaces.md)) |
| M64 | From zero to hero: seven levels at the head of the tutorial, each ending on a real `GO`; an Open in Codespaces dev container proven on Actions (668 s to the rollback); the site's first screen on the frozen console ([ADR 0097](docs/adr/0097-from-zero-to-hero-and-a-start-with-nothing-installed.md), *proposed*) |
| M65 | Verify from the proxy: `verify/slo.promql` reads Envoy cluster stats instead of requiring app `/metrics` or `target_info`; optional `WORKLOAD_HEALTH_PROBE` (`http:/<path>` or `tcp[:<port>]`) and optional empty `WORKLOAD_KILL_SWITCH`; uninstrumented stdlib `workloads/plain/` + `./go demo-seam-plain` ([ADR 0098](docs/adr/0098-verify-from-the-proxy.md)) |
| M69 | One install: `uv tool install lights-out-gates` ships both `lights-out` (via `script-files`, byte-identical POSIX wrapper with `--version`) and `lights-out-gates`; updated docs and `package.yml`/`stranger.yml` checks ([ADR 0099](docs/adr/0099-one-install-via-lights-out-gates.md)) |
| 0.4.0 | Tagged 2026-11-12: the agent network ([ADR 0071](docs/adr/0071-the-governed-network-on-screen-and-in-text.md)), per-role instructions and MCP servers ([ADR 0073](docs/adr/0073-a-repository-customizes-its-agents.md)), the toolbelt ([ADR 0069](docs/adr/0069-the-agents-own-toolbelt.md)), the Trello-style kanban ([ADR 0068](docs/adr/0068-the-kanban-that-is-looked-at.md)), `self` judged from its last green ancestor ([ADR 0074](docs/adr/0074-judge-what-was-never-judged.md)) |
| 0.5.0 | Tagged 2026-11-12, the first release cut by its own tag ([ADR 0075](docs/adr/0075-the-tag-releases-itself.md)): security scanning ([ADR 0072](docs/adr/0072-security-scanning-sast-sca-dast-sbom.md)); [release notes](docs/releases/v0.5.0.md) |
| 0.6.0 | Tagged 2026-11-13: the why of a NO-GO, the human gate on the card, the live canary, the evidence pack, `./go dora` and `./go spend` ([ADR 0078](docs/adr/0078-the-why-of-a-no-go-one-click-away.md) to [0083](docs/adr/0083-cost-and-time-per-ticket-with-a-budget.md)); [release notes](docs/releases/v0.6.0.md) |
| 0.7.0 | Tagged 2026-11-14, on the Marketplace 2026-11-15: the intake interview ([ADR 0090](docs/adr/0090-the-intake-interview.md)), the flow strip, the role matrix and the burn, the provider scoreboard and the frozen console ([ADR 0085](docs/adr/0085-the-flow-strip.md) to [0089](docs/adr/0089-a-read-only-console-snapshot.md)), outside adapters with GitLab and Jenkins ([ADR 0058](docs/adr/0058-outside-adapters-and-other-cis-as-surfaces.md)), the gates' logic in Python ([ADR 0084](docs/adr/0084-gates-under-the-shell-boundary.md)); [release notes](docs/releases/v0.7.0.md) |
| 0.8.0 | Tagged 2026-11-16: the first run without friction (M60), GLM and a budget per provider (M59), closed decisions (M58), Flagger and the CI recipes (M62), the second Python slice (M61) |
| 0.8.1 | Cut 2026-11-19, no new command or flag: a dev container and Open in Codespaces with the seven levels (M64, [ADR 0097](docs/adr/0097-from-zero-to-hero-and-a-start-with-nothing-installed.md)), the mutation runner stopping with one line on a coverage-hook failure ([ADR 0076](docs/adr/0076-a-mutant-runs-the-tests-that-reach-it.md) amendment), DAST scanning the stable again when its report lost whole paths ([ADR 0072](docs/adr/0072-security-scanning-sast-sca-dast-sbom.md) amendment), plain git hooks, the gates image waiting for a `.git` file a podman machine shows late; [release notes](docs/releases/v0.8.1.md) |
| Agent network, built | The dev-worker step split between workers behind one harness port, governed like any PR (ADR 0036, still *proposed*; hand-written at L0) |

## Next

The third arc (plan §16) is built, except for the runs that are the
owner's: the OpenAI recording, a committed Claude recording and the L1
end-to-end on GitHub.

Left from the sprint of 2026-11-16: an outside AgentHarness, the full
tower on GitLab and on a real Jenkins (after M62), and `ci/` under the
shell-boundary ratchet (ADR 0094).

M24's remaining legs: OpenAI has not run, and GLM runs after M59. The
exit criterion is plan §14's: if at least two tickets in three do not
reach a mergeable PR without a human touching the code, the thesis is
weaker than expected.

The adoption roadmap behind M31, M34 and M35, with what to drop, what to
change and the sequence after them (attestation, the jury, `./go
demo-loop`, the toolbelt, then the launch), is
[docs/plans/adoption-roadmap-2026-11.md](docs/plans/adoption-roadmap-2026-11.md).

## Later

- **M17 — the dashboard.** The console grows into the view of every
  ticket, attempt, worker and threshold you changed. It already shows the
  tickets, their timelines and the thresholds (M29, M33), whose turn it
  is (ADR 0063), the governed network (ADR 0071), the flow strip, the
  role matrix and the burn (ADRs 0085–0087).

## When someone needs it

- **M44 — deploy on the main clouds** (planned 2026-11-11, nothing built):
  connectors and installable plugins that deploy, release and operate the
  repository lights-out is attached to on GCP, AWS, Azure, Hetzner and the
  other main providers, each behind the same Delivery, Registry, Metrics and
  Flags ports and its conformance suite, with no long-lived credential in
  the repository. Started by the owner when a user needs it.
- **M8 cloud track (L2)**: Google Cloud with Terraform and Cloud Deploy.
- **M10 (L3)**: Agent Engine and the nightly self-test.

Each cloud adapter costs money on every run and needs a nightly to stay
honest, so it is built when someone needs it.
