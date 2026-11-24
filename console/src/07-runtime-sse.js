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
    ((screen === "ticket" || screen === "intake") && a.dataset.s === "kanban") || (screen === "adr" && a.dataset.s === "adrs") || (screen === "agents" && a.dataset.s === "thresholds") || (screen === "dag" && a.dataset.s === "epics"));
  let body;
  if (screen !== "hive") stopHivePlay();
  if (screen === "ticket") body = screenTicket(arg);
  else if (screen === "hive") body = screenHive();
  else if (screen === "epics" || screen === "dag") body = screenEpics();
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
  main.classList.toggle("wide", screen === "kanban" || screen === "network" || screen === "epics" || screen === "dag");
  if (screen !== "kanban") showBack(null);
  main.replaceChildren(...banners, ...body.filter(Boolean));
  if (screen === "kanban") drawBoard();
  if (screen === "adrs") drawAdrs();
  if (screen === "request") { showCheck(); if (!lastCheck && draftFields.title) scheduleCheck(); }
  if (screen === "launch" && con && con.mode === "live" && !liveCharts && !liveChartsLoading) fetchLiveCharts(false);
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

// --- Cmd+K / Ctrl+K command palette (docs/adr/0125 §3) ---------------------
let cmdOpener = null, cmdSelected = 0;
function cmdPaletteEntries() {
  const items = [];
  const screens = [
    ["Overview", "#overview", "screen home your turn"],
    ["Hive Control Center", "#hive", "screen agents roles wizard"],
    ["Epics & Dependency DAG", "#epics", "screen dag graph depends_on"],
    ["Kanban Board", "#kanban", "screen cards board"],
    ["Network Inspector", "#network", "screen gates patch diff"],
    ["This week's tickets", "#tickets", "screen list"],
    ["Specs", "#specs", "screen criteria"],
    ["New request", "#request", "screen draft"],
    ["ADRs Explorer", "#adrs", "screen architecture decisions"],
    ["Incidents & post mortems", "#incidents", "screen postmortem"],
    ["Go-for-launch & Live Charts", "#launch", "screen canary release charts"],
    ["Thresholds", "#thresholds", "screen policy provenance"],
    ["Agents", "#agents", "screen roles"],
  ];
  for (const [label, hash, kw] of screens) {
    items.push({group: "Screen", label: label, meta: hash, search: (label + " " + kw + " " + hash).toLowerCase(),
      disabled: false, run: () => { location.hash = hash; if (typeof render === "function") render(); }});
  }
  const perms = opsPermissions();
  items.push({group: "Action", label: "Watch once (make watch ONCE=1)", meta: "POST", search: "action watch once runner tower",
    disabled: !perms.canOps, run: () => { watchOnce(); }});
  items.push({group: "Action", label: "Freeze releases (set release-freeze on)", meta: "POST", search: "action freeze release flag on",
    disabled: !perms.canPrivileged, run: () => { openOpsDialog("freeze", {state: "on"}, cmdOpener); }});
  items.push({group: "Action", label: "Unfreeze releases (set release-freeze off)", meta: "POST", search: "action unfreeze release flag off",
    disabled: !perms.canPrivileged, run: () => { openOpsDialog("freeze", {state: "off"}, cmdOpener); }});
  items.push({group: "Action", label: "Emergency rollback", meta: "POST", search: "action rollback kill switch emergency",
    disabled: !perms.canPrivileged, run: () => { openOpsDialog("rollback", {}, cmdOpener); }});

  if (book) {
    for (const t of ticketsList()) {
      const name = named(t);
      const id = String(t.id || "");
      items.push({group: "Ticket", label: name, meta: t.state || "", search: ("ticket " + id + " " + name + " " + (t.state || "") + " " + (t.feature || "")).toLowerCase(),
        disabled: false, run: () => { location.hash = "#ticket/" + encodeURIComponent(id); if (typeof render === "function") render(); }});
      if (t.state === "opened") {
        items.push({group: "Action", label: "Assign ticket " + id, meta: "opened", search: ("assign " + id + " " + name).toLowerCase(),
          disabled: !perms.canOps, run: () => { assignTicket(id); }});
      }
      if (isResumableTicket(t)) {
        items.push({group: "Action", label: "Resume stranded ticket " + id, meta: "resume", search: ("resume stranded " + id + " " + name).toLowerCase(),
          disabled: !perms.canOps, run: () => { resumeTicket(id); }});
      }
    }
    for (const a of adrItems()) {
      items.push({group: "ADR", label: a.title || ("ADR " + a.number), meta: a.status || a.number,
        search: ("adr " + a.number + " " + (a.title || "") + " " + (a.status || "") + " " + (a.files || []).join(" ")).toLowerCase(),
        disabled: false, run: () => { location.hash = "#adr/" + encodeURIComponent(a.number); if (typeof render === "function") render(); }});
    }
    for (const k of (Array.isArray(book.intakes) ? book.intakes : [])) {
      items.push({group: "Intake", label: "Intake " + k.id, meta: k.state || "",
        search: ("intake " + k.id + " " + (k.state || "")).toLowerCase(),
        disabled: false, run: () => { location.hash = "#intake/" + encodeURIComponent(k.id); if (typeof render === "function") render(); }});
    }
  }
  return items;
}
function filterCmdPalette(q) {
  const words = String(q || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
  const all = cmdPaletteEntries();
  if (!words.length) return all.slice(0, CMD_PALETTE_MAX_RESULTS);
  return all.filter((it) => words.every((w) => it.search.includes(w))).slice(0, CMD_PALETTE_MAX_RESULTS);
}
function closeCmdPalette() {
  const d = document.getElementById("cmd-palette");
  if (!d) return;
  if (d.open && typeof d.close === "function") d.close(); else d.open = false;
  d.replaceChildren();
  const back = cmdOpener;
  cmdOpener = null;
  if (back && back.isConnected && typeof back.focus === "function") back.focus();
}
function openCmdPalette(opener) {
  const d = document.getElementById("cmd-palette");
  if (!d) return;
  cmdOpener = opener || null;
  cmdSelected = 0;
  let matches = filterCmdPalette("");
  const input = h("input", {type: "search", id: "cmd-k-input", cls: "cmd-search",
    placeholder: "Jump to screen, ticket, ADR, intake, or operator action…", "aria-label": "Filter commands"});
  const list = h("ul", {cls: "cmd-results", id: "cmd-k-results", role: "listbox"});
  function drawList() {
    if (cmdSelected >= matches.length) cmdSelected = Math.max(0, matches.length - 1);
    if (!matches.length) {
      list.replaceChildren(h("li", {cls: "muted", style: "padding:8px"}, "No matching screen, ticket, ADR, or action."));
      return;
    }
    list.replaceChildren(...matches.map((it, idx) => h("li", {},
      h("button", {type: "button", cls: "cmd-item-btn", role: "option", "data-idx": String(idx),
        "aria-selected": String(idx === cmdSelected), disabled: it.disabled,
        onclick: () => { if (it.disabled) return; closeCmdPalette(); it.run(); }},
        h("span", {}, h("span", {cls: "cmd-group"}, it.group), " ", it.label),
        h("span", {cls: "muted mono"}, it.disabled ? "disabled" : it.meta)))));
  }
  input.addEventListener("input", () => {
    matches = filterCmdPalette(input.value);
    cmdSelected = 0;
    drawList();
  });
  input.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "ArrowDown" && matches.length) {
      e.preventDefault();
      cmdSelected = (cmdSelected + 1) % matches.length;
      drawList();
    } else if (e.key === "ArrowUp" && matches.length) {
      e.preventDefault();
      cmdSelected = (cmdSelected - 1 + matches.length) % matches.length;
      drawList();
    } else if (e.key === "Enter" && matches.length) {
      e.preventDefault();
      const chosen = matches[cmdSelected];
      if (chosen && !chosen.disabled) { closeCmdPalette(); chosen.run(); }
    }
  });
  drawList();
  d.replaceChildren(
    h("div", {cls: "back-top"},
      h("h2", {id: "cmd-palette-title", style: "flex:1;margin:0"}, "Command Palette"),
      h("button", {type: "button", cls: "close", onclick: closeCmdPalette}, "Close (Esc)")),
    input,
    list);
  if (!d.open) { if (typeof d.showModal === "function") d.showModal(); else d.open = true; }
  input.focus();
}
(function () {
  const btn = document.getElementById("cmd-k-btn");
  if (btn && typeof btn.addEventListener === "function") {
    btn.addEventListener("click", (e) => openCmdPalette(e.currentTarget));
  }
  const d = document.getElementById("cmd-palette");
  if (d && typeof d.addEventListener === "function") {
    d.addEventListener("cancel", (e) => { if (typeof e.preventDefault === "function") e.preventDefault(); closeCmdPalette(); });
    if (!(typeof HTMLDialogElement !== "undefined" && HTMLDialogElement.prototype && "closedBy" in HTMLDialogElement.prototype)) {
      d.addEventListener("click", (e) => { if (e.target === d) closeCmdPalette(); });
    }
  }
})();
window.addEventListener("hashchange", () => { render(); });
window.addEventListener("keydown", (e) => {
  if (e.isComposing || e.keyCode === 229) return;
  if ((e.metaKey || e.ctrlKey) && !e.altKey && String(e.key || "").toLowerCase() === "k") {
    e.preventDefault();
    openCmdPalette(document.activeElement);
  }
});
window.addEventListener("pagehide", stopEventStream);
load().then(() => { render(); syncEventStream(); });
// A replay holds the poll: it sends no request until it ends (docs/adr/0071 §3).
setInterval(() => { if (con && con.mode === "live" && !replaying()) tick(); }, POLL_FALLBACK_MS);
