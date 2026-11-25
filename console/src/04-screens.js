// --- the role matrix (docs/adr/0086 §2) ---------------------------------------
// One row per role, from the logbook's `roles`: what the profile configures,
// the last run's tokens against the role's TOKEN_BUDGET, and its state in the
// logbook's words. The summary sentence is the logbook's too.
const n_ = (x) => Number(x).toLocaleString("en");
function tokenCell(used, budget) {
  if (Number.isInteger(used) && Number.isInteger(budget)) {
    const text = n_(used) + " of " + n_(budget) + (used > budget ? " — over" : "");
    return [text, h("meter", {min: "0", max: String(budget), value: String(Math.min(used, budget)), "aria-label": text + " tokens"})];
  }
  if (budget === null || budget === undefined) return (Number.isInteger(used) ? n_(used) + " tokens, " : "") + "no budget";
  return "none yet, of " + n_(budget);
}
function rolesPanel() {
  const rows = book.roles;
  let body;
  if (rows === undefined || rows === null) body = h("p", {cls: "muted"}, "This logbook has no role matrix: ./go logbook writes it from logbook 1.6.");
  else if (unread(rows)) body = err(rows);
  else body = [h("p", {id: "roles-summary"}, shown(book.roles_summary)),
    h("table", {id: "role-matrix"}, h("tr", {}, ["Role", "Provider", "Harness", "Thinking", "Tokens and budget", "State"].map((c) => h("th", {}, c))),
      rows.map((r) => {
        const c = r.configured || {}, l = r.last || null;
        const configured = c.provider ? c.provider + " " + shown(c.model) : "replay (recorded runs)";
        const other = l && l.provider && (l.provider !== c.provider || l.model !== c.model) ? "last ran on " + l.provider + " " + shown(l.model) : null;
        return h("tr", {cls: "role-row", "data-role": r.role, "data-state": r.state},
          h("td", {cls: "role"}, r.role),
          h("td", {"data-provider": c.provider || ""}, configured, other ? [h("br"), h("span", {cls: "muted"}, other)] : null),
          h("td", {cls: "harness"}, shown(c.harness)), h("td", {cls: "thinking"}, shown(c.thinking)),
          h("td", {cls: "tokens"}, tokenCell(r.used, r.budget)),
          h("td", {cls: "state"}, r.error ? h("span", {cls: "unread"}, r.error) : r.line));
      }))];
  return h("section", {id: "roles"}, h("h2", {}, "Who runs each role (ADR 0086)"),
    h("div", {cls: "panel", tabindex: "0", role: "region", "aria-label": "Role configuration and token matrix"}, body));
}

// --- the run in flight (docs/adr/0087 §4-§5) ----------------------------------
const short = (x) => x >= 1e6 ? (x / 1e6).toFixed(1) + "M" : x >= 1e3 ? Math.round(x / 1e3) + "k" : String(x);
function liveBadge(t) {
  const lv = t.live;
  if (!lv || typeof lv !== "object") return null;
  const warn = lv.warn === true;
  return h("span", {cls: "live", "data-warn": String(warn), role: "img", "aria-label": lv.line, title: lv.line},
    (warn ? "near " : "") + short(lv.tokens) + (Number.isInteger(lv.budget) ? " / " + short(lv.budget) : ""),
    Number.isInteger(lv.budget) ? h("meter", {min: "0", max: String(lv.budget), value: String(Math.min(lv.tokens, lv.budget))}) : null);
}
function liveLine(t) {
  const lv = t.live;
  return lv && typeof lv === "object" ? h("p", {cls: "live-line", "data-warn": String(lv.warn === true)}, lv.line) : null;
}
// The burn's 24 hours as bars (no chart library: an inline SVG of rectangles),
// labelled with the burn line itself: the page formats no number of its own.
function hoursSpark(b, label) {
  const hourly = Array.isArray(b.hourly) ? b.hourly : [];
  const top = Math.max(...hourly, 0);
  return svg("svg", {id: "spend-hours", viewBox: "0 0 240 40", preserveAspectRatio: "none", role: "img", "aria-label": label},
    hourly.map((v, i) => {
      const height = top > 0 ? Math.max(1, Math.round(38 * v / top)) : 1;
      return svg("rect", {cls: "hour", "data-hour": String(i), "data-zero": v > 0 ? null : "", x: String(i * 10 + 1), y: String(40 - height), width: "8", height: String(height)});
    }));
}

// --- delivery and spend (docs/adr/0082, 0083, amendments of 2026-11-13) --
// The words are the commands' own, carried by logbook 1.5: `./go dora` and
// `./go spend` print the same lines. The page formats no number.
const DORA_KEYS = ["deployment_frequency", "lead_time", "change_fail_rate", "recovery_time"];
function doraPanel() {
  const d = book.dora;
  const lines = d && Array.isArray(d.lines) ? d.lines.map(String) : [];
  const body = d === undefined || d === null ? h("p", {cls: "muted"}, "This logbook has no delivery measures: ./go logbook writes them from logbook 1.5.")
    : unread(d) ? err(d)
    : [h("p", {id: "dora-window"}, lines[0] || ""),
      h("p", {cls: "muted"}, "The window ends at the record's last event, not at a clock; ./go dora ends it now."),
      h("div", {cls: "dora-grid"}, DORA_KEYS.map((k, i) => {
        const line = lines[i + 1] || "", at = line.indexOf(": ");
        const m = (d.measures || {})[k] || {};
        return h("div", {cls: "dora-measure", "data-key": k, "data-measured": m.measured === true ? "true" : "false"},
          at < 0 ? line : [h("b", {}, line.slice(0, at)), line.slice(at)]);
      }))];
  return h("section", {id: "dora"}, h("h2", {}, "Delivery, the four keys (./go dora, ADR 0082)"), h("div", {cls: "panel"}, body));
}
function spendPanel() {
  const s = book.spend;
  let body;
  if (s === undefined || s === null) body = h("p", {cls: "muted"}, "This logbook has no spend: ./go logbook writes it from logbook 1.5.");
  else if (unread(s)) body = err(s);
  else {
    const lines = Array.isArray(s.lines) ? s.lines.map(String) : [];
    const kind = (w) => lines.filter((l) => l.startsWith(w + " "));
    const list = (id, title, rows) => h("div", {}, h("b", {}, title), h("ul", {id: id, cls: "spend-lines"}, rows.map((l) => h("li", {}, l))));
    // The burn and the day's projection (docs/adr/0087 §3, §5), in ./go spend's words.
    const b = s.burn === undefined ? undefined : s.burn;
    const burnLine = lines.find((l) => l.startsWith("burn: ")), projLine = lines.find((l) => l.startsWith("projection: "));
    const flying = ticketsList().filter((t) => t.live && typeof t.live === "object");
    body = [h("p", {id: "spend-summary"}, shown(s.summary)),
      b === undefined ? null : [h("p", {id: "spend-burn"}, shown(burnLine)), b ? hoursSpark(b, burnLine || "") : null,
        h("p", {id: "spend-projection", "data-over": b && b.over !== null && b.over !== undefined ? String(b.over) : ""}, shown(projLine))],
      flying.length ? [h("b", {}, "Running now"), h("ul", {id: "spend-live", cls: "spend-lines"},
        flying.map((t) => h("li", {"data-id": t.id}, h("a", {href: "#ticket/" + encodeURIComponent(t.id)}, t.id), ": " + t.live.line)))] : null,
      h("div", {cls: "grid3"}, list("spend-tickets", "Per ticket", kind("ticket")), list("spend-roles", "Per role", kind("role")), list("spend-days", "Per day (UTC)", kind("day"))),
      h("p", {id: "spend-budget", cls: "muted"}, lines.filter((l) => l.startsWith("budget: ")).pop() || "")];
  }
  return h("section", {id: "spend"}, h("h2", {}, "Spend, cost and time (./go spend, ADR 0083)"), h("div", {cls: "panel"}, body));
}

// --- overview (docs/adr/0055 §6) --------------------------------------
const ORDER = ["requested", "questions", "spec-ready", "opened", "pr-opened", "review", "ci-passed", "ci-failed",
  "needs-human", "merged", "main-green", "held", "rolled-back"];
function waitLine(w) {
  return "waiting since " + shown(w.since) + ", " + w.refusals + " refused assign" + (w.refusals > 1 ? "s" : "") +
    (w.holders.length ? "; held by " + w.holders.join(", ") : "; no open ticket holds its files: the base, or a missing perimeter (./go resume)");
}
function personaOverviewDeck(all, yours) {
  const mode = typeof personaMode === "function" ? personaMode() : "junior";
  const rec = book.launch && book.launch.record && !unread(book.launch.record) ? book.launch.record : null;
  if (mode === "senior") {
    const thList = Array.isArray(book.thresholds) ? book.thresholds : [];
    const overCount = thList.filter((t) => t && t.overridden).length;
    const dora = book.dora && !unread(book.dora) ? book.dora : null;
    const doraMeasured = dora && dora.measures ? DORA_KEYS.filter((k) => dora.measures[k] && dora.measures[k].measured === true).length : 0;
    const spend = book.spend && !unread(book.spend) ? book.spend : null;
    const burnOver = spend && spend.burn ? spend.burn.over === true : false;
    const canaryTxt = rec ? shown(rec.verdict) + (rec.held_at !== null && rec.held_at !== undefined ? " @" + rec.held_at + "%" : "") : "none";
    const roleTxt = con && con.role ? String(con.role) : shown(con && con.auth);
    return h("section", {id: "persona-deck", "data-persona-view": "senior"},
      h("h2", {}, "Fleet & Telemetry Cockpit"),
      h("div", {cls: "panel persona-panel"},
        h("div", {cls: "persona-head"},
          h("span", {cls: "muted"}, "Dense telemetry summary with shortcut navigation: Cmd+K command palette, / filter."),
          h("div", {cls: "buttons", style: "margin:0"},
            h("a", {href: "#epics"}, "Epics DAG →"),
            h("a", {href: "#adrs"}, "ADRs →"),
            h("a", {href: "#thresholds"}, "Thresholds →"))),
        h("div", {cls: "cockpit-kpis"},
          h("div", {cls: "cockpit-kpi"},
            h("span", {cls: "kpi-label"}, "Tickets"),
            h("span", {cls: "kpi-val"}, String(all.length) + " total"),
            h("span", {cls: "kpi-sub"}, String(yours.length) + " waiting on human")),
          h("div", {cls: "cockpit-kpi"},
            h("span", {cls: "kpi-label"}, "Canary Plane"),
            h("span", {cls: "kpi-val"}, canaryTxt),
            h("span", {cls: "kpi-sub"}, rec ? shown(rec.version) : "no active rollout")),
          h("div", {cls: "cockpit-kpi"},
            h("span", {cls: "kpi-label"}, "DORA Four Keys"),
            h("span", {cls: "kpi-val"}, dora ? String(doraMeasured) + "/4 measured" : "unavailable"),
            h("span", {cls: "kpi-sub"}, "logbook window")),
          h("div", {cls: "cockpit-kpi"},
            h("span", {cls: "kpi-label"}, "Spend & Burn"),
            h("span", {cls: "kpi-val"}, spend ? (burnOver ? "over day cap" : "within cap") : "unavailable"),
            h("span", {cls: "kpi-sub"}, spend ? shown(spend.summary).slice(0, 48) : "no ledger")),
          h("div", {cls: "cockpit-kpi"},
            h("span", {cls: "kpi-label"}, "Policy Overrides"),
            h("span", {cls: "kpi-val"}, String(overCount) + " override(s)"),
            h("span", {cls: "kpi-sub"}, String(thList.length) + " declared bounds")),
          h("div", {cls: "cockpit-kpi"},
            h("span", {cls: "kpi-label"}, "Session & Access"),
            h("span", {cls: "kpi-val"}, shown(con && con.mode) + " · " + roleTxt),
            h("span", {cls: "kpi-sub"}, con && con.can_launch ? "can promote / rollback" : "operator read/assign")))));
  }
  const guidePrompt = yours.length
    ? "You have " + yours.length + " item(s) waiting for a human decision right above in Waiting for you."
    : !all.length
      ? "Start by describing what you want built in New request, or configure your repository with the wizard."
      : "No human decision is blocking right now — follow progress on Kanban or explore the 7 phases in Hive.";
  return h("section", {id: "persona-deck", "data-persona-view": "junior"},
    h("h2", {}, "Guided Workflow & Safety Guardrails"),
    h("div", {cls: "panel persona-panel"},
      h("p", {cls: "persona-prose"}, h("b", {}, "Next step: "), guidePrompt),
      h("div", {cls: "buttons"},
        h("a", {cls: "act primary", style: "margin:0", href: "#hive"}, "Open Animated Hive Guide →"),
        h("a", {href: "#request", style: "align-self:center"}, "Describe a new need →"),
        h("button", {type: "button", id: "overview-wizard-btn", onclick: () => { if (typeof openWizard === "function") openWizard(); }}, "Repo Onboarding Wizard")),
      h("div", {cls: "guardrail-grid"},
        h("div", {cls: "guardrail-item"},
          h("h3", {}, "Scoped Perimeters"),
          h("p", {}, "Every ticket locks which files an AI worker can modify before any code edit begins.")),
        h("div", {cls: "guardrail-item"},
          h("h3", {}, "8 Deterministic Gates"),
          h("p", {}, "Risk, secrets, test-weakening, dependencies, SAST, tests, mutation, and test-proof judge every PR.")),
        h("div", {cls: "guardrail-item"},
          h("h3", {}, "5% → 25% Canary & Rollback"),
          h("p", {}, "Live releases verify error-rate and p95 latency SLOs and wait for human Go-for-launch at 25%.")))));
}
function screenOverview() {
  const out = [h("h1", {}, "Overview"),
    h("p", {cls: "lede"}, "What waits for a person first; then every ticket the tower holds, by where it is in the flow; the delivery measures and the spend; the files open tickets hold; the docs a merge left to realign. ",
      h("a", {href: "#kanban"}, "The kanban →"))];
  if (unread(book.tickets)) return out.concat(h("div", {cls: "panel"}, err(book.tickets)));
  const all = ticketsList();
  // A release in course is shown first (docs/adr/0080 §5).
  const rec = book.launch && book.launch.record;
  if (rec && !unread(rec) && rec.verdict === "in-progress") out.push(canaryPanel(rec));
  // Waiting for you (docs/adr/0063 §4): exactly the tickets whose now line is a person's turn.
  const yours = all.filter((t) => now(t).turn === "you");
  out.push(h("h2", {}, "Waiting for you"), h("div", {cls: "panel", id: "your-turn"}, yours.length
    ? h("ul", {cls: "yours"}, yours.map((t) => h("li", {"data-id": t.id}, h("a", {href: "#ticket/" + encodeURIComponent(t.id)}, named(t)), h("br"), nowLine(t))))
    : h("p", {cls: "muted"}, "Nothing waits for a person: the tower or a role has the ball on every ticket.")));
  out.push(personaOverviewDeck(all, yours));
  const states = ORDER.concat([...new Set(all.map((t) => t.state))].filter((st) => !ORDER.includes(st)));
  const inState = (st) => all.filter((t) => t.state === st);
  const bands = BANDS.concat([{band: "other", name: "Other", states: states.filter((st) => bandOf(st) === "other")}]);
  out.push(rolesPanel());
  out.push(h("h2", {}, "Where the tickets are"), h("div", {cls: "panel"}, h("div", {cls: "grid3"},
    bands.filter((b) => b.states.some((st) => inState(st).length)).map((b) => h("div", {cls: "band-sum", "data-band": b.band},
      h("div", {}, h("b", {}, b.name), " ", h("span", {cls: "count"}, b.states.reduce((s, st) => s + inState(st).length, 0))),
      b.states.filter((st) => inState(st).length).map((st) => h("div", {},
        h("div", {cls: "muted"}, st + " · " + inState(st).length),
        inState(st).map((t) => h("div", {}, h("a", {href: "#ticket/" + encodeURIComponent(t.id)}, named(t)))))))))));
  out.push(doraPanel(), spendPanel());
  const held = book.perimeters_held;
  out.push(h("h2", {}, "Perimeters held (perimeters/held, ADR 0053)"), h("div", {cls: "panel"},
    unread(held) ? err(held) : !Array.isArray(held) ? h("p", {cls: "muted"}, "This logbook has no perimeters_held.")
    : held.length ? h("table", {}, h("tr", {}, ["Ticket", "State", "Globs", "Holds now"].map((c) => h("th", {}, c))),
      held.map((x) => h("tr", {}, h("td", {}, h("a", {href: "#ticket/" + encodeURIComponent(x.ticket)}, x.ticket)), h("td", {}, shown(x.state)),
        h("td", {cls: "mono"}, x.globs.join("  ·  ")), h("td", {}, x.holding ? h("span", {cls: "chip pending"}, "yes") : "no: its PR is not open"))))
      : h("p", {cls: "muted"}, "No assign has claimed a perimeter yet.")));
  const waits = all.filter((t) => t.wait);
  out.push(h("h2", {}, "Tickets waiting at assign"), h("div", {cls: "panel"}, waits.length
    ? h("ul", {}, waits.map((t) => h("li", {}, h("a", {href: "#ticket/" + encodeURIComponent(t.id)}, named(t)), ": ", waitLine(t.wait))))
    : h("p", {cls: "muted"}, "No ticket waits.")));
  const docs = book.docs_to_realign;
  out.push(h("h2", {}, "Docs a merge left to realign (ADR 0054)"), h("div", {cls: "panel"},
    unread(docs) ? err(docs) : !Array.isArray(docs) ? h("p", {cls: "muted"}, "This logbook has no docs_to_realign.")
    : docs.length ? h("table", {}, h("tr", {}, ["Merged ticket", "Merge", "Docs ticket", "Its state"].map((c) => h("th", {}, c))),
      docs.map((d) => h("tr", {}, h("td", {}, d.ticket), h("td", {cls: "mono"}, shown(d.merge_sha)),
        h("td", {}, d.docs_ticket_state ? h("a", {href: "#ticket/" + encodeURIComponent(d.docs_ticket)}, d.docs_ticket) : d.docs_ticket),
        h("td", {}, shown(d.docs_ticket_state) === "—" ? "not opened by the tower" : d.docs_ticket_state))))
      : h("p", {cls: "muted"}, "No merge has left docs to realign.")));
  out.push(h("h2", {}, "Workstation Controls"),
    h("div", {cls: "panel", id: "overview-ops"},
      opsBar("overview"),
      h("p", {cls: "muted", style: "margin:0"}, "Quick navigation: ",
        h("a", {href: "#epics"}, "Epics & Dependency DAG"), " · ",
        h("a", {href: "#adrs"}, "ADR Explorer"), " · ",
        h("a", {href: "#launch"}, "Canary & Live Charts"), " · Press Cmd+K / Ctrl+K for Command Palette.")));
  out.push(journeyPanel());
  return out;
}

function journeyPanel() {
  const src = book.source && typeof book.source === "object" ? book.source : {};
  const tlist = Array.isArray(book.tickets) ? book.tickets : [];
  const ilist = Array.isArray(book.intakes) ? book.intakes : [];
  const rlist = Array.isArray(book.releases) ? book.releases : [];
  const roles = Array.isArray(book.roles) ? book.roles : [];
  const rec = book.launch && typeof book.launch === "object" && book.launch.record && !unread(book.launch.record) ? book.launch.record : null;
  const doraOk = !!(book.dora && !unread(book.dora));
  const spendOk = !!(book.spend && !unread(book.spend));
  const pms = Array.isArray(book.postmortems) ? book.postmortems : [];

  const s1Done = !!src.workload;
  const s2Done = s1Done && (roles.length > 0 || !!src.level);
  const s3Past = new Set(["spec-ready", "opened", "pr-opened", "review", "ci-passed", "ci-failed", "needs-human", "merged", "main-green", "held", "rolled-back"]);
  const s4Past = new Set(["ci-passed", "merged", "main-green", "held", "rolled-back"]);
  const s3Done = s2Done && (ilist.some((x) => x && x.state === "confirmed") || tlist.some((t) => t && s3Past.has(t.state)));
  const s4Done = s3Done && (tlist.some((t) => t && s4Past.has(t.state)) || rlist.length > 0 || !!rec);
  const s5Done = s4Done && (
    rlist.some((r) => r && (r.verdict === "rolled-back" || (r.answer && r.answer.answer)))
    || !!(rec && (rec.verdict === "rolled-back" || rec.promoted || rec.rejected))
    || tlist.some((t) => t && t.state === "rolled-back")
  );
  const s6Done = s5Done && (doraOk || spendOk || pms.length > 0);

  const rawStages = [
    {n: 1, title: "Connect & contract", done: s1Done, active: !s1Done,
      detail: src.workload ? "workload " + src.workload + (src.code_host ? " · " + src.code_host : "") : "Run ./go attach and ./go init",
      href: "#thresholds", label: "Thresholds"},
    {n: 2, title: "Provider & budget", done: s2Done, active: s1Done && !s2Done,
      detail: roles.length ? roles.length + " roles configured" + (src.level ? " (" + src.level + ")" : "") : (src.level ? "level " + src.level : "Run ./go start"),
      href: "#agents", label: "Agents"},
    {n: 3, title: "Intake & spec gate", done: s3Done, active: s2Done && !s3Done && (ilist.length > 0 || tlist.length > 0),
      detail: ilist.length + " intake(s), " + tlist.length + " ticket(s)",
      href: "#specs", label: "Specs"},
    {n: 4, title: "Build, 8 gates & rework", done: s4Done, active: s3Done && !s4Done,
      detail: tlist.filter((t) => t && t.state !== "requested" && t.state !== "questions").length + " ticket(s) in build or beyond",
      href: "#kanban", label: "Kanban"},
    {n: 5, title: "Canary & human gate", done: s5Done, active: s4Done && !s5Done,
      detail: rec ? "latest " + shown(rec.version) + " (" + shown(rec.verdict) + ")" : (rlist.length ? rlist.length + " release(s)" : "No canary yet"),
      href: "#launch", label: "Go-for-launch"},
    {n: 6, title: "Observe & postmortem", done: s6Done, active: s5Done && !s6Done,
      detail: [doraOk ? "DORA" : null, spendOk ? "spend" : null, pms.length ? pms.length + " postmortem(s)" : null].filter(Boolean).join(" · ") || "Run ./go logbook & ./go dora",
      href: "#network", label: "Network"},
  ];
  let seenCurrent = false;
  const stages = rawStages.map((s) => {
    let state = "todo";
    if (s.done) state = "done";
    else if (!seenCurrent) { state = "now"; seenCurrent = true; }
    else if (s.active) { state = "now"; seenCurrent = true; }
    return Object.assign({}, s, {state});
  });
  const doneCount = stages.filter((s) => s.done).length;
  const chipCls = {done: "GO", now: "pending", todo: "none"};
  const chipWord = {done: "done", now: "current", todo: "next"};

  return h("section", {id: "journey"},
    h("h2", {}, "End-to-end journey (./go next, ADR 0103)"),
    h("div", {cls: "panel"},
      h("p", {cls: "muted"}, doneCount + " of 6 stages recorded in this logbook. Quick CLI check: ./go next (MODE=junior or MODE=expert)."),
      h("ol", {cls: "journey-bar", "aria-label": "Six-stage end-to-end delivery lifecycle"},
        stages.map((s) => h("li", {cls: "journey-stage", "data-stage": String(s.n), "data-state": s.state},
          h("div", {cls: "journey-head"},
            h("span", {}, s.n + ". " + s.title),
            h("span", {cls: "chip " + chipCls[s.state]}, chipWord[s.state])),
          h("p", {cls: "journey-note"}, s.detail, " · ", h("a", {href: s.href}, s.label + " →"))))),
      h("details", {cls: "journey-guide"},
        h("summary", {}, "Junior Guide: how the 6 stages protect every change"),
        h("ol", {},
          h("li", {}, h("b", {}, "Connect & contract: "), "./go init measures your baseline mutation score and locks test, SAST, and mutation rules into a read-only contract."),
          h("li", {}, h("b", {}, "Provider, harness & budget: "), "./go start picks replay (zero cost) or a live AI provider and validates doctor status without ever storing secrets in the plan file."),
          h("li", {}, h("b", {}, "Intake & spec gate: "), "./go intake turns plain language into verifiable Given/When/Then criteria and a strict file perimeter before agents touch code."),
          h("li", {}, h("b", {}, "Build, 8 gates & rework: "), "Every PR passes risk, secrets, test-weakening, dependencies, SAST, tests, mutation testing, and test-proof, with automatic rework on NO-GO."),
          h("li", {}, h("b", {}, "Canary deploy, SLO verify & human gate: "), "Rollouts shift 5% then 25% traffic, verify error-rate and p95 latency SLOs, roll back automatically on regression, and wait for human approval at 25%."),
          h("li", {}, h("b", {}, "Observe & postmortem: "), "./go logbook, ./go dora, ./go spend, signed ./go evidence packs, and ./go postmortem turn rollbacks into trackable follow-up tickets."))),
      h("p", {style: "margin: 0 0 10px"}, h("a", {cls: "act primary", id: "open-hive-guide", href: "#hive"}, "Open Animated Hive Guide →")),
      h("div", {cls: "journey-links"},
        h("span", {cls: "muted"}, "Quick links:"),
        h("a", {href: "#specs"}, "Specs"),
        h("a", {href: "#kanban"}, "Kanban"),
        h("a", {href: "#network"}, "Network"),
        h("a", {href: "#launch"}, "Go-for-launch"),
        h("a", {href: "#thresholds"}, "Thresholds"),
        h("a", {href: "#agents"}, "Agents"))));
}

// --- the view in the link (docs/adr/0059 §1) ----------------------------
// `#<screen>[/<arg>][?k=v&...]`: the query is the screen's view, keys in a
// fixed order, defaults left out, values percent-encoded.
function route() {
  const raw = location.hash.slice(1);
  const at = raw.indexOf("?");
  const path = decodeURIComponent(at < 0 ? raw : raw.slice(0, at)) || "overview";
  const query = {};
  for (const pair of (at < 0 ? "" : raw.slice(at + 1)).split("&")) {
    if (!pair) continue;
    const eq = pair.indexOf("=");
    const k = eq < 0 ? pair : pair.slice(0, eq);
    try { query[decodeURIComponent(k)] = eq < 0 ? "" : decodeURIComponent(pair.slice(eq + 1)); } catch (e) { /* a malformed pair is dropped */ }
  }
  const slash = path.indexOf("/");
  return {screen: slash < 0 ? path : path.slice(0, slash), arg: slash < 0 ? "" : path.slice(slash + 1), query};
}
// `turn=you` is "Your turn only" (docs/adr/0068 §5).
const VIEW_KEYS = ["q", "feature", "priority", "turn", "group", "empty", "collapsed", "card"];
function kanbanView() {
  const q = route().query;
  const v = {};
  for (const k of VIEW_KEYS) v[k] = q[k] || "";
  v.collapsed = new Set(v.collapsed ? v.collapsed.split(",").filter(Boolean) : []);
  return v;
}
function viewHash(screen, v, keys) {
  const parts = [];
  for (const k of keys) {
    const val = v[k] instanceof Set ? [...v[k]].join(",") : (v[k] || "");
    if (val) parts.push(k + "=" + encodeURIComponent(val));
  }
  return "#" + screen + (parts.length ? "?" + parts.join("&") : "");
}
// Every kanban interaction lands here: it changes the view and the link,
// never a column.
function setView(patch) {
  const v = Object.assign(kanbanView(), patch);
  history.replaceState(null, "", viewHash("kanban", v, VIEW_KEYS));
  drawBoard();
}


// --- screen 1 ----------------------------------------------------------
function screenTickets() {
  const out = [h("h1", {}, "This week's tickets"),
    h("p", {cls: "lede"}, "Did each change get through, and if not, which judge stopped it and with what line.")];
  if ((book.source || {}).level === "l0" && con.mode === "live")
    out.push(h("div", {cls: "banner warn"}, "The tower is in memory at L0: tickets from before the last ./go down are not here (ADR 0047 §2)."));
  if (unread(book.tickets)) return out.concat(h("div", {cls: "panel"}, err(book.tickets)));
  if (windowDays === null) windowDays = con.mode === "snapshot" ? 0 : 7;
  const pick = h("select", {onchange: (e) => { windowDays = Number(e.target.value); render(); }},
    [[7, "last 7 days"], [30, "last 30 days"], [0, "all"]].map(([v, t]) => h("option", {value: v, selected: v === windowDays}, t)));
  out.push(h("p", {cls: "muted"}, "Window, by this browser's clock: ", pick));
  const since = windowDays ? Date.now() - windowDays * 86400e3 : 0;
  const rows = ticketsList().filter((t) => !since || (t.updated_at && Date.parse(t.updated_at) >= since))
    .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  if (!rows.length) return out.concat(h("div", {cls: "panel"}, h("p", {cls: "muted"}, "No ticket touched in this window. Switch the window selector to all or ", h("a", {href: "#request"}, "create a new request →"))));
  out.push(h("div", {cls: "panel", tabindex: "0", role: "region", "aria-label": "This week's tickets table"}, h("table", {},
    h("tr", {}, ["Ticket", "Outcome", "Decided by", "Why, in one line", "State", "Attempts", "Last activity", "Cost"].map((c) => h("th", {}, c))),
    rows.map((t) => h("tr", {},
      h("td", {}, h("a", {href: "#ticket/" + encodeURIComponent(t.id)}, t.id), " ", t.title,
        t.origin ? h("div", {cls: "muted"}, "from " + t.origin) : null),
      h("td", {}, chip(t.outcome.verdict)),
      h("td", {}, shown(t.outcome.judge)),
      h("td", {cls: "mono"}, shown(t.outcome.line)),
      h("td", {}, t.state),
      h("td", {}, shown(t.attempt)),
      h("td", {cls: "mono"}, shown(t.updated_at)),
      h("td", {}, cost(t.task)))))));
  return out;
}
function cost(task) {
  if (!task) return "—";
  const tokens = task.tokens && task.tokens.total;
  const parts = [];
  if (tokens !== undefined && tokens !== null) parts.push(Number(tokens).toLocaleString("en") + " tokens");
  if (task.duration_s !== null && task.duration_s !== undefined) parts.push(task.duration_s + " s");
  if (!parts.length) return "—";
  return parts.join(", ") + (task.replayed ? " (recorded, replayed)" : "");
}

// --- screen 2: one ticket's timeline (docs/adr/0055 §6) ----------------
const VERDICTS = {"waiting": "pending", "spec-ready": "GO", "questions": "pending", "ci-passed": "GO", "ci-failed": "NO-GO", "review": "pending",
  "merged": "GO", "main-green": "GO", "rolled-back": "NO-GO", "held": "GO", "failed": "stranded", "approved": "GO", "changes-requested": "NO-GO"};
function rowDetail(r) {
  const bits = [];
  if (r.line) bits.push(h("div", {cls: "mono"}, r.line));
  if (r.phase === "attempt") bits.push(dl([["Attempt", r.attempt], ["Harness", r.harness], ["Model", r.model], ["Turns", r.turns],
    ["Tokens", r.tokens && r.tokens.total], ["Duration", r.duration_s === null || r.duration_s === undefined ? null : r.duration_s + " s"],
    r.replayed ? ["Replay", "recorded at L1, replayed: no tokens spent now"] : null]));
  if (r.phase === "product" && (r.turns !== undefined || r.model !== undefined)) bits.push(dl([["Harness", r.harness], ["Model", r.model],
    ["Turns", r.turns], ["Tokens", r.tokens && r.tokens.total], r.replayed ? ["Replay", "recorded at L1, replayed"] : null]));
  if (r.phase === "assign") bits.push(dl([["Perimeter", unread(r.perimeter) ? err(r.perimeter) : h("span", {cls: "mono"}, (r.perimeter || []).join("  ·  "))],
    ["Waited", r.wait ? r.wait.refusals + " refused assign(s) since " + shown(r.wait.since) : "no"]]));
  if (r.phase === "canary") bits.push(h("div", {}, h("b", {}, r.pct + "%"), (r.numbers || []).length ? h("pre", {}, r.numbers.join("\n")) : null));
  if (r.by) bits.push(h("div", {cls: "muted"}, "by " + r.by + (r.via ? " via " + r.via : "")));
  if (r.phase === "rework" && r.judge) bits.push(h("div", {cls: "muted"}, "judged by " + r.judge));
  return bits.length ? bits : "—";
}
async function pressReply(t, index, box) {
  const text = box.value.trim();
  if (!text) return;
  busy = true; render();
  try {
    const r = await fetch("/v1/answer", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ticket: t.id, question: index, answer: text})});
    const body = await r.json().catch(() => ({}));
    answerLine = body.line || ("refused (" + r.status + "): " + (body.error || r.statusText));
  } catch (e) { answerLine = "the console did not answer: " + e; }
  busy = false; await load(); render();
}
function answerPanel(t) {
  const qs = t.questions || [];
  const last = qs.length - 1;
  if ((t.state !== "questions" && t.state !== "intake-questions") || last < 0 || qs[last].answer) return null;
  const box = h("textarea", {placeholder: "Your answer, as the product owner", "aria-label": "Your answer to the open question"});
  return h("div", {cls: "panel"}, h("h2", {}, "Open question"),
    h("p", {}, h("b", {}, qs[last].from === "spec" ? "The spec gate: " : "The product role: "), qs[last].question),
    con.mode === "snapshot" ? h("p", {cls: "muted"}, "Snapshot: no tower behind it, nothing can be answered.") : box,
    h("div", {cls: "buttons"}, h("button", {cls: "go", disabled: busy || con.mode === "snapshot",
      onclick: () => pressReply(t, last, box)}, "Answer — ./go answer TICKET=" + t.id)),
    busy ? h("p", {cls: "muted"}, "./go answer is running: the product role, then the spec gate…") : null,
    answerLine ? h("pre", {}, answerLine) : null);
}
// The band a timeline row belongs to (docs/adr/0063 §3): by its phase, or,
// for a failed step's row, by the step. A phase the map does not name (the
// outcome, a resume) stays under the heading above it: a heading is only a
// label, and no row moves.
const ROW_BAND = {request: "spec", product: "spec", question: "spec", answer: "spec", spec: "spec", open: "spec", clarify: "spec",
  wait: "build", assign: "build", attempt: "build", rework: "build",
  gates: "test", gate: "test", ci: "test", review: "test",
  merge: "release", mainline: "release", canary: "release", release: "release", "sign-off": "release"};
// The four bands as a strip: done, now, stopped (the band a NO-GO ending
// stopped in), or ahead.
// `id` only on the timeline screen: the back draws the same strip without it.
function stageStrip(t, id) {
  const here = BANDS.findIndex((b) => b.states.includes(t.state));
  const out = t.outcome || {};
  const ended = ["rolled-back", "needs-human"].includes(t.state) || (t.state === "held" && out.judge === "human" && out.verdict === "NO-GO");
  const finished = t.state === "held" && out.judge === "human" && out.verdict === "GO";
  const direct = (t.history || []).some((e) => e.step === "open");
  return h("ol", {cls: "stages", id: id || null, "aria-label": "Where ticket " + t.id + " is"}, BANDS.map((b, i) => {
    let mark = here < 0 ? "ahead" : i < here || (i === here && finished) ? "done" : i === here ? (ended ? "stopped" : "now") : "ahead";
    // Opened directly: no request and no spec gate, so Spec was skipped, not passed.
    if (b.band === "spec" && direct) mark = "skipped";
    return h("li", {"data-band": b.band, "data-mark": mark, "aria-current": mark === "now" || mark === "stopped" ? "step" : null},
      b.name + (mark === "done" ? " ✓" : mark === "stopped" ? " ✗" : mark === "skipped" ? " (skipped)" : ""));
  }));
}
function verdictOf(r) {
  // A gates row's verdict is the state it moved to: `review` means the gates said GO;
  // a product row that moved to `questions` asked, it was not left pending.
  if (r.phase === "gates" && r.verdict === "review") return "GO";
  if (r.phase === "product" && r.verdict === "questions") return "asked";
  return VERDICTS[r.verdict] || r.verdict;
}
// A timeline row that names a rejection opens its why (docs/adr/0078 §4):
// the gates row of the attempt the gates sent back, and the rework row,
// which names the rejection of the attempt before it.
function whyAttemptOf(r) {
  if (r.phase === "gates" && verdictOf(r) === "NO-GO" && Number.isInteger(r.attempt)) return r.attempt;
  if (r.phase === "rework" && Number.isInteger(r.attempt)) return r.attempt - 1;
  return null;
}
function whyRow(t, r, inner) {
  const n = whyAttemptOf(r);
  const b = n === null ? null : whyButton(t, n, inner || "why");
  return b || inner;
}
function screenTicket(id) {
  const t = ticketsList().find((x) => x.id === id);
  const back = h("p", {cls: "lede"}, h("a", {id: "back-to-kanban", href: "#kanban?card=" + encodeURIComponent(id)}, "← The kanban, with this card open"));
  if (!t) return [back, h("h1", {}, "Ticket " + id), h("div", {cls: "panel"}, "This ticket is not in the logbook: the tower never opened it, or the store could not be read.")];
  const rows = t.timeline || [];
  const body = [];
  let band = null;
  rows.forEach((r) => {
    const b = ROW_BAND[r.phase] || band || "other";
    if (b !== band) {
      band = b;
      body.push(h("tr", {cls: "band-row", "data-band": b}, h("th", {colspan: "5"}, bandName(b))));
    }
    body.push(h("tr", {"data-phase": r.phase}, h("td", {}, r.phase), h("td", {}, r.verdict ? whyRow(t, r, chip(verdictOf(r))) : whyRow(t, r, null) || "—"),
      h("td", {}, rowDetail(r)), h("td", {cls: "mono at"}, shown(r.at)), h("td", {cls: "muted"}, r.record)));
  });
  const w = now(t);
  const perms = opsPermissions();
  const tasks = Array.isArray(t.tasks) ? t.tasks : [];
  const patches = tasks.filter((tk) => tk && tk.patch && (typeof tk.patch === "string" || typeof tk.patch.text === "string"));
  return [back, h("h1", {}, named(t)),
    h("div", {cls: "panel", id: "ticket-now"},
      h("div", {cls: "nowbox", "data-turn": w.turn}, nowLine(t)),
      h("div", {cls: "ops-actions", style: "margin:8px 0"},
        t.state === "opened" ? h("button", {type: "button", id: "ticket-assign-btn", cls: "go", disabled: !perms.canOps,
          onclick: () => assignTicket(t.id)}, "Assign ticket") : null,
        isResumableTicket(t) ? h("button", {type: "button", id: "ticket-resume-btn", cls: "warn", disabled: !perms.canOps,
          onclick: () => resumeTicket(t.id)}, "Resume stranded step") : null,
        h("a", {href: "#network/" + encodeURIComponent(t.id)}, "Inspect in Network →")),
      stageStrip(t, "stages"),
      flowDetail(t, "flow"),
      liveLine(t),
      h("p", {cls: "muted", style: "margin: 0"}, "Last verdict: ", chip(t.outcome.verdict), " ", shown(t.outcome.judge), ": ", h("span", {cls: "mono"}, shown(t.outcome.line)))),
    answerPanel(t),
    h("p", {cls: "lede"}, "Every phase from the request to the release, one row each, each from its record, under the band it belongs to. Check it against the code host: ",
      h("code", {}, "./go audit TICKET=" + t.id)),
    h("div", {cls: "panel"}, rows.length ? h("table", {}, h("tr", {}, ["Phase", "Verdict", "What", "At", "Record"].map((c) => h("th", {}, c))), body)
      : h("p", {cls: "muted"}, "This logbook has no timeline (written before 1.1).")),
    t.spec && (t.spec.criteria || []).length ? h("div", {cls: "panel"}, h("h2", {}, "The spec's criteria (" + shown(t.spec.by) + ")"),
      h("ol", {}, t.spec.criteria.map((c) => h("li", {}, c)))) : null,
    t.adrs !== undefined ? h("div", {cls: "panel"}, h("h2", {}, "ADRs that govern its perimeter"), adrLinks(t.adrs)) : null,
    gateExplorerPanel(t),
    patches.length ? h("div", {cls: "panel", id: "ticket-patches"},
      h("h2", {}, "PR Unified Patches"),
      patches.map((tk, idx) => h("details", {cls: "ticket-patch", open: idx === 0},
        h("summary", {}, "Attempt " + shown(tk.attempt) + (tk.worker ? " · " + shown(tk.worker) : "")),
        renderPatchPre(typeof tk.patch === "string" ? tk.patch : tk.patch.text)))) : null,
    h("h2", {}, "History"),
    h("div", {cls: "panel"}, h("table", {}, h("tr", {}, ["Step", "State", "Attempt", "At", "Error"].map((c) => h("th", {}, c))),
      t.history.map((e) => h("tr", {}, h("td", {}, e.step), h("td", {}, e.state), h("td", {}, shown(e.attempt)), h("td", {cls: "mono at"}, shown(e.at)), h("td", {cls: "mono"}, shown(e.error))))))];
}

// --- screen 3 ----------------------------------------------------------
function screenIncidents() {
  const out = [h("h1", {}, "Incidents and post mortems"),
    h("p", {cls: "lede"}, "What went wrong in production, the evidence behind each step, and the fix tickets it filed.")];
  if (unread(book.postmortems)) return out.concat(h("div", {cls: "panel"}, err(book.postmortems)));
  if (!book.postmortems.length) return out.concat(h("div", {cls: "panel"}, h("p", {cls: "muted"},
    "No post mortem recorded. ./go postmortem INCIDENT=<file> writes one; ./go demo-postmortem names the recording it needs.")));
  for (const pm of book.postmortems) {
    const rel = pm.release || {};
    out.push(h("div", {cls: "panel"},
      h("h2", {}, pm.name + ": " + pm.title),
      dl([["Window", shown(pm.window && pm.window.from) + " to " + shown(pm.window && pm.window.to)],
        ["Release", shown(rel.version) + " (" + shown(rel.verdict) + "), stable " + shown(rel.stable)],
        ["Chain", pm.chain ? h("span", {}, h("a", {href: "#ticket/" + encodeURIComponent(pm.chain.ticket)}, "ticket " + pm.chain.ticket), ", PR #" + pm.chain.pr + ", merge " + pm.chain.merge) : "not released by the tower"],
        ["Written by", (pm.run.replayed ? "replayed, recorded by " : "") + shown(pm.run.provider) + " " + shown(pm.run.model)]]),
      h("h2", {}, "Timeline"),
      h("ul", {}, pm.timeline.map((e) => h("li", {}, e.event, " ", h("code", {}, e.anchor), e.evidence.length ? h("pre", {}, e.evidence.join("\n")) : null))),
      h("h2", {}, "Contributing causes"),
      h("ul", {}, pm.causes.map((c) => h("li", {}, c.cause, h("div", {cls: "muted"}, c.evidence)))),
      h("h2", {}, "Action items"),
      h("table", {}, h("tr", {}, ["#", "Action", "Fix ticket", "Its state"].map((c) => h("th", {}, c))),
        pm.actions.map((a) => {
          const t = ticketsList().find((x) => x.id === a.ticket);
          return h("tr", {}, h("td", {}, a.n), h("td", {}, a.title),
            h("td", {}, t ? h("a", {href: "#ticket/" + encodeURIComponent(a.ticket)}, a.ticket) : a.ticket),
            h("td", {}, t ? chip(t.outcome.verdict) : "not opened yet: waiting for a human to declare the perimeter"));
        }))));
  }
  return out;
}

// --- screen 4 ----------------------------------------------------------
// The one press (docs/adr/0047 §3): GO and NO-GO only from the confirmation
// panel (docs/adr/0079 §4), with its reason; PREPARE moves nothing.
async function pressAnswer(answer, reason) {
  const L = book.launch, rec = L.record;
  busy = true; render(); if (lc) drawLaunch();
  try {
    const body = {answer, version: rec.version, held_at: rec.held_at, brief_sha256: L.brief ? L.brief.sha256 : null};
    if (reason) body.reason = reason;
    const r = await fetch("/v1/go-for-launch", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    const said = await r.json().catch(() => ({}));
    lastLine = said.line || ("refused (" + r.status + "): " + (said.error || r.statusText));
  } catch (e) { lastLine = "the console did not answer: " + e; }
  busy = false; await load(); render(); if (lc) drawLaunch();
}
// --- the human gate on the card (docs/adr/0079 §3-§4) -------------------------
// The held record no one has answered yet, when it can be pressed or prepared.
function launchable() {
  const L = book.launch || {}, rec = L.record && !unread(L.record) ? L.record : null;
  return rec && rec.verdict === "held" && !rec.promoted && !rec.rejected && (L.pressable || L.preparable) ? rec : null;
}
// The ticket that release carries: its card is the one that drags.
function launchTicket() {
  const rec = launchable();
  return rec ? ticketsList().find((t) => t.release && t.release.version === rec.version) || null : null;
}
function launchDrop() {
  const t = launchTicket();
  if (!t) return null;
  const target = (id, answer, text) => h("button", {type: "button", id, "data-answer": answer, "aria-haspopup": "dialog",
    title: text + ": drop the card of " + t.id + " here, or press Enter",
    ondragover: (e) => { e.preventDefault(); e.currentTarget.classList.add("over"); },
    ondragleave: (e) => e.currentTarget.classList.remove("over"),
    ondrop: (e) => { e.preventDefault(); e.currentTarget.classList.remove("over"); openLaunch(answer, e.currentTarget); },
    onclick: (e) => openLaunch(answer, e.currentTarget)}, text + " — " + launchable().version);
  return h("div", {id: "launch-drop", role: "group", "aria-label": "The human gate for " + named(t)},
    target("drop-promote", "GO", "Promote to 100%"), target("drop-rollback", "NO-GO", "Rollback"));
}
let lc = null;  // {answer, opener, reason} while the confirmation is open
function openLaunch(answer, opener) {
  lc = {answer, opener: opener || null, reason: ""};
  const d = document.getElementById("launch-confirm");
  drawLaunch();
  if (!d.open) d.showModal();
  const box = d.querySelector("#lc-reason");
  if (box) box.focus();
}
function closeLaunch() {
  const d = document.getElementById("launch-confirm");
  if (d && d.open) d.close();
  if (d) d.replaceChildren();
  const back = lc && lc.opener;
  lc = null;
  if (back && back.isConnected) back.focus();
}
function drawLaunch() {
  const d = document.getElementById("launch-confirm");
  const L = book.launch || {}, rec = L.record && !unread(L.record) ? L.record : {};
  const t = ticketsList().find((x) => x.release && x.release.version === rec.version) || null;
  const go = lc.answer === "GO";
  const verify = (Array.isArray(book.thresholds) ? book.thresholds : []).filter((x) => x.judge === "verify");
  const gates = t && t.ci && Array.isArray(t.ci.gates) ? t.ci.gates : null;
  const ready = () => con.can_launch && !busy && L.pressable && (go || lc.reason.trim() !== "");
  const confirmBtn = h("button", {type: "button", id: "lc-confirm", cls: go ? "go" : "nogo", disabled: !ready(),
    onclick: () => { if (ready()) pressAnswer(lc.answer, lc.reason.trim()); }}, go ? "Confirm: promote to 100%" : "Confirm: roll back");
  const box = h("textarea", {id: "lc-reason", placeholder: go ? "Why, if you want it on the record" : "Why the canary goes back: it is recorded with your name",
    "aria-required": go ? "false" : "true", oninput: (e) => { lc.reason = e.target.value; confirmBtn.disabled = !ready(); }});
  box.value = lc.reason;
  d.replaceChildren(...[
    h("div", {cls: "back-top"}, h("h2", {id: "lc-title", style: "flex: 1; margin: 0"}, (go ? "Promote " : "Roll back ") + shown(rec.version) + (t ? " — " + named(t) : "")),
      h("button", {type: "button", cls: "close", onclick: closeLaunch}, "Close (Esc)")),
    h("p", {cls: "muted"}, go ? "100% of traffic goes to the canary." : "The canary is rolled back.", " Nothing moves until you confirm."),
    h("h3", {}, "The brief"), h("div", {id: "lc-brief"}, L.brief
      ? [h("p", {cls: "muted"}, "By " + shown(L.brief.author) + " · sha256 ", h("span", {cls: "mono"}, L.brief.sha256)), h("pre", {}, L.brief.text)]
      : h("p", {cls: "muted"}, "No brief on disk yet: prepare it first.")),
    h("h3", {}, "Thresholds"), h("table", {id: "lc-thresholds"}, h("tr", {}, ["Key", "Value", "Provenance"].map((c) => h("th", {}, c))),
      verify.map((x) => h("tr", {}, h("td", {cls: "mono"}, x.key), h("td", {cls: "mono"}, x.value),
        h("td", {}, x.provenance ? x.provenance.kind + ": " + x.provenance.text : "undeclared")))),
    h("h3", {}, "Stages"), h("table", {id: "lc-stages"}, h("tr", {}, ["Stage", "Verdict", "Numbers"].map((c) => h("th", {}, c))),
      (rec.stages || []).map((st) => h("tr", {}, h("td", {}, st.pct + "%"), h("td", {cls: "mono"}, st.verdict), h("td", {cls: "mono"}, (st.numbers || []).join("\n"))))),
    h("h3", {}, "Risk"), h("p", {id: "lc-risk"}, t ? shown(t.ci && t.ci.risk_level) : "No ticket carries this release."),
    h("h3", {}, "The PR's gates"), h("ul", {id: "lc-gates"}, gates && gates.length ? gates.map((g) => h("li", {cls: "mono"}, g.line))
      : h("li", {cls: "muted"}, "No gate lines recorded.")),
    h("label", {for: "lc-reason"}, go ? "Reason (optional)" : "Reason (required for a rollback)"), box,
    h("div", {cls: "buttons"},
      L.preparable && !L.pressable ? h("button", {type: "button", id: "lc-prepare", disabled: busy || !con.can_launch,
        onclick: () => pressAnswer("PREPARE")}, "Prepare the brief") : null,
      confirmBtn),
    busy ? h("p", {cls: "muted"}, "core/promote.sh is running…") : null,
    lastLine ? h("pre", {id: "lc-line"}, lastLine) : null].filter(Boolean));
}
(function () {
  bindDialogLightDismiss(document.getElementById("launch-confirm"), closeLaunch);
})();
// --- the live canary (docs/adr/0080 §5) ------------------------------------
// The record's stages, one row each, with the numbers against the stable and
// the threshold; the stage in course with the time left before its
// judgement, against the console's read_at, never the browser's clock; and
// the automatic rollback with its reason. One point per stage: no chart.
const CANARY_QUERIES = ["error_rate", "latency_p95"];
function sloCell(st, q) {
  const m = (Array.isArray(st.slo) ? st.slo : []).find((x) => x.query === q);
  if (!m) return h("td", {cls: "muted"}, "—");
  const within = m.delta === null || m.max_delta === null ? null : m.delta <= m.max_delta;
  return h("td", {cls: "mono", "data-query": q, "data-within": within === null ? null : String(within)},
    "canary " + shown(m.canary) + " · stable " + shown(m.stable) + " · Δ " + shown(m.delta) + (m.max_delta === null ? "" : " / max " + m.max_delta));
}
function timeLeft(run) {
  const due = Date.parse(run.started_at) + 1000 * Number(run.judge_after_s);
  if (con && con.mode === "live" && con.read_at && !isNaN(due)) {
    const s = Math.max(0, Math.round((due - Date.parse(con.read_at)) / 1000));
    return "judged in about " + s + " s";
  }
  return "judged after " + run.judge_after_s + " s";
}
function canaryPanel(rec) {
  if (!rec || unread(rec)) return null;
  const stages = Array.isArray(rec.stages) ? rec.stages : [];
  const run = rec.verdict === "in-progress" && rec.running && typeof rec.running === "object" ? rec.running : null;
  const back = rec.verdict === "rolled-back";
  const decided = back ? stages.length - 1 : -1;
  const reason = back ? (rec.reason || (stages.length ? stages[stages.length - 1].verdict : null)) : null;
  return h("section", {id: "canary", "data-version": rec.version, "data-verdict": rec.verdict},
    h("h2", {}, "The canary, " + shown(rec.version) + " against stable " + shown(rec.stable)),
    h("div", {cls: "panel"},
      back ? h("p", {id: "canary-rollback", cls: "banner err"}, "Rolled back automatically: ", h("span", {cls: "mono"}, shown(reason))) : null,
      h("table", {}, h("tr", {}, ["Traffic", "Error rate", "Latency p95", "Started", "Judged", "Verdict"].map((c) => h("th", {}, c))),
        stages.map((st, i) => h("tr", {cls: "canary-stage", "data-pct": String(st.pct), "data-decided": i === decided ? "" : null},
          h("td", {}, st.pct + "%"), CANARY_QUERIES.map((q) => sloCell(st, q)),
          h("td", {cls: "mono at"}, shown(st.started_at)), h("td", {cls: "mono at"}, shown(st.judged_at)),
          h("td", {cls: "mono"}, String(st.verdict || "").startsWith("GO") ? chip("GO") : chip("NO-GO"), " ", shown(st.verdict)))),
        run ? h("tr", {cls: "canary-stage", "data-pct": String(run.pct), "data-running": ""},
          h("td", {}, run.pct + "%"), h("td", {colspan: "2", cls: "muted"}, "watching: verify reads the numbers at the end of the stage"),
          h("td", {cls: "mono at"}, shown(run.started_at)), h("td", {cls: "canary-left"}, timeLeft(run)), h("td", {}, chip("pending"))) : null),
      rec.verdict === "in-progress" && !run ? h("p", {cls: "muted"}, "Staging: deploying the canary before any traffic.") : null));
}
function svgEl(tag, attrs, ...kids) {
  const NS = "http://www.w3.org/2000/svg";
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    el.setAttribute(k === "cls" ? "class" : k, String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function sloDeltaChart(rec) {
  const stages = Array.isArray(rec && rec.stages) ? rec.stages : [];
  if (!stages.length) return h("p", {cls: "muted"}, "No canary stages verified yet.");
  const metrics = ["error_rate", "latency_p95"];
  const cards = metrics.map((metric) => {
    const pts = stages.map((st) => {
      const m = (Array.isArray(st.slo) ? st.slo : []).find((x) => x.query === metric);
      return {pct: st.pct, delta: m && typeof m.delta === "number" ? m.delta : null, max: m && typeof m.max_delta === "number" ? m.max_delta : null};
    }).filter((p) => p.delta !== null);
    if (!pts.length) return h("div", {cls: "chart-card"}, h("h3", {}, metric), h("p", {cls: "muted"}, "No samples."));
    const W = 280, H = 110, padL = 32, padB = 22, padT = 14, padR = 10;
    const maxVal = Math.max(0.0001, ...pts.map((p) => Math.max(Math.abs(p.delta), p.max || 0)));
    const bw = Math.max(18, Math.floor((W - padL - padR) / Math.max(1, pts.length) * 0.6));
    const stepX = (W - padL - padR) / Math.max(1, pts.length);
    const bars = pts.map((p, idx) => {
      const x = padL + idx * stepX + (stepX - bw) / 2;
      const ratio = Math.min(1, Math.max(0, Math.abs(p.delta) / maxVal));
      const bh = Math.max(3, ratio * (H - padT - padB));
      const y = H - padB - bh;
      const ok = p.max === null || p.delta <= p.max;
      return [svgEl("rect", {x: x.toFixed(1), y: y.toFixed(1), width: bw, height: bh.toFixed(1), rx: 3,
          fill: ok ? "var(--go)" : "var(--nogo)"}),
        svgEl("text", {x: (x + bw / 2).toFixed(1), y: H - 6, "text-anchor": "middle"}, p.pct + "%")];
    });
    const maxLimit = pts[0].max;
    const limitY = maxLimit !== null ? H - padB - Math.min(1, maxLimit / maxVal) * (H - padT - padB) : null;
    const svg = svgEl("svg", {cls: "slo-chart", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Canary " + metric + " delta by stage"},
      svgEl("line", {x1: padL, y1: H - padB, x2: W - padR, y2: H - padB, stroke: "var(--line)"}),
      limitY !== null ? svgEl("line", {x1: padL, y1: limitY.toFixed(1), x2: W - padR, y2: limitY.toFixed(1), stroke: "var(--wait)", "stroke-dasharray": "4 3"}) : null,
      ...bars);
    return h("div", {cls: "chart-card"},
      h("h3", {style: "margin:0 0 6px"}, metric + " delta"),
      svg,
      h("p", {cls: "mono muted", style: "margin:4px 0 0"}, pts.map((p) => p.pct + "%: " + p.delta).join(" · ") + (maxLimit !== null ? " (max " + maxLimit + ")" : "")));
  });
  return h("div", {cls: "charts-grid"}, cards);
}
function timeSeriesSvg(series) {
  const W = 420, H = 140, padL = 36, padR = 12, padT = 14, padB = 24;
  const allPts = series.flatMap((s) => (Array.isArray(s.points) ? s.points : []).map((pt) => ({t: Number(pt[0]), v: Number(pt[1])}))).filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v));
  if (!allPts.length) return h("p", {cls: "muted"}, "No time-series data points returned.");
  const minT = Math.min(...allPts.map((p) => p.t)), maxT = Math.max(...allPts.map((p) => p.t));
  const minV = Math.min(0, ...allPts.map((p) => p.v)), maxV = Math.max(minV + 0.0001, ...allPts.map((p) => p.v));
  const spanT = Math.max(1, maxT - minT), spanV = Math.max(0.0001, maxV - minV);
  const colors = ["var(--accent)", "var(--go)", "var(--wait)", "var(--nogo)"];
  const paths = series.slice(0, 8).map((s, idx) => {
    const pts = (Array.isArray(s.points) ? s.points : []).map((pt) => {
      const x = padL + ((Number(pt[0]) - minT) / spanT) * (W - padL - padR);
      const y = H - padB - ((Number(pt[1]) - minV) / spanV) * (H - padT - padB);
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    return pts.length ? svgEl("polyline", {fill: "none", stroke: colors[idx % colors.length], "stroke-width": 2, points: pts.join(" ")}) : null;
  }).filter(Boolean);
  const svg = svgEl("svg", {cls: "ts-chart", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Live CHARTS time-series"},
    svgEl("line", {x1: padL, y1: H - padB, x2: W - padR, y2: H - padB, stroke: "var(--line)"}),
    svgEl("line", {x1: padL, y1: padT, x2: padL, y2: H - padB, stroke: "var(--line)"}),
    svgEl("text", {x: 4, y: padT + 4}, maxV.toFixed(2)),
    svgEl("text", {x: 4, y: H - padB}, minV.toFixed(2)),
    ...paths);
  return h("div", {}, svg, h("ul", {cls: "muted", style: "margin:6px 0 0;padding-left:18px"},
    series.slice(0, 8).map((s) => {
      const pts = Array.isArray(s.points) ? s.points : [];
      const last = pts.length ? pts[pts.length - 1][1] : null;
      return h("li", {cls: "mono"}, shown(s.name) + ": " + (last === null ? "—" : String(last)) + " (" + pts.length + " pts)");
    })));
}
function liveChartsPanel(rec) {
  const isLive = con && con.mode === "live";
  let liveBody;
  if (!isLive) liveBody = h("p", {cls: "muted"}, "Snapshot mode: no live charts.");
  else if (liveChartsLoading && !liveCharts) liveBody = h("p", {cls: "muted"}, "Loading live time-series from CHARTS port…");
  else if (!liveCharts) liveBody = h("p", {cls: "muted"}, "Click Refresh charts to query /v1/charts.json.");
  else if (!liveCharts.ok) liveBody = h("p", {cls: "muted mono"}, shown(liveCharts.error));
  else liveBody = timeSeriesSvg(Array.isArray(liveCharts.series) ? liveCharts.series : []);
  return h("section", {id: "canary-charts", cls: "panel"},
    h("h2", {}, "Canary SLO & Live Time-Series Charts"),
    sloDeltaChart(rec),
    h("div", {cls: "chart-card", style: "margin-top:12px"},
      h("div", {cls: "ops-actions", style: "justify-content:space-between;margin-bottom:6px"},
        h("h3", {style: "margin:0"}, "CHARTS Port Series"),
        isLive ? h("button", {type: "button", id: "charts-refresh-btn", onclick: () => fetchLiveCharts(true)}, liveChartsLoading ? "Refreshing…" : "Refresh charts") : null),
      liveBody));
}
function screenLaunch() {
  const L = book.launch, rec = L.record;
  const out = [h("h1", {}, "Go-for-launch"),
    h("p", {cls: "lede"}, "The one human decision before 100% of traffic. The same check and the same script as ./go promote."),
    canaryPanel(rec),
    liveChartsPanel(rec)];
  if (rec && !unread(rec)) {
    // The ticket this release carries (docs/adr/0063 §3): its release.version is the record's.
    const carried = ticketsList().find((x) => x.release && x.release.version && x.release.version === rec.version);
    out.push(h("div", {cls: "panel", id: "launch-ticket"}, carried
      ? h("p", {style: "margin: 0"}, "This release carries ", h("a", {href: "#ticket/" + encodeURIComponent(carried.id)}, named(carried)),
        h("br"), nowLine(carried))
      : h("p", {cls: "muted", style: "margin: 0"}, "No ticket in the logbook carries " + shown(rec.version) + ": a release made without the tower (./go release).")));
  }
  if (!rec || unread(rec)) out.push(h("div", {cls: "panel"}, rec ? err(rec) : h("p", {cls: "muted"}, "No release record yet.")));
  else {
    const answered = rec.promoted ? ["GO", rec.promoted] : rec.rejected ? ["NO-GO", rec.rejected] : null;
    out.push(h("div", {cls: "panel"},
      dl([["Version", rec.version], ["Stable", rec.stable], ["Image", h("span", {cls: "mono"}, shown(rec.image))],
        ["Verdict", rec.verdict], ["Held at", rec.held_at === null ? null : rec.held_at + "%"],
        answered ? ["Answered", h("span", {}, chip(answered[0]), " by " + shown(answered[1].by) + " via " + shown(answered[1].via) + " at " + shown(answered[1].at))] : null]),
      (rec.stages || []).length ? h("table", {}, h("tr", {}, h("th", {}, "Stage"), h("th", {}, "Verdict"), h("th", {}, "Numbers")),
        rec.stages.map((st) => h("tr", {}, h("td", {}, st.pct + "%"), h("td", {cls: "mono"}, st.verdict), h("td", {cls: "mono"}, st.numbers.join("\n"))))) : null));
  }
  const p = L.plane;
  out.push(h("h2", {}, "What the plane runs now"), h("div", {cls: "panel"}, unread(p) ? err(p) : dl([["Stable", p.stable], ["Canary", p.canary], ["Canary weight", p.weight === null ? null : p.weight + "%"]])));
  out.push(h("h2", {}, "The brief"), h("div", {cls: "panel"}, L.brief
    ? h("div", {}, h("p", {cls: "muted"}, "By " + shown(L.brief.author) + " · sha256 ", h("span", {cls: "mono"}, L.brief.sha256)), h("pre", {}, L.brief.text))
    : h("p", {cls: "muted"}, "No brief on disk.")));
  const reasons = [];
  if (!con.can_launch && con.reason) reasons.push(con.reason);
  if (L.disabled_reason) reasons.push(L.disabled_reason);
  const can = con.can_launch && !busy;
  out.push(h("h2", {}, "Answer"), h("div", {cls: "panel"},
    reasons.length ? h("p", {}, "Disabled: ", reasons.join("; ")) : h("p", {}, "Pressing answers the brief above, as " + shown(con.identity === "loopback" ? "unauthenticated (loopback)" : con.identity) + "."),
    h("div", {cls: "buttons"},
      L.preparable ? h("button", {onclick: () => pressAnswer("PREPARE"), disabled: !can}, "Prepare the brief") : null,
      h("button", {cls: "go", id: "launch-go", onclick: (e) => openLaunch("GO", e.currentTarget), disabled: !(can && L.pressable)}, "GO — promote to 100%"),
      h("button", {cls: "nogo", id: "launch-nogo", onclick: (e) => openLaunch("NO-GO", e.currentTarget), disabled: !(can && L.pressable)}, "NO-GO — roll back")),
    busy ? h("p", {cls: "muted"}, "core/promote.sh is running…") : null,
    lastLine ? h("pre", {}, lastLine) : null));
  out.push(h("h2", {}, "Workstation Controls"), h("div", {cls: "panel", id: "launch-ops"}, opsBar("launch")));
  return out;
}

// --- screen 5 ----------------------------------------------------------
function screenThresholds() {
  const out = [h("h1", {}, "Thresholds"),
    h("p", {cls: "lede"}, "Every number that can stop a change, where it is set, and why it has that value.")];
  if (unread(book.thresholds)) out.push(h("div", {cls: "panel"}, err(book.thresholds)));
  else out.push(h("div", {cls: "panel", tabindex: "0", role: "region", "aria-label": "Thresholds and provenance table"}, h("table", {},
    h("tr", {}, ["Key", "Value", "Judges", "Set in", "Provenance"].map((c) => h("th", {}, c))),
    book.thresholds.map((t) => h("tr", {"data-overridden": t.overridden ? "true" : null},
      h("td", {cls: "mono"}, t.key), h("td", {cls: "mono"}, t.value), h("td", {}, t.judge),
      h("td", {cls: "mono"}, t.file + (t.overridden ? " (overrides the platform)" : ""),
        t.overridden ? [" ", h("span", {cls: "chip pending"}, "override")] : null),
      h("td", {}, t.provenance ? h("span", {}, h("b", {}, t.provenance.kind + ": "), t.provenance.text) : h("span", {cls: "chip NO-GO"}, "undeclared")))))));
  // What the contract gives the agents (docs/adr/0073 §10): the lines `./go
  // agents` prints, verbatim; a logbook before 1.3 has none to show.
  if (book.agents !== undefined) out.push(h("h2", {}, "Agents"), h("div", {cls: "panel", id: "agents"},
    unread(book.agents) ? err(book.agents) : h("pre", {}, (book.agents.lines || []).join("\n"))));
  return out;
}


// --- the specs (docs/adr/0059 §4) ----------------------------------------
function screenSpecs() {
  const out = [h("h1", {}, "Specs"),
    h("p", {cls: "lede"}, "Every ticket's spec: the criteria confirmed and by whom, the spec gate's last verdict, and the question still open. ",
      h("a", {href: "#request"}, "Write a new request →"))];
  if (unread(book.tickets)) return out.concat(h("div", {cls: "panel"}, err(book.tickets)));
  const order = Array.isArray(book.kanban) ? book.kanban.flatMap((c) => c.tickets) : ticketsList().map((t) => t.id);
  const byId = Object.fromEntries(ticketsList().map((t) => [t.id, t]));
  const all = order.map((id) => byId[id]).filter(Boolean);
  if (!all.length) return out.concat(h("div", {cls: "panel"}, h("p", {cls: "muted"}, "No ticket in the tower yet. ", h("a", {href: "#request"}, "Write a new request →"))));
  for (const t of all) {
    const spec = t.spec || {};
    const q = openQuestion(t);
    out.push(h("div", {cls: "panel spec", "data-id": t.id},
      h("h2", {style: "margin-top: 0"}, h("a", {href: "#ticket/" + encodeURIComponent(t.id)}, named(t)), " ", h("span", {cls: "chip none"}, t.state)),
      (spec.criteria || []).length ? h("div", {}, h("div", {cls: "muted"}, "Criteria, confirmed by " + shown(spec.by)),
        h("ol", {}, spec.criteria.map((c) => h("li", {}, c)))) : h("p", {cls: "muted"}, "No criteria confirmed yet."),
      h("p", {}, "Spec gate: ", spec.verdict ? chip(spec.verdict) : h("span", {cls: "chip none"}, "not run"), " ",
        h("span", {cls: "mono"}, shown(spec.line)), spec.at ? h("span", {cls: "muted"}, " at " + spec.at) : null),
      q ? h("p", {}, h("b", {}, q.from === "spec" ? "Open, from the spec gate: " : "Open, from the product role: "), q.question,
        " ", h("a", {href: "#ticket/" + encodeURIComponent(t.id)}, "answer it →")) : null));
  }
  return out;
}

// --- the request editor (docs/adr/0059 §4) --------------------------------
// The fields live here, not in the DOM, so a redraw keeps them. The check
// is /v1/spec-check: gates/spec.sh itself on the draft, which writes
// nothing; its line is shown as the gate printed it.
const draftFields = {title: "", context: "", criteria: [{given: "", when: "", then: ""}], perimeter: "", feature: "", priority: ""};
let checkTimer = null, checkSeq = 0, lastCheck = null, saveLine = null;
// Provenance, decision: docs/adr/0059 §4, one check per pause, not per key.
const CHECK_DELAY_MS = 300;
function draftBody() {
  return {title: draftFields.title, context: draftFields.context,
    criteria: draftFields.criteria.filter((c) => c.given.trim() || c.when.trim() || c.then.trim()),
    perimeter: draftFields.perimeter.split("\n").map((l) => l.trim()).filter(Boolean),
    feature: draftFields.feature.trim(), priority: draftFields.priority};
}
function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(runCheck, CHECK_DELAY_MS);
}
async function runCheck() {
  const seq = ++checkSeq;
  let result;
  if (!draftFields.title.trim()) result = {error: "a draft needs a title before the spec gate can read it"};
  else {
    try {
      const r = await fetch("/v1/spec-check", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(draftBody())});
      const body = await r.json().catch(() => ({}));
      result = r.ok ? body : {error: "refused (" + r.status + "): " + (body.error || r.statusText)};
    } catch (e) { result = {error: "the console did not answer: " + e}; }
  }
  if (seq !== checkSeq) return;  // a later keystroke's check is on its way
  lastCheck = result;
  showCheck();
}
function showCheck() {
  const line = document.getElementById("spec-line"), chipEl = document.getElementById("spec-chip"),
    pre = document.getElementById("spec-preview"), save = document.getElementById("save");
  if (!line) return;
  const r = lastCheck;
  line.textContent = r ? (r.line || r.error) : "—";
  const go = !!(r && r.line && r.line.startsWith("GO: spec"));
  chipEl.replaceChildren(r && r.line ? chip(go ? "GO" : "NO-GO") : h("span", {cls: "chip none"}, "no line"));
  pre.textContent = r && r.markdown ? r.markdown : "";
  if (save) save.disabled = !canSave() || !go || busy;
}
function canSave() { return con && con.mode !== "snapshot" && con.auth !== "public"; }
async function pressRequest() {
  busy = true; showCheck();
  try {
    const r = await fetch("/v1/request", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(draftBody())});
    const body = await r.json().catch(() => ({}));
    saveLine = r.ok ? {ticket: body.ticket, line: body.line} : {line: "refused (" + r.status + "): " + (body.error || r.statusText) + (body.line ? "\n" + body.line : "")};
  } catch (e) { saveLine = {line: "the console did not answer: " + e}; }
  busy = false;
  // A ticket file was written (docs/adr/0063 §5): the draft is done, so it is
  // cleared and Request waits for the next edit; a second press cannot file
  // the same text twice.
  if (saveLine.ticket) {
    Object.assign(draftFields, {title: "", context: "", criteria: [{given: "", when: "", then: ""}], perimeter: "", feature: "", priority: ""});
    lastCheck = null; clearTimeout(checkTimer); checkSeq++;
  }
  await load();
  render();
}
function field(label, el) { return [h("label", {}, label), el]; }
// "Describe the need" (docs/adr/0090 §8): a client's words start an intake.
const need = {text: ""};
let needLine = null;
async function pressIntake() {
  if (!need.text.trim()) return;
  busy = true; render();
  try {
    const r = await fetch("/v1/intake", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text: need.text})});
    const body = await r.json().catch(() => ({}));
    needLine = body.line || ("refused (" + r.status + "): " + (body.error || r.statusText));
    if (r.ok && String(body.line || "").startsWith("GO")) need.text = "";
  } catch (e) { needLine = "the console did not answer: " + e; }
  busy = false; await load(); render();
}
function needPanel() {
  const m = /intake — (\d{4})/.exec(String(needLine || ""));
  return h("div", {cls: "panel editor", id: "need"}, h("h2", {}, "Describe the need"),
    h("p", {cls: "muted"}, "No ticket yet? Write what is needed in your own words. The product role interviews you, one question at a time, then proposes tickets; the architect role proposes where each may change the code; you confirm, edit or drop each one (./go intake)."),
    h("label", {for: "need-text"}, "The need, in your words"),
    h("textarea", {id: "need-text", name: "need", oninput: (e) => { need.text = e.target.value; }}, need.text),
    h("div", {cls: "buttons"}, h("button", {cls: "go", id: "start-intake", disabled: busy || !canSave(),
      onclick: pressIntake}, "Start the interview — ./go intake")),
    canSave() ? null : h("p", {cls: "muted"}, con.mode === "snapshot" ? "Snapshot: no tower behind it, no intake can start." : "This console cannot start an intake."),
    needLine ? h("pre", {id: "need-line"}, needLine) : null,
    m ? h("p", {}, h("a", {href: "#intake/" + m[1]}, "Intake " + m[1] + " →")) : null);
}
function screenRequest() {
  const level = (book.source || {}).level || "";
  const set = (k) => (e) => { draftFields[k] = e.target.value; scheduleCheck(); };
  const rows = draftFields.criteria.map((c, i) => h("div", {cls: "criterion"},
    h("span", {cls: "muted"}, "Given"), h("input", {type: "text", name: "given", value: c.given, placeholder: "the state before",
      oninput: (e) => { c.given = e.target.value; scheduleCheck(); }}),
    h("span", {cls: "muted"}, "When"), h("input", {type: "text", name: "when", value: c.when, placeholder: "the action",
      oninput: (e) => { c.when = e.target.value; scheduleCheck(); }}),
    h("span", {cls: "muted"}, "Then"), h("input", {type: "text", name: "then", value: c.then, placeholder: "what is observed: a status, a value, a line",
      oninput: (e) => { c.then = e.target.value; scheduleCheck(); }}),
    h("span", {}), h("div", {}, draftFields.criteria.length > 1 ? h("button", {onclick: () => { draftFields.criteria.splice(i, 1); render(); scheduleCheck(); }}, "Remove this criterion") : null)));
  const prio = h("select", {name: "priority", onchange: set("priority")},
    [["", "unprioritised"], ["P0", "P0"], ["P1", "P1"], ["P2", "P2"], ["P3", "P3"]].map(([v, t]) => h("option", {value: v, selected: v === draftFields.priority}, t)));
  const out = [h("h1", {}, "New request"), needPanel(),
    h("p", {cls: "lede"}, "A ticket's request, checked as you type by the spec gate itself (gates/spec.sh, through the console, writing nothing). Saving writes the ticket file and runs ./go request."),
    level === "l0"
      ? h("div", {cls: "banner warn", id: "l0-note"}, "Level l0: the product role replays recordings, and a new request has none. It will not be consulted on this draft; the spec gate alone judges it (ADR 0055 §3). With a key (l1) saving consults it, and its questions come back on the ticket's page.")
      : h("div", {cls: "banner"}, "Saving requests the ticket: the product role reads it and asks or confirms, and its questions come back on the ticket's page with an answer box."),
    saveLine && saveLine.ticket ? h("div", {cls: "panel", id: "saved"}, h("p", {style: "margin: 0"},
      chip(String(saveLine.line || "").startsWith("GO") ? "GO" : "NO-GO"), " ", h("span", {cls: "mono"}, shown(saveLine.line)), h("br"),
      "tickets/" + saveLine.ticket + ".md is written, not committed. ",
      h("a", {id: "new-ticket", href: "#ticket/" + encodeURIComponent(saveLine.ticket)}, "Ticket " + saveLine.ticket + "'s timeline →"),
      " The form below is empty for the next request.")) : null,
    h("div", {cls: "panel editor"},
      field("Title", h("input", {type: "text", name: "title", value: draftFields.title, oninput: set("title")})),
      field("Context: the problem, in a few sentences", h("textarea", {name: "context", oninput: set("context")}, draftFields.context)),
      h("label", {}, "Acceptance criteria: each a Given, a When and a Then with an observable outcome"), rows,
      h("button", {id: "add-criterion", onclick: () => { draftFields.criteria.push({given: "", when: "", then: ""}); render(); }}, "Add a criterion"),
      field("Perimeter: one path or glob per line, the files the change may touch", h("textarea", {name: "perimeter", oninput: set("perimeter")}, draftFields.perimeter)),
      field("Feature (optional): lowercase letters and digits joined by hyphens", h("input", {type: "text", name: "feature", value: draftFields.feature, oninput: set("feature")})),
      field("Priority (optional)", prio)),
    h("div", {cls: "savebar", id: "savebar"}, h("span", {id: "spec-chip"}), h("span", {cls: "mono", id: "spec-line"}, "—"),
      h("div", {cls: "buttons"}, h("button", {cls: "go", id: "save", disabled: true, onclick: pressRequest}, "Request it — ./go request"))),
    h("h2", {}, "The ticket as it will be written"),
    h("div", {cls: "panel"}, h("pre", {id: "spec-preview"}),
      canSave() ? h("p", {}, "Saving writes this file as it is shown, under the next free id, uncommitted; commit it with the change or before. Only a draft the gate passes is saved.")
        : h("p", {cls: "muted"}, con.mode === "snapshot" ? "Snapshot: no tower behind it, nothing can be requested. The check still runs." : "This console cannot request."),
      busy ? h("p", {cls: "muted"}, "./go request is running: the product role, then the spec gate…") : null,
      saveLine && !saveLine.ticket ? h("pre", {}, saveLine.line) : null)];
  return out;
}

// --- Epics & Dependency DAG (docs/adr/0125) -------------------------------
function buildDagModel() {
  const all = ticketsList();
  const byId = Object.fromEntries(all.map((t) => [t.id, t]));
  const edges = [];
  const depsOf = {};
  const blocksOf = {};
  for (const t of all) {
    const rawDeps = [];
    if (Array.isArray(t.depends_on)) rawDeps.push(...t.depends_on);
    if (t.wait && Array.isArray(t.wait.holders)) rawDeps.push(...t.wait.holders);
    const deps = [...new Set(rawDeps.filter((d) => typeof d === "string" && d && d !== t.id && byId[d]))];
    depsOf[t.id] = deps;
    for (const d of deps) {
      edges.push([d, t.id]);
      (blocksOf[d] = blocksOf[d] || []).push(t.id);
    }
  }
  const memo = {};
  function depthOf(id, seen) {
    if (id in memo) return memo[id];
    if (seen.has(id)) return 0;
    const nextSeen = new Set(seen); nextSeen.add(id);
    let maxD = 0;
    for (const d of (depsOf[id] || [])) maxD = Math.max(maxD, depthOf(d, nextSeen) + 1);
    memo[id] = maxD;
    return maxD;
  }
  for (const t of all) depthOf(t.id, new Set());
  return {tickets: all, byId: byId, edges: edges, depsOf: depsOf, blocksOf: blocksOf, depth: memo};
}
function screenEpics() {
  const out = [h("h1", {}, "Epics & Dependency DAG"),
    h("p", {cls: "lede"}, "Epic cohorts and depends_on / wait-holder dependency edges across tickets. Works seamlessly across historical and multi-epic logbooks.")];
  if (unread(book.tickets)) return out.concat(h("div", {cls: "panel"}, err(book.tickets)));
  const model = buildDagModel();
  if (!model.tickets.length) return out.concat(h("div", {cls: "panel"}, h("p", {cls: "muted"}, "No tickets in this logbook yet. ", h("a", {href: "#request"}, "Describe a need →"), " or configure epics with lights-out ticket epics.")));
  const epics = Array.isArray(book.epics) && book.epics.length ? book.epics : null;
  if (epics) {
    out.push(h("div", {cls: "panel", id: "epics-summary"},
      h("h2", {}, "Epics"),
      h("table", {},
        h("tr", {}, ["Epic", "Title", "State", "Tickets"].map((c) => h("th", {}, c))),
        epics.map((ep) => h("tr", {},
          h("td", {cls: "mono"}, shown(ep.id)),
          h("td", {}, shown(ep.title)),
          h("td", {}, chip(shown(ep.state))),
          h("td", {}, Array.isArray(ep.tickets) ? ep.tickets.map((id, i) => [i ? ", " : null, h("a", {href: "#ticket/" + encodeURIComponent(id)}, id)]) : "—"))))));
  }
  const byCol = {};
  if (model.edges.length) {
    for (const t of model.tickets) (byCol[model.depth[t.id] || 0] = byCol[model.depth[t.id] || 0] || []).push(t);
  } else {
    const groups = [...new Set(model.tickets.map((t) => String(t.epic || t.feature || "default")))];
    for (const t of model.tickets) {
      const col = groups.indexOf(String(t.epic || t.feature || "default"));
      (byCol[col] = byCol[col] || []).push(t);
    }
  }
  const cols = Object.keys(byCol).map(Number).sort((a, b) => a - b);
  const maxRows = Math.max(1, ...cols.map((c) => byCol[c].length));
  const nodeW = 190, nodeH = 58, gapX = 56, gapY = 20, pad = 20;
  const W = Math.max(540, pad * 2 + cols.length * nodeW + Math.max(0, cols.length - 1) * gapX);
  const H = Math.max(140, pad * 2 + maxRows * nodeH + Math.max(0, maxRows - 1) * gapY);
  const pos = {};
  cols.forEach((c, ci) => {
    byCol[c].forEach((t, ri) => {
      pos[t.id] = {x: pad + ci * (nodeW + gapX), y: pad + ri * (nodeH + gapY)};
    });
  });
  const marker = svgEl("defs", {},
    svgEl("marker", {id: "dag-arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse"},
      svgEl("path", {d: "M 0 1 L 10 5 L 0 9 z", fill: "var(--muted)"})));
  const edgeEls = model.edges.map(([fromId, toId]) => {
    const a = pos[fromId], b = pos[toId];
    if (!a || !b) return null;
    const x1 = a.x + nodeW, y1 = a.y + nodeH / 2, x2 = b.x, y2 = b.y + nodeH / 2;
    const mx = (x1 + x2) / 2;
    return svgEl("path", {cls: "dag-edge", d: "M" + x1 + " " + y1 + " C" + mx + " " + y1 + " " + mx + " " + y2 + " " + x2 + " " + y2,
      "marker-end": "url(#dag-arrow)"});
  }).filter(Boolean);
  const nodeEls = model.tickets.map((t) => {
    const p = pos[t.id], w = now(t), v = (t.outcome && t.outcome.verdict) || "";
    const label = named(t).slice(0, 24);
    const link = svgEl("a", {href: "#ticket/" + encodeURIComponent(t.id)});
    const grp = svgEl("g", {cls: "dag-node", "data-ticket": t.id, "data-turn": w.turn, "data-verdict": v},
      svgEl("rect", {x: p.x, y: p.y, width: nodeW, height: nodeH, rx: 8}),
      svgEl("text", {x: p.x + 10, y: p.y + 22}, label),
      svgEl("text", {cls: "dag-sub", x: p.x + 10, y: p.y + 42}, t.state + (t.epic || t.feature ? " · " + (t.epic || t.feature) : "")));
    link.append(grp);
    return link;
  });
  const svg = svgEl("svg", {id: "epics-dag", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Ticket dependency graph"}, marker, ...edgeEls, ...nodeEls);
  out.push(h("div", {cls: "panel dag-wrap"}, h("h2", {}, "Dependency Graph"), svg));
  out.push(h("div", {cls: "panel", tabindex: "0", role: "region", "aria-label": "Ticket dependency matrix"},
    h("h2", {}, "Dependency Matrix"),
    h("table", {id: "epics-matrix"},
      h("tr", {}, ["Ticket", "Epic / Feature", "State", "Depends on", "Blocks"].map((c) => h("th", {}, c))),
      model.tickets.map((t) => h("tr", {},
        h("td", {}, h("a", {href: "#ticket/" + encodeURIComponent(t.id)}, named(t))),
        h("td", {}, shown(t.epic || t.feature)),
        h("td", {}, t.state),
        h("td", {cls: "mono"}, (model.depsOf[t.id] || []).length ? model.depsOf[t.id].map((id, i) => [i ? ", " : null, h("a", {href: "#ticket/" + encodeURIComponent(id)}, id)]) : "—"),
        h("td", {cls: "mono"}, (model.blocksOf[t.id] || []).length ? model.blocksOf[t.id].map((id, i) => [i ? ", " : null, h("a", {href: "#ticket/" + encodeURIComponent(id)}, id)]) : "—"))))));
  return out;
}

// --- ADR search (docs/adr/0059 §3 & docs/adr/0125) -------------------------
// The ADRs are the logbook's, read from the project map: the page searches
// and links them, it computes no relation of its own.
function adrItems() { return book.adrs && Array.isArray(book.adrs.items) ? book.adrs.items : []; }
function adrSearch(q, filter) {
  const text = q.trim();
  const base = !text ? adrItems() : (() => {
    const num = text.match(/^(?:adr\s*)?0*(\d{1,4})$/i);
    const want = num ? num[1].padStart(4, "0") : null;
    const words = text.toLowerCase().split(/\s+/);
    const hits = [];
    for (const a of adrItems()) {
      const hay = (a.number + " " + a.title + " " + a.status + " " + a.text).toLowerCase();
      const byNumber = a.number === want;
      const byFile = a.files.some((f) => f === text || f.endsWith("/" + text) || (text.includes("/") && f.startsWith(text.replace(/\/$/, "") + "/")));
      const byTitle = words.every((w) => a.title.toLowerCase().includes(w));
      if (byNumber || byFile || words.every((w) => hay.includes(w)))
        hits.push([byNumber ? 0 : byFile ? 1 : byTitle ? 2 : 3, a]);
    }
    return hits.sort((x, y) => x[0] - y[0] || x[1].number.localeCompare(y[1].number)).map(([, a]) => a);
  })();
  if (!filter || filter === "all") return base;
  if (filter === "with-files") return base.filter((a) => Array.isArray(a.files) && a.files.length > 0);
  if (filter === "with-tickets") return base.filter((a) => Array.isArray(a.tickets) && a.tickets.length > 0);
  return base.filter((a) => String(a.status || "").toLowerCase().includes(filter));
}
function snippet(a, q) {
  const w = q.trim().toLowerCase().split(/\s+/)[0];
  const at = w ? a.text.toLowerCase().indexOf(w) : -1;
  if (at < 0) return null;
  const from = Math.max(0, at - 80);
  return (from ? "…" : "") + a.text.slice(from, at + 120).replace(/\s+/g, " ") + "…";
}
function adrRow(a, q) {
  const tickets = Object.fromEntries(ticketsList().map((t) => [t.id, t]));
  return h("div", {cls: "adr", "data-number": a.number},
    h("div", {}, h("a", {href: "#adr/" + a.number}, h("b", {}, a.title))),
    h("div", {cls: "muted"}, "Status: " + (a.status || "none").slice(0, 160)),
    q ? h("div", {cls: "links"}, snippet(a, q)) : null,
    h("div", {cls: "links"}, "Files: ", a.files.length ? a.files.slice(0, 12).map((f) => h("code", {}, f)) : "none",
      a.files.length > 12 ? " and " + (a.files.length - 12) + " more" : ""),
    h("div", {cls: "links"}, "Tickets: ", a.tickets.length ? a.tickets.map((id) => [tickets[id] ? h("a", {href: "#ticket/" + id}, id) : id, " "]) : "none",
      " · ADRs: ", a.adrs.length ? a.adrs.map((n) => [h("a", {href: "#adr/" + n}, n), " "]) : "none"));
}
function drawAdrs() {
  const area = document.getElementById("adr-results");
  if (!area) return;
  const q = route().query.q || "";
  const f = route().query.f || "all";
  const found = adrSearch(q, f);
  area.replaceChildren(h("p", {cls: "muted"}, found.length + " of " + adrItems().length + " ADRs"), ...found.map((a) => adrRow(a, q)));
}
function screenAdrs() {
  const out = [h("h1", {}, "ADRs"),
    h("p", {cls: "lede"}, "The project's decisions, from the project map (./go map): search by number, a word of the title, the status, the text, or a file one cites.")];
  if (unread(book.adrs)) return out.concat(h("div", {cls: "panel"}, err(book.adrs)));
  if (!book.adrs) return out.concat(h("div", {cls: "panel"}, h("p", {cls: "muted"}, "This logbook has no adrs (written before 1.2).")));
  const q = route().query.q || "";
  const f = route().query.f || "all";
  const filters = [["all", "All"], ["accepted", "Accepted"], ["proposed", "Proposed"], ["with-files", "With files"], ["with-tickets", "With tickets"]];
  out.push(h("div", {cls: "controls"},
    h("input", {type: "search", id: "adr-q", value: q, placeholder: "0055, kanban, proposed, core/spec.py", "aria-label": "Search the ADRs",
      oninput: (e) => { history.replaceState(null, "", viewHash("adrs", {q: e.target.value, f: route().query.f || ""}, ["q", "f"])); drawAdrs(); }}),
    h("span", {}, "map built at ", h("span", {cls: "mono"}, shown(book.adrs.map_sha).slice(0, 12))),
    h("div", {cls: "adr-filters", role: "group", "aria-label": "Filter ADRs"},
      filters.map(([key, label]) => h("button", {type: "button", cls: "adr-chip-btn", "data-filter": key, "aria-pressed": String(f === key),
        onclick: () => { history.replaceState(null, "", viewHash("adrs", {q: route().query.q || "", f: key === "all" ? "" : key}, ["q", "f"])); render(); }}, label)))),
    h("div", {cls: "panel", id: "adr-results"}));
  return out;
}
function screenAdr(n) {
  const a = adrItems().find((x) => x.number === n);
  if (!a) return [h("h1", {}, "ADR " + n), h("div", {cls: "panel"}, unread(book.adrs) ? err(book.adrs) : "This ADR is not in the project map.")];
  const byTicket = Object.fromEntries(ticketsList().map((t) => [t.id, t]));
  const sections = String(a.text || "").split("\n").filter((ln) => /^#{1,3}\s+/.test(ln));
  return [h("h1", {}, a.title), h("p", {cls: "lede"}, h("a", {href: "#adrs"}, "← every ADR"), " · ", h("code", {}, a.path)),
    h("div", {cls: "panel"}, adrRow(a, "")),
    h("div", {cls: "panel", id: "adr-link-explorer"},
      h("h2", {}, "Link & Scope Inspector"),
      dl([["Sections", sections.length ? sections.join(" · ") : "none"],
        ["Governed files", Array.isArray(a.files) && a.files.length ? h("span", {cls: "mono"}, a.files.join(", ")) : "none"],
        ["Linked tickets", Array.isArray(a.tickets) && a.tickets.length ? h("span", {}, a.tickets.map((id, i) => [i ? " · " : null,
          byTicket[id] ? [h("a", {href: "#ticket/" + encodeURIComponent(id)}, named(byTicket[id])), " (", byTicket[id].state, ")"] : id])) : "none"],
        ["Related ADRs", Array.isArray(a.adrs) && a.adrs.length ? h("span", {}, a.adrs.map((num, i) => [i ? ", " : null, h("a", {href: "#adr/" + encodeURIComponent(num)}, "ADR " + num)])) : "none"]])),
    h("div", {cls: "panel"}, h("pre", {}, a.text))];
}

