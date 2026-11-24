"""Integration tests for guided UX (ADR 0103): providers, Makefile/go, and Console."""

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


class ProvidersMetadataTest(unittest.TestCase):
    def test_providers_json_covers_all_configured_providers_and_harnesses(self) -> None:
        path = ROOT / "profiles" / "providers.json"
        self.assertTrue(path.is_file(), "profiles/providers.json missing")
        data = json.loads(path.read_text(encoding="utf-8"))
        providers = data.get("providers")
        self.assertIsInstance(providers, dict)
        for name in ("replay", "gemini", "anthropic", "openai", "zai"):
            self.assertIn(name, providers)
            entry = providers[name]
            self.assertTrue(entry.get("label"))
            self.assertIsInstance(entry.get("harnesses"), list)
            self.assertTrue(entry["harnesses"])
            self.assertIn(entry.get("default_harness"), entry["harnesses"])
            self.assertIn("env_var", entry)
            self.assertIn("default_profile", entry)
            self.assertTrue(entry.get("budget_guidance"))
            for harness in entry["harnesses"]:
                self.assertTrue(
                    (ROOT / "adapters" / harness).is_dir(),
                    f"provider {name} references missing adapters/{harness}",
                )


class MakefileAndGoWiringTest(unittest.TestCase):
    def test_make_help_lists_start_and_next_and_go_header_cites_them(self) -> None:
        help_res = subprocess.run(
            ["make", "--no-print-directory", "help"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(help_res.returncode, 0, help_res.stderr)
        targets = {
            line.split()[0]
            for line in help_res.stdout.splitlines()
            if line.startswith("  ") and line.strip()
        }
        self.assertIn("start", targets)
        self.assertIn("next", targets)

        go_text = (ROOT / "go").read_text(encoding="utf-8")
        self.assertIn("./go start", go_text)
        self.assertIn("./go next", go_text)

    def test_start_sh_within_shell_boundary_and_next_runs(self) -> None:
        sh_path = ROOT / "core" / "start.sh"
        self.assertTrue(sh_path.is_file(), "core/start.sh missing")
        code_lines = [
            ln for ln in sh_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        self.assertLessEqual(len(code_lines), 30, f"core/start.sh has {len(code_lines)} code lines (> 30)")

        res = subprocess.run(
            ["make", "--no-print-directory", "next", "NEXT_ARGS=--json"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        payload = json.loads(res.stdout)
        self.assertIn(payload["stage"], range(1, 7))
        self.assertEqual(payload["total_stages"], 6)

    def test_start_non_interactive_end_to_end_on_isolated_copy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for rel in ("profiles", "policy", "core", "adapters"):
                shutil.copytree(ROOT / rel, tmp / rel)
            (tmp / "app").mkdir()
            (tmp / "app" / "workload.env").write_text(
                "TOOLCHAIN_IMAGE=workbench\nMUTATION_BASELINE=0.80\n",
                encoding="utf-8",
            )
            res = subprocess.run(
                [
                    "sh",
                    "core/start.sh",
                    "start",
                    "--non-interactive",
                    "--preset",
                    "junior",
                    "--provider",
                    "gemini",
                    "--harness",
                    "gemini-cli",
                    "--delivery",
                    "envoy-canary",
                ],
                cwd=tmp,
                env={**os.environ, "WORKLOAD_CONTRACT": str(tmp / "app")},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertRegex(res.stdout.strip(), r"^GO: start — ")
            plan = (tmp / ".crew" / "start-plan.env").read_text(encoding="utf-8")
            self.assertIn("MODEL_PROVIDER=gemini", plan)
            self.assertIn("AGENT_HARNESS=gemini-cli", plan)
            self.assertIn("DELIVERY_ADAPTER=envoy-canary", plan)
            self.assertIn("PROVIDER_KEY_VAR=GEMINI_API_KEY", plan)


class ConsoleJourneyOverviewTest(unittest.TestCase):
    def test_console_html_has_journey_after_your_turn_and_passes_node_dom_check(self) -> None:
        html_path = ROOT / "console" / "index.html"
        html = html_path.read_text(encoding="utf-8")
        self.assertIn('id: "journey"', html)
        self.assertIn("#specs", html)
        self.assertIn("#kanban", html)
        self.assertIn("#network", html)
        self.assertIn("#launch", html)
        self.assertIn("#thresholds", html)
        self.assertIn("#agents", html)
        self.assertIn('h("details"', html)
        self.assertIn("overflow-wrap: anywhere", html)
        self.assertIn("@media (max-width: 720px) { .journey-bar", html)
        for banned in ("adapters/", ".crew/", "firestore", "state_store"):
            self.assertNotIn(banned, html.lower())

        # Execute the console's own overview/thresholds functions in Node 24 against golden 1.8 and 1.2/1.4/1.5
        if shutil.which("node") is None:
            self.skipTest("node not on PATH")
        # Extract the main inline <script> block and embedded JSON blocks from console/index.html
        tower_m = re.search(r'<script type="application/json" id="tower-machine">(.*?)</script>', html, re.S)
        words_m = re.search(r'<script type="application/json" id="network-words">(.*?)</script>', html, re.S)
        self.assertIsNotNone(tower_m)
        self.assertIsNotNone(words_m)
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertGreaterEqual(len(scripts), 2)
        main_js = scripts[-1]
        # Strip bottom auto-boot (window.addEventListener("hashchange", ...) onwards)
        boot_idx = main_js.find('window.addEventListener("hashchange"')
        self.assertGreater(boot_idx, 0)
        core_js = main_js[:boot_idx]

        probe = f"""
const fs = require("fs");
class FakeNode {{
  constructor(tag) {{
    this.tagName = tag.toUpperCase();
    this.attrs = {{}};
    this.children = [];
    this.dataset = {{}};
    this.classList = {{ toggle: () => {{}}, add: () => {{}}, contains: () => false }};
    this.value = "";
  }}
  setAttribute(k, v) {{ this.attrs[k] = String(v); }}
  getAttribute(k) {{ return this.attrs[k] ?? null; }}
  append(...kids) {{
    for (const k of kids) {{
      if (k === null || k === undefined || k === false) continue;
      this.children.push(typeof k === "string" || typeof k === "number" ? {{ text: String(k) }} : k);
    }}
  }}
  replaceChildren(...kids) {{ this.children = []; this.append(...kids); }}
  addEventListener() {{}}
  querySelectorAll() {{ return []; }}
  querySelector() {{ return null; }}
  get id() {{ return this.attrs.id || ""; }}
  set id(v) {{ this.attrs.id = v; }}
  get className() {{ return this.attrs.class || ""; }}
  set className(v) {{ this.attrs.class = v; }}
  get textContent() {{
    return this.children.map((c) => ("text" in c ? c.text : c.textContent)).join("");
  }}
}}
const towerJson = {json.dumps(tower_m.group(1))};
const nwJson = {json.dumps(words_m.group(1))};
const themeEl = new FakeNode("select");
const mainEl = new FakeNode("main");
const whoEl = new FakeNode("span");
const freshEl = new FakeNode("span");
global.document = {{
  documentElement: new FakeNode("html"),
  createElement: (tag) => new FakeNode(tag),
  createElementNS: (_ns, tag) => new FakeNode(tag),
  createTextNode: (t) => ({{ text: String(t) }}),
  getElementById: (id) => ({{ "theme": themeEl, "main": mainEl, "who": whoEl, "fresh": freshEl, "tower-machine": {{ textContent: towerJson }}, "network-words": {{ textContent: nwJson }} }}[id] || new FakeNode("div")),
  querySelectorAll: () => [],
  querySelector: () => null,
  addEventListener: () => {{}},
  activeElement: null,
}};
global.Node = FakeNode;
global.location = {{ hash: "#overview" }};
global.matchMedia = () => ({{ matches: false }});
Object.defineProperty(global, "localStorage", {{ get() {{ throw new Error("storage refused"); }} }});
global.fetch = () => Promise.reject(new Error("no fetch"));
global.setInterval = () => 0;
global.window = global;
""" + core_js + r"""
function walk(node, fn) {
  if (!node || "text" in node) return;
  fn(node);
  for (const c of node.children || []) walk(c, fn);
}
for (const file of ["fixtures/logbook/golden-1.8.json", "fixtures/logbook/golden-1.5.json", "fixtures/logbook/golden-1.4.json", "fixtures/logbook/golden-1.2.json"]) {
  book = JSON.parse(fs.readFileSync(file, "utf8"));
  con = { mode: "snapshot", read_at: "2026-11-24T00:00:00Z" };
  const body = screenOverview();
  const root = new FakeNode("main");
  root.replaceChildren(...body);
  const panels = [];
  const sections = [];
  const links = [];
  let hasDetails = false;
  let stages = [];
  walk(root, (n) => {
    const cls = (n.attrs.class || "").split(/\s+/);
    if (cls.includes("panel")) panels.push(n.id || "(no-id)");
    if (n.tagName === "SECTION" && n.id) sections.push(n.id);
    if (n.tagName === "A" && n.attrs.href) links.push(n.attrs.href);
    if (n.tagName === "DETAILS") hasDetails = true;
    if (cls.includes("journey-stage")) stages.push(n.attrs["data-stage"]);
  });
  if (panels[0] !== "your-turn") throw new Error(file + ": first panel is " + panels[0]);
  if (!sections.includes("journey")) throw new Error(file + ": missing section#journey");
  if (sections[sections.length - 1] !== "journey") throw new Error(file + ": section#journey not last: " + sections.join(","));
  if (stages.length !== 6) throw new Error(file + ": expected 6 stages, got " + stages.length);
  if (!hasDetails) throw new Error(file + ": missing <details> Junior Guide");
  for (const href of ["#specs", "#kanban", "#network", "#launch", "#thresholds", "#agents"]) {
    if (!links.includes(href)) throw new Error(file + ": missing link " + href);
  }
}
global.location = { hash: "#agents" };
book = JSON.parse(fs.readFileSync("fixtures/logbook/golden-1.8.json", "utf8"));
render();
const agentsHeadings = [];
walk(mainEl, (n) => { if (n.tagName === "H2") agentsHeadings.push(n.textContent); });
if (!agentsHeadings.includes("Agents")) throw new Error("#agents did not render Agents panel");
console.log("OK");
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
        self.assertEqual(res.stdout.strip(), "OK")


if __name__ == "__main__":
    unittest.main()
