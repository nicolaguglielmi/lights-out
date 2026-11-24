"""Unit, contract, and Node 24 DOM tests for M85 Control-Plane Workstation (ADR 0125)."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

from core import adr_gov, console_ops

ROOT = Path(__file__).resolve().parents[2]
CONSOLE_HTML = ROOT / "console" / "index.html"
ADR_0125 = ROOT / "docs" / "adr" / "0125-control-plane-web-workstation.md"
CONSOLE_OPS_SH = ROOT / "core" / "console_ops.sh"


def _make_exec(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class AdrAndShellBoundaryTest(unittest.TestCase):
    def test_adr_0125_passes_adr_gov_validation(self) -> None:
        self.assertTrue(ADR_0125.is_file(), "docs/adr/0125-control-plane-web-workstation.md missing")
        text = ADR_0125.read_text(encoding="utf-8")
        self.assertEqual(adr_gov.validate_adr(text), [])
        self.assertIn("## Provenance", text)

    def test_console_ops_sh_executable_and_within_25_code_lines(self) -> None:
        self.assertTrue(CONSOLE_OPS_SH.is_file(), "core/console_ops.sh missing")
        self.assertTrue(CONSOLE_OPS_SH.stat().st_mode & stat.S_IXUSR, "core/console_ops.sh must be +x")
        code_lines = [
            ln for ln in CONSOLE_OPS_SH.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        self.assertLessEqual(len(code_lines), 25, f"core/console_ops.sh has {len(code_lines)} code lines (> 25)")

    def test_serve_py_retains_clean_identity_and_may_launch_hooks_for_m87(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("console_serve", ROOT / "console" / "serve.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        self.assertEqual(list(inspect.signature(mod.identity).parameters), ["headers"])
        self.assertEqual(list(inspect.signature(mod.may_launch).parameters), ["who"])


class ConsoleOpsUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.orig_cwd = os.getcwd()
        os.chdir(self.tmp)
        self.log = self.tmp / ".crew" / "console-ops.jsonl"
        self.effects = self.tmp / "effects.log"
        self.flags_dir = self.tmp / "adapters" / "fake-flags"
        self.deliv_dir = self.tmp / "adapters" / "fake-deliv"
        self.charts_dir = self.tmp / "adapters" / "fake-charts"

        _make_exec(
            self.flags_dir / "set.sh",
            f"#!/bin/sh\nset -eu\nprintf 'flag %s %s\\n' \"$1\" \"$2\" >> {json.dumps(str(self.effects))}\n",
        )
        _make_exec(
            self.deliv_dir / "rollback.sh",
            f"#!/bin/sh\nset -eu\nprintf 'rollback\\n' >> {json.dumps(str(self.effects))}\n",
        )
        _make_exec(
            self.charts_dir / "query-range.sh",
            "#!/bin/sh\nset -eu\nprintf '{\"status\":\"ok\",\"series\":[{\"name\":\"qps\",\"points\":[[1700000000,12.5],[1700000015,14.0]]}]}\\n'\n",
        )

    def tearDown(self) -> None:
        os.chdir(self.orig_cwd)
        self._td.cleanup()

    def _base_env(self) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", ""),
            "FLAGS": "fake-flags",
            "DELIVERY": "fake-deliv",
            "CHARTS": "fake-charts",
            "WORKLOAD_KILL_SWITCH": "canary-kill",
            "CONSOLE_OPS_LOG": str(self.log),
        }

    def test_rollback_engages_kill_switch_runs_delivery_and_writes_audit(self) -> None:
        rc = console_ops.main(["rollback", "--reason", "  high   error rate  ", "--by", "alice"], env=self._base_env())
        self.assertEqual(rc, 0)
        self.assertEqual(self.effects.read_text(encoding="utf-8").splitlines(), ["flag canary-kill on", "rollback"])
        entries = [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "rollback")
        self.assertEqual(entries[0]["by"], "alice")
        self.assertEqual(entries[0]["reason"], "high error rate")
        self.assertEqual(entries[0]["kill_switch"], "canary-kill")
        self.assertEqual(entries[0]["verdict"], "applied")

    def test_rollback_without_kill_switch_skips_flags_and_rejects_empty_reason(self) -> None:
        env = self._base_env()
        env["WORKLOAD_KILL_SWITCH"] = ""
        self.assertEqual(console_ops.main(["rollback", "--reason", "   ", "--by", "bob"], env=env), 1)
        self.assertFalse(self.effects.exists())

        self.assertEqual(console_ops.main(["rollback", "--reason", "manual drill", "--by", "bob"], env=env), 0)
        self.assertEqual(self.effects.read_text(encoding="utf-8").splitlines(), ["rollback"])

    def test_rollback_aborts_when_kill_switch_fails_and_records_failure(self) -> None:
        _make_exec(self.flags_dir / "set.sh", "#!/bin/sh\necho flag error >&2\nexit 2\n")
        rc = console_ops.main(["rollback", "--reason", "spike", "--by", "alice"], env=self._base_env())
        self.assertEqual(rc, 1)
        self.assertFalse(self.effects.exists())
        entries = [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(entries[0]["verdict"], "failed")
        self.assertEqual(entries[0]["rc"], 2)

    def test_freeze_on_and_off_honor_release_freeze_flag_and_reject_console_audit_path(self) -> None:
        env = self._base_env()
        env["RELEASE_FREEZE_FLAG"] = "custom_freeze"
        self.assertEqual(console_ops.main(["freeze", "--state", "on", "--reason", "window open", "--by", "alice"], env=env), 0)
        self.assertEqual(console_ops.main(["freeze", "--state", "off", "--reason", "window closed", "--by", "alice"], env=env), 0)
        self.assertEqual(
            self.effects.read_text(encoding="utf-8").splitlines(),
            ["flag custom_freeze on", "flag custom_freeze off"],
        )
        bad_env = dict(env, CONSOLE_OPS_LOG="console/evil.jsonl")
        self.assertEqual(console_ops.main(["freeze", "--state", "on", "--reason", "bad", "--by", "alice"], env=bad_env), 1)

    def test_charts_unconfigured_vs_configured_and_outside_adapter(self) -> None:
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = console_ops.main(["charts"], env={"PATH": os.environ.get("PATH", "")})
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue()), {"ok": False, "error": "CHARTS is not configured"})

        out_root = self.tmp / "ext-adapters"
        ext_charts = out_root / "mycharts"
        _make_exec(
            ext_charts / "query-range.sh",
            "#!/bin/sh\nset -eu\nprintf '{\"status\":\"ok\",\"series\":[{\"name\":\"err\",\"points\":[[10,0.01]]}]}\\n'\n",
        )
        env = {"PATH": os.environ.get("PATH", ""), "CHARTS": "outside/mycharts", "ADAPTERS_PATH": str(out_root)}
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = console_ops.main(["charts", "--window-s", "999999", "--step-s", "0"], env=env)
        self.assertEqual(rc2, 0)
        data = json.loads(buf2.getvalue())
        self.assertTrue(data["ok"])
        self.assertEqual(data["window_s"], 86400)
        self.assertEqual(data["step_s"], 1)
        self.assertEqual(data["series"][0]["name"], "err")


class ConsoleWorkstationNodeTest(unittest.TestCase):
    def _run_node(self, body_js: str) -> str:
        if shutil.which("node") is None:
            self.skipTest("node not on PATH")
        html = CONSOLE_HTML.read_text(encoding="utf-8")
        tower_m = __import__("re").search(r'<script type="application/json" id="tower-machine">(.*?)</script>', html, __import__("re").S)
        words_m = __import__("re").search(r'<script type="application/json" id="network-words">(.*?)</script>', html, __import__("re").S)
        scripts = __import__("re").findall(r"<script>(.*?)</script>", html, __import__("re").S)
        main_js = scripts[-1]
        core_js = main_js[: main_js.find('window.addEventListener("hashchange"')]
        assert tower_m and words_m

        harness = f"""
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
      toggle: () => {{}},
      add: () => {{}},
      remove: () => {{}},
      contains: () => false,
    }};
    this.value = "";
    this.open = false;
    this.focused = false;
    this.isConnected = true;
  }}
  setAttribute(k, v) {{
    this.attrs[k] = String(v);
    if (k === "id") idMap.set(String(v), this);
    if (k.startsWith("data-")) this.dataset[k.slice(5)] = String(v);
  }}
  getAttribute(k) {{ return k in this.attrs ? this.attrs[k] : null; }}
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
  focus() {{ this.focused = true; global.document.activeElement = this; }}
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
      if (sel === "[data-key]") return n.getAttribute("data-key") !== null;
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
const opsEl = new FakeNode("dialog"); opsEl.id = "ops-dialog";
const cmdBtn = new FakeNode("button"); cmdBtn.id = "cmd-k-btn";
const fixed = {{
  "theme": new FakeNode("select"),
  "main": mainEl,
  "who": new FakeNode("span"),
  "fresh": new FakeNode("span"),
  "announce": new FakeNode("div"),
  "card-back": new FakeNode("dialog"),
  "why": new FakeNode("dialog"),
  "launch-confirm": new FakeNode("dialog"),
  "onboard-wizard": new FakeNode("dialog"),
  "cmd-palette": cmdEl,
  "ops-dialog": opsEl,
  "cmd-k-btn": cmdBtn,
  "tower-machine": {{ textContent: {json.dumps(tower_m.group(1))} }},
  "network-words": {{ textContent: {json.dumps(words_m.group(1))} }},
}};
global.document = {{
  documentElement: new FakeNode("html"),
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
global.history = {{ replaceState: (_s, _t, u) => {{ global.location.hash = u; }} }};
global.matchMedia = () => ({{ matches: false }});
global.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
global.setInterval = () => 1;
global.clearInterval = () => {{}};
global.fetchCalls = [];
global.fetch = (url, opts) => {{
  global.fetchCalls.push({{ url: String(url), opts: opts || {{}} }});
  if (String(url) === "/v1/charts.json") {{
    return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve({{ ok: true, series: [{{ name: "qps", points: [[100, 1.5], [115, 2.5]] }}] }}) }});
  }}
  if (String(url) === "/v1/console.json") {{
    return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve(con) }});
  }}
  if (String(url) === "/v1/logbook.json") {{
    return Promise.resolve({{ ok: true, status: 304, headers: {{ get: () => null }} }});
  }}
  return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve({{ exit: 0, line: "GO: stub " + url }}) }});
}};
global.window = global;
""" + core_js + "\n" + body_js

        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as tf:
            tf.write(harness)
            tf_path = tf.name
        try:
            proc = subprocess.run(
                ["node", tf_path],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            return proc.stdout.strip()
        finally:
            os.unlink(tf_path)

    def test_cmd_palette_epics_dag_diff_mutants_adrs_charts_and_ops_in_node(self) -> None:
        out = self._run_node(
            """
(async () => {
  book = JSON.parse(fs.readFileSync("fixtures/logbook/golden-1.8.json", "utf8"));
  // Snapshot mode checks: zero fetches on #launch, operator actions disabled.
  con = { mode: "snapshot", auth: "loopback", can_launch: false, reason: "snapshot" };
  global.location.hash = "#launch";
  render();
  if (global.fetchCalls.length !== 0) throw new Error("snapshot #launch triggered fetches: " + JSON.stringify(global.fetchCalls));
  if (!mainEl.textContent.includes("Snapshot mode: no live charts")) throw new Error("missing snapshot charts notice");
  if (document.getElementById("launch-rollback-btn").attrs.disabled !== "") throw new Error("rollback btn not disabled in snapshot");

  // Golden 1.2 - 1.8 compatibility on #epics & #adrs.
  for (const gf of ["fixtures/logbook/golden-1.2.json", "fixtures/logbook/golden-1.5.json", "fixtures/logbook/golden-1.8.json"]) {
    book = JSON.parse(fs.readFileSync(gf, "utf8"));
    global.location.hash = "#epics";
    render();
    if (!document.getElementById("epics-dag")) throw new Error("missing #epics-dag on " + gf);
    global.location.hash = "#adrs";
    render();
    drawAdrs();
  }

  // Synthetic depends_on cycle guard + epics summary.
  book = JSON.parse(fs.readFileSync("fixtures/logbook/golden-1.8.json", "utf8"));
  book.epics = [{ id: "E-1", title: "Core Control Plane", state: "in-progress", tickets: ["0004", "0005"] }];
  book.tickets.find((x) => x.id === "0004").depends_on = ["0005"];
  book.tickets.find((x) => x.id === "0005").depends_on = ["0004"];
  global.location.hash = "#epics";
  render();
  if (document.getElementById("epics-dag").querySelectorAll(".dag-edge").length < 2) throw new Error("missing DAG edges");

  // Unified diff highlighter preserves raw textContent byte-for-byte + explores mutants.
  const rawPatch = "diff --git a/a.py b/a.py\\n@@ -1 +1 @@\\n-old\\n+new";
  const pre = renderPatchPre(rawPatch);
  if (pre.textContent !== rawPatch) throw new Error("diff textContent mismatch");
  if (!pre.querySelector(".diff-add") || !pre.querySelector(".diff-del") || !pre.querySelector(".diff-hunk")) {
    throw new Error("missing diff token classes");
  }
  const t3 = book.tickets.find((x) => x.id === "0003");
  t3.ci = { why: { attempt: 1, gate: "mutation", line: "NO-GO: mutation 0.50 < 0.80", details: ["survived mutant a.py:4"], sast: [] } };
  global.location.hash = "#ticket/0003";
  render();
  if (!document.getElementById("gate-mutant-explorer")) throw new Error("missing gate/mutant explorer on #ticket/0003");

  // ADR Explorer filter & #adr/<n> link inspector.
  global.location.hash = "#adr/" + book.adrs.items[0].number;
  render();
  if (!document.getElementById("adr-link-explorer")) throw new Error("missing #adr-link-explorer");

  // Live mode: Cmd+K palette (with IME safety) + charts fetch + all 5 POST routes.
  con = { mode: "live", auth: "loopback", can_launch: true, identity: "loopback", read_at: "2026-11-24T00:00:00Z" };
  global.location.hash = "#launch";
  render();
  await Promise.resolve();
  await Promise.resolve();
  if (!global.fetchCalls.some((c) => c.url === "/v1/charts.json")) throw new Error("live #launch did not fetch /v1/charts.json");

  openCmdPalette(cmdBtn);
  const inp = document.getElementById("cmd-k-input");
  inp.value = "epics";
  inp.dispatchEvent({ type: "input" });
  // IME composing Enter must NOT navigate or close dialog.
  inp.dispatchEvent({ type: "keydown", key: "Enter", isComposing: true, keyCode: 229 });
  if (!cmdEl.open) throw new Error("IME Enter closed command palette prematurely");
  inp.dispatchEvent({ type: "keydown", key: "Enter", isComposing: false, keyCode: 13 });
  if (cmdEl.open) throw new Error("Enter did not execute selected entry and close palette");
  if (!global.location.hash.startsWith("#epics")) throw new Error("palette did not navigate to #epics: " + global.location.hash);

  // Confirm modal & literal POSTs.
  openOpsDialog("rollback", {}, cmdBtn);
  const rIn = document.getElementById("ops-reason-input");
  rIn.value = "synthetic probe breach";
  document.getElementById("ops-dialog-confirm").click();
  await new Promise((r) => setTimeout(r, 10));
  openOpsDialog("freeze", { state: "on" }, cmdBtn);
  document.getElementById("ops-reason-input").value = "maintenance";
  document.getElementById("ops-dialog-confirm").click();
  await new Promise((r) => setTimeout(r, 10));
  await assignTicket("0005");
  await resumeTicket("0004");
  await watchOnce();

  const posted = new Set(global.fetchCalls.filter((c) => c.opts.method === "POST").map((c) => c.url));
  for (const r of ["/v1/assign", "/v1/resume", "/v1/watch-once", "/v1/rollback", "/v1/freeze"]) {
    if (!posted.has(r)) throw new Error("missing POST call for " + r);
  }
  console.log("NODE_OK");
})();
"""
        )
        self.assertIn("NODE_OK", out)


if __name__ == "__main__":
    unittest.main()
