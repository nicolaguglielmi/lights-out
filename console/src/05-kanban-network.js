// --- kanban (docs/adr/0055 §7, docs/adr/0059 §1) -------------------------
// The columns and the order inside them are the logbook's (`kanban`): the
// page filters, groups, searches, collapses and turns a card over, and never
// moves a card. No drag-and-drop, no button that moves one: a card moves
// when the tower moves its ticket, and then it shows where it came from.
// Provenance, decision: docs/adr/0059 §1, two beats of the console's 2 s poll.
const MOVED_MS = 4000;
// A moved card flies from where it was to where it is (docs/adr/0068 §4).
// Provenance, decision: 400 ms ends inside one 2 s poll, so two flights
// never overlap, and inside the 4 s mark.
const FLIGHT_MS = 400;
let moved = {};  // id -> {from, until, rect, flown}: cards the last logbook moved
const still = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
function noteMoves(before, after) {
  if (!before || !Array.isArray(before.kanban) || !Array.isArray(after.kanban)) return;
  const col = (b) => Object.fromEntries(b.kanban.flatMap((c) => c.tickets.map((id) => [id, c.state])));
  const was = col(before), now = col(after), until = Date.now() + MOVED_MS;
  const said = [];
  for (const [id, st] of Object.entries(now)) if (was[id] && was[id] !== st) {
    // Where the card is drawn now, before the redraw puts it elsewhere.
    const el = document.querySelector('.card[data-id="' + CSS.escape(id) + '"]');
    moved[id] = {from: was[id], until, rect: el ? el.getBoundingClientRect() : null, flown: false};
    said.push(id + " moved from " + was[id] + " to " + st);
  }
  // Said to a screen reader too (WCAG 2.1 SC 4.1.3): a polite live region.
  if (said.length) document.getElementById("announce").textContent = said.join(". ") + ".";
  setTimeout(drawBoard, MOVED_MS + 50);
}
function fly() {
  for (const [id, m] of Object.entries(moved)) {
    if (m.flown || m.until <= Date.now()) continue;
    m.flown = true;
    const el = document.querySelector('.card[data-id="' + CSS.escape(id) + '"]');
    if (!el || !m.rect || still()) continue;
    const r = el.getBoundingClientRect();
    const dx = m.rect.left - r.left, dy = m.rect.top - r.top;
    if (dx || dy) el.animate([{transform: "translate(" + dx + "px, " + dy + "px)"}, {transform: "none"}],
      {duration: FLIGHT_MS, easing: "cubic-bezier(.2, .8, .2, 1)"});
  }
}
// A path matches a perimeter glob as core/perimeter.sh matches it (`*`
// crosses `/`), or the glob contains the text typed.
function globMatches(glob, text) {
  if (glob.toLowerCase().includes(text.toLowerCase())) return true;
  const re = "^" + glob.replace(/[.+^${}()|\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$";
  try { return new RegExp(re).test(text); } catch (e) { return false; }
}
function searched(t, q) {
  if (!q) return true;
  const low = q.toLowerCase();
  if (t.id.includes(q) || String(t.title).toLowerCase().includes(low)) return true;
  return Array.isArray(t.perimeter) && t.perimeter.some((g) => globMatches(g, q));
}
// A card's face (docs/adr/0068 §2): labels, the title, the now line, and a
// badge row: who has the ball, how long in this state, the attempts, the outcome.
function card(t, v) {
  const m = moved[t.id] && moved[t.id].until > Date.now() ? moved[t.id] : null;
  const open = () => setView({card: t.id});
  const held = launchTicket() === t ? launchable() : null;
  return h("div", {cls: "card" + (m ? " moved" : "") + (v.card === t.id ? " selected" : ""), "data-id": t.id, "data-turn": now(t).turn,
    "data-from": m ? m.from : null, tabindex: "0", role: "button", "aria-label": named(t), onclick: open,
    draggable: held ? "true" : null, "data-launch": held ? held.version : null,
    ondragstart: held ? (e) => { e.dataTransfer.setData("text/plain", held.version); e.dataTransfer.effectAllowed = "move"; } : null},
    labels(t),
    titled(t, v.q),
    nowLine(t),
    flowFace(t),
    h("div", {cls: "badges"}, whoBadge(t), age(t), pips(t), liveBadge(t),
      t.outcome && t.outcome.verdict !== "pending"
        ? (t.outcome.verdict === "NO-GO" && whyButton(t, t.attempt, chip(t.outcome.verdict))) || chip(t.outcome.verdict) : null),
    m ? h("div", {cls: "muted"}, "moved here from " + m.from) : null);
}
function adrName(n) {
  const items = book.adrs && Array.isArray(book.adrs.items) ? book.adrs.items : [];
  const a = items.find((x) => x.number === n);
  return a ? a.title : "ADR " + n;
}
function adrLinks(nums) {
  return nums && nums.length ? h("ul", {}, nums.map((n) => h("li", {}, h("a", {href: "#adr/" + n}, adrName(n)))))
    : h("p", {cls: "muted"}, unread(book.adrs) ? "No project map: " + book.adrs.error : "No ADR cites a file of its perimeter.");
}
// The card's back (docs/adr/0068 §3): what the docked drawer showed, inside
// a modal dialog over the board, with the now line's one action drawn as the
// primary link, the open question's text, the band strip and a compact
// timeline. No answer box: the kanban writes nothing (ADR 0059 §1).
function closeBack(id) { setView({card: ""}); focusCard(id); }
// What the last run used (docs/adr/0073 §10): the dev-worker's MCP calls and
// the reviewer's instructions from the base; no row when the run had none.
function agentRows(t) {
  const mcp = t.task && t.task.mcp, ins = t.review && t.review.instructions;
  const calls = mcp ? (mcp.calls || []).map((c) => c.server + "." + c.tool + " ×" + c.count).join(", ") : "";
  return [mcp ? ["MCP", calls || "declared, no calls"] : null,
    ins ? ["Reviewer instructions", ins.file + " (sha256 " + String(ins.sha256).slice(0, 12) + ", from base " + String(ins.base).slice(0, 7) + ")"] : null];
}
function drawer(t) {
  const rows = t.timeline || [];
  const q = openQuestion(t);
  return h("aside", {id: "drawer", "data-id": t.id, "aria-label": "Ticket " + t.id},
    h("div", {cls: "back-top"}, labels(t), h("button", {cls: "close", onclick: () => closeBack(t.id)}, "Close (Esc)")),
    h("h2", {}, named(t)),
    h("div", {cls: "nowbox", "data-turn": now(t).turn}, nowLine(t, true)),
    q ? h("blockquote", {cls: "question"}, h("b", {}, q.from === "spec" ? "The spec gate asks: " : "The product role asks: "), q.question) : null,
    stageStrip(t),
    flowDetail(t),
    liveLine(t),
    h("div", {cls: "badges"}, whoBadge(t), age(t), pips(t)),
    // No verdict yet: the now line says what is awaited, and no empty row stands in.
    dl([["State", t.state + " · " + bandName(bandOf(t.state))],
      t.outcome.verdict !== "pending" ? ["Outcome", h("span", {}, chip(t.outcome.verdict), " ", shown(t.outcome.judge))] : null,
      t.outcome.verdict !== "pending" ? ["Line", h("span", {cls: "mono"}, shown(t.outcome.line))] : null,
      ["Perimeter", unread(t.perimeter) ? err(t.perimeter) : h("span", {cls: "mono"}, (t.perimeter || []).join("  ·  ") || "—")],
      ["Waits", t.wait ? waitLine(t.wait) : "no"],
      t.screen ? ["Screen", screenLine(t.screen)] : null,
      ...agentRows(t)]),
    h("h3", {}, "Timeline"),
    rows.length ? h("ol", {cls: "compact"}, rows.map((r) => h("li", {"data-phase": r.phase},
      h("span", {}, r.phase), r.verdict ? whyRow(t, r, chip(verdictOf(r))) : whyRow(t, r, null) || h("span", {cls: "muted"}, "—"),
      h("span", {cls: "line", title: r.line || ""}, shown(r.line)), h("span", {cls: "at"}, shown(r.at)))))
      : h("p", {cls: "muted"}, "This logbook has no timeline (written before 1.1)."),
    h("h3", {}, "ADRs that govern its perimeter"), adrLinks(t.adrs),
    h("p", {}, h("a", {href: "#ticket/" + encodeURIComponent(t.id)}, "The whole timeline →")));
}
function screenKanban() {
  const out = [h("h1", {}, "Kanban"),
    h("p", {cls: "lede"}, "One list per tower state, under the four bands of the flow. A card moves only when the tower moves its ticket. The one card that drags is a held release, onto Promote or Rollback: the drop opens the confirmation, it moves nothing. ",
      h("span", {cls: "muted"}, "Click a card, or press Enter on it, to turn it over; the arrows move between cards, Esc closes, / searches; the link keeps the view."))];
  if (unread(book.kanban) || unread(book.tickets)) return out.concat(h("div", {cls: "panel"}, err(unread(book.kanban) ? book.kanban : book.tickets)));
  if (!Array.isArray(book.kanban)) return out.concat(h("div", {cls: "panel"}, h("p", {cls: "muted"}, "This logbook has no kanban (written before 1.1).")));
  const v = kanbanView();
  const features = [...new Set(ticketsList().map((t) => t.feature || ""))].sort();
  const sel = (name, value, opts) => h("select", {name, onchange: (e) => setView({[name]: e.target.value})},
    opts.map(([val, txt]) => h("option", {value: val, selected: val === value}, txt)));
  out.push(h("div", {cls: "controls"},
    h("input", {type: "search", id: "kanban-q", value: v.q, placeholder: "Search id, title or a perimeter path", "aria-label": "Search",
      oninput: (e) => setView({q: e.target.value})}),
    h("span", {}, "Feature ", sel("feature", v.feature, [["", "all"]].concat(features.map((f) => [f || "-", f || "no feature"])))),
    h("span", {}, "Priority ", sel("priority", v.priority, [["", "all"], ["P0", "P0"], ["P1", "P1"], ["P2", "P2"], ["P3", "P3"], ["-", "unprioritised"]])),
    h("span", {}, "Group by ", sel("group", v.group, [["", "nothing"], ["feature", "feature"], ["priority", "priority"]])),
    h("span", {}, "Empty columns ", sel("empty", v.empty, [["", "shown"], ["hide", "hidden"]])),
    h("button", {id: "only-mine", cls: "only-mine", "aria-pressed": v.turn === "you" ? "true" : "false",
      onclick: () => setView({turn: kanbanView().turn === "you" ? "" : "you"})}, "Your turn only")),
    intakesPanel(),
    h("div", {id: "board-area"}));
  return out;
}
// --- the intakes (docs/adr/0090 §8) ---------------------------------------
// One card per intake, above the board: the client's first words, the
// interview so far and the tickets it became, each a link to its card.
// Every value is the logbook's `intakes` field; the page decides nothing.
function intakesList() { return Array.isArray(book.intakes) ? book.intakes : []; }
function intakeWords(i, n) { const w = String(i.text || "").replace(/\s+/g, " ").trim(); return w.length > n ? w.slice(0, n - 1) + "…" : w; }
function intakeChip(i) {
  const st = String(i.state || "");
  if (i.stranded) return chip("NO-GO");
  const cls = st === "confirmed" ? "GO" : st === "intake-questions" || st === "proposed" ? "pending" : "none";
  return h("span", {cls: "chip " + cls}, st);
}
function intakeCounts(i) {
  const qs = i.questions || [];
  return qs.length + " question" + (qs.length === 1 ? "" : "s") + ", " + qs.filter((q) => q.answer).length + " answered" +
    ((i.proposal || []).length ? " · " + i.proposal.length + " proposed" : "");
}
function intakesPanel() {
  if (unread(book.intakes)) return h("section", {id: "intakes"}, h("h2", {}, "Intakes"), h("div", {cls: "panel"}, err(book.intakes)));
  const all = intakesList();
  if (!all.length) return null;
  return h("section", {id: "intakes", "aria-label": "Intakes"}, h("h2", {}, "Intakes"),
    h("div", {cls: "intakes"}, all.map((i) => h("a", {cls: "intake-card", href: "#intake/" + encodeURIComponent(i.id), "data-intake": i.id,
      "aria-label": "Intake " + i.id + ", " + i.state + ": " + intakeWords(i, 80)},
      h("div", {cls: "badges"}, h("b", {}, "Intake " + i.id), intakeChip(i), screenMark(i.screen)),
      h("p", {cls: "words"}, "“" + intakeWords(i, 140) + "”"),
      h("div", {cls: "muted"}, intakeCounts(i)),
      (i.tickets || []).length ? h("div", {cls: "fan"}, "→ ", i.tickets.map((id) => h("span", {cls: "mono"}, id))) : null))));
}
const intakeEdits = {};  // intake id -> [{action, title, problem, criteria, perimeter, feature, priority}]
let intakeLine = null;
function intakeForm(i) {
  if (!intakeEdits[i.id] || intakeEdits[i.id].length !== (i.proposal || []).length) {
    intakeEdits[i.id] = (i.proposal || []).map((t) => ({action: "confirm", title: t.title || "", problem: t.problem || "",
      criteria: (t.criteria || []).join("\n"), perimeter: (t.perimeter || []).join("\n"), feature: t.feature || "", priority: t.priority || ""}));
  }
  return intakeEdits[i.id];
}
function decisions(i) {
  return intakeForm(i).map((e) => e.action !== "edit" ? e.action : {edit: {title: e.title, problem: e.problem,
    criteria: e.criteria.split("\n").map((l) => l.trim()).filter(Boolean),
    perimeter: e.perimeter.split("\n").map((l) => l.trim()).filter(Boolean), feature: e.feature.trim(), priority: e.priority}});
}
async function pressConfirm(i) {
  busy = true; render();
  try {
    const r = await fetch("/v1/intake-confirm", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({intake: i.id, decisions: decisions(i)})});
    const body = await r.json().catch(() => ({}));
    intakeLine = body.line || ("refused (" + r.status + "): " + (body.error || r.statusText));
  } catch (e) { intakeLine = "the console did not answer: " + e; }
  busy = false; delete intakeEdits[i.id]; await load(); render();
}
function proposedTicket(i, t, n, form) {
  const e = form ? form[n] : null;
  const where = "intake-" + i.id + "-" + t.n;
  const pick = e ? h("fieldset", {}, h("legend", {cls: "muted"}, "Ticket " + t.n + ": your decision"),
    ["confirm", "edit", "drop"].map((a) => h("label", {}, h("input", {type: "radio", name: where + "-action", value: a, checked: e.action === a,
      onchange: () => { e.action = a; render(); }}), " " + a))) : null;
  const set = (k) => (ev) => { e[k] = ev.target.value; };
  const edit = e && e.action === "edit" ? h("div", {cls: "edit"},
    h("label", {for: where + "-title"}, "Title"), h("input", {type: "text", id: where + "-title", value: e.title, oninput: set("title")}),
    h("label", {for: where + "-problem"}, "Problem"), h("textarea", {id: where + "-problem", oninput: set("problem")}, e.problem),
    h("label", {for: where + "-criteria"}, "Acceptance criteria, one Given/When/Then per line"), h("textarea", {id: where + "-criteria", oninput: set("criteria")}, e.criteria),
    h("label", {for: where + "-perimeter"}, "Perimeter, one path or glob per line"), h("textarea", {id: where + "-perimeter", oninput: set("perimeter")}, e.perimeter),
    h("label", {for: where + "-feature"}, "Feature (optional)"), h("input", {type: "text", id: where + "-feature", value: e.feature, oninput: set("feature")}),
    h("label", {for: where + "-priority"}, "Priority (optional)"), h("select", {id: where + "-priority", onchange: set("priority")},
      [["", "unprioritised"], ["P0", "P0"], ["P1", "P1"], ["P2", "P2"], ["P3", "P3"]].map(([v, x]) => h("option", {value: v, selected: v === e.priority}, x)))) : null;
  const line = String(t.check || "");
  return h("div", {cls: "proposed", "data-n": t.n},
    h("div", {cls: "badges"}, h("b", {}, t.n + ". " + t.title), t.decision ? h("span", {cls: "chip " + (t.decision === "drop" ? "none" : "GO")}, t.decision) : null),
    h("p", {}, t.problem),
    h("ul", {}, (t.criteria || []).map((c) => h("li", {}, c))),
    dl([["Feature", shown(t.feature)], ["Priority", shown(t.priority)],
      ["Perimeter", (t.perimeter || []).length ? h("span", {cls: "mono"}, t.perimeter.join(", ")) : h("span", {cls: "muted"}, "none proposed: write it before confirming")],
      (t.uncertain || []).length ? ["Uncertain, yours to decide", h("span", {cls: "mono", "data-kind": "uncertain"}, t.uncertain.join(", "))] : null,
      ["Spec gate on the draft", line ? h("span", {}, chip(line.startsWith("GO") ? "GO" : "NO-GO"), " ", h("span", {cls: "mono"}, line)) : "—"],
      ["Ticket", t.ticket ? h("a", {href: "#ticket/" + encodeURIComponent(t.ticket)}, t.ticket) : "—"]]),
    pick, edit);
}
function screenIntake(id) {
  const i = intakesList().find((x) => x.id === id);
  const out = [h("p", {}, h("a", {href: "#kanban"}, "← Kanban"))];
  if (!i) return out.concat(h("h1", {}, "Intake " + id), h("div", {cls: "panel"}, h("p", {cls: "muted"}, "The logbook holds no intake " + id + ".")));
  const roleLine = (r, who) => !r ? null : r.consulted === false
    ? h("p", {cls: "muted"}, "The " + who + " role was not consulted: " + shown(r.why))
    : r.by === "decide"
      ? h("p", {cls: "muted"}, "The " + who + " role asked one yes/no question per file and ticket (ADR 0091): " + shown(r.questions) + " questions, " + shown(r.harness) + (r.replayed ? ", replayed" : "") + ".")
      : h("p", {cls: "muted"}, "The " + who + " role: " + shown(r.harness) + (r.replayed ? ", replayed" : "") + ", " + shown(r.turns) + " turns.");
  const form = i.state === "proposed" && con.mode !== "snapshot" ? intakeForm(i) : null;
  out.push(h("h1", {}, "Intake " + i.id, " ", intakeChip(i)),
    h("p", {cls: "lede"}, "A client's request in their own words, the interview of the product role, the tickets it proposed with the perimeter the architect role proposed, and the person's decision on each (ADR 0090)."),
    i.stranded ? h("div", {cls: "banner err"}, "Stopped at " + i.stranded.step + ": " + i.stranded.error) : null,
    h("div", {cls: "panel"}, h("h2", {}, "The request"), h("pre", {cls: "words"}, i.text),
      h("p", {cls: "muted"}, "By " + shown(i.by) + " via " + shown(i.via) + ", " + shown(i.at) + "."),
      i.screen ? h("p", {cls: "muted", "data-kind": "screen"}, "Screen (ADR 0091): " + screenLine(i.screen) + ".") : null),
    h("div", {cls: "panel"}, h("h2", {}, "The interview"), roleLine(i.product, "product"),
      (i.questions || []).length ? h("ol", {}, i.questions.map((q) => h("li", {}, h("b", {}, q.question),
        q.answer ? h("p", {}, "“" + q.answer + "” — " + shown(q.answered_by) + " via " + shown(q.answered_via)) : h("p", {cls: "muted"}, "Not answered yet."))))
        : h("p", {cls: "muted"}, "No question asked.")),
    answerPanel(i));
  if ((i.proposal || []).length) {
    out.push(h("div", {cls: "panel", id: "proposal"}, h("h2", {}, "Proposed tickets"), roleLine(i.architect, "architect"),
      i.proposal.map((t, n) => proposedTicket(i, t, n, form)),
      form ? h("div", {cls: "buttons"}, h("button", {cls: "go", id: "confirm-intake", disabled: busy,
        onclick: () => pressConfirm(i)}, "Confirm these decisions — ./go intake-confirm TICKET=" + i.id)) : null,
      i.state === "proposed" && con.mode === "snapshot" ? h("p", {cls: "muted"}, "Snapshot: no tower behind it, nothing can be confirmed.") : null,
      form ? h("p", {cls: "muted"}, "Each ticket you keep is written under the next free id and starts at spec-ready: the spec gate, unchanged, opens it or sends it back with its reason.") : null,
      busy ? h("p", {cls: "muted"}, "./go intake-confirm is running…") : null,
      intakeLine ? h("pre", {}, intakeLine) : null));
  }
  if (i.confirmation) out.push(h("div", {cls: "panel"}, h("h2", {}, "Confirmed"),
    h("p", {}, "By " + shown(i.confirmation.by) + " via " + shown(i.confirmation.via) + ", " + shown(i.confirmation.at) + ". "),
    h("p", {cls: "fan"}, (i.tickets || []).length ? ["Tickets: "].concat(i.tickets.map((tid) => h("a", {href: "#ticket/" + encodeURIComponent(tid)}, tid))) : "No ticket was kept.")));
  return out;
}
// The board and the drawer, redrawn in place: the controls keep their focus.
function drawBoard() {
  const area = document.getElementById("board-area");
  if (!area || !book || !Array.isArray(book.kanban)) return;
  const v = kanbanView();
  const byId = Object.fromEntries(ticketsList().map((t) => [t.id, t]));
  const keep = (t) => (!v.feature || (t.feature || "-") === v.feature) && (!v.priority || (t.priority || "-") === v.priority) && searched(t, v.q)
    && (v.turn !== "you" || now(t).turn === "you");
  const mine = document.getElementById("only-mine");
  if (mine) mine.setAttribute("aria-pressed", v.turn === "you" ? "true" : "false");
  const group = v.group === "feature" || v.group === "priority" ? v.group : "";
  const lanes = group ? [...new Set(ticketsList().filter(keep).map((t) => t[group] || ""))]
    .sort((a, b) => (a || "~").localeCompare(b || "~")) : [null];
  const focused = document.activeElement && document.activeElement.classList.contains("card") ? document.activeElement.dataset.id : null;
  const kids = [], held = new Set();
  for (const lane of lanes) {
    const inLane = (t) => keep(t) && (lane === null || (t[group] || "") === lane);
    // Hiding the empty columns is a view, like the filters: the order of
    // the columns left is still the tower's.
    const cols = book.kanban.map((col) => [col.state, col.tickets.filter((id) => byId[id] && inLane(byId[id]))])
      .filter(([, ids]) => !v.empty || ids.length);
    const column = ([state, ids]) => {
      const shut = v.collapsed.has(state);
      const toggle = () => { const c = new Set(kanbanView().collapsed); if (c.has(state)) c.delete(state); else c.add(state); setView({collapsed: c}); };
      // A column with no card in this view is drawn narrow (docs/adr/0063
      // §1): a drawing rule, not part of the view or the link.
      return h("div", {cls: "col" + (shut ? " collapsed" : ids.length ? "" : " empty"), "data-state": state},
        h("h3", {}, h("button", {cls: "col-toggle", onclick: toggle, "aria-expanded": shut ? "false" : "true",
          title: (shut ? "Expand " : "Collapse ") + state}, shut ? "+" : "−"), state + " · " + ids.length),
        shut ? null : ids.map((id) => card(byId[id], v)));
    };
    // The columns stay in the logbook's order; consecutive columns of one
    // band sit under its name.
    const runs = [];
    for (const c of cols) {
      const b = bandOf(c[0]);
      if (c[1].length) held.add(b);
      if (!runs.length || runs[runs.length - 1].band !== b) runs.push({band: b, cols: []});
      runs[runs.length - 1].cols.push(c);
    }
    const board = h("div", {cls: "board"}, runs.map((r) => {
      const wide = r.cols.some(([state, ids]) => ids.length && !v.collapsed.has(state));
      return h("div", {cls: "band" + (wide ? " wide" : ""), "data-band": r.band,
        style: wide ? "flex-grow: " + r.cols.filter(([state, ids]) => ids.length && !v.collapsed.has(state)).length : null},
        h("div", {cls: "band-name"}, bandName(r.band)), h("div", {cls: "band-cols"}, r.cols.map(column)));
    }));
    kids.push(h("div", {cls: "lane", "data-lane": lane === null ? "" : lane},
      lane === null ? null : h("h2", {}, lane || (group === "priority" ? "unprioritised" : "no feature")),
      h("div", {cls: "panel board-wrap"}, board)));
  }
  // On a phone the stack is long: a bar of the bands that hold a card, each
  // button scrolling to its band; no hash, no history entry (docs/adr/0068 §5).
  const jump = BANDS.concat([{band: "other", name: "Other"}]).filter((b) => held.has(b.band));
  kids.unshift(h("div", {cls: "band-jump", role: "navigation", "aria-label": "Jump to a band"}, jump.map((b) =>
    h("button", {"data-band": b.band, onclick: () => {
      const el = area.querySelector('.band[data-band="' + b.band + '"]');
      if (el) el.scrollIntoView({block: "start", behavior: still() ? "auto" : "smooth"});
    }}, b.name))));
  const drop = launchDrop();
  if (drop) kids.unshift(drop);
  area.replaceChildren(...kids);
  fly();
  showBack(v.card ? byId[v.card] : null);
  if (focused && !v.card) focusCard(focused);
}
// The back is one dialog, outside the board, opened modal: the board behind
// it is inert, and Esc or the backdrop closes it, giving the focus back to
// the card.
function showBack(t) {
  const d = document.getElementById("card-back");
  if (!t) {
    if (d.open) d.close();
    d.replaceChildren();
    return;
  }
  const inside = d.open && d.contains(document.activeElement);
  d.replaceChildren(drawer(t));
  if (!d.open) d.showModal();
  else if (inside || !d.contains(document.activeElement)) { const c = d.querySelector("button.close"); if (c) c.focus(); }
}
(function () {
  const d = document.getElementById("card-back");
  d.addEventListener("cancel", (e) => { e.preventDefault(); const id = kanbanView().card; if (id) closeBack(id); });
  // A click on the backdrop lands on the dialog itself: its content fills it.
  d.addEventListener("click", (e) => { if (e.target === d) { const id = kanbanView().card; if (id) closeBack(id); } });
})();
function focusCard(id) {
  const el = document.querySelector('.card[data-id="' + CSS.escape(id) + '"]');
  if (el) el.focus();
}
// Arrows between cards: left and right across the lane's columns that hold
// cards, up and down inside one.
function moveFocus(el, key) {
  const col = el.closest(".col"), lane = el.closest(".lane");
  if (!col || !lane) return;
  const cols = [...lane.querySelectorAll(".col")].filter((c) => c.querySelector(".card"));
  const cards = [...col.querySelectorAll(".card")];
  const i = cards.indexOf(el), c = cols.indexOf(col);
  let next = null;
  if (key === "ArrowUp") next = cards[Math.max(0, i - 1)];
  else if (key === "ArrowDown") next = cards[Math.min(cards.length - 1, i + 1)];
  else {
    const to = cols[key === "ArrowLeft" ? c - 1 : c + 1];
    if (to) { const there = [...to.querySelectorAll(".card")]; next = there[Math.min(i, there.length - 1)]; }
  }
  if (next) next.focus();
}
document.addEventListener("keydown", (e) => {
  if (route().screen !== "kanban" || document.getElementById("why").open || document.getElementById("launch-confirm").open) return;
  const el = e.target;
  const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT");
  if (e.key === "Escape" && kanbanView().card) {
    const id = kanbanView().card;
    e.preventDefault(); closeBack(id);
  } else if (kanbanView().card) {
    return;  // the back is open: its own controls take the keys
  } else if (e.key === "/" && !typing) {
    e.preventDefault(); const q = document.getElementById("kanban-q"); if (q) q.focus();
  } else if (el && el.classList && el.classList.contains("card")) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setView({card: el.dataset.id}); }
    else if (e.key.startsWith("Arrow")) { e.preventDefault(); moveFocus(el, e.key); }
  }
});

// --- the governed network (docs/adr/0071 §2-§5) -----------------------------
// One ticket's network from the logbook alone: the tower, the six roles, the
// attempt's workers, each gate's verdict, the review, the rework loop, the
// canary stages and the human gate; beside it the stream of attempts with
// their gate lines and patches; below, the files by perimeter. Every fact is
// a sentence of #network-words, the table core/network.py prints with
// --table: `./go network` prints the same facts in the same words, and ./go
// ci proves the copy equal. Nothing here writes, and no node moves by hand.
const NET = JSON.parse(document.getElementById("network-words").textContent);
const nw = (key, kw) => NET.words[key].replace(/\{(\w+)\}/g, (m, k) => String(kw[k] === null || kw[k] === undefined ? "None" : kw[k]));
const NET_KEYS = ["attempt", "replay"];
// Provenance, decision: one live poll (docs/adr/0047 §4), so the 400 ms
// flight and the 4 s mark keep their meaning (docs/adr/0071 §3).
const REPLAY_BEAT_MS = 2000;
// Provenance, decision: docs/adr/0059 §1, two beats of the 2 s poll.
const NET_MARK_MS = 4000;
let replay = null;  // {ticket, row, timer} while a replay runs
let netMarks = {};  // fact text -> until, for the ticket shown
let netShown = null;  // {ticket, facts} the screen last drew

function netMinor(b) { const m = /\.(\d+)$/.exec(String(b.schema || "")); return m ? Number(m[1]) : 0; }
function netDefault(b) {
  let best = null;
  for (const t of Array.isArray(b.tickets) ? b.tickets : []) {
    const key = [t.updated_at !== null && t.updated_at !== undefined, t.updated_at || "", t.id];
    if (!best || key[0] > best.key[0] || (key[0] === best.key[0] && (key[1] > best.key[1] || (key[1] === best.key[1] && key[2] > best.key[2])))) best = {key, id: t.id};
  }
  return best ? best.id : null;
}
function netAttempts(t) {
  const s = new Set();
  for (const x of t.tasks || []) if (x && Number.isInteger(x.attempt)) s.add(x.attempt);
  for (const r of t.rework || []) if (r && Number.isInteger(r.attempt)) s.add(r.attempt);
  if (Number.isInteger(t.attempt)) s.add(t.attempt);
  return [...s].sort((a, b) => a - b);
}
function netTask(t, n) {
  const x = (t.tasks || []).find((y) => y && y.attempt === n);
  if (x) return x;
  return n !== null && n === t.attempt && t.task && typeof t.task === "object" ? t.task : null;
}
const netRework = (t, n) => (t.rework || []).find((r) => r && r.attempt === n) || null;
function netJudge(t, n) {
  const r = netRework(t, n);
  if (r) return r;
  return t.ci && typeof t.ci === "object" && n === t.attempt ? t.ci : null;
}
function netWorkers(task) {
  if (!task || typeof task !== "object") return [];
  if (Array.isArray(task.members)) return task.members.map((m) => ({id: m.id, harness: m.harness, model: m.model, replayed: m.replayed === true, hand_written: m.hand_written === true}));
  return [{id: task.role || "dev-worker", harness: task.harness, model: task.model, replayed: task.replayed === true, hand_written: task.hand_written === true}];
}
function netWorkerFact(m) {
  return nw("worker", {id: m.id, harness: m.harness || "unknown", model: m.model || nw("no_model", {})}) +
    (m.replayed ? nw("replayed", {}) : "") + (m.hand_written ? nw("hand_written", {}) : "");
}
function netGates(t, n) {
  const judge = netJudge(t, n);
  const rows = judge && Array.isArray(judge.gates) ? judge.gates : null;
  if (rows === null && judge) {
    const back = netRework(t, n);
    if (back && back.judge !== "gates") return [{gate: "gates", verdict: "GO", text: nw("gates_summary", {line: "GO"})}];
    const v = back ? "NO-GO" : judge.verdict;
    return [{gate: "gates", verdict: v === "GO" || v === "NO-GO" ? v : "not-run", text: nw("gates_summary", {line: judge.line || "—"})}];
  }
  const out = [];
  let failed = false;
  for (const g of rows || []) {
    if (g.gate === null || g.gate === undefined) { out.push({gate: null, verdict: null, text: nw("gate_off", {line: g.line})}); continue; }
    failed = failed || g.verdict === "NO-GO";
    out.push({gate: g.gate, verdict: g.verdict, text: nw("gate", {gate: g.gate, verdict: g.verdict})});
  }
  const ran = new Set(out.map((g) => g.gate));
  for (const g of NET.gates) if (!ran.has(g)) out.push({gate: g, verdict: "not-run", text: nw(failed ? "not_run" : "not_run_yet", {gate: g})});
  return out;
}
function netRolesRan(t) {
  const s = new Set((t.tasks || []).map((x) => x && x.role));
  for (const k of ["task", "review"]) if (t[k] && typeof t[k] === "object") s.add(t[k].role);
  if (t.product && t.product.consulted === true) s.add("product");
  return s;
}
// The ticket's release in the release log: its ticket and its version, the last one.
function netRelease(b, t) {
  const version = t.release && typeof t.release === "object" ? t.release.version : null;
  const mine = (Array.isArray(b.releases) ? b.releases : []).filter((r) => r && r.ticket === t.id && version && r.version === version);
  return mine.length ? mine[mine.length - 1] : null;
}
function netHuman(rel) {
  const a = rel && rel.answer;
  if (a && typeof a === "object" && a.answer) return {verdict: a.answer, who: a.by + " · " + a.via, text: nw("human", {answer: a.answer, by: a.by, via: a.via})};
  if (rel && rel.verdict === "held") return {verdict: "waiting", text: nw("human_waiting", {})};
  if (rel && rel.verdict === "rolled-back") return {verdict: "not-reached", text: nw("human_rolled_back", {})};
  return {verdict: "not-reached", text: nw("human_none", {})};
}
function netReview(t, n) {
  const back = netRework(t, n);
  if (back && back.judge === "review") return {verdict: "changes-requested", text: nw("review_back", {attempt: n})};
  const r = t.review;
  if (n === t.attempt && r && typeof r === "object" && r.verdict) return {verdict: r.verdict, text: nw("review", {verdict: r.verdict, role: r.role || "the reviewer"})};
  return {verdict: "not-reached", text: n !== null ? nw("review_none", {attempt: n}) : nw("review_none_yet", {})};
}
const netJoined = (v) => v === null || v === undefined ? null : (v.length ? v.join(", ") : nw("none", {}));
// The same facts as core/network.py's view(), in the same order.
function netView(b, tid, n) {
  const t = (Array.isArray(b.tickets) ? b.tickets : []).find((x) => x.id === tid);
  const ns = netAttempts(t);
  if (n === null) n = ns.length ? ns[ns.length - 1] : null;
  const old = netMinor(b) < 3;
  let title = t.title || tid;
  if (String(title).startsWith(tid)) title = String(title).slice(tid.length).replace(/^[ —]+/, "");
  const v = {ticket: tid, attempt: n, old, t,
    heading: n !== null ? nw("heading", {id: tid, title, attempt: n}) : nw("heading_none", {id: tid, title})};
  const g = [{kind: "tower", key: "tower", text: nw("tower", {state: t.state})}];
  const ran = netRolesRan(t);
  for (const r of NET.roles) g.push({kind: "role", key: "role:" + r, role: r, ran: ran.has(r), text: nw(ran.has(r) ? "role_ran" : "role_idle", {role: r})});
  if (n !== null) {
    const ws = netWorkers(netTask(t, n));
    if (ws.length) for (const m of ws) g.push(Object.assign({kind: "worker", key: "worker:" + n + ":" + m.id, text: netWorkerFact(m)}, m));
    else g.push({kind: "worker", key: "worker:" + n, id: null, text: nw("no_worker", {attempt: n})});
  }
  // With no attempt yet every gate is still drawn, none of them run.
  for (const e of netGates(t, n)) g.push(Object.assign({kind: "gate", key: "gate:" + n + ":" + e.gate, attempt: n}, e));
  g.push(Object.assign({kind: "review", key: "review"}, netReview(t, n)));
  const rework = (t.rework || []).filter((r) => r && typeof r === "object");
  if (rework.length) {
    const mx = reworkMax();
    g.push({kind: "rework", key: "rework", count: rework.length, max: mx,
      text: mx !== null ? nw("rework", {count: rework.length, max: mx}) : nw("rework_nomax", {count: rework.length})});
  }
  const rel = netRelease(b, t);
  const stages = ((rel && rel.stages) || []).filter((s) => s && typeof s === "object");
  stages.forEach((s, i) => g.push({kind: "stage", key: "stage:" + i, pct: s.pct, verdict: String(s.verdict || "").startsWith("GO") ? "GO" : "NO-GO",
    text: nw("stage", {pct: s.pct, verdict: s.verdict})}));
  if (!stages.length) g.push({kind: "stage", key: "stage:none", pct: null, verdict: null, text: nw("stage_none", {})});
  g.push(Object.assign({kind: "human", key: "human"}, netHuman(rel)));
  v.graph = g;

  const stream = [];
  const version = String(b.schema || "").split("/").pop();
  if (old) stream.push({kind: "old", key: "old", text: nw("old_logbook", {version})});
  for (const k of ns.slice().reverse()) {
    const task = netTask(t, k);
    const who = netWorkers(task).map((m) => m.id).join(", ") || nw("who_unknown", {});
    const e = {kind: "attempt", key: "attempt:" + k, attempt: k, text: nw("attempt", {attempt: k, who}), items: []};
    for (const f of (task && task.files) || []) {
      const text = f.added === null || f.added === undefined ? nw("file_binary", {path: f.path}) : nw("file", {path: f.path, added: f.added, removed: f.removed});
      e.items.push({kind: "file", key: "attempt:" + k, path: f.path, text: text + (f.member ? nw("file_by", {member: f.member}) : "")});
    }
    const judge = netJudge(t, k);
    for (const r of (judge && judge.gates) || []) e.items.push({kind: "line", key: "gate:" + k + ":" + r.gate, gate: r.gate, verdict: r.verdict, text: r.line});
    const back = netRework(t, k);
    if (back) e.items.push({kind: "sent-back", key: "rework", text: nw("attempt_sent_back", {line: back.line})});
    const p = !old && task ? task.patch : null;
    if (p && unread(p)) e.items.push({kind: "patch", key: "attempt:" + k, text: p.error});
    else if (p && typeof p === "object") e.items.push({kind: "patch", key: "attempt:" + k,
      text: p.truncated ? nw("patch_cut", {bytes: p.bytes, shown: new TextEncoder().encode(p.text).length}) : nw("patch", {bytes: p.bytes})});
    else if (!old) e.items.push({kind: "patch", key: "attempt:" + k, text: nw("patch_none", {})});
    e.patch = p && typeof p === "object" && !unread(p) ? p : null;
    e.withheld = p && unread(p) ? p.error : null;
    stream.push(e);
  }
  if (!ns.length) stream.push({kind: "none", key: "none", text: nw("no_attempt", {})});
  v.stream = stream;

  const files = old ? [{kind: "old", text: nw("old_logbook", {version})}] : [];
  if (unread(b.adrs)) files.push({kind: "unread", text: nw("unread", {what: "the project map", error: b.adrs.error})});
  if (unread(b.perimeters_held)) files.push({kind: "unread", text: nw("unread", {what: "who holds which files", error: b.perimeters_held.error})});
  let globs = t.perimeter;
  const rows = Array.isArray(t.files) ? t.files : [];
  if (unread(globs)) { files.push({kind: "unread", text: nw("unread", {what: "the perimeter", error: globs.error})}); globs = []; }
  const all = (globs || []).concat(rows.some((r) => r.glob === null) ? [null] : []);
  for (const glob of all) {
    const mine = rows.filter((r) => r.glob === glob);
    const label = glob !== null ? glob : nw("outside", {});
    if (!mine.length) { files.push({kind: "glob", glob, text: nw(old ? "perimeter" : "perimeter_empty", {glob: label})}); continue; }
    files.push({kind: "glob", glob, text: nw("perimeter", {glob: label})});
    for (const r of mine) {
      const first = r.attempts && r.attempts.length ? r.attempts[0] : null;
      files.push({kind: "file", row: r, key: "attempt:" + first, parts: {
        path: r.path, member: r.member || nw("single", {}), attempts: netJoined(r.attempts),
        tests: netJoined(r.tests) !== null ? netJoined(r.tests) : nw("unknown_to_map", {}),
        adrs: r.adrs !== null && r.adrs !== undefined ? netJoined(r.adrs) : nw("unknown_to_map", {}),
        held: r.held_by !== null && r.held_by !== undefined ? netJoined(r.held_by) : nw("none", {})}});
      const f = files[files.length - 1];
      f.text = nw("file_row", f.parts);
    }
  }
  if (Array.isArray(t.adrs)) files.push({kind: "adrs", text: nw("perimeter_adrs", {adrs: netJoined(t.adrs)})});
  v.files = files;
  return v;
}

// The row of the ticket's timeline that reveals each key in a replay.
function netReveal(t) {
  const rows = Array.isArray(t.timeline) ? t.timeline : [];
  const at = {tower: 0}, canary = [];
  const first = (key, i) => { if (!(key in at)) at[key] = i; };
  let attemptNow = null;
  rows.forEach((r, i) => {
    if (!r) return;
    if (Number.isInteger(r.attempt)) attemptNow = r.attempt;
    if (r.phase === "assign" || r.phase === "attempt" || r.phase === "rework") {
      first("role:dev-worker", i);
      const n = Number.isInteger(r.attempt) ? r.attempt : (attemptNow || 1);
      first("attempt:" + n, i);
      first("worker:" + n, i);
      if (r.phase === "rework") first("rework", i);
    }
    if (r.phase === "gates") { const n = Number.isInteger(r.attempt) ? r.attempt : attemptNow; first("gates:" + n, i); }
    if (r.phase === "review") { first("review", i); first("role:test-reviewer", i); }
    if (r.phase === "product") first("role:product", i);
    if (r.phase === "canary") canary.push(i);
    if (r.phase === "release") first("release", i);
    if (r.phase === "sign-off") first("human", i);
  });
  canary.forEach((i, k) => { at["stage:" + k] = i; });
  return (key) => {
    if (!key) return 0;
    if (key in at) return at[key];
    const m = /^(worker|gate):(\d+)(?::|$)/.exec(key);
    if (m) { const k = (m[1] === "gate" ? "gates:" : "worker:") + m[2]; if (k in at) return at[k]; }
    if (key === "human" && "release" in at) return at.release;
    if (key.startsWith("role:")) return 0;
    return rows.length;
  };
}

function svg(tag, attrs, ...kids) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined && v !== false) el.setAttribute(k === "cls" ? "class" : k, v === true ? "" : v);
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
const netLabel = (x, y, s, cls, anchor) => svg("text", {x, y, "text-anchor": anchor || "middle", cls}, s);
// The graph: fixed slots in a 760 x 340 drawing; the tower at the centre,
// the six roles above it, the lane the change runs along below it.
function netGraph(v) {
  const roles = v.graph.filter((f) => f.kind === "role"), workers = v.graph.filter((f) => f.kind === "worker" && f.id !== null);
  const gates = v.graph.filter((f) => f.kind === "gate"), stages = v.graph.filter((f) => f.kind === "stage" && f.pct !== null);
  const review = v.graph.find((f) => f.kind === "review"), rework = v.graph.find((f) => f.kind === "rework"), human = v.graph.find((f) => f.kind === "human");
  const lane = 270, out = [];
  const tower = svg("g", {id: "net-tower", "data-key": "tower"}, svg("circle", {cls: "node", cx: 380, cy: 150, r: 38}), netLabel(380, 147, "tower"), netLabel(380, 163, v.t.state, "sub"));
  // Spokes: the tower requests every step (docs/adr/0019).
  roles.forEach((r, i) => out.push(svg("line", {cls: "spoke", x1: 380, y1: 112, x2: 68 + i * 125, y2: 52})));
  for (const x of [70, 300, 520, 610, 712]) out.push(svg("line", {cls: "spoke", x1: 380, y1: 188, x2: x, y2: lane - 20}));
  roles.forEach((r, i) => out.push(svg("g", {cls: "net-role", "data-role": r.role, "data-ran": String(r.ran), "data-key": r.key},
    svg("rect", {cls: "node", x: 10 + i * 125, y: 22, width: 116, height: 30, rx: 6}), netLabel(68 + i * 125, 41, r.role))));
  // Workers, at most four (docs/adr/0036): stacked at the lane's start.
  const top = lane - (workers.length - 1) * 17;
  workers.forEach((m, i) => out.push(svg("g", {cls: "net-worker", "data-worker": m.id, "data-harness": m.harness || "", "data-model": m.model || "",
    "data-replayed": String(m.replayed), "data-hand-written": String(m.hand_written), "data-key": m.key},
    svg("rect", {cls: "node", x: 6, y: top + i * 34 - 14, width: 150, height: 28, rx: 6}),
    netLabel(14, top + i * 34 - 2, m.id, null, "start"), netLabel(14, top + i * 34 + 10, (m.harness || "unknown") + " · " + (m.model || nw("no_model", {})), "sub", "start"))));
  // The gates along the lane, in the order they ran, then those that did not.
  const x0 = 182, step = gates.length > 1 ? 270 / (gates.length - 1) : 0;
  out.push(svg("line", {cls: "wire", x1: 156, y1: lane, x2: 712, y2: lane}));
  gates.forEach((g, i) => {
    const x = x0 + i * step;
    const node = svg("g", {cls: "net-gate", "data-gate": g.gate || "", "data-verdict": g.verdict || "", "data-attempt": g.attempt === null ? "" : String(g.attempt), "data-key": g.key},
      svg("line", {cls: "seg", x1: i ? x - step : 156, y1: lane, x2: x, y2: lane}),
      svg("circle", {cls: "dot", cx: x, cy: lane, r: 8}),
      svg("text", {x, y: lane + 22, "text-anchor": "end", transform: "rotate(-35 " + x + " " + (lane + 22) + ")", cls: "sub"}, g.gate || "?"));
    out.push(g.verdict === "NO-GO" && Number.isInteger(g.attempt) ? whyFact(node, v.t, g.attempt) : node);
  });
  if (review) out.push(svg("g", {id: "net-review", "data-verdict": review.verdict, "data-key": "review"},
    svg("rect", {cls: "node", x: 482, y: lane - 15, width: 76, height: 30, rx: 6}), netLabel(520, lane + 4, "review", "v")));
  stages.forEach((s, i) => {
    const x = 590 + i * (stages.length > 1 ? 60 / (stages.length - 1) : 0);
    out.push(svg("g", {cls: "net-stage", "data-pct": String(s.pct), "data-verdict": s.verdict || "", "data-key": s.key},
      svg("circle", {cls: "dot", cx: x, cy: lane, r: 15}), svg("text", {x, y: lane + 4, "text-anchor": "middle", cls: "v"}, s.pct + "%")));
  });
  if (human) out.push(svg("g", {id: "net-human", "data-verdict": human.verdict, "data-key": "human"},
    svg("rect", {cls: "node", x: 682, y: lane - 16, width: 64, height: 32, rx: 16}), svg("text", {x: 714, y: lane + 4, "text-anchor": "middle", cls: "v"}, "human"),
    human.who ? netLabel(714, lane + 34, human.who, "sub") : null));
  const failed = gates.findIndex((g) => g.verdict === "NO-GO");
  const loopFrom = failed >= 0 ? x0 + failed * step : x0 + 150;
  if (rework) out.push(svg("g", {id: "net-rework", "data-count": String(v.reworkSoFar === undefined ? rework.count : v.reworkSoFar), "data-max": rework.max === null ? null : String(rework.max), "data-key": "rework"},
    // From the gate that said NO-GO on this attempt, when one did.
    svg("path", {d: "M " + loopFrom + " " + (lane - 12) + " C " + (loopFrom - 30) + " " + (lane - 100) + ", 110 " + (lane - 100) + ", 80 " + (top - 18)}),
    netLabel(x0 + 12, lane - 86, "rework " + rework.count + (rework.max !== null ? " of " + rework.max : ""))));
  return svg("svg", {viewBox: "0 0 760 340", role: "img", "aria-labelledby": "net-facts-title"}, out, tower);
}
// A sentence of the table with some of its values drawn as elements: the
// text stays the sentence, letter for letter.
function netSentence(key, parts, render) {
  const marks = {}, kw = {};
  for (const [k, val] of Object.entries(parts)) { if (render[k]) { marks["\u0000" + k + "\u0000"] = k; kw[k] = "\u0000" + k + "\u0000"; } else kw[k] = val; }
  return nw(key, kw).split(/(\u0000\w+\u0000)/).filter(Boolean).map((piece) => marks[piece] ? render[marks[piece]](parts[marks[piece]]) : piece);
}
function netFact(tag, f, attrs, ...kids) {
  return h(tag, Object.assign({cls: "net-fact", "data-key": f.key || null}, attrs || {}), kids.length ? kids : f.text);
}
function screenNetwork(arg) {
  const tickets = ticketsList();
  const q = route().query;
  if (!tickets.length) return [h("h1", {}, "Network"), h("div", {cls: "panel"}, unread(book.tickets) ? err(book.tickets) : "No ticket in the logbook yet.")];
  const tid = arg || netDefault(book);
  const t = tickets.find((x) => x.id === tid);
  if (!t) return [h("h1", {}, "Network"), h("div", {cls: "panel"}, "The logbook holds no ticket " + tid + ".")];
  // `replay=1` in the link starts the replay once the screen is drawn.
  if (q.replay === "1" && !(replay && replay.ticket === tid)) startReplay(tid, false);
  const ns = netAttempts(t);
  const n = q.attempt && ns.includes(Number(q.attempt)) ? Number(q.attempt) : null;
  const v = netView(book, tid, n);
  const playing = replay && replay.ticket === tid;
  const reveal = netReveal(t), row = playing ? replay.row : null;
  const pending = (key) => row !== null && !replay.done && reveal(key) >= row ? true : null;
  const select = h("select", {id: "net-ticket", "aria-label": "Ticket",
    onchange: (e) => { location.hash = "#network/" + encodeURIComponent(e.target.value); }},
    tickets.map((x) => h("option", Object.assign({value: x.id}, x.id === tid ? {selected: true} : {}), named(x))));
  // The rework count of a replay counts only the rework rows revealed so far.
  // The graph of a replay draws the attempt of the last row revealed, so
  // an attempt the gates sent back lights its NO-GO before the next one
  // runs; its facts are the final drawing's, revealed row by row.
  let drawn = v;
  if (playing && !replay.done) {
    const seen = (t.timeline || []).slice(0, replay.row).filter((r) => r && Number.isInteger(r.attempt));
    const at = seen.length ? seen[seen.length - 1].attempt : (ns.length ? ns[0] : null);
    if (at !== v.attempt && ns.includes(at)) drawn = netView(book, tid, at);
    drawn.reworkSoFar = (t.timeline || []).slice(0, replay.row).filter((r) => r && r.phase === "rework").length;
  }
  const graph = netGraph(drawn);
  for (const el of graph.querySelectorAll("[data-key]")) if (pending(el.getAttribute("data-key"))) el.setAttribute("data-pending", "");
  // In a replay a fact is drawn once its row is revealed; the graph's
  // nodes stay in place, dimmed until then (docs/adr/0071 §3).
  const shown = (f) => !pending(f.key);
  const facts = h("ul", {cls: "net-facts", id: "net-facts"}, v.graph.filter(shown).map((f) => netFact("li", f)));
  const stream = h("ol", {id: "net-stream"}, v.stream.filter(shown).map((e) => e.kind !== "attempt"
    ? h("li", {cls: "net-note"}, netFact("p", e))
    : h("li", {cls: "net-attempt", "data-attempt": String(e.attempt)},
      netFact("div", e),
      h("ul", {}, e.items.filter(shown).map((i) => i.kind === "line"
        ? (i.verdict === "NO-GO" ? whyFact : (el) => el)(netFact("li", i, {cls: "net-fact net-line", "data-gate": i.gate || "", "data-verdict": i.verdict || ""}), t, e.attempt)
        : netFact("li", i))),
      e.patch || e.withheld ? h("details", Object.assign({cls: "net-patch", "data-truncated": e.patch ? String(e.patch.truncated === true) : "false"},
        e.attempt === v.attempt ? {open: true} : {}),
        h("summary", {}, "The patch of attempt " + e.attempt), h("pre", {}, e.patch ? e.patch.text : e.withheld)) : null)));
  const rows = [];
  for (const f of v.files) {
    if (f.kind === "file" && !shown(f)) continue;
    if (f.kind === "file") {
      const r = f.row;
      rows.push(h("tr", {cls: "net-file", "data-path": r.path, "data-glob": r.glob === null ? "" : r.glob, "data-member": r.member || "",
        "data-held-by": (r.held_by || []).join(",")},
        h("td", {cls: "net-fact", "data-key": f.key}, netSentence("file_row", f.parts, {
          tests: (s) => h("span", {cls: "net-tests mono"}, s),
          adrs: (s) => h("span", {cls: "net-adrs"}, (r.adrs || []).length ? r.adrs.flatMap((a, i) => [i ? ", " : null, h("a", {href: "#adr/" + a}, a)]) : s)}))));
    } else rows.push(h("tr", {cls: f.kind === "glob" ? "net-glob" : "net-note"}, h("td", {cls: "net-fact"}, f.text)));
  }
  const live = con.mode === "live";
  const state = replay && replay.ticket === tid ? (replay.done ? "done" : "on") : "off";
  const sec = h("section", {id: "net", "data-ticket": tid, "data-schema": String(book.schema || ""), "data-replay": state,
      "data-row": playing ? String(replay.row) : null},
    h("div", {cls: "net-top"}, h("h1", {}, drawn.heading),
      h("label", {cls: "meta"}, "Ticket ", select),
      h("button", {type: "button", id: "net-replay", onclick: () => startReplay(tid)}, playing && !replay.done ? "Replaying…" : "Replay"),
      h("span", {id: "net-replay-label", role: "status"}, "Replay of the recorded logbook: nothing is running")),
    h("p", {cls: "lede"}, "The agent network of one ticket, from the logbook alone: the tower, the six roles, the attempt's workers, each gate's verdict, the rework loop, the canary and the one human gate; beside it every attempt with the lines that judged it and its patch; below, the files by perimeter. ",
      live ? "Live: a node lights up when the logbook moves it." : "A snapshot: press Replay to watch the record unfold.", " The same facts in text: ", h("code", {}, "./go network TICKET=" + tid)),
    h("div", {cls: "net-cols"},
      h("div", {cls: "panel net-graph"}, h("h2", {id: "net-facts-title"}, "The governed network, attempt " + (drawn.attempt === null ? "—" : drawn.attempt)),
        ns.length > 1 ? h("p", {cls: "meta"}, "Attempts: ", ns.flatMap((k, i) => [i ? " · " : null, k === v.attempt ? h("b", {}, String(k))
          : h("a", {href: "#network/" + encodeURIComponent(tid) + (k === ns[ns.length - 1] ? "" : "?attempt=" + k)}, String(k))])) : null,
        graph, facts),
      h("div", {cls: "panel"}, h("h2", {}, "Attempts, newest first"), stream)),
    h("div", {cls: "panel"}, h("h2", {}, "Files by perimeter"), h("table", {cls: "net-files"}, rows)));
  // The live mark (docs/adr/0071 §3): a fact the last logbook added.
  const now = Date.now();
  for (const el of sec.querySelectorAll(".net-fact")) {
    const until = netMarks[el.textContent.replace(/\s+/g, " ").trim()];
    if (until && until > now) {
      el.classList.add("net-new");
      const k = el.getAttribute("data-key");
      const node = k ? graph.querySelector('[data-key="' + CSS.escape(k) + '"]') : null;
      if (node) node.classList.add("net-new");
    }
  }
  netShown = {ticket: tid, facts: netFactsOf(v)};
  return [sec];
}
function netFactsOf(v) {
  return v.graph.map((f) => f.text).concat(v.stream.flatMap((e) => [e.text].concat((e.items || []).map((i) => i.text))), v.files.map((f) => f.text));
}
// A new logbook in live mode: the facts of the ticket shown that it adds are
// marked for 4 s and said in #announce.
function noteNet(after) {
  if (!netShown || route().screen !== "network") return;
  const t = (Array.isArray(after.tickets) ? after.tickets : []).find((x) => x.id === netShown.ticket);
  if (!t) return;
  let v;
  try { v = netView(after, netShown.ticket, null); } catch (e) { return; }
  const before = new Set(netShown.facts), until = Date.now() + NET_MARK_MS;
  const added = netFactsOf(v).filter((f) => !before.has(f));
  if (!added.length) return;
  for (const f of added) netMarks[f] = until;
  document.getElementById("announce").textContent = netShown.ticket + ": " + added.slice(0, 3).join("; ") + (added.length > 3 ? "; and " + (added.length - 3) + " more" : "") + ".";
  setTimeout(() => { if (route().screen === "network") render(); }, NET_MARK_MS + 50);
}
// The replay (docs/adr/0071 §3): the final record revealed one timeline row
// at a time on the 2 s beat, labelled, with no request until it ends.
function startReplay(tid, draw = true) {
  if (replay && replay.timer) clearInterval(replay.timer);
  const t = ticketsList().find((x) => x.id === tid);
  const total = t && Array.isArray(t.timeline) ? t.timeline.length : 0;
  replay = {ticket: tid, row: 0, total, done: false, timer: null};
  stopEventStream();
  if (draw) render();
  replay.timer = setInterval(() => {
    replay.row += 1;
    if (replay.row >= replay.total) {
      replay.row = replay.total; replay.done = true; clearInterval(replay.timer); replay.timer = null;
      syncEventStream();
    }
    if (route().screen === "network") render();
  }, REPLAY_BEAT_MS);
}
const replaying = () => replay !== null && !replay.done;
