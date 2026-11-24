// --- animated hive control center & onboarding wizard (docs/adr/0111) -------
const HIVE_ROLES = [
  {role: "product", label: "Product", cx: 250, cy: 58, short: "Interview & Spec"},
  {role: "architect", label: "Architect", cx: 364, cy: 124, short: "Map & Perimeter"},
  {role: "dev-worker", label: "Dev Worker", cx: 364, cy: 256, short: "PR & Rework"},
  {role: "test-reviewer", label: "Test Reviewer", cx: 250, cy: 322, short: "Diff & Test Audit"},
  {role: "release", label: "Release", cx: 136, cy: 256, short: "Canary & Brief"},
  {role: "ops-triage", label: "Ops Triage", cx: 136, cy: 124, short: "Alerts & Postmortem"},
];
const HIVE_PHASES = [
  {n: 1, title: "Connect & Contract", short: "1. Connect",
    roles: ["architect"], gates: false, canary: false, rollback: false,
    junior: "Measures your baseline mutation score and locks test, SAST, and mutation rules into a read-only contract before any AI worker runs.",
    expert: "Drafts workload contract & baseline via lights-out init (or ./go attach && ./go init). Enforces MUTATION_BASELINE - MUTATION_MARGIN and immutable base-branch policy.",
    cmd: "lights-out init"},
  {n: 2, title: "Setup & AI Provider", short: "2. Setup",
    roles: ["product", "architect", "dev-worker", "test-reviewer", "release", "ops-triage"], gates: false, canary: false, rollback: false,
    junior: "Selects replay (zero key cost) or a live AI provider, verifies your container engine with doctor, and configures USD caps without saving secrets to disk.",
    expert: "Persists non-secret MODEL_PROVIDER, AGENT_HARNESS, DELIVERY, and spend budgets; provider credentials remain strictly in shell env or gitignored .env.",
    cmd: "lights-out doctor && lights-out start --preset junior"},
  {n: 3, title: "Intake & Spec Gate", short: "3. Intake & Spec",
    roles: ["product", "architect"], gates: false, canary: false, rollback: false,
    junior: "Product clarifies requirements until criteria have observable Given/When/Then outcomes, and Architect scopes exact file globs a worker may touch.",
    expert: "Product drives intake/clarify until gates/spec.sh emits GO: spec; Architect assigns ## Perimeter globs from core/map.py project map.",
    cmd: "lights-out intake TEXT=\"Describe the feature\" && lights-out next"},
  {n: 4, title: "Agentic Hive Build + 8 Gates + Rework", short: "4. Build & 8 Gates",
    roles: ["dev-worker", "test-reviewer"], gates: true, canary: false, rollback: false,
    junior: "Dev Worker edits only files inside its perimeter. Every PR runs all 8 quality gates in order; any NO-GO triggers bounded rework with exact failure lines.",
    expert: "GATES := risk secrets test-weakening deps sast test mutation test-proof; Test Reviewer audits non-low-risk diffs; rework capped by REWORK_MAX_ATTEMPTS and TOKEN_BUDGET.",
    cmd: "lights-out gates && ./go network"},
  {n: 5, title: "Canary Deploy (5% → 25%) & SLO Verify", short: "5. Canary & SLO",
    roles: ["release"], gates: false, canary: true, rollback: false,
    junior: "Shifts 5% then 25% of live traffic to the canary while comparing error rate and p95 latency against stable. Any SLO regression rolls back to 0% automatically.",
    expert: "Launchpad shifts 5% -> 25% via DELIVERY, verifies error_rate <= ERROR_RATE_MAX_DELTA and latency_p95 <= LATENCY_P95_MAX_DELTA over STAGE_SECONDS, then holds at 25%.",
    cmd: "lights-out release"},
  {n: 6, title: "Human Go-for-Launch (100%)", short: "6. Human Gate",
    roles: ["release"], gates: false, canary: true, rollback: false,
    junior: "Pauses indefinitely at 25% with a signed brief of gate results and canary SLOs. Only an authorized human approver can promote to 100% or roll back.",
    expert: "Indefinite pause at 25%: requires GO_FOR_LAUNCH=GO|NO-GO (reason mandatory on NO-GO), verified against brief_sha256 and CONSOLE_LAUNCHERS under CONSOLE_AUTH=proxy.",
    cmd: "lights-out promote"},
  {n: 7, title: "Observe, Rollback & Postmortem", short: "7. Observe & Loop",
    roles: ["ops-triage", "product"], gates: false, canary: false, rollback: true,
    junior: "Tracks DORA delivery speed, daily token burn, and signed evidence packs. On rollback or alert, Ops Triage investigates root causes and files follow-up fix tickets.",
    expert: "Collects immutable logbook, DORA window, hourly burn projection, signed release evidence packs, and rollback -> incident -> postmortem action-item tickets.",
    cmd: "lights-out dora && lights-out spend && ./go evidence VERSION=<v>"},
];
const HIVE_STACKS = {
  python: {label: "Python (uv.lock / poetry.lock / pinned requirements)", note: "Commit uv.lock, poetry.lock, or pinned requirements.txt before lights-out init."},
  "node-ts": {label: "Node.js / TypeScript (package.json + lockfile)", note: "Commit package-lock.json, pnpm-lock.yaml, or yarn.lock alongside package.json."},
  go: {label: "Go (go.mod + go.sum)", note: "Run go mod tidy and commit go.sum beside go.mod (uses --engine for Go container gates)."},
  rust: {label: "Rust (Cargo.toml + Cargo.lock)", note: "Run cargo generate-lockfile and commit Cargo.lock before init."},
  java: {label: "Java / Kotlin (Maven pom.xml or Gradle)", note: "Ensure reproducible build wrapper (mvnw/gradlew) and test suite run cleanly."},
};
const HIVE_PROVIDERS = {
  replay: {label: "Local replay (L0, zero API key)", harness: "replay", profile: "l0", envVar: ""},
  gemini: {label: "Google Gemini (gemini-cli)", harness: "gemini-cli", profile: "l1", envVar: "GEMINI_API_KEY"},
  anthropic: {label: "Anthropic Claude (claude-code)", harness: "claude-code", profile: "l1", envVar: "ANTHROPIC_API_KEY"},
  openai: {label: "OpenAI (codex)", harness: "codex", profile: "l1", envVar: "OPENAI_API_KEY"},
  zai: {label: "Z.ai GLM (claude-code)", harness: "claude-code", profile: "l1", envVar: "ZAI_API_KEY"},
};
const HIVE_CODEHOSTS = {
  github: {label: "GitHub", envVar: "GITHUB_TOKEN"},
  gitlab: {label: "GitLab", envVar: "GITLAB_TOKEN"},
  gitea: {label: "Local Gitea (L0 plane)", envVar: ""},
};
const HIVE_DELIVERIES = {
  "envoy-canary": {label: "Local Envoy Canary (L0/L1 compose)", cloud: false},
  "argo-rollouts": {label: "Kubernetes Argo Rollouts", cloud: false},
  "flagger": {label: "Kubernetes Flagger", cloud: false},
  "gcp-cloud-deploy": {label: "GCP Cloud Run + Cloud Deploy", cloud: true},
  "aws-ecs-codedeploy": {label: "AWS ECS Fargate + CodeDeploy", cloud: true},
  "azure-container-apps": {label: "Azure Container Apps", cloud: true},
  "hetzner-k3s": {label: "Hetzner Cloud k3s", cloud: true},
};

const HIVE_MODE_KEY = "lights-out.hiveMode";
function loadHiveMode() {
  try {
    const v = localStorage.getItem(HIVE_MODE_KEY);
    if (v === "junior" || v === "expert") return v;
  } catch (e) { /* storage refused */ }
  return "junior";
}
function saveHiveMode(m) {
  try { localStorage.setItem(HIVE_MODE_KEY, m); } catch (e) { /* storage refused */ }
}

const hiveState = {phase: null, mode: loadHiveMode(), playing: false, timer: null};
const wizState = {stack: "python", provider: "replay", codehost: "github", delivery: "envoy-canary",
  ticketBudget: "2.00", dayBudget: "10.00", preset: "junior"};

function stopHivePlay() {
  if (hiveState.timer) { clearInterval(hiveState.timer); hiveState.timer = null; }
  hiveState.playing = false;
}

function hiveHexPoints(cx, cy, r) {
  const pts = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 3) * i - Math.PI / 6;
    pts.push((cx + r * Math.cos(a)).toFixed(1) + "," + (cy + r * Math.sin(a)).toFixed(1));
  }
  return pts.join(" ");
}

function hiveSnapshot(b) {
  const src = b.source && typeof b.source === "object" ? b.source : {};
  const tickets = Array.isArray(b.tickets) ? b.tickets : [];
  const intakes = Array.isArray(b.intakes) ? b.intakes : [];
  const releases = Array.isArray(b.releases) ? b.releases : [];
  const roles = Array.isArray(b.roles) ? b.roles : [];
  const pms = Array.isArray(b.postmortems) ? b.postmortems : [];
  const rec = b.launch && typeof b.launch === "object" && b.launch.record && !unread(b.launch.record) ? b.launch.record : null;
  const doraOk = !!(b.dora && !unread(b.dora));
  const spendOk = !!(b.spend && !unread(b.spend));

  const roleMap = {};
  for (const r of HIVE_ROLES) roleMap[r.role] = {state: "idle", label: "idle"};
  if (roles.length) {
    for (const r of roles) {
      if (!r || !r.role || !roleMap[r.role]) continue;
      roleMap[r.role] = {state: r.state || "idle", label: r.state || "idle"};
    }
  } else {
    for (const t of tickets) {
      if (!t || typeof t !== "object") continue;
      if (t.product && t.product.consulted) roleMap.product = {state: "ran", label: "ran (inferred)"};
      if (Array.isArray(t.tasks) && t.tasks.length) roleMap["dev-worker"] = {state: "ran", label: "ran (inferred)"};
      if (t.review && t.review.verdict) roleMap["test-reviewer"] = {state: "ran", label: "ran (inferred)"};
      if (t.release && t.release.version) roleMap.release = {state: "ran", label: "ran (inferred)"};
    }
    if (pms.length) roleMap["ops-triage"] = {state: "ran", label: "ran (inferred)"};
  }

  // Gate LEDs from default/newest ticket
  const tid = netDefault(b);
  const activeTicket = tid ? tickets.find((t) => t && t.id === tid) : null;
  const latestAttempt = activeTicket ? (netAttempts(activeTicket).slice(-1)[0] ?? null) : null;
  const gateRows = activeTicket ? netGates(activeTicket, latestAttempt) : [];
  const gateStatus = {};
  for (const g of NET.gates) gateStatus[g] = "not-run";
  for (const gr of gateRows) {
    if (gr.gate && gr.gate in gateStatus) gateStatus[gr.gate] = gr.verdict || "not-run";
    else if (gr.gate === "gates") {
      for (const g of NET.gates) gateStatus[g] = gr.verdict === "GO" ? "GO" : gr.verdict === "NO-GO" ? "NO-GO" : "not-run";
    }
  }

  const s3Past = new Set(["spec-ready", "opened", "pr-opened", "review", "ci-passed", "ci-failed", "needs-human", "merged", "main-green", "held", "rolled-back"]);
  const s4Past = new Set(["ci-passed", "merged", "main-green", "held", "rolled-back"]);
  const p1 = !!src.workload;
  const p2 = p1 && (roles.length > 0 || !!src.level);
  const p3 = p2 && (intakes.some((x) => x && x.state === "confirmed") || tickets.some((t) => t && s3Past.has(t.state)));
  const p4 = p3 && (tickets.some((t) => t && s4Past.has(t.state)) || releases.length > 0 || !!rec);
  const p5 = p4 && (!!rec || releases.length > 0);
  const p6 = p5 && (!!(rec && (rec.promoted || rec.rejected)) || releases.some((r) => r && r.answer && r.answer.answer));
  const p7 = (doraOk || spendOk || pms.length > 0 || !!(rec && rec.verdict === "rolled-back"));
  const hasWarn = tickets.some((t) => t && ["ci-failed", "needs-human", "rolled-back"].includes(t.state)) || !!(rec && rec.verdict === "rolled-back");

  const doneFlags = [p1, p2, p3, p4, p5, p6, p7];
  let livePhase = doneFlags.findIndex((d) => !d) + 1;
  if (livePhase === 0) livePhase = 7;

  const phaseStatuses = doneFlags.map((done, idx) => {
    const n = idx + 1;
    if (n === 5 && rec && rec.verdict === "rolled-back") return "warn";
    if (n === 4 && tickets.some((t) => t && (t.state === "ci-failed" || t.state === "needs-human"))) return "warn";
    if (done) return "done";
    if (n === livePhase) return "now";
    return "todo";
  });

  const liveFacts = [
    src.workload ? "Workload " + src.workload + " (" + shown(src.level) + ", " + shown(src.code_host) + ")" : "No workload recorded",
    roles.length ? roles.length + " roles tracked" : "Schema " + shown(b.schema) + " (roles inferred)",
    intakes.length + " intake(s) · " + tickets.length + " ticket(s)",
    activeTicket ? "Latest ticket " + activeTicket.id + " (" + activeTicket.state + ")" : "No active ticket",
    rec ? "Canary " + shown(rec.version) + ": " + shown(rec.verdict) + (rec.held_at !== null && rec.held_at !== undefined ? " at " + rec.held_at + "%" : "") : "No active canary release",
    rec && (rec.promoted || rec.rejected) ? "Human gate answered" : (rec && rec.verdict === "held" ? "Human gate waiting at " + shown(rec.held_at) + "%" : "Human gate not waiting"),
    [doraOk ? "DORA ready" : null, spendOk ? "Spend ready" : null, pms.length + " postmortem(s)"].filter(Boolean).join(" · ") + (hasWarn ? " · rollback/rework observed" : ""),
  ];

  return {src, tickets, intakes, releases, roles, pms, rec, roleMap, gateStatus, livePhase, phaseStatuses, liveFacts};
}

function hiveSvg(snap, activePhase) {
  const phase = HIVE_PHASES[activePhase - 1] || HIVE_PHASES[0];
  const activeRoles = new Set(phase.roles);
  const children = [];

  children.push(svg("title", {}, "Animated lights-out Hive Control Center: Tower, 6 roles, 7 E2E phases, 8 quality gate LEDs, canary traffic arc, and rollback return arc"));

  // Section headings inside SVG
  children.push(svg("text", {x: 24, y: 22, cls: "hive-label-muted"}, "Agentic Hive (Tower + 6 Roles)"));
  children.push(svg("text", {x: 472, y: 22, cls: "hive-label-muted"}, "8 Quality Gates & Canary Release Arc"));

  // Spokes + animated signals between Tower (250, 190) and orbiting roles
  for (const r of HIVE_ROLES) {
    const active = activeRoles.has(r.role) || (snap.roleMap[r.role] && snap.roleMap[r.role].state === "running");
    children.push(svg("line", {cls: "hive-spoke", x1: 250, y1: 190, x2: r.cx, y2: r.cy}));
    children.push(svg("line", {cls: "hive-signal", "data-role": r.role, "data-active": String(active), x1: 250, y1: 190, x2: r.cx, y2: r.cy}));
  }

  // Central Tower hex
  children.push(svg("g", {cls: "hive-node", "data-role": "tower", "data-active": "true"},
    svg("polygon", {cls: "hive-beacon", points: hiveHexPoints(250, 190, 52)}),
    svg("polygon", {cls: "hive-cell", points: hiveHexPoints(250, 190, 44)}),
    svg("text", {x: 250, y: 186, "text-anchor": "middle"}, "Tower"),
    svg("text", {x: 250, y: 202, "text-anchor": "middle", cls: "hive-sub"}, snap.tickets.length + " ticket(s)")));

  // Orbiting 6 role hex cells
  for (const r of HIVE_ROLES) {
    const st = snap.roleMap[r.role] || {state: "idle", label: "idle"};
    const active = activeRoles.has(r.role) || st.state === "running";
    children.push(svg("g", {cls: "hive-hex", "data-role": r.role, "data-state": st.state, "data-active": String(active)},
      svg("polygon", {cls: "hive-beacon", points: hiveHexPoints(r.cx, r.cy, 43)}),
      svg("polygon", {cls: "hive-cell", points: hiveHexPoints(r.cx, r.cy, 36)}),
      svg("text", {x: r.cx, y: r.cy - 2, "text-anchor": "middle"}, r.role),
      svg("text", {x: r.cx, y: r.cy + 13, "text-anchor": "middle", cls: "hive-sub"}, st.label)));
  }

  // 8-Gate LED Bank (x=472..798, y=34..164)
  children.push(svg("rect", {x: 472, y: 34, width: 326, height: 132, rx: 10, fill: "var(--bg)", stroke: "var(--line)", "stroke-width": 1.25}));
  const marks = {"GO": "✓", "NO-GO": "✗", "running": "◌", "not-run": "·"};
  NET.gates.forEach((g, idx) => {
    const col = idx % 4, row = Math.floor(idx / 4);
    const gx = 484 + col * 78, gy = 48 + row * 56;
    const verdict = snap.gateStatus[g] || "not-run";
    const ledActive = phase.gates || verdict === "NO-GO" || verdict === "running";
    children.push(svg("g", {cls: "hive-gate-led", "data-gate": g, "data-verdict": verdict, "data-active": String(ledActive)},
      svg("rect", {x: gx, y: gy, width: 70, height: 46, rx: 7}),
      svg("circle", {cls: "hive-led-ring", cx: gx + 16, cy: gy + 17, r: 10}),
      svg("circle", {cls: "hive-led", cx: gx + 16, cy: gy + 17, r: 7}),
      svg("text", {x: gx + 31, y: gy + 21, "text-anchor": "start", cls: "hive-sub"}, marks[verdict] || "·"),
      svg("text", {x: gx + 35, y: gy + 38, "text-anchor": "middle", cls: "hive-sub"}, g.length > 9 ? g.slice(0, 8) + "…" : g)));
  });

  // Canary traffic arc (5% -> 25% -> 100%)
  const canaryActive = phase.canary || !!(snap.rec && snap.rec.verdict === "in-progress");
  children.push(svg("g", {cls: "hive-canary-arc", "data-active": String(canaryActive)},
    svg("path", {cls: "hive-arc-base", d: "M 476 232 Q 635 174 794 232"}),
    svg("path", {cls: "hive-arc-flow", d: "M 476 232 Q 635 174 794 232"}),
    svg("circle", {cx: 494, cy: 226, r: 12, fill: "var(--go-bg)", stroke: "var(--go)", "stroke-width": 1.5}),
    svg("text", {x: 494, y: 230, "text-anchor": "middle", cls: "hive-sub"}, "5%"),
    svg("circle", {cx: 590, cy: 204, r: 14, fill: "var(--wait-bg)", stroke: "var(--wait)", "stroke-width": 1.5}),
    svg("text", {x: 590, y: 208, "text-anchor": "middle", cls: "hive-sub"}, "25%"),
    svg("rect", {x: 644, y: 192, width: 66, height: 24, rx: 6, fill: "var(--panel)", stroke: "var(--accent)", "stroke-width": 1.5}),
    svg("text", {x: 677, y: 208, "text-anchor": "middle", cls: "hive-sub"}, "Human"),
    svg("circle", {cx: 774, cy: 226, r: 15, fill: "var(--go-bg)", stroke: "var(--go)", "stroke-width": 1.5}),
    svg("text", {x: 774, y: 230, "text-anchor": "middle", cls: "hive-sub"}, "100%")));

  // Rollback return arc (Canary/Verify back to Ops-Triage & Tower)
  const rollbackActive = phase.rollback || !!(snap.rec && snap.rec.verdict === "rolled-back");
  children.push(svg("g", {cls: "hive-rollback-arc", "data-active": String(rollbackActive)},
    svg("path", {cls: "hive-arc-base", d: "M 590 250 C 550 328, 300 356, 136 164"}),
    svg("path", {cls: "hive-arc-flow", d: "M 590 250 C 550 328, 300 356, 136 164"}),
    svg("text", {x: 596, y: 318, "text-anchor": "start", cls: "hive-sub"}, "Rollback & Auto-Incident Arc")));

  // Bottom 7-phase pipeline strip inside SVG
  children.push(svg("line", {cls: "hive-spoke", x1: 60, y1: 396, x2: 760, y2: 396}));
  HIVE_PHASES.forEach((p, idx) => {
    const px = 22 + idx * 112;
    const status = snap.phaseStatuses[idx];
    const active = p.n === activePhase;
    children.push(svg("g", {cls: "hive-pipe-node", "data-phase": String(p.n), "data-status": status, "data-active": String(active)},
      svg("rect", {cls: "hive-pipe-pill", x: px, y: 374, width: 102, height: 44, rx: 8}),
      svg("text", {x: px + 51, y: 393, "text-anchor": "middle"}, p.short),
      svg("text", {x: px + 51, y: 409, "text-anchor": "middle", cls: "hive-sub"}, status)));
  });

  return svg("svg", {id: "hive-svg", viewBox: "0 0 820 434", role: "img",
    "data-active-phase": String(activePhase),
    "aria-label": "Animated Hive Control Center: Tower, 6 roles, 7 E2E phases, 8 quality gate LEDs, canary traffic arc, and rollback return arc"},
    ...children);
}

function wizardCommands(cfg) {
  const st = HIVE_STACKS[cfg.stack] || HIVE_STACKS.python;
  const pr = HIVE_PROVIDERS[cfg.provider] || HIVE_PROVIDERS.replay;
  const ch = HIVE_CODEHOSTS[cfg.codehost] || HIVE_CODEHOSTS.github;
  const dlv = HIVE_DELIVERIES[cfg.delivery] || HIVE_DELIVERIES["envoy-canary"];
  const delivArg = dlv.cloud ? "outside/" + cfg.delivery : cfg.delivery;
  const lines = [
    "# " + st.label,
    "# " + st.note,
    "lights-out doctor PROFILE=" + pr.profile + " MODEL_PROVIDER=" + cfg.provider,
    "lights-out init",
  ];
  if (dlv.cloud) {
    lines.push("lights-out cloud-setup CLOUD=" + cfg.delivery);
    lines.push("# After Terraform + OIDC setup, pass ADAPTERS_PATH=/abs/path/to/plugins/cloud and DELIVERY=" + delivArg);
  }
  if (pr.envVar) lines.push("export " + pr.envVar + "=<your-provider-key>  # kept in shell/gitignored .env, never committed");
  if (ch.envVar) lines.push("export " + ch.envVar + "=<your-codehost-token>  # CODE_HOST=" + cfg.codehost);
  lines.push(
    "lights-out start --non-interactive --preset " + cfg.preset +
      " --provider " + cfg.provider +
      " --harness " + pr.harness +
      " --delivery " + delivArg +
      " --profile " + pr.profile +
      " --ticket-budget-usd " + (cfg.ticketBudget || "2.00") +
      " --day-budget-usd " + (cfg.dayBudget || "10.00") +
      " CODE_HOST=" + cfg.codehost,
    "lights-out gates",
    "lights-out next",
    "lights-out intake TEXT=\"Add your first requirement\"",
    "lights-out release",
    "lights-out promote",
    "lights-out console"
  );
  return lines.join("\n");
}

function drawWizard() {
  const d = document.getElementById("onboard-wizard");
  if (!d) return;
  const field = (id, label, hint, ctrl) => h("div", {cls: "wiz-field"},
    h("label", {for: id}, label),
    hint ? h("span", {id: id + "-hint", cls: "muted"}, hint) : null,
    ctrl);
  const sel = (id, opts, val, key, hintId) => h("select", {id, "aria-describedby": hintId || null,
    onchange: (e) => { wizState[key] = e.target.value; updateWizardOut(); }},
    Object.entries(opts).map(([k, v]) => h("option", {value: k, selected: k === val}, v.label || v)));

  const outPre = h("pre", {id: "wizard-output", role: "region", "aria-label": "Generated lights-out commands", tabindex: "0"}, wizardCommands(wizState));
  const copyStatus = h("span", {id: "wizard-copy-status", cls: "muted"}, "");
  const copyBtn = h("button", {type: "button", id: "wizard-copy", cls: "hive-primary", onclick: () => {
    const txt = wizardCommands(wizState);
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(() => { copyStatus.textContent = "Copied commands to clipboard."; })
          .catch(() => { copyStatus.textContent = "Copy manually from the box above."; });
      } else { copyStatus.textContent = "Copy manually from the box above."; }
    } catch (e) { copyStatus.textContent = "Copy manually from the box above."; }
  }}, "Copy commands");

  d.replaceChildren(
    h("div", {cls: "back-top"},
      h("h1", {id: "wizard-title", style: "flex: 1; margin: 0; font-size: 18px"}, "Interactive Repo Wizard"),
      h("button", {type: "button", id: "wizard-close", cls: "close", onclick: closeWizard}, "Close (Esc)")),
    h("p", {cls: "muted", style: "margin: 6px 0 0"}, "Generates zero-secret lights-out wrapper commands for your repository. Nothing is saved or posted."),
    h("div", {cls: "wiz-grid"},
      field("wiz-stack", "Stack", "Language & lockfile contract",
        sel("wiz-stack", HIVE_STACKS, wizState.stack, "stack", "wiz-stack-hint")),
      field("wiz-provider", "AI Provider & Harness", "L0 replay needs no API key",
        sel("wiz-provider", HIVE_PROVIDERS, wizState.provider, "provider", "wiz-provider-hint")),
      field("wiz-codehost", "CodeHost", "Where PRs & CI statuses live",
        sel("wiz-codehost", HIVE_CODEHOSTS, wizState.codehost, "codehost", "wiz-codehost-hint")),
      field("wiz-delivery", "Cloud / Delivery Target", "Canary rollout adapter",
        sel("wiz-delivery", HIVE_DELIVERIES, wizState.delivery, "delivery", "wiz-delivery-hint")),
      field("wiz-preset", "Onboarding Preset", "Controls defaults & verbosity",
        sel("wiz-preset", {junior: "Junior (guided defaults)", strict: "Strict (production gates)", custom: "Custom"}, wizState.preset, "preset", "wiz-preset-hint")),
      field("wiz-ticket-budget", "Per-ticket budget (USD)", "SPEND_BUDGET_TICKET_USD",
        h("input", {type: "text", id: "wiz-ticket-budget", inputmode: "decimal", value: wizState.ticketBudget,
          "aria-describedby": "wiz-ticket-budget-hint",
          oninput: (e) => { wizState.ticketBudget = e.target.value; updateWizardOut(); }})),
      field("wiz-day-budget", "Daily budget (USD)", "SPEND_BUDGET_DAY_USD",
        h("input", {type: "text", id: "wiz-day-budget", inputmode: "decimal", value: wizState.dayBudget,
          "aria-describedby": "wiz-day-budget-hint",
          oninput: (e) => { wizState.dayBudget = e.target.value; updateWizardOut(); }}))),
    h("h2", {style: "margin-top: 8px"}, "Copy-paste lights-out commands"),
    outPre,
    h("div", {cls: "buttons", style: "align-items: center"}, copyBtn, copyStatus));
}

function updateWizardOut() {
  const getVal = (id, cur) => { const el = document.getElementById(id); return el && el.value !== undefined ? String(el.value) : cur; };
  wizState.stack = getVal("wiz-stack", wizState.stack);
  wizState.provider = getVal("wiz-provider", wizState.provider);
  wizState.codehost = getVal("wiz-codehost", wizState.codehost);
  wizState.delivery = getVal("wiz-delivery", wizState.delivery);
  wizState.preset = getVal("wiz-preset", wizState.preset);
  wizState.ticketBudget = getVal("wiz-ticket-budget", wizState.ticketBudget);
  wizState.dayBudget = getVal("wiz-day-budget", wizState.dayBudget);
  const pre = document.getElementById("wizard-output");
  if (pre) pre.textContent = wizardCommands(wizState);
}

function openWizard() {
  const d = document.getElementById("onboard-wizard");
  if (!d) return;
  drawWizard();
  if (!d.open) d.showModal();
  const first = document.getElementById("wiz-stack");
  if (first && first.focus) first.focus();
}

function closeWizard() {
  const d = document.getElementById("onboard-wizard");
  if (!d) return;
  if (d.open) d.close();
}

(function initWizardDialog() {
  const d = document.getElementById("onboard-wizard");
  if (!d) return;
  d.addEventListener("close", () => { d.returnValue = ""; });
  d.addEventListener("cancel", (e) => { e.preventDefault(); closeWizard(); });
  if (typeof HTMLDialogElement !== "undefined" && !("closedBy" in HTMLDialogElement.prototype)) {
    d.addEventListener("click", (e) => {
      if (e.target !== d) return;
      const r = d.getBoundingClientRect();
      if (r.top <= e.clientY && e.clientY <= r.top + r.height && r.left <= e.clientX && e.clientX <= r.left + r.width) return;
      closeWizard();
    });
  }
})();

function fillHiveNarration(box, snap, activePhase) {
  if (!box) return;
  const p = HIVE_PHASES[activePhase - 1] || HIVE_PHASES[0];
  const isExpert = hiveState.mode === "expert";
  const st = snap.phaseStatuses[activePhase - 1];
  const chipCls = st === "done" ? "GO" : st === "warn" ? "NO-GO" : st === "now" ? "pending" : "none";
  box.replaceChildren(
    h("div", {cls: "badges", style: "margin: 0 0 8px"},
      h("h2", {style: "margin: 0"}, "Phase " + p.n + "/7: " + p.title),
      h("span", {cls: "chip " + chipCls}, st)),
    h("div", {cls: "hive-role-chips"},
      h("span", {cls: "chip none"}, "Tower"),
      p.roles.map((r) => h("span", {cls: "chip " + (snap.roleMap[r] && snap.roleMap[r].state === "running" ? "pending" : "GO")}, r))),
    h("p", {style: "margin: 6px 0"}, isExpert ? p.expert : p.junior),
    h("p", {cls: "muted", style: "margin: 6px 0"}, "Live logbook binding: ", snap.liveFacts[activePhase - 1]),
    h("pre", {cls: "hive-narr-cmd"}, p.cmd));
}

function updateHiveView(announceStep) {
  if (!book) return;
  const snap = hiveSnapshot(book);
  const activePhase = hiveState.phase || snap.livePhase;
  const p = HIVE_PHASES[activePhase - 1] || HIVE_PHASES[0];
  const activeRoles = new Set(p.roles);

  const svgHost = document.getElementById("hive-svg-wrap");
  if (svgHost) svgHost.replaceChildren(hiveSvg(snap, activePhase));
  const wrap = document.getElementById("hive-wrap");
  if (wrap) wrap.setAttribute("data-motion", still() ? "reduced" : "on");

  for (const btn of document.querySelectorAll(".hive-phase-btn")) {
    const n = Number(btn.getAttribute("data-phase"));
    if (n === activePhase) btn.setAttribute("aria-current", "step");
    else btn.removeAttribute("aria-current");
  }
  const playBtn = document.getElementById("hive-play");
  if (playBtn) playBtn.textContent = hiveState.playing ? "Pause" : "Play Hive Walkthrough";
  const jBtn = document.getElementById("hive-mode-junior");
  const eBtn = document.getElementById("hive-mode-expert");
  if (jBtn) jBtn.setAttribute("aria-selected", String(hiveState.mode === "junior"));
  if (eBtn) eBtn.setAttribute("aria-selected", String(hiveState.mode === "expert"));

  fillHiveNarration(document.getElementById("hive-narration"), snap, activePhase);

  if (announceStep) {
    const ann = document.getElementById("announce");
    if (ann) ann.textContent = "Hive phase " + p.n + " of 7: " + p.title + ". Active roles: " + [...activeRoles].join(", ") + ".";
  }
}

function screenHive() {
  const snap = hiveSnapshot(book);
  if (hiveState.phase === null) hiveState.phase = snap.livePhase;
  const activePhase = hiveState.phase;
  const stepChips = {done: "GO", now: "pending", warn: "NO-GO", todo: "none"};
  const stepMarks = {done: "✓", now: "◌", warn: "✗", todo: "·"};

  const setPhase = (n, announce) => {
    stopHivePlay();
    hiveState.phase = n;
    updateHiveView(announce);
  };
  const togglePlay = () => {
    if (hiveState.playing) {
      stopHivePlay();
      updateHiveView(true);
      return;
    }
    hiveState.playing = true;
    updateHiveView(true);
    hiveState.timer = setInterval(() => {
      if (route().screen !== "hive") { stopHivePlay(); return; }
      hiveState.phase = ((hiveState.phase || 1) % 7) + 1;
      updateHiveView(false);
    }, 2800);
  };
  const stepForward = () => {
    stopHivePlay();
    hiveState.phase = ((hiveState.phase || 1) % 7) + 1;
    updateHiveView(true);
  };
  const resetWalk = () => {
    stopHivePlay();
    hiveState.phase = 1;
    updateHiveView(true);
  };
  const setMode = (mode) => {
    hiveState.mode = mode;
    saveHiveMode(mode);
    updateHiveView(true);
  };
  const tabKey = (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const next = hiveState.mode === "junior" ? "expert" : "junior";
    setMode(next);
    const el = document.getElementById("hive-mode-" + next);
    if (el && el.focus) el.focus();
  };

  const narrEl = h("div", {cls: "panel", id: "hive-narration"});
  fillHiveNarration(narrEl, snap, activePhase);

  return [
    h("h1", {}, "Animated Hive Control Center"),
    h("p", {cls: "lede"}, "Watch the Tower coordinate all 6 roles across the 7-phase lifecycle, 8 PR quality gates, canary traffic shifts, and automatic rollback recovery. Purely client-side over your logbook."),
    h("div", {cls: "hive-toolbar"},
      h("button", {type: "button", id: "hive-play", cls: "hive-primary", onclick: togglePlay}, hiveState.playing ? "Pause" : "Play Hive Walkthrough"),
      h("button", {type: "button", id: "hive-step", onclick: stepForward}, "Step"),
      h("button", {type: "button", id: "hive-reset", onclick: resetWalk}, "Reset"),
      h("div", {cls: "hive-tabs", role: "tablist", "aria-label": "Walkthrough detail level", onkeydown: tabKey},
        h("button", {type: "button", role: "tab", id: "hive-mode-junior", "aria-selected": String(hiveState.mode === "junior"),
          onclick: () => setMode("junior")}, "Junior"),
        h("button", {type: "button", role: "tab", id: "hive-mode-expert", "aria-selected": String(hiveState.mode === "expert"),
          onclick: () => setMode("expert")}, "Expert")),
      h("button", {type: "button", id: "open-wizard", onclick: openWizard}, "Interactive Repo Wizard")),
    h("ol", {cls: "hive-phases", "aria-label": "Seven end-to-end Hive phases"},
      HIVE_PHASES.map((p, idx) => {
        const st = snap.phaseStatuses[idx];
        return h("li", {},
          h("button", {type: "button", cls: "hive-phase-btn", "data-phase": String(p.n), "data-status": st,
            "aria-current": p.n === activePhase ? "step" : null, onclick: () => setPhase(p.n, true)},
            h("span", {cls: "hive-phase-head"},
              h("span", {}, p.short),
              h("span", {cls: "chip " + stepChips[st]}, (stepMarks[st] || "·") + " " + st)),
            h("span", {cls: "muted"}, p.roles.join(", "))));
      })),
    h("div", {cls: "hive-layout hive-wrap", id: "hive-wrap", "data-motion": still() ? "reduced" : "on"},
      h("div", {cls: "panel hive-graph", id: "hive-svg-wrap"}, hiveSvg(snap, activePhase)),
      narrEl),
  ];
}

