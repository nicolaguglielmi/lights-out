// --- frame -------------------------------------------------------------
// The theme switch (docs/adr/0059 §2): tokens on <html data-theme>, kept in
// the browser when it lets us; no screen reads it.
const themeSelect = document.getElementById("theme");
function storedTheme() {
  try { return localStorage.getItem("lights-out.theme") || "system"; } catch (e) { return document.documentElement.dataset.theme || "system"; }
}
themeSelect.value = ["light", "dark"].includes(storedTheme()) ? storedTheme() : "system";
themeSelect.addEventListener("change", () => {
  const t = themeSelect.value;
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
  try { localStorage.setItem("lights-out.theme", t); } catch (e) { /* storage refused: this page keeps it until it closes */ }
});

function header() {
  const src = book.source || {};
  document.getElementById("who").textContent = [src.workload || "?", "level " + (src.level || "?"),
    con.mode === "snapshot" ? "snapshot of " + (src.commit || "?").slice(0, 12) + ", no plane behind it" : "live",
    con.identity ? "as " + con.identity : null].filter(Boolean).join(" · ");
  document.getElementById("fresh").textContent = con.mode === "live" ? "read at " + (con.read_at || "—") : "";
}
// Which element had the focus, and where its caret was, so a redraw can
// give them back.
function focusMark() {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const all = el.name ? [...document.querySelectorAll('[name="' + CSS.escape(el.name) + '"]')] : [];
  return {id: el.id, name: el.name, index: all.indexOf(el), card: el.classList.contains("card") ? el.dataset.id : null,
    start: el.selectionStart, end: el.selectionEnd};
}
function refocus(m) {
  if (!m) return;
  let el = m.card ? document.querySelector('.card[data-id="' + CSS.escape(m.card) + '"]')
    : m.id ? document.getElementById(m.id)
    : m.name ? document.querySelectorAll('[name="' + CSS.escape(m.name) + '"]')[m.index] : null;
  if (!el) return;
  el.focus();
  try { if (m.start !== null && m.start !== undefined) el.setSelectionRange(m.start, m.end); } catch (e) { /* not a text field */ }
}
function render() {
  const main = document.getElementById("main");
  if (!book || !con) return;
  header();
  const {screen, arg} = route();
  for (const a of document.querySelectorAll("nav a")) a.classList.toggle("on", a.dataset.s === screen ||
    ((screen === "ticket" || screen === "intake") && a.dataset.s === "kanban") || (screen === "adr" && a.dataset.s === "adrs") || (screen === "agents" && a.dataset.s === "thresholds"));
  let body;
  if (screen !== "hive") stopHivePlay();
  if (screen === "ticket") body = screenTicket(arg);
  else if (screen === "hive") body = screenHive();
  else if (screen === "intake") body = screenIntake(arg);
  else if (screen === "incidents") body = screenIncidents();
  else if (screen === "launch") body = screenLaunch();
  else if (screen === "thresholds" || screen === "agents") body = screenThresholds();
  else if (screen === "overview") body = screenOverview();
  else if (screen === "kanban") body = screenKanban();
  else if (screen === "network") body = screenNetwork(arg);
  else if (screen === "specs") body = screenSpecs();
  else if (screen === "request") body = screenRequest();
  else if (screen === "adrs") body = screenAdrs();
  else if (screen === "adr") body = screenAdr(arg);
  else body = screenTickets();
  const banners = [];
  if (con.read_error) banners.push(h("div", {cls: "banner err"}, "Stale: the last read failed (" + con.read_error + "); showing the last good logbook."));
  const mark = focusMark();
  main.classList.toggle("wide", screen === "kanban" || screen === "network");
  if (screen !== "kanban") showBack(null);
  main.replaceChildren(...banners, ...body.filter(Boolean));
  if (screen === "kanban") drawBoard();
  if (screen === "adrs") drawAdrs();
  if (screen === "request") { showCheck(); if (!lastCheck && draftFields.title) scheduleCheck(); }
  refocus(mark);
}
// The logbook is fetched with its ETag: an unchanged one costs no body and
// no redraw (docs/adr/0059 §1). The editor is redrawn only by its own
// inputs, so a refresh never takes a keystroke.
async function load() {
  let changed = false;
  try {
    const [c, r] = await Promise.all([fetch("/v1/console.json").then((x) => x.json()),
      fetch("/v1/logbook.json", {headers: etag ? {"If-None-Match": etag} : {}})]);
    con = c;
    if (r.status === 200) {
      const b = await r.json();
      etag = r.headers.get("ETag");
      if (book) { noteMoves(book, b); noteNet(b); }
      book = b; changed = true;
    }
  } catch (e) { /* keep what is shown; the header says when it was read */ }
  if (!book) {
    document.getElementById("main").textContent = "No logbook yet" + (con && con.read_error ? ": " + con.read_error : "; the first read is still running.");
    return false;
  }
  return changed;
}

// --- progressive SSE live updates (docs/adr/0124 §3) -----------------------
// Provenance, decision: docs/adr/0047 §4 & 0124, 2000 ms If-None-Match poll
// remains active as resilient fallback when EventSource is unavailable.
const POLL_FALLBACK_MS = 2000;
let eventStream = null;
let sseState = "off";
let ticking = false, tickPending = false;

function setSseState(next) {
  sseState = next;
  if (document.documentElement && document.documentElement.dataset) {
    document.documentElement.dataset.sse = next;
  }
}

function stopEventStream() {
  if (eventStream) {
    try { eventStream.close(); } catch (e) { /* stream already closed */ }
    eventStream = null;
  }
  setSseState("off");
}

function handleLogbookEvent(ev) {
  if (!con || con.mode === "snapshot") { stopEventStream(); return; }
  if (replaying()) return;
  let payload = null;
  try { payload = JSON.parse(String((ev && ev.data) || "")); } catch (e) { return; }
  if (!payload || typeof payload !== "object") return;
  if (payload.mode === "snapshot") { stopEventStream(); return; }
  setSseState("live");
  const errChanged = (payload.read_error || null) !== (con.read_error || null);
  const tagChanged = Boolean(payload.etag) && payload.etag !== etag;
  if (tagChanged || errChanged) tick();
}

function syncEventStream() {
  if (!con || con.mode === "snapshot" || replaying()) {
    stopEventStream();
    return;
  }
  if (typeof EventSource !== "function") {
    setSseState("fallback");
    return;
  }
  if (eventStream) return;
  try {
    const es = new EventSource("/v1/events");
    eventStream = es;
    setSseState("connecting");
    es.addEventListener("open", () => {
      if (eventStream === es) setSseState("live");
    });
    es.addEventListener("logbook", handleLogbookEvent);
    es.addEventListener("pending", handleLogbookEvent);
    es.addEventListener("error", () => {
      if (eventStream === es) setSseState("fallback");
    });
  } catch (e) {
    eventStream = null;
    setSseState("fallback");
  }
}

async function tick() {
  if (ticking) { tickPending = true; return; }
  ticking = true;
  try {
    do {
      tickPending = false;
      const changed = await load();
      syncEventStream();
      if (!book || busy) continue;
      // The editor and an intake's open decisions keep their inputs (docs/adr/0090 §8).
      const editing = route().screen === "request" || (route().screen === "intake" && intakeEdits[route().arg]);
      if (changed && !editing) render(); else header();
    } while (tickPending && con && con.mode === "live" && !replaying());
  } finally {
    ticking = false;
  }
}
window.addEventListener("hashchange", () => { render(); });
window.addEventListener("pagehide", stopEventStream);
load().then(() => { render(); syncEventStream(); });
// A replay holds the poll: it sends no request until it ends (docs/adr/0071 §3).
setInterval(() => { if (con && con.mode === "live" && !replaying()) tick(); }, POLL_FALLBACK_MS);
