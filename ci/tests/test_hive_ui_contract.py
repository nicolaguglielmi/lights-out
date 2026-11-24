"""Integration & contract tests for Hive UI, WCAG contrast, 390px nav, and OIDC proxy recipes (ADR 0111)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE_HTML = ROOT / "console" / "index.html"
ADR_0111 = ROOT / "docs" / "adr" / "0111-hive-guided-onboarding-ui-and-proxy-recipes.md"
PROXY_DIR = ROOT / "deploy" / "proxy"


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


class AdrAndProxyRecipesTest(unittest.TestCase):
    def test_adr_0111_cites_modern_web_guidance(self) -> None:
        self.assertTrue(ADR_0111.is_file(), "docs/adr/0111-hive-guided-onboarding-ui-and-proxy-recipes.md missing")
        text = ADR_0111.read_text(encoding="utf-8")
        for guide_id in (
            "dialog",
            "light-dismiss-a-dialog",
            "prevent-page-scroll-under-modal-dialog",
            "motion",
            "accessibility",
        ):
            self.assertIn(guide_id, text, f"ADR 0111 missing modern-web-guidance citation {guide_id}")

    def test_proxy_recipes_present_hardened_and_secret_free(self) -> None:
        expected = ("README.md", "oauth2-proxy.cfg", "Caddyfile", "cloudflare-access.md")
        for fname in expected:
            path = PROXY_DIR / fname
            self.assertTrue(path.is_file(), f"missing {path}")
            content = path.read_text(encoding="utf-8")
            self.assertIn("CONSOLE_AUTH=proxy", content)
            self.assertIn("CONSOLE_IDENTITY_HEADER", content)
            self.assertIn("CONSOLE_LAUNCHERS", content)
            # Ensure no accidental real-looking secret tokens
            self.assertNotRegex(content, r"AIza[0-9A-Za-z_-]{20,}")
            self.assertNotRegex(content, r"sk-[0-9A-Za-z]{20,}")
            self.assertNotIn("PRIVATE KEY-----", content)

        caddy = (PROXY_DIR / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:8098", caddy)
        self.assertIn("forward_auth", caddy)
        # Must strip client-forged identity headers before proxy injection
        self.assertIn("-X-Auth-Request-Email", caddy)

        o2p = (PROXY_DIR / "oauth2-proxy.cfg").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:8098", o2p)
        self.assertIn("set_xauthrequest", o2p)
        self.assertIn("pass_access_token = false", o2p)

        cf = (PROXY_DIR / "cloudflare-access.md").read_text(encoding="utf-8")
        self.assertIn("Cf-Access-Authenticated-User-Email", cf)


class ConsoleHiveIntegrationContractTest(unittest.TestCase):
    def test_nav_single_line_width_and_wcag_contrast_both_themes(self) -> None:
        html = CONSOLE_HTML.read_text(encoding="utf-8")
        nav_m = re.search(r'<nav id="nav">(.*?)</nav>', html, re.S)
        self.assertIsNotNone(nav_m)
        links = re.findall(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', nav_m.group(1))
        self.assertIn(("#hive", "Hive"), links)
        # Approximate desktop width at 14px system-ui (~6.8px/char + 20px horiz padding + 4px gap) stays inside 1180px
        est_width = sum(len(label) * 6.8 + 20 + 4 for _, label in links)
        self.assertLess(est_width, 1140, f"desktop nav width {est_width:.1f}px risks wrapping inside 1180px")

        # Verify contrast ratios of core palette pairs used across #hive & #onboard-wizard
        light = {
            "bg": "#f6f7f9",
            "panel": "#ffffff",
            "ink": "#17202a",
            "muted": "#5b6673",
            "go": "#13795b",
            "go-bg": "#e3f4ec",
            "nogo": "#b42318",
            "nogo-bg": "#fde8e6",
            "wait": "#8a5a00",
            "wait-bg": "#fdf3dc",
            "accent": "#1d4ed8",
            "focus": "#1d4ed8",
        }
        dark = {
            "bg": "#0f1419",
            "panel": "#171e26",
            "ink": "#e6ebf0",
            "muted": "#9aa6b2",
            "go": "#4cc38a",
            "go-bg": "#12301f",
            "nogo": "#ff7b72",
            "nogo-bg": "#3a1513",
            "wait": "#e3b341",
            "wait-bg": "#33280e",
            "accent": "#79a8ff",
            "focus": "#79a8ff",
        }
        for name, tok in (("light", light), ("dark", dark)):
            self.assertGreaterEqual(_contrast(tok["ink"], tok["panel"]), 4.5, name)
            self.assertGreaterEqual(_contrast(tok["ink"], tok["bg"]), 4.5, name)
            self.assertGreaterEqual(_contrast(tok["muted"], tok["panel"]), 4.5, name)
            self.assertGreaterEqual(_contrast(tok["go"], tok["go-bg"]), 4.5, name)
            self.assertGreaterEqual(_contrast(tok["nogo"], tok["nogo-bg"]), 4.5, name)
            self.assertGreaterEqual(_contrast(tok["wait"], tok["wait-bg"]), 4.5, name)
            self.assertGreaterEqual(_contrast(tok["panel"], tok["accent"]), 4.5, name)
            self.assertGreaterEqual(_contrast(tok["focus"], tok["panel"]), 3.0, name)
            self.assertGreaterEqual(_contrast(tok["focus"], tok["bg"]), 3.0, name)

    def test_wizard_all_providers_codehosts_cloud_targets_in_node(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node not on PATH")
        html = CONSOLE_HTML.read_text(encoding="utf-8")
        tower_m = re.search(r'<script type="application/json" id="tower-machine">(.*?)</script>', html, re.S)
        words_m = re.search(r'<script type="application/json" id="network-words">(.*?)</script>', html, re.S)
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        main_js = scripts[-1]
        core_js = main_js[: main_js.find('window.addEventListener("hashchange"')]

        probe = f"""
const fs = require("fs");
const idRegistry = new Map();
class FakeNode {{
  constructor(tag) {{
    this.tagName = tag.toUpperCase(); this.attrs = {{}}; this.children = []; this.dataset = {{}};
    this.listeners = {{}}; this.style = {{ setProperty: () => {{}} }};
    this.classList = {{ toggle: () => {{}}, add: () => {{}}, remove: () => {{}}, contains: () => false }};
    this.value = ""; this.open = false; this.returnValue = "";
  }}
  setAttribute(k, v) {{ this.attrs[k] = String(v); if (k === "id") idRegistry.set(String(v), this); if (k.startsWith("data-")) this.dataset[k.slice(5)] = String(v); }}
  getAttribute(k) {{ return k in this.attrs ? this.attrs[k] : null; }}
  append(...kids) {{ for (const k of kids) if (k !== null && k !== undefined && k !== false) this.children.push(typeof k === "string" || typeof k === "number" ? {{ text: String(k) }} : k); }}
  replaceChildren(...kids) {{ this.children = []; this.append(...kids); }}
  addEventListener(type, fn) {{ (this.listeners[type] ||= []).push(fn); }}
  dispatchEvent(ev) {{ ev.target = this; ev.currentTarget = this; ev.preventDefault = () => {{}}; for (const fn of this.listeners[ev.type] || []) fn(ev); }}
  click() {{ this.dispatchEvent({{ type: "click" }}); }}
  focus() {{}}
  showModal() {{ this.open = true; }}
  close() {{ this.open = false; }}
  _all() {{ const out = []; const w = (n) => {{ if (!n || "text" in n) return; out.push(n); for (const c of n.children || []) w(c); }}; for (const c of this.children || []) w(c); return out; }}
  querySelectorAll(sel) {{ return this._all().filter((n) => sel.startsWith("#") ? n.id === sel.slice(1) : n.tagName.toLowerCase() === sel.toLowerCase()); }}
  querySelector(sel) {{ return this.querySelectorAll(sel)[0] || null; }}
  get id() {{ return this.attrs.id || ""; }}
  set id(v) {{ this.setAttribute("id", v); }}
  get textContent() {{ return this.children.map((c) => ("text" in c ? c.text : c.textContent)).join(""); }}
  set textContent(v) {{ this.replaceChildren(String(v)); }}
}}
const mainEl = new FakeNode("main"); mainEl.id = "main";
const wizEl = new FakeNode("dialog"); wizEl.id = "onboard-wizard";
const fixed = {{
  "theme": new FakeNode("select"), "main": mainEl, "who": new FakeNode("span"), "fresh": new FakeNode("span"),
  "announce": new FakeNode("div"), "card-back": new FakeNode("dialog"), "why": new FakeNode("dialog"),
  "launch-confirm": new FakeNode("dialog"), "onboard-wizard": wizEl,
  "tower-machine": {{ textContent: {json.dumps(tower_m.group(1))} }},
  "network-words": {{ textContent: {json.dumps(words_m.group(1))} }},
}};
global.document = {{
  documentElement: new FakeNode("html"),
  createElement: (t) => new FakeNode(t),
  createElementNS: (_ns, t) => new FakeNode(t),
  createTextNode: (t) => ({{ text: String(t) }}),
  getElementById: (id) => fixed[id] || idRegistry.get(id) || null,
  querySelectorAll: (s) => mainEl.querySelectorAll(s),
  querySelector: (s) => mainEl.querySelector(s),
  addEventListener: () => {{}},
}};
global.Node = FakeNode;
global.CSS = {{ escape: (s) => String(s) }};
global.HTMLDialogElement = function() {{}};
global.HTMLDialogElement.prototype = {{}};
global.location = {{ hash: "#hive" }};
global.history = {{ replaceState: () => {{}} }};
global.matchMedia = () => ({{ matches: false }});
global.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
global.fetch = () => Promise.reject(new Error("no network"));
global.setInterval = () => 1;
global.clearInterval = () => {{}};
global.window = global;
""" + core_js + """
book = JSON.parse(fs.readFileSync("fixtures/logbook/golden-1.8.json", "utf8"));
con = { mode: "snapshot", read_at: "2026-11-24T00:00:00Z" };
render();
document.getElementById("open-wizard").click();

const sets = [
  ["python", "gemini", "github", "gcp-cloud-deploy", "2.50", "12.00"],
  ["node-ts", "anthropic", "gitlab", "aws-ecs-codedeploy", "3.00", "15.00"],
  ["go", "openai", "github", "azure-container-apps", "1.50", "8.00"],
  ["rust", "zai", "gitea", "hetzner-k3s", "1.00", "5.00"],
  ["java", "replay", "gitea", "envoy-canary", "0.50", "4.00"],
];
for (const [stack, prov, ch, deliv, tb, db] of sets) {
  document.getElementById("wiz-stack").value = stack;
  document.getElementById("wiz-provider").value = prov;
  document.getElementById("wiz-codehost").value = ch;
  document.getElementById("wiz-delivery").value = deliv;
  document.getElementById("wiz-ticket-budget").value = tb;
  document.getElementById("wiz-day-budget").value = db;
  document.getElementById("wiz-delivery").dispatchEvent({ type: "change" });
  const out = document.getElementById("wizard-output").textContent;
  if (!out.includes("--provider " + prov)) throw new Error("missing provider " + prov);
  if (!out.includes("CODE_HOST=" + ch)) throw new Error("missing codehost " + ch);
  if (!out.includes("--ticket-budget-usd " + tb)) throw new Error("missing ticket budget " + tb);
  if (!out.includes("--day-budget-usd " + db)) throw new Error("missing day budget " + db);
  if (deliv.includes("-")) {
    if (!["envoy-canary", "argo-rollouts", "flagger"].includes(deliv) && !out.includes("lights-out cloud-setup CLOUD=" + deliv)) {
      throw new Error("missing cloud-setup for " + deliv);
    }
  }
}
console.log("WIZARD_ALL_OK");
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
        self.assertEqual(res.stdout.strip(), "WIZARD_ALL_OK")

    def test_console_serve_py_serves_hive_in_snapshot_mode(self) -> None:
        import socket
        import time

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("CONSOLE_")
        }
        env.update(
            {
                "CONSOLE_LISTEN": str(port),
                "CONSOLE_PORT": str(port),
                "CONSOLE_SNAPSHOT": str(ROOT / "fixtures" / "logbook" / "golden-1.8.json"),
            }
        )
        proc = subprocess.Popen(
            ["python3", str(ROOT / "console" / "serve.py")],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            for _ in range(50):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                        body = resp.read().decode("utf-8")
                        self.assertEqual(resp.status, 200)
                        self.assertIn('href="#hive"', body)
                        self.assertIn('id="onboard-wizard"', body)
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                self.fail("console/serve.py did not respond in snapshot mode")
        finally:
            proc.kill()
            proc.wait(timeout=5)
            if proc.stderr:
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
