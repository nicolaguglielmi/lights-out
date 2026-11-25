"""Unit tests for Animated Hive UI and Onboarding Wizard contract (ADR 0111).

Strictly vendor-free, app-free, and model-free (ci/vendor-free.sh,
ci/app-free.sh, ci/model-free.sh): validates console/index.html static
invariants and executes its JS screens in Node 24 across golden logbooks
1.2, 1.4, 1.5, and 1.8 with localStorage disabled.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE_HTML = ROOT / "console" / "index.html"

ROLE_NAMES = (
    "product",
    "architect",
    "dev-worker",
    "test-reviewer",
    "release",
    "ops-triage",
)

EXPECTED_GATES = (
    "risk",
    "secrets",
    "test-weakening",
    "deps",
    "sast",
    "test",
    "mutation",
    "test-proof",
)


def _extract_core_js(html: str) -> tuple[str, str, str]:
    tower_m = re.search(r'<script type="application/json" id="tower-machine">(.*?)</script>', html, re.S)
    words_m = re.search(r'<script type="application/json" id="network-words">(.*?)</script>', html, re.S)
    assert tower_m is not None and words_m is not None
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) >= 2
    main_js = scripts[-1]
    boot_idx = main_js.find('window.addEventListener("hashchange"')
    assert boot_idx > 0
    return tower_m.group(1), words_m.group(1), main_js[:boot_idx]


def _run_node_probe(js_body: str) -> str:
    html = CONSOLE_HTML.read_text(encoding="utf-8")
    tower_json, words_json, core_js = _extract_core_js(html)
    harness = f"""
const fs = require("fs");
let activeEl = null;
const idRegistry = new Map();
class FakeNode {{
  constructor(tag) {{
    this.tagName = tag.toUpperCase();
    this.attrs = {{}};
    this.children = [];
    this.dataset = {{}};
    this.listeners = {{}};
    this.classList = {{
      toggle: (cls, force) => {{
        const cur = new Set((this.attrs.class || "").split(/\\s+/).filter(Boolean));
        if (force === undefined) cur.has(cls) ? cur.delete(cls) : cur.add(cls);
        else if (force) cur.add(cls);
        else cur.delete(cls);
        this.attrs.class = [...cur].join(" ");
      }},
      add: (...clss) => {{
        const cur = new Set((this.attrs.class || "").split(/\\s+/).filter(Boolean));
        for (const c of clss) cur.add(c);
        this.attrs.class = [...cur].join(" ");
      }},
      remove: (...clss) => {{
        const cur = new Set((this.attrs.class || "").split(/\\s+/).filter(Boolean));
        for (const c of clss) cur.delete(c);
        this.attrs.class = [...cur].join(" ");
      }},
      contains: (cls) => (this.attrs.class || "").split(/\\s+/).includes(cls),
    }};
    this.style = {{ setProperty: () => {{}} }};
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.open = false;
    this.returnValue = "";
    this._invoker = null;
  }}
  setAttribute(k, v) {{
    this.attrs[k] = String(v);
    if (k === "id") idRegistry.set(String(v), this);
    if (k.startsWith("data-")) this.dataset[k.slice(5)] = String(v);
  }}
  getAttribute(k) {{ return k in this.attrs ? this.attrs[k] : null; }}
  removeAttribute(k) {{ delete this.attrs[k]; }}
  append(...kids) {{
    for (const k of kids) {{
      if (k === null || k === undefined || k === false) continue;
      if (typeof k === "string" || typeof k === "number") this.children.push({{ text: String(k) }});
      else {{ k.parentNode = this; this.children.push(k); }}
    }}
  }}
  replaceChildren(...kids) {{ this.children = []; this.append(...kids); }}
  addEventListener(type, fn) {{ (this.listeners[type] ||= []).push(fn); }}
  dispatchEvent(ev) {{
    ev.target = ev.target || this;
    ev.currentTarget = this;
    ev.preventDefault = ev.preventDefault || (() => {{ ev.defaultPrevented = true; }});
    ev.stopPropagation = ev.stopPropagation || (() => {{}});
    for (const fn of this.listeners[ev.type] || []) fn(ev);
    return !ev.defaultPrevented;
  }}
  click() {{
    const prev = activeEl;
    activeEl = this;
    this.dispatchEvent({{ type: "click" }});
    if (!this.open && prev && this.tagName === "DIALOG") activeEl = prev;
  }}
  focus() {{ activeEl = this; }}
  showModal() {{
    this._invoker = activeEl;
    this.open = true;
    this.attrs.open = "";
    const auto = this.querySelector("[autofocus]");
    if (auto) auto.focus();
  }}
  close(val) {{
    if (val !== undefined) this.returnValue = String(val);
    this.open = false;
    delete this.attrs.open;
    this.dispatchEvent({{ type: "close" }});
    if (this._invoker && typeof this._invoker.focus === "function") this._invoker.focus();
  }}
  getBoundingClientRect() {{ return {{ left: 10, right: 310, top: 10, bottom: 210, width: 300, height: 200 }}; }}
  get isConnected() {{ return true; }}
  _allNodes() {{
    const out = [];
    const walk = (n) => {{
      if (!n || "text" in n) return;
      out.push(n);
      for (const c of n.children || []) walk(c);
    }};
    for (const c of this.children || []) walk(c);
    return out;
  }}
  _matches(sel) {{
    let s = sel.trim();
    const tagM = /^[a-zA-Z][a-zA-Z0-9-]*/.exec(s);
    if (tagM) {{
      if (this.tagName.toLowerCase() !== tagM[0].toLowerCase()) return false;
      s = s.slice(tagM[0].length);
    }}
    while (s.length) {{
      if (s.startsWith("#")) {{
        const m = /^#([A-Za-z0-9_-]+)/.exec(s);
        if (!m || this.id !== m[1]) return false;
        s = s.slice(m[0].length);
      }} else if (s.startsWith(".")) {{
        const m = /^\\.([A-Za-z0-9_-]+)/.exec(s);
        if (!m || !(this.attrs.class || "").split(/\\s+/).includes(m[1])) return false;
        s = s.slice(m[0].length);
      }} else if (s.startsWith("[")) {{
        const m = /^\\[([^=\\]]+)(?:=["']([^"']*)["'])?\\]/.exec(s);
        if (!m) return false;
        if (m[2] === undefined ? !(m[1] in this.attrs) : this.attrs[m[1]] !== m[2]) return false;
        s = s.slice(m[0].length);
      }} else {{
        return false;
      }}
    }}
    return true;
  }}
  querySelectorAll(sel) {{
    const parts = sel.split(",").map((s) => s.trim()).filter(Boolean);
    return this._allNodes().filter((n) => parts.some((p) => n._matches(p)));
  }}
  querySelector(sel) {{ return this.querySelectorAll(sel)[0] || null; }}
  get id() {{ return this.attrs.id || ""; }}
  set id(v) {{ this.setAttribute("id", v); }}
  get className() {{ return this.attrs.class || ""; }}
  set className(v) {{ this.attrs.class = v; }}
  get textContent() {{
    return this.children.map((c) => ("text" in c ? c.text : c.textContent)).join("");
  }}
  set textContent(v) {{ this.replaceChildren(String(v)); }}
}}
const towerJson = {json.dumps(tower_json)};
const nwJson = {json.dumps(words_json)};
const themeEl = new FakeNode("select"); themeEl.id = "theme";
const mainEl = new FakeNode("main"); mainEl.id = "main";
const whoEl = new FakeNode("span"); whoEl.id = "who";
const freshEl = new FakeNode("span"); freshEl.id = "fresh";
const announceEl = new FakeNode("div"); announceEl.id = "announce";
const cardBackEl = new FakeNode("dialog"); cardBackEl.id = "card-back";
const whyEl = new FakeNode("dialog"); whyEl.id = "why";
const launchConfirmEl = new FakeNode("dialog"); launchConfirmEl.id = "launch-confirm";
const wizardEl = new FakeNode("dialog"); wizardEl.id = "onboard-wizard";
const navEl = new FakeNode("nav"); navEl.id = "nav";
const fixedMap = {{
  "theme": themeEl,
  "main": mainEl,
  "who": whoEl,
  "fresh": freshEl,
  "announce": announceEl,
  "card-back": cardBackEl,
  "why": whyEl,
  "launch-confirm": launchConfirmEl,
  "onboard-wizard": wizardEl,
  "tower-machine": {{ textContent: towerJson }},
  "network-words": {{ textContent: nwJson }},
}};
global.document = {{
  documentElement: new FakeNode("html"),
  createElement: (tag) => new FakeNode(tag),
  createElementNS: (_ns, tag) => new FakeNode(tag),
  createTextNode: (t) => ({{ text: String(t) }}),
  getElementById: (id) => fixedMap[id] || mainEl.querySelector("#" + id) || wizardEl.querySelector("#" + id) || idRegistry.get(id) || null,
  querySelectorAll: (sel) => mainEl.querySelectorAll(sel),
  querySelector: (sel) => mainEl.querySelector(sel),
  addEventListener: () => {{}},
  get activeElement() {{ return activeEl; }},
}};
global.Node = FakeNode;
global.CSS = {{ escape: (s) => String(s) }};
global.HTMLDialogElement = function() {{}};
global.HTMLDialogElement.prototype = {{}};
global.location = {{ hash: "#hive" }};
global.history = {{ replaceState: (_a, _b, h) => {{ if (h) global.location.hash = h; }} }};
global.matchMedia = () => ({{ matches: false }});
Object.defineProperty(global, "localStorage", {{ get() {{ throw new Error("storage refused"); }} }});
let fetchCalls = 0;
global.fetch = () => {{ fetchCalls++; return Promise.reject(new Error("no network")); }};
global.navigator = {{ clipboard: {{ writeText: () => Promise.resolve() }} }};
global.setInterval = () => 1;
global.clearInterval = () => {{}};
global.window = global;
""" + core_js + "\n" + js_body

    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as tf:
        tf.write(harness)
        tf_path = tf.name
    try:
        res = subprocess.run(
            ["node", tf_path],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    finally:
        os.unlink(tf_path)
    if res.returncode != 0:
        raise AssertionError(f"Node probe failed ({res.returncode}):\nSTDOUT:{res.stdout}\nSTDERR:{res.stderr}")
    return res.stdout.strip()


class HiveConsolePerimeterTest(unittest.TestCase):
    def test_console_html_perimeter_and_accessibility_styles(self) -> None:
        html = CONSOLE_HTML.read_text(encoding="utf-8")
        # Nav link + overview entrypoint
        self.assertIn('href="#hive"', html)
        self.assertIn('data-s="hive"', html)
        self.assertIn("Open Animated Hive Guide", html)

        # Single-file, no external assets, no banned words, no <form
        self.assertNotIn("<form", html.lower())
        self.assertNotIn("xmlhttprequest", html.lower())
        self.assertNotIn("sendbeacon", html.lower())
        for banned in ("adapters/", ".crew/", "firestore", "state_store"):
            self.assertNotIn(banned, html.lower())
        self.assertNotRegex(html, r'(?i)\b(?:src|href)\s*=\s*["\']https?:')

        # Dialog & reduced-motion CSS
        self.assertIn('<dialog id="onboard-wizard"', html)
        self.assertIn('closedby="any"', html)
        self.assertIn("dialog:modal", html)
        self.assertIn("dialog::backdrop", html)
        self.assertIn("overscroll-behavior: contain", html)
        self.assertIn("@media (prefers-reduced-motion: reduce)", html)

        # Mobile single-line nav safety
        self.assertRegex(html, r"(?s)@media \(max-width: 720px\).*?nav\s*\{[^}]*flex-wrap:\s*nowrap;\s*overflow-x:\s*auto;")


class HiveScreenAndWizardNodeTest(unittest.TestCase):
    def test_hive_renders_across_golden_schemas_and_keeps_your_turn_first(self) -> None:
        if shutil.which("node") is None:
            self.fail("node is not on PATH: this test runs in the workbench, ./go ci")
        out = _run_node_probe(f"""
const roles = {json.dumps(list(ROLE_NAMES))};
const gates = {json.dumps(list(EXPECTED_GATES))};
for (const file of [
  "fixtures/logbook/golden-1.8.json",
  "fixtures/logbook/golden-1.5.json",
  "fixtures/logbook/golden-1.4.json",
  "fixtures/logbook/golden-1.2.json",
]) {{
  book = JSON.parse(fs.readFileSync(file, "utf8"));
  con = {{ mode: "snapshot", read_at: "2026-11-24T00:00:00Z" }};
  global.location.hash = "#overview";
  render();
  const panels = [...mainEl.querySelectorAll(".panel")].map((p) => p.id);
  if (panels[0] !== "your-turn") throw new Error(file + ": first panel is " + panels[0]);
  const guideLink = mainEl.querySelectorAll("a").find((a) => a.getAttribute("href") === "#hive");
  if (!guideLink || !guideLink.textContent.includes("Open Animated Hive Guide")) {{
    throw new Error(file + ": missing Open Animated Hive Guide link on #overview");
  }}

  global.location.hash = "#hive";
  render();
  const svgEl = mainEl.querySelector("#hive-svg");
  if (!svgEl || svgEl.getAttribute("role") !== "img" || !svgEl.getAttribute("aria-label")) {{
    throw new Error(file + ": #hive-svg missing role=img or aria-label");
  }}
  const tower = mainEl.querySelector("[data-role='tower']");
  if (!tower) throw new Error(file + ": missing central Tower hex node");
  for (const r of roles) {{
    if (!mainEl.querySelector(".hive-hex[data-role='" + r + "']")) {{
      throw new Error(file + ": missing role hex " + r);
    }}
  }}
  const phaseBtns = mainEl.querySelectorAll(".hive-phase-btn");
  if (phaseBtns.length !== 7) throw new Error(file + ": expected 7 phases, got " + phaseBtns.length);
  for (const g of gates) {{
    if (!mainEl.querySelector(".hive-gate-led[data-gate='" + g + "']")) {{
      throw new Error(file + ": missing gate LED " + g);
    }}
  }}
  if (!mainEl.querySelector(".hive-canary-arc")) throw new Error(file + ": missing canary arc");
  if (!mainEl.querySelector(".hive-rollback-arc")) throw new Error(file + ": missing rollback arc");
  if (fetchCalls !== 0) throw new Error(file + ": unexpected fetch calls " + fetchCalls);
}}
// Graceful handling of unread/empty logbook fields
book = {{
  schema: "lights-out/logbook/1.8",
  source: {{ commit: null, level: "l0", workload: "synth", code_host: "local" }},
  tickets: {{ ok: false, error: "store down" }},
  releases: {{ ok: false, error: "log missing" }},
  postmortems: [],
  thresholds: [],
  launch: {{ record: null, plane: {{ ok: false, error: "down" }}, brief: null, pressable: false, preparable: false }},
  roles: {{ ok: false, error: "roles unread" }},
  intakes: {{ ok: false, error: "intakes unread" }},
  dora: {{ ok: false, error: "dora unread" }},
  spend: {{ ok: false, error: "spend unread" }},
}};
global.location.hash = "#hive";
render();
if (!mainEl.querySelector("#hive-svg")) throw new Error("unread logbook failed to render #hive-svg");
console.log("SCHEMAS_OK");
""")
        self.assertEqual(out, "SCHEMAS_OK")

    def test_walkthrough_controls_and_junior_expert_toggle_with_localstorage_refused(self) -> None:
        if shutil.which("node") is None:
            self.fail("node is not on PATH: this test runs in the workbench, ./go ci")
        out = _run_node_probe("""
book = JSON.parse(fs.readFileSync("fixtures/logbook/golden-1.8.json", "utf8"));
con = { mode: "snapshot", read_at: "2026-11-24T00:00:00Z" };
global.location.hash = "#hive";
render();

const playBtn = document.getElementById("hive-play");
const stepBtn = document.getElementById("hive-step");
const resetBtn = document.getElementById("hive-reset");
const juniorBtn = document.getElementById("hive-mode-junior");
const expertBtn = document.getElementById("hive-mode-expert");
if (!playBtn || !stepBtn || !resetBtn || !juniorBtn || !expertBtn) {
  throw new Error("missing hive interactive controls");
}
if (playBtn.getAttribute("aria-pressed") !== null) {
  throw new Error("Play/Pause button must not combine changing label with aria-pressed");
}
resetBtn.click();
const svg0 = document.getElementById("hive-svg");
if (svg0.getAttribute("data-active-phase") !== "1") {
  throw new Error("expected Reset to put active phase at 1, got " + svg0.getAttribute("data-active-phase"));
}
stepBtn.click();
if (document.getElementById("hive-svg").getAttribute("data-active-phase") !== "2") {
  throw new Error("expected Step to move to phase 2");
}
playBtn.click();
if (!document.getElementById("hive-play").textContent.includes("Pause")) {
  throw new Error("expected Play click to switch label to Pause");
}
playBtn.click();
if (!document.getElementById("hive-play").textContent.includes("Play")) {
  throw new Error("expected second Play click to pause");
}
const juniorText = document.getElementById("hive-narration").textContent;
expertBtn.click();
const expertText = document.getElementById("hive-narration").textContent;
if (juniorText === expertText) {
  throw new Error("Junior and Expert modes produced identical narration");
}
if (document.getElementById("hive-mode-expert").getAttribute("aria-selected") !== "true") {
  throw new Error("Expert tab must update aria-selected=true");
}
if (fetchCalls !== 0) throw new Error("Walkthrough/mode controls sent fetch");
console.log("CONTROLS_OK");
""")
        self.assertEqual(out, "CONTROLS_OK")

    def test_wizard_dialog_modal_focus_escape_and_commands(self) -> None:
        if shutil.which("node") is None:
            self.fail("node is not on PATH: this test runs in the workbench, ./go ci")
        out = _run_node_probe("""
book = JSON.parse(fs.readFileSync("fixtures/logbook/golden-1.8.json", "utf8"));
con = { mode: "snapshot", read_at: "2026-11-24T00:00:00Z" };
global.location.hash = "#hive";
render();

const opener = document.getElementById("open-wizard");
if (!opener) throw new Error("missing #open-wizard button");
opener.focus();
opener.click();
const dlg = document.getElementById("onboard-wizard");
if (!dlg || !dlg.open) throw new Error("onboard-wizard did not open with showModal()");
const cmdEl = document.getElementById("wizard-output");
if (!cmdEl) throw new Error("missing #wizard-output");

for (const stack of ["python", "go", "rust", "java"]) {
  const stackSel = document.getElementById("wiz-stack");
  stackSel.value = stack;
  stackSel.dispatchEvent({ type: "change" });
  const txt = document.getElementById("wizard-output").textContent;
  if (!txt.includes("lights-out init") || !txt.includes("lights-out gates") || !txt.includes("lights-out start")) {
    throw new Error("missing lights-out commands for stack " + stack + ": " + txt);
  }
  for (const banned of ["adapters/", ".crew/", "firestore", "state_store"]) {
    if (txt.toLowerCase().includes(banned)) throw new Error("banned string in wizard output: " + banned);
  }
}
const copyBtn = document.getElementById("wizard-copy");
if (!copyBtn) throw new Error("missing #wizard-copy");
copyBtn.click();
const closeBtn = document.getElementById("wizard-close");
if (!closeBtn) throw new Error("missing #wizard-close");
closeBtn.click();
if (dlg.open) throw new Error("wizard did not close via close button");
if (document.activeElement !== opener) throw new Error("focus did not return to opener after close button");
opener.focus();
opener.click();
if (!dlg.open) throw new Error("wizard did not reopen via showModal()");
dlg.dispatchEvent({ type: "cancel" });
if (dlg.open) throw new Error("wizard did not close on Escape/cancel");
if (document.activeElement !== opener) throw new Error("focus did not return to opener after Escape/cancel");
if (fetchCalls !== 0) throw new Error("wizard sent fetch");
console.log("WIZARD_OK");
""")
        self.assertEqual(out, "WIZARD_OK")


if __name__ == "__main__":
    unittest.main()
