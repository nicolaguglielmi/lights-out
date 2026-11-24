"use strict";
let book = null, con = null, lastLine = null, busy = false, windowDays = null, answerLine = null;
let etag = null;

// h(tag, attrs, ...children): every text goes through a text node, never HTML.
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "cls") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
// A ticket's name: the logbook's title already starts with the id when it
// comes from the ticket's first line.
const named = (t) => String(t.title).startsWith(t.id) ? t.title : t.id + " — " + t.title;
const unread = (v) => v && typeof v === "object" && !Array.isArray(v) && v.ok === false;
const shown = (v) => (v === null || v === undefined || v === "") ? "—" : v;
function chip(verdict) {
  const cls = ["GO", "NO-GO", "pending", "needs-human", "stranded"].includes(verdict) ? verdict : "none";
  return h("span", {cls: "chip " + cls}, verdict || "not reached");
}
function err(v) { return h("span", {cls: "unread"}, "could not read: " + v.error); }
function dl(pairs) {
  return h("dl", {}, pairs.filter(Boolean).map(([k, v]) => [h("dt", {}, k), h("dd", {}, v instanceof Node ? v : shown(v))]));
}
function ticketsList() { return Array.isArray(book.tickets) ? book.tickets : []; }
function releasesList() { return Array.isArray(book.releases) ? book.releases : []; }

// --- whose turn it is (docs/adr/0063 §2) ----------------------------------
// One sentence per ticket from its state and the logbook's own fields,
// through the tower's table embedded above: who has the ball and what it
// waits for. A person's turn says so, with the one thing to do. It is a
// reading of the record, never a verdict: the columns stay the logbook's.
const MACHINE = JSON.parse(document.getElementById("tower-machine").textContent);
const BANDS = MACHINE.bands;
const bandOf = (state) => (BANDS.find((b) => b.states.includes(state)) || {band: "other"}).band;
const bandName = (band) => (BANDS.find((b) => b.band === band) || {name: "Other"}).name;
function reworkMax() {
  const t = Array.isArray(book.thresholds) ? book.thresholds.find((x) => x.key === "REWORK_MAX_ATTEMPTS") : null;
  return t && /^[0-9]+$/.test(String(t.value)) ? Number(t.value) : null;
}
function openQuestion(t) {
  const qs = t.questions || [];
  const q = qs[qs.length - 1];
  return t.state === "questions" && q && !q.answer ? q : null;
}
// {turn: you | tower | done | stopped, text, href, act}
function now(t) {
  const out = t.outcome || {};
  const id = t.id, n = t.attempt;
  const history = Array.isArray(t.history) ? t.history : [];
  const last = history[history.length - 1] || {};
  const resume = "./go resume TICKET=" + id;
  // A failed step strands the ticket (ADR 0034): it waits for the tickets
  // holding its files, or for a person to resume it.
  if (out.verdict === "stranded" || t.wait) {
    if (t.wait && t.wait.holders.length) return {turn: "stopped", text: "Waits for " + t.wait.holders.join(", ") + " to release its files, then " + resume};
    return {turn: "you", text: "Your turn: " + (t.wait ? "assign was refused (the base, or a missing perimeter)" : shown(last.step) + " stopped (" + shown(out.line) + ")") + "; resume it with",
      code: resume};
  }
  const q = openQuestion(t);
  if (q) return {turn: "you", text: "Your turn: answer the " + (q.from === "spec" ? "spec gate's" : "product role's") + " question",
    href: "#ticket/" + encodeURIComponent(id), act: "Answer it"};
  // The spend budget stopped the next attempt: its own line (docs/adr/0083, amendment §4).
  if (t.state === "needs-human" && out.stop === "budget" && out.line) return {turn: "you", text: "Your turn: " + out.line + "; a person decides",
    href: "#ticket/" + encodeURIComponent(id), act: "See the attempts"};
  if (t.state === "needs-human") return {turn: "you", text: "Your turn: " + shown(n) + " attempts used, the last stopped by the " + (out.judge === "review" ? "reviewer" : shown(out.judge)) + "; a person decides",
    href: "#ticket/" + encodeURIComponent(id), act: "See the attempts"};
  if (t.state === "held") {
    if (out.judge === "human") return {turn: "done", text: out.verdict === "GO" ? "Done: at 100%, " + out.line : "Done: " + out.line + ", the canary rolled back"};
    const L = book.launch || {}, rec = L.record && !unread(L.record) ? L.record : null;
    const rel = t.release || {};
    if (rec && rel.version && rec.version === rel.version && !rec.promoted && !rec.rejected)
      return {turn: "you", text: "Your turn: go-for-launch, the canary holds at " + shown(rec.held_at) + "%", href: "#launch", act: "Go-for-launch"};
    return {turn: "done", text: "Held with no answer; the launch record now belongs to another version"};
  }
  if (t.state === "rolled-back") return {turn: "done", text: "Rolled back automatically: " + shown(out.line)};
  if (t.state === "questions") return {turn: "tower", text: MACHINE.who.answer};
  const step = MACHINE.next[t.state];
  if (!step) return {turn: "tower", text: "At " + t.state + ": the tower's table requests no step from it"};
  const max = reworkMax();
  if (step === "rework" && max !== null && Number(n) >= max) return {turn: "tower", text: "The rework step hands it to a person: attempt " + n + " was the last of " + max};
  if (step === "rework") return {turn: "tower", text: MACHINE.who.rework + ", attempt " + (Number(n || 1) + 1) + (max !== null ? " of " + max : "")};
  if ((step === "ci" || step === "review") && n) return {turn: "tower", text: MACHINE.who[step] + ", attempt " + n};
  return {turn: "tower", text: MACHINE.who[step]};
}
// The now line; on the card's back its one action is drawn as the primary
// link (docs/adr/0068 §3). The words are the same everywhere.
function nowLine(t, primary) {
  const w = now(t);
  return h("span", {cls: "now", "data-turn": w.turn}, w.text, w.code ? [" ", h("code", {}, w.code)] : null,
    w.href ? [primary ? h("br") : null, h("a", {cls: "act" + (primary ? " primary" : ""), href: w.href, onclick: (e) => e.stopPropagation()}, w.act + " →")] : null);
}

// --- the why of a NO-GO (docs/adr/0078 §4) ---------------------------------
// One dialog, one builder: the gate's line and its details (or why they are
// withheld), the new SAST findings, the attempt, the attempts left on
// REWORK_MAX_ATTEMPTS and the next move. It writes nothing.
function whyFor(t, n) {
  const back = (t.rework || []).find((r) => r && r.attempt === n && r.why);
  if (back) return back.why;
  return t.ci && t.ci.why && t.ci.why.attempt === n ? t.ci.why : null;
}
let whyOpener = null;
function whyAttrs(t, n) {
  return {"data-ticket": t.id, "data-attempt": String(n), "aria-haspopup": "dialog",
    onclick: (e) => { e.stopPropagation(); openWhy(t.id, n, e.currentTarget); }};
}
// A button that opens the panel, or null when attempt n has no why.
function whyButton(t, n, ...kids) {
  return whyFor(t, n) ? h("button", Object.assign({type: "button", cls: "why-open", title: "Why attempt " + n + " was rejected"}, whyAttrs(t, n)), kids) : null;
}
// A fact whose words are fixed (#network) opens it itself, by mouse and key.
function whyFact(el, t, n) {
  if (!whyFor(t, n)) return el;
  el.classList.add("why-open");
  el.setAttribute("role", "button"); el.setAttribute("tabindex", "0");
  el.setAttribute("data-ticket", t.id); el.setAttribute("data-attempt", String(n)); el.setAttribute("aria-haspopup", "dialog");
  el.addEventListener("click", (e) => { e.stopPropagation(); openWhy(t.id, n, el); });
  el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); openWhy(t.id, n, el); } });
  return el;
}
function whyNext(t, n) {
  return n === t.attempt ? now(t).text : "sent back to the dev-worker: attempt " + (Number(n) + 1);
}
function whyPanel(t, w) {
  const max = reworkMax(), n = w.attempt;
  const listed = (v, row) => unread(v) ? h("p", {cls: "withheld"}, v.error) : v;
  const details = unread(w.details) ? listed(w.details)
    : w.details.length ? h("ul", {}, w.details.map((d) => h("li", {cls: "mono"}, d))) : h("p", {cls: "muted"}, "The gate gave no details.");
  return [h("div", {cls: "back-top"}, h("h2", {id: "why-title", style: "flex: 1; margin: 0"}, "Why attempt " + shown(n) + " of " + named(t) + " was rejected"),
      h("button", {type: "button", cls: "close", onclick: closeWhy}, "Close (Esc)")),
    h("p", {id: "why-line", cls: "mono"}, shown(w.line)),
    h("p", {id: "why-gate", cls: "muted"}, "gate " + shown(w.gate) + " · judged by " + shown(w.judge)),
    h("h3", {}, "Details"), h("div", {id: "why-details"}, details),
    w.sast === null || w.sast === undefined ? null : [h("h3", {}, "New SAST findings"), h("div", {id: "why-sast"}, unread(w.sast) ? listed(w.sast)
      : h("table", {}, h("tr", {}, ["Rule", "File", "Line"].map((c) => h("th", {}, c))),
        w.sast.map((f) => h("tr", {}, h("td", {cls: "mono"}, f.rule), h("td", {cls: "mono"}, f.file), h("td", {cls: "mono"}, String(f.line))))))],
    dl([["Attempt", h("span", {id: "why-attempt"}, "attempt " + shown(n))],
      max !== null && Number.isInteger(n) ? ["Left", h("span", {id: "why-left"}, Math.max(0, max - n) + " of " + max + " attempts left")] : null,
      ["Next", h("span", {id: "why-next"}, whyNext(t, n))]])];
}
function openWhy(id, n, opener) {
  const t = ticketsList().find((x) => x.id === id), w = t ? whyFor(t, n) : null;
  if (!w) return;
  const d = document.getElementById("why");
  whyOpener = opener || null;
  d.replaceChildren(...whyPanel(t, w).flat().filter(Boolean));
  if (!d.open) d.showModal();
  d.querySelector("button.close").focus();
}
function closeWhy() {
  const d = document.getElementById("why");
  if (d.open) d.close();
  d.replaceChildren();
  const back = whyOpener;
  whyOpener = null;
  if (back && back.isConnected) back.focus();
}
(function () {
  const d = document.getElementById("why");
  d.addEventListener("cancel", (e) => { e.preventDefault(); closeWhy(); });
  d.addEventListener("click", (e) => { if (e.target === d) closeWhy(); });
})();

// --- the card's face (docs/adr/0068 §2) -------------------------------------
// Who has the ball, as an icon: the actor of the step the tower requests
// next when the turn is the tower's (the embedded table's `actor`, checked
// by ./go ci), otherwise the turn itself. No step, no badge.
function actorOf(t) {
  const w = now(t);
  if (w.turn !== "tower") return w.turn;
  const step = t.state === "questions" ? "answer" : MACHINE.next[t.state];
  return step && MACHINE.actor[step] ? MACHINE.actor[step] : null;
}
const ACTOR_NAME = {product: "the product role", architect: "the architect role", spec: "the spec gate", dev: "the dev-worker", gates: "the gates", reviewer: "the reviewer",
  tower: "the tower", canary: "the canary", you: "you", stopped: "nobody: it is stopped", done: "nobody: it is done"};
// Inline icons, 16x16, drawn with the text's colour; no asset from the network.
const ICONS = {
  product: "M3 3h10v7H8l-3 3v-3H3z",
  spec: "M4 2h8v12H4zM6 5h4M6 8h4M6 11h2",
  architect: "M2 14h12M4 14V7l4-4 4 4v7M7 14v-3h2v3",
  dev: "M6 4L2 8l4 4M10 4l4 4-4 4",
  gates: "M8 2l5 2v4c0 3-2 5-5 6-3-1-5-3-5-6V4zM5.5 8l2 2 3-3.5",
  reviewer: "M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8zM8 6a2 2 0 100 4 2 2 0 000-4z",
  tower: "M5 3v10M5 6c0 3 6 2 6 5M5 3a1 1 0 100 .1M11 13a1 1 0 100 .1",
  canary: "M3 13l3-4 3 2 4-6M10 5h3v3",
  you: "M8 8a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM3 14c0-3 2.2-4.5 5-4.5s5 1.5 5 4.5",
  stopped: "M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM6.5 5.5v5M9.5 5.5v5",
  done: "M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM5 8.2l2 2 4-4.2",
  clock: "M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM8 4.5V8l2.5 1.5"};
function icon(name) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16"); svg.setAttribute("aria-hidden", "true"); svg.setAttribute("focusable", "false");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", ICONS[name]); path.setAttribute("fill", "none"); path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.5"); path.setAttribute("stroke-linecap", "round"); path.setAttribute("stroke-linejoin", "round");
  svg.append(path);
  return svg;
}
function whoBadge(t) {
  const a = actorOf(t);
  return a ? h("span", {cls: "who", "data-actor": a, role: "img", "aria-label": "Has the ball: " + ACTOR_NAME[a], title: "Has the ball: " + ACTOR_NAME[a]}, icon(a)) : null;
}
// A feature's colour: the sum of its name's UTF-16 code units, modulo six
// (docs/adr/0068 §2): the same on every board, no table to edit.
function hueOf(name) { let n = 0; for (let i = 0; i < name.length; i++) n += name.charCodeAt(i); return n % 6; }
// The screen before a role read the text (docs/adr/0091 §7): its one line,
// and the card's mark when it was flagged or a person cleared it.
function screenLine(sc) {
  if (!sc || !sc.verdict) return null;
  if (sc.verdict === "cleared") return "cleared by " + shown(sc.by) + " via " + shown(sc.via) + ", " + shown(sc.at);
  if (sc.verdict === "not-screened") return "not screened: " + shown(sc.why);
  const p = typeof sc.p_yes === "number" ? sc.p_yes.toFixed(2) : "?";
  return sc.verdict + " · p=" + p + " that it holds instructions to an AI agent · " + shown(sc.adapter) + (sc.replayed ? ", replayed" : "");
}
function screenMark(sc) {
  if (!sc || (sc.verdict !== "flag" && sc.verdict !== "cleared")) return null;
  return h("span", {cls: "chip pending", "data-kind": "screen", title: "Screen: " + screenLine(sc)}, sc.verdict === "flag" ? "screen: flagged" : "screen: cleared");
}
function labels(t) {
  return h("div", {cls: "labels"},
    screenMark(t.screen),
    t.priority ? h("span", {cls: "label p-" + t.priority, "data-kind": "priority", title: "Priority " + t.priority}, t.priority) : null,
    t.feature ? h("span", {cls: "label", "data-kind": "feature", "data-hue": String(hueOf(String(t.feature))), title: "Feature " + t.feature}, t.feature) : null);
}
// How long the ticket has been in its state: from the first entry of the
// trailing run of history entries that carry it, to the console's read_at
// when live, to the newest `at` in the logbook in a snapshot; never the
// viewer's clock. Left out when the last entry is not in the ticket's state.
let refBook = null, refAt = null;
function referenceAt() {
  if (con && con.mode === "live" && con.read_at) return con.read_at;
  if (refBook !== book) {
    refBook = book; refAt = null;
    for (const t of ticketsList()) for (const e of (Array.isArray(t.history) ? t.history : []))
      if (e && e.at && (!refAt || Date.parse(e.at) > Date.parse(refAt))) refAt = e.at;
  }
  return refAt;
}
function since(t) {
  const hist = Array.isArray(t.history) ? t.history : [];
  let i = hist.length - 1;
  if (i < 0 || hist[i].state !== t.state) return null;
  while (i > 0 && hist[i - 1].state === t.state) i--;
  return hist[i].at || null;
}
function span(from, to) {
  const ms = Date.parse(to) - Date.parse(from);
  if (!(ms >= 0)) return null;
  const m = Math.floor(ms / 60000);
  if (m < 60) return m + "m";
  const hrs = Math.floor(m / 60);
  return hrs < 24 ? hrs + "h" : Math.floor(hrs / 24) + "d";
}
function age(t) {
  const from = since(t), to = referenceAt();
  const text = from && to ? span(from, to) : null;
  return text ? h("time", {cls: "age", datetime: from, title: "In " + t.state + " since " + from + ", as of " + to}, icon("clock"), text) : null;
}
// The rework attempts as pips, out of REWORK_MAX_ATTEMPTS; left out when
// either cannot be read, never defaulted.
function pips(t) {
  const max = reworkMax(), n = t.attempt;
  if (max === null || n === null || n === undefined || !/^[0-9]+$/.test(String(n))) return null;
  const on = Math.min(Number(n), max);
  return h("span", {cls: "pips", "data-max": String(max), "data-filled": String(Number(n)), role: "img", "aria-label": "Attempt " + n + " of " + max, title: "Attempt " + n + " of " + max},
    Array.from({length: max}, (_, i) => h("span", {cls: "pip" + (i < on ? " on" : "")})));
}
// The search's match in the title, marked; the text is the same.
function titled(t, q) {
  const name = named(t);
  const at = q ? name.toLowerCase().indexOf(q.toLowerCase()) : -1;
  return h("span", {cls: "title"}, at < 0 ? name : [name.slice(0, at), h("mark", {}, name.slice(at, at + q.length)), name.slice(at + q.length)]);
}

// --- the flow strip (docs/adr/0085 §6) ----------------------------------------
// The logbook's fourteen nodes as they are: the page computes no verdict, no
// time and no reason. The card's face draws them compact, one label for the
// whole strip (the card is the board's keyboard unit); the back and the
// timeline make each node a tab stop with its reason on hover and on focus,
// and a NO-GO gate with a why opens it (ADR 0078).
const FLOW_NAMES = {ticket: "ticket", perimeter: "perimeter", pr: "PR", canary: "canary", human: "human gate", full: "100%"};
const FLOW_WORDS = {"GO": "GO", "NO-GO": "NO-GO", running: "running", "not-reached": "not reached"};
const FLOW_MARKS = {"GO": "✓", "NO-GO": "✗", running: "◌", "not-reached": "·"};
const flowName = (n) => n.node === "gate" ? String(n.gate) : (FLOW_NAMES[n.node] || String(n.node));
const flowOf = (t) => Array.isArray(t.flow) && t.flow.length ? t.flow : null;
function took(s) {
  if (s === null || s === undefined) return null;
  s = Number(s);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  return Math.round(s / 360) / 10 + "h";
}
function flowFace(t) {
  const f = flowOf(t);
  if (!f) return null;
  return h("ol", {cls: "flow", role: "img", "aria-label": "Flow: " + f.map((n) => flowName(n) + " " + FLOW_WORDS[n.verdict]).join(", ")},
    f.map((n) => h("li", {cls: "flow-node", "data-node": n.node, "data-gate": n.node === "gate" ? n.gate : null, "data-verdict": n.verdict,
      title: n.line || FLOW_WORDS[n.verdict]}, FLOW_MARKS[n.verdict] || "?")));
}
// `id` only on the timeline screen (ol#flow); the back draws the same strip without it.
function flowDetail(t, id) {
  const f = flowOf(t);
  if (!f) return null;
  const pre = (id ? "flow-t-" : "flow-b-") + t.id + "-";
  return h("ol", {cls: "flow flow-detail", id: id || null, "aria-label": "The path of ticket " + t.id + ", from the request to 100%"},
    f.map((n, i) => {
      const opens = n.node === "gate" && n.verdict === "NO-GO" && whyFor(t, t.attempt);
      const time = n.verdict === "running" ? "…" : took(n.seconds);
      const open = (e) => { e.stopPropagation(); openWhy(t.id, t.attempt, e.currentTarget); };
      return h("li", {cls: "flow-node", tabindex: "0", "data-node": n.node, "data-gate": n.node === "gate" ? n.gate : null,
        "data-verdict": n.verdict, "data-seconds": n.seconds === null || n.seconds === undefined ? null : String(n.seconds),
        "aria-describedby": n.line ? pre + i : null, "aria-haspopup": opens ? "dialog" : null,
        onclick: opens ? open : null,
        onkeydown: opens ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(e); } } : null},
        flowName(n), " ", h("span", {"aria-hidden": "true"}, FLOW_MARKS[n.verdict] || "?"), h("span", {cls: "flow-verdict"}, " " + FLOW_WORDS[n.verdict]),
        time ? h("span", {cls: "flow-time"}, time) : null,
        n.line ? h("span", {cls: "flow-why", id: pre + i, role: "tooltip"}, n.line) : null);
    }));
}

// --- workstation ops, diff highlighter & gate/mutant helpers (docs/adr/0125) ---
const CMD_PALETTE_MAX_RESULTS = 40;
const DIFF_RENDER_MAX_LINES = 2000;
const OPS_REASON_MAX_LEN = 512;
let opsNotice = null;
let liveCharts = null, liveChartsLoading = false;

function announceMsg(msg) {
  const ann = document.getElementById("announce");
  if (ann) ann.textContent = String(msg || "");
}
function isResumableTicket(t) {
  if (!t || typeof t !== "object") return false;
  if (t.outcome && t.outcome.verdict === "stranded") return true;
  if (t.stranded) return true;
  const hist = Array.isArray(t.history) ? t.history : [];
  const last = hist.length ? hist[hist.length - 1] : null;
  return Boolean(last && last.error);
}
function opsPermissions() {
  const mode = con && con.mode;
  const auth = con && con.auth;
  const isLive = Boolean(mode === "live" && auth !== "public");
  const reason = mode !== "live" ? "Snapshot mode: operator actions disabled."
    : auth === "public" ? "Public mode: operator actions disabled."
    : !con.can_launch ? (con.reason || "Launch privilege required for rollback or freeze.")
    : busy ? "Another operator command is running." : null;
  return {
    isLive: isLive,
    canOps: isLive && !busy,
    canPrivileged: Boolean(isLive && con.can_launch && !busy),
    reason: reason,
  };
}
async function _runOpsFetch(label, runFetch) {
  if (busy || !opsPermissions().isLive) return null;
  busy = true;
  if (typeof render === "function") render();
  try {
    const r = await runFetch();
    const body = await r.json().catch(() => ({}));
    const ok = Boolean(r.ok && (body.exit === 0 || body.exit === undefined));
    const line = body.line || body.error || r.statusText || (label + " finished");
    opsNotice = {action: label, ok: ok, line: line, at: new Date().toISOString()};
    announceMsg(line);
    if (con && con.mode === "live" && typeof load === "function") await load();
    return opsNotice;
  } catch (e) {
    const msg = label + " failed: " + e;
    opsNotice = {action: label, ok: false, line: msg, at: new Date().toISOString()};
    announceMsg(msg);
    return opsNotice;
  } finally {
    busy = false;
    if (typeof render === "function") render();
  }
}
function assignTicket(ticketId) {
  return _runOpsFetch("assign " + ticketId, () =>
    fetch("/v1/assign", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ticket: ticketId})}));
}
function resumeTicket(ticketId) {
  return _runOpsFetch("resume " + ticketId, () =>
    fetch("/v1/resume", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ticket: ticketId})}));
}
function watchOnce() {
  return _runOpsFetch("watch-once", () =>
    fetch("/v1/watch-once", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({})}));
}
function rollbackRelease(reason) {
  return _runOpsFetch("rollback", () =>
    fetch("/v1/rollback", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({reason: reason})}));
}
function toggleFreeze(state, reason) {
  return _runOpsFetch("freeze " + state, () =>
    fetch("/v1/freeze", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({state: state, reason: reason})}));
}
async function fetchLiveCharts(force) {
  if (!con || con.mode !== "live") return null;
  if (liveChartsLoading && !force) return liveCharts;
  liveChartsLoading = true;
  try {
    const r = await fetch("/v1/charts.json");
    const body = await r.json().catch(() => ({ok: false, error: "invalid charts response"}));
    liveCharts = body && typeof body === "object" ? body : {ok: false, error: "empty charts payload"};
  } catch (e) {
    liveCharts = {ok: false, error: "failed to load live charts: " + e};
  } finally {
    liveChartsLoading = false;
    if (typeof render === "function") render();
  }
  return liveCharts;
}
let opsOpener = null;
function closeOpsDialog() {
  const d = document.getElementById("ops-dialog");
  if (!d) return;
  if (d.open && typeof d.close === "function") d.close(); else d.open = false;
  d.replaceChildren();
  const back = opsOpener;
  opsOpener = null;
  if (back && back.isConnected && typeof back.focus === "function") back.focus();
}
function openOpsDialog(kind, opts, opener) {
  const d = document.getElementById("ops-dialog");
  if (!d) return;
  const p = opsPermissions();
  if (!p.canPrivileged) return;
  const st = (opts && opts.state) === "off" ? "off" : "on";
  const isRollback = kind === "rollback";
  opsOpener = opener || null;
  const note = h("p", {cls: "nogo", style: "display:none"});
  const input = h("input", {type: "text", id: "ops-reason-input", cls: "ops-reason-input", maxlength: String(OPS_REASON_MAX_LEN),
    placeholder: isRollback ? "Reason for emergency rollback (required, audited)" : "Reason for release-freeze " + st + " (required, audited)",
    "aria-label": "Audit reason"});
  function submit() {
    const reason = input.value.replace(/\s+/g, " ").trim();
    if (!reason) { note.textContent = "A non-empty reason is required for the audit log."; note.style.display = "block"; input.focus(); return; }
    closeOpsDialog();
    if (isRollback) rollbackRelease(reason);
    else toggleFreeze(st, reason);
  }
  input.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter") { e.preventDefault(); submit(); }
  });
  const titleText = isRollback ? "Confirm Emergency Rollback" : "Confirm Release Freeze: " + st.toUpperCase();
  const bodyCopy = isRollback
    ? "Engages WORKLOAD_KILL_SWITCH (if configured), executes DELIVERY rollback, and writes an audit record."
    : "Sets the release-freeze flag to " + st + " via the FLAGS port and writes an audit record.";
  d.replaceChildren(
    h("div", {cls: "back-top"},
      h("h2", {id: "ops-dialog-title", style: "flex:1;margin:0"}, titleText),
      h("button", {type: "button", cls: "close", onclick: closeOpsDialog}, "Close (Esc)")),
    h("p", {cls: "muted"}, bodyCopy),
    h("label", {for: "ops-reason-input"}, "Reason (required, max " + OPS_REASON_MAX_LEN + " chars)"),
    input,
    note,
    h("div", {cls: "buttons"},
      h("button", {type: "button", id: "ops-dialog-confirm", cls: isRollback ? "nogo" : "warn", onclick: submit},
        isRollback ? "Execute rollback" : "Set freeze " + st),
      h("button", {type: "button", onclick: closeOpsDialog}, "Cancel")));
  if (!d.open) { if (typeof d.showModal === "function") d.showModal(); else d.open = true; }
  input.focus();
}
(function () {
  const d = document.getElementById("ops-dialog");
  if (!d || typeof d.addEventListener !== "function") return;
  d.addEventListener("cancel", (e) => { if (typeof e.preventDefault === "function") e.preventDefault(); closeOpsDialog(); });
  if (!(typeof HTMLDialogElement !== "undefined" && HTMLDialogElement.prototype && "closedBy" in HTMLDialogElement.prototype)) {
    d.addEventListener("click", (e) => { if (e.target === d) closeOpsDialog(); });
  }
})();
function opsBar(scopeId) {
  const p = opsPermissions();
  return h("div", {cls: "ops-bar", id: scopeId + "-ops-bar"},
    h("div", {cls: "ops-actions"},
      h("button", {type: "button", id: scopeId + "-watch-btn", disabled: !p.canOps, onclick: watchOnce}, "Watch once"),
      h("button", {type: "button", id: scopeId + "-freeze-on-btn", cls: "warn", disabled: !p.canPrivileged,
        onclick: (e) => openOpsDialog("freeze", {state: "on"}, e.currentTarget)}, "Freeze releases"),
      h("button", {type: "button", id: scopeId + "-freeze-off-btn", disabled: !p.canPrivileged,
        onclick: (e) => openOpsDialog("freeze", {state: "off"}, e.currentTarget)}, "Unfreeze releases"),
      h("button", {type: "button", id: scopeId + "-rollback-btn", cls: "nogo", disabled: !p.canPrivileged,
        onclick: (e) => openOpsDialog("rollback", {}, e.currentTarget)}, "Emergency rollback")),
    p.reason ? h("p", {cls: "muted ops-status", style: "margin:0"}, p.reason) : null,
    opsNotice ? h("p", {cls: "ops-status mono " + (opsNotice.ok ? "go" : "nogo"), style: "margin:0"},
      opsNotice.action + ": " + opsNotice.line) : null);
}
function diffLineClass(line) {
  if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff --git") || line.startsWith("index ")) return "diff-meta";
  if (line.startsWith("@@")) return "diff-hunk";
  if (line.startsWith("+")) return "diff-add";
  if (line.startsWith("-")) return "diff-del";
  return "";
}
function renderPatchPre(text) {
  const raw = typeof text === "string" ? text : String(text || "");
  const lines = raw.split("\n");
  if (lines.length > DIFF_RENDER_MAX_LINES) return h("pre", {cls: "diff-view"}, raw);
  const children = [];
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    const kind = diffLineClass(ln);
    children.push(kind ? h("span", {cls: "diff-line " + kind}, ln) : ln);
    if (i < lines.length - 1) children.push("\n");
  }
  return h("pre", {cls: "diff-view"}, children);
}
function gateExplorerPanel(t) {
  const whys = [];
  if (t.ci && t.ci.why) whys.push(t.ci.why);
  for (const r of (Array.isArray(t.rework) ? t.rework : [])) {
    if (r && r.why && !whys.includes(r.why)) whys.push(r.why);
  }
  const tasks = Array.isArray(t.tasks) ? t.tasks : [];
  const mutLines = [];
  for (const w of whys) {
    const details = Array.isArray(w.details) ? w.details : [];
    const hit = w.gate === "mutation" || String(w.line || "").toLowerCase().includes("mutat");
    if (hit) mutLines.push({attempt: w.attempt, line: w.line, details: details});
  }
  for (const tk of tasks) {
    if (!tk || typeof tk !== "object") continue;
    for (const g of (Array.isArray(tk.gates) ? tk.gates : [])) {
      if (g && (g.gate === "mutation" || String(g.line || "").toLowerCase().includes("mutat"))) {
        mutLines.push({attempt: tk.attempt, line: g.line, details: Array.isArray(g.details) ? g.details : []});
      }
    }
  }
  if (!whys.length && !mutLines.length && !tasks.length) return null;
  const blocks = [];
  if (mutLines.length) {
    blocks.push(h("div", {cls: "mutant-box"},
      h("h3", {}, "Mutation Gate Findings"),
      mutLines.map((m) => h("div", {cls: "panel"},
        h("p", {cls: "mono", style: "margin:0 0 6px"}, "attempt " + shown(m.attempt) + ": " + shown(m.line)),
        m.details.length ? h("ul", {}, m.details.map((d) => h("li", {cls: "mono"}, d))) : null))));
  }
  if (whys.length) {
    blocks.push(h("div", {},
      h("h3", {}, "Gate Rejections & SAST Explorer"),
      whys.map((w) => h("div", {cls: "panel"},
        h("p", {cls: "mono", style: "margin:0 0 6px"}, chip("NO-GO"), " attempt " + shown(w.attempt) + " · gate " + shown(w.gate) + ": " + shown(w.line)),
        Array.isArray(w.details) && w.details.length ? h("ul", {}, w.details.map((d) => h("li", {cls: "mono"}, d))) : null,
        Array.isArray(w.sast) && w.sast.length ? h("table", {},
          h("tr", {}, ["Rule", "File", "Line"].map((c) => h("th", {}, c))),
          w.sast.map((f) => h("tr", {}, h("td", {cls: "mono"}, f.rule), h("td", {cls: "mono"}, f.file), h("td", {cls: "mono"}, String(f.line))))) : null))));
  }
  return blocks.length ? h("div", {cls: "panel", id: "gate-mutant-explorer"},
    h("h2", {}, "Gate, SAST & Mutant Explorer"), ...blocks) : null;
}
