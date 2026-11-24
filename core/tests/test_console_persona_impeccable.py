"""Spec-derived tests for M86 Adaptive Persona UX and Operate-mode UI polish (ADR 0126).

Covers ADR 0126 provenance validation, WCAG 2.1 AA light/dark button & control-border
contrast tokens, native <dialog closedby="any"> invariants, CONSOLE_PERSONA propagation
through core/start.py and console/serve.py, and Node 24 DOM persona & palette checks.
Strictly compliant with vendor-free, app-free, and model-free repository gates.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from core import adr_gov, start

ROOT = Path(__file__).resolve().parents[2]
ADR_0126 = ROOT / "docs" / "adr" / "0126-adaptive-persona-ux-and-impeccable-polish.md"
CONSOLE_HTML = ROOT / "console" / "index.html"
GOLDEN = ROOT / "fixtures" / "logbook" / "golden-1.8.json"


def _hex_to_rgb(hx: str) -> tuple[int, int, int]:
    hx = hx.lstrip("#")
    if len(hx) == 3:
        hx = "".join(c * 2 for c in hx)
    return int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)


def _rel_lum(rgb: tuple[int, int, int]) -> float:
    def ch(v: int) -> float:
        c = v / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(fg: str, bg: str) -> float:
    l1, l2 = _rel_lum(_hex_to_rgb(fg)), _rel_lum(_hex_to_rgb(bg))
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def _parse_tokens(block: str) -> dict[str, str]:
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})", block)
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class AdrAndCssCraftFloorTest(unittest.TestCase):
    def test_adr_0126_valid_and_cites_all_modern_web_guidance_ids(self) -> None:
        self.assertTrue(ADR_0126.is_file(), f"missing {ADR_0126}")
        text = ADR_0126.read_text(encoding="utf-8")
        self.assertEqual(adr_gov.validate_adr(text), [])
        self.assertIn("## Provenance", text)
        for guide_id in (
            "dialog",
            "light-dismiss-a-dialog",
            "dark-mode",
            "color",
            "responsive-table",
            "form-factor-adaptation",
            "content-first-noise-reduction",
        ):
            self.assertIn(guide_id, text, f"ADR 0126 missing guide citation {guide_id}")

    def test_html_color_scheme_and_six_closedby_dialogs(self) -> None:
        html = CONSOLE_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            html,
            r'<meta\s+name="color-scheme"\s+content="light dark">',
            "console/index.html <head> must declare <meta name=\"color-scheme\" content=\"light dark\">",
        )
        dialogs = re.findall(r"<dialog\b([^>]*)>", html)
        self.assertEqual(len(dialogs), 6, f"expected 6 modal dialogs, found {len(dialogs)}")
        for attrs in dialogs:
            self.assertIn('closedby="any"', attrs)
            self.assertNotIn('role="dialog"', attrs)
            self.assertNotIn("aria-modal", attrs)
        # Bare `dialog { display: ... }` must never appear outside `dialog[open]`
        style_m = re.search(r"<style>(.*?)</style>", html, re.S)
        self.assertIsNotNone(style_m)
        css = style_m.group(1)
        self.assertNotRegex(css, r"(?m)^dialog[^{]*\{[^}]*\bdisplay\s*:")

    def test_wcag_aa_solid_buttons_and_control_borders_both_themes(self) -> None:
        html = CONSOLE_HTML.read_text(encoding="utf-8")
        style_m = re.search(r"<style>(.*?)</style>", html, re.S)
        assert style_m is not None
        css = style_m.group(1)
        light_m = re.search(r":root\s*\{(.*?)\}", css, re.S)
        dark_m = re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\}', css, re.S)
        assert light_m is not None and dark_m is not None
        for theme_name, block in (("light", light_m.group(1)), ("dark", dark_m.group(1))):
            tok = _parse_tokens(block)
            self.assertGreaterEqual(_contrast(tok["go-on-solid"], tok["go-solid"]), 4.5, theme_name)
            self.assertGreaterEqual(_contrast(tok["nogo-on-solid"], tok["nogo-solid"]), 4.5, theme_name)
            self.assertGreaterEqual(_contrast(tok["accent-on-solid"], tok["accent-solid"]), 4.5, theme_name)
            self.assertGreaterEqual(_contrast(tok["muted"], tok["surface-subtle"]), 4.5, theme_name)
            self.assertGreaterEqual(_contrast(tok["control-border"], tok["panel"]), 3.0, theme_name)
            self.assertGreaterEqual(_contrast(tok["control-border"], tok["bg"]), 3.0, theme_name)


class ConsolePersonaPropagationTest(unittest.TestCase):
    def _build_start_root(self, tmp: Path) -> Path:
        root = tmp / "repo"
        (root / "profiles").mkdir(parents=True)
        (root / "adapters" / "replay").mkdir(parents=True)
        (root / "adapters" / "fake-delivery").mkdir(parents=True)
        (root / "adapters" / "fake-delivery" / "deploy.sh").write_text("#!/bin/sh\necho GO\n", encoding="utf-8")
        (root / "core").mkdir(parents=True)
        (root / "core" / "doctor.sh").write_text("#!/bin/sh\necho 'GO: doctor'\n", encoding="utf-8")
        (root / "profiles" / "l0.env").write_text("DELIVERY=fake-delivery\n", encoding="utf-8")
        (root / "profiles" / "l1.env").write_text("DELIVERY=fake-delivery\n", encoding="utf-8")
        (root / "profiles" / "providers.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "replay": {
                            "default_profile": "l0",
                            "default_harness": "replay",
                            "harnesses": ["replay"],
                            "default_model": "",
                            "env_var": "",
                            "decide": "replay",
                        },
                        "alpha": {
                            "default_profile": "l1",
                            "default_harness": "replay",
                            "harnesses": ["replay"],
                            "default_model": "alpha-default",
                            "env_var": "ALPHA_TOKEN",
                            "decide": "structured",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_start_plan_writes_console_persona(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = self._build_start_root(Path(td))
            start.run_start(
                root,
                non_interactive=True,
                preset="junior",
                provider="replay",
                harness="replay",
                delivery="fake-delivery",
                profile_override="l0",
                mode_override=None,
                contract_arg=None,
                ticket_budget_usd="2.00",
                day_budget_usd="10.00",
            )
            plan_junior = (root / ".crew" / "start-plan.env").read_text(encoding="utf-8")
            self.assertIn("CONSOLE_PERSONA=junior\n", plan_junior)

            (root / ".env").write_text("ALPHA_TOKEN=present-token\n", encoding="utf-8")
            start.run_start(
                root,
                non_interactive=True,
                preset="strict",
                provider="alpha",
                harness="replay",
                delivery="fake-delivery",
                profile_override="l1",
                mode_override="expert",
                contract_arg=None,
                ticket_budget_usd="3.00",
                day_budget_usd="12.00",
            )
            plan_senior = (root / ".crew" / "start-plan.env").read_text(encoding="utf-8")
            self.assertIn("CONSOLE_PERSONA=senior\n", plan_senior)

    def test_serve_py_exposes_default_persona_on_console_json(self) -> None:
        for env_val, want in (("", "junior"), ("senior", "senior"), ("invalid", "junior")):
            port = _free_port()
            env = {k: v for k, v in os.environ.items() if not k.startswith("CONSOLE_")}
            env.update(
                {
                    "CONSOLE_LISTEN": str(port),
                    "CONSOLE_PORT": str(port),
                    "CONSOLE_SNAPSHOT": str(GOLDEN),
                }
            )
            if env_val:
                env["CONSOLE_PERSONA"] = env_val
            proc = subprocess.Popen(
                [sys.executable, "-B", str(ROOT / "console" / "serve.py")],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                info = None
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/console.json", timeout=2) as resp:
                            info = json.loads(resp.read().decode("utf-8"))
                            break
                    except OSError:
                        time.sleep(0.05)
                self.assertIsNotNone(info, f"serve.py did not respond for CONSOLE_PERSONA={env_val!r}")
                assert info is not None
                self.assertEqual(info["default_persona"], want)
            finally:
                proc.kill()
                proc.wait(timeout=5)
                if proc.stderr:
                    proc.stderr.close()


class ConsolePersonaNodeDomTest(unittest.TestCase):
    def test_persona_toggle_cockpit_thresholds_and_first_esc_palette(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node not on PATH")
        html = CONSOLE_HTML.read_text(encoding="utf-8")
        tower_m = re.search(r'<script type="application/json" id="tower-machine">(.*?)</script>', html, re.S)
        words_m = re.search(r'<script type="application/json" id="network-words">(.*?)</script>', html, re.S)
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        assert tower_m is not None and words_m is not None
        core_js = scripts[-1][: scripts[-1].find('window.addEventListener("hashchange"')]

        probe = f"""
const fs = require("fs");
const idMap = new Map();
class FakeNode {{
  constructor(tag) {{
    this.tagName = tag.toUpperCase();
    this.attrs = {{}};
    this.children = [];
    this.dataset = {{}};
    this.listeners = {{}};
    this.style = {{ setProperty: () => {{}} }};
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
    this.value = "";
    this.open = false;
    this.returnValue = "";
    this.hidden = false;
    this.isConnected = true;
  }}
  setAttribute(k, v) {{
    this.attrs[k] = String(v);
    if (k === "id") idMap.set(String(v), this);
    if (k.startsWith("data-")) this.dataset[k.slice(5)] = String(v);
  }}
  getAttribute(k) {{ return k in this.attrs ? this.attrs[k] : null; }}
  removeAttribute(k) {{ delete this.attrs[k]; }}
  append(...kids) {{
    for (const k of kids) {{
      if (k !== null && k !== undefined && k !== false) {{
        this.children.push(typeof k === "string" || typeof k === "number" ? {{ text: String(k) }} : k);
      }}
    }}
  }}
  replaceChildren(...kids) {{ this.children = []; this.append(...kids); }}
  addEventListener(type, fn) {{ (this.listeners[type] ||= []).push(fn); }}
  dispatchEvent(ev) {{
    const e = Object.assign({{ target: this, currentTarget: this, preventDefault: () => {{}}, stopPropagation: () => {{}} }}, ev);
    for (const fn of this.listeners[ev.type] || []) fn(e);
  }}
  click() {{ this.dispatchEvent({{ type: "click" }}); }}
  focus() {{ global.document.activeElement = this; }}
  showModal() {{ this.open = true; }}
  close() {{ this.open = false; }}
  _all() {{
    const out = [];
    const walk = (n) => {{ if (!n || "text" in n) return; out.push(n); for (const c of n.children || []) walk(c); }};
    for (const c of this.children || []) walk(c);
    return out;
  }}
  querySelectorAll(sel) {{
    return this._all().filter((n) => {{
      if (sel.startsWith("#")) return n.id === sel.slice(1);
      if (sel.startsWith(".")) return (n.attrs.class || "").split(/\\s+/).includes(sel.slice(1));
      return n.tagName.toLowerCase() === sel.toLowerCase();
    }});
  }}
  querySelector(sel) {{ return this.querySelectorAll(sel)[0] || null; }}
  get id() {{ return this.attrs.id || ""; }}
  set id(v) {{ this.setAttribute("id", v); }}
  get className() {{ return this.attrs.class || ""; }}
  set className(v) {{ this.setAttribute("class", v); }}
  get textContent() {{ return this.children.map((c) => ("text" in c ? c.text : c.textContent)).join(""); }}
  set textContent(v) {{ this.replaceChildren(String(v)); }}
}}
const mainEl = new FakeNode("main"); mainEl.id = "main";
const cmdEl = new FakeNode("dialog"); cmdEl.id = "cmd-palette";
const wizEl = new FakeNode("dialog"); wizEl.id = "onboard-wizard";
const jBtn = new FakeNode("button"); jBtn.id = "persona-junior";
const sBtn = new FakeNode("button"); sBtn.id = "persona-senior";
const ssePill = new FakeNode("span"); ssePill.id = "sse-pill";
const rolePill = new FakeNode("span"); rolePill.id = "role-pill";
const cmdBtn = new FakeNode("button"); cmdBtn.id = "cmd-k-btn";
const fixed = {{
  "theme": new FakeNode("select"),
  "main": mainEl,
  "who": new FakeNode("span"),
  "fresh": new FakeNode("span"),
  "sse-pill": ssePill,
  "role-pill": rolePill,
  "persona-junior": jBtn,
  "persona-senior": sBtn,
  "cmd-k-btn": cmdBtn,
  "announce": new FakeNode("div"),
  "card-back": new FakeNode("dialog"),
  "why": new FakeNode("dialog"),
  "launch-confirm": new FakeNode("dialog"),
  "onboard-wizard": wizEl,
  "cmd-palette": cmdEl,
  "ops-dialog": new FakeNode("dialog"),
  "tower-machine": {{ textContent: {json.dumps(tower_m.group(1))} }},
  "network-words": {{ textContent: {json.dumps(words_m.group(1))} }},
}};
global.document = {{
  documentElement: new FakeNode("html"),
  body: new FakeNode("body"),
  activeElement: cmdBtn,
  createElement: (t) => new FakeNode(t),
  createElementNS: (_ns, t) => new FakeNode(t),
  createTextNode: (t) => ({{ text: String(t) }}),
  getElementById: (id) => fixed[id] || idMap.get(id) || null,
  querySelectorAll: (s) => mainEl.querySelectorAll(s),
  querySelector: (s) => mainEl.querySelector(s),
  addEventListener: () => {{}},
}};
global.Node = FakeNode;
global.CSS = {{ escape: (s) => String(s) }};
global.HTMLDialogElement = function() {{}};
global.HTMLDialogElement.prototype = {{}};
global.location = {{ hash: "#overview" }};
global.history = {{ replaceState: () => {{}} }};
global.matchMedia = () => ({{ matches: false }});
const store = {{}};
let throwStorage = false;
global.localStorage = {{
  getItem: (k) => {{ if (throwStorage) throw new Error("refused"); return k in store ? store[k] : null; }},
  setItem: (k, v) => {{ if (throwStorage) throw new Error("refused"); store[k] = String(v); }},
}};
global.fetch = () => Promise.reject(new Error("no network"));
global.setInterval = () => 1;
global.clearInterval = () => {{}};
global.window = global;
""" + core_js + """
book = JSON.parse(fs.readFileSync("fixtures/logbook/golden-1.8.json", "utf8"));
con = { mode: "snapshot", auth: "proxy", identity: "alice@example.com", role: "launcher", default_persona: "junior", can_launch: true };
render();

// 1. Default Guided (junior) mode + panel ordering + pills
const firstPanel = mainEl.querySelectorAll(".panel")[0];
if (!firstPanel || firstPanel.id !== "your-turn") throw new Error("#your-turn is not first .panel");
const deckJ = document.getElementById("persona-deck");
if (!deckJ || deckJ.getAttribute("data-persona-view") !== "junior") throw new Error("expected junior persona-deck");
if (jBtn.getAttribute("aria-pressed") !== "true") throw new Error("Guided button not active");
if (!ssePill.textContent.includes("snapshot")) throw new Error("missing snapshot pill");
if (!rolePill.textContent.includes("launcher")) throw new Error("missing role pill");

// 2. Switch to Cockpit (senior) via button click -> persists in storage & flips view
sBtn.click();
if (store["lights-out.persona"] !== "senior") throw new Error("persona not saved to localStorage");
if (document.documentElement.dataset.persona !== "senior") throw new Error("dataset.persona not updated");
if (sBtn.getAttribute("aria-pressed") !== "true") throw new Error("Cockpit button not active");
const deckS = document.getElementById("persona-deck");
if (!deckS || deckS.getAttribute("data-persona-view") !== "senior") throw new Error("expected senior persona-deck");
if (deckS.querySelectorAll(".cockpit-kpi").length !== 6) throw new Error("expected 6 KPIs in cockpit");

// 3. Throwing localStorage fallback uses default_persona
memPersona = null;
throwStorage = true;
con.default_persona = "senior";
if (personaMode() !== "senior") throw new Error("expected default_persona=senior on refused storage");
con.default_persona = "junior";
if (personaMode() !== "junior") throw new Error("expected default_persona=junior on refused storage");
throwStorage = false;

// 4. First Escape closes Command Palette while search input has text
openCmdPalette(cmdBtn);
const cmdIn = document.getElementById("cmd-k-input");
cmdIn.value = "cockpit";
cmdIn.dispatchEvent({ type: "input" });
if (document.getElementById("cmd-k-results").textContent.indexOf("Fleet Cockpit") < 0) {{
  throw new Error("missing Cockpit command in palette");
}}
cmdIn.dispatchEvent({ type: "keydown", key: "Escape", isComposing: false, keyCode: 27 });
if (cmdEl.open) throw new Error("Command Palette did not close on first Escape");

// 5. All 10 stacks in Hive onboarding wizard
const stackKeys = Object.keys(HIVE_STACKS);
for (const k of ["python", "node-ts", "go", "rust", "java", "kotlin", "dotnet", "swift", "php", "ruby"]) {{
  if (!stackKeys.includes(k)) throw new Error("missing stack " + k + " in HIVE_STACKS");
}}

console.log("PERSONA_IMPECCABLE_OK");
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as tf:
            tf.write(probe)
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
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout.strip(), "PERSONA_IMPECCABLE_OK")


if __name__ == "__main__":
    unittest.main()
