"""Spec-derived unit tests for modular Console build and SSE client (ADR 0124).

Strictly compliant with ci/vendor-free.sh, ci/app-free.sh, and ci/model-free.sh:
validates console/build.py determinism, drift detection, orphan-module rejection,
static perimeter invariants across console/src/*, and Node 24 progressive SSE
behavior across live, snapshot, replay, and fallback states.
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

from core import adr_gov

ROOT = Path(__file__).resolve().parents[2]
ADR_0124 = ROOT / "docs" / "adr" / "0124-modular-console-build-and-sse-streaming.md"
CONSOLE_DIR = ROOT / "console"
SRC_DIR = CONSOLE_DIR / "src"
BUILD_PY = CONSOLE_DIR / "build.py"
INDEX_HTML = CONSOLE_DIR / "index.html"

EXPECTED_MODULES = (
    "00-head.html",
    "01-styles.css",
    "02-shell.html",
    "03-state.js",
    "04-screens.js",
    "05-kanban-network.js",
    "06-hive-wizard.js",
    "07-runtime-sse.js",
    "99-tail.html",
)


def _extract_core_js(html: str) -> tuple[str, str, str]:
    tower_m = re.search(
        r'<script type="application/json" id="tower-machine">(.*?)</script>',
        html,
        re.S,
    )
    words_m = re.search(
        r'<script type="application/json" id="network-words">(.*?)</script>',
        html,
        re.S,
    )
    assert tower_m is not None and words_m is not None
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) >= 2
    main_js = scripts[-1]
    boot_idx = main_js.find('window.addEventListener("hashchange"')
    assert boot_idx > 0
    return tower_m.group(1), words_m.group(1), main_js[:boot_idx]


def _run_sse_node_probe(js_body: str) -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
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
    this.open = false;
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
  focus() {{ activeEl = this; }}
  showModal() {{ this.open = true; }}
  close() {{ this.open = false; }}
  getBoundingClientRect() {{ return {{ left: 0, right: 100, top: 0, bottom: 40, width: 100, height: 40 }}; }}
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
  querySelectorAll() {{ return []; }}
  querySelector() {{ return null; }}
  get id() {{ return this.attrs.id || ""; }}
  set id(v) {{ this.setAttribute("id", v); }}
  get className() {{ return this.attrs.class || ""; }}
  set className(v) {{ this.attrs.class = v; }}
  get textContent() {{
    return this.children.map((c) => ("text" in c ? c.text : c.textContent)).join("");
  }}
  set textContent(v) {{ this.replaceChildren(String(v)); }}
}}

const eventSources = [];
class FakeEventSource {{
  constructor(url) {{
    this.url = url;
    this.closed = false;
    this.listeners = {{}};
    eventSources.push(this);
  }}
  addEventListener(type, fn) {{
    (this.listeners[type] ||= []).push(fn);
  }}
  emit(type, dataObj) {{
    const ev = dataObj !== undefined ? {{ type, data: typeof dataObj === "string" ? dataObj : JSON.stringify(dataObj) }} : {{ type }};
    for (const fn of this.listeners[type] || []) fn(ev);
  }}
  close() {{
    this.closed = true;
  }}
}}

const htmlRoot = new FakeNode("html");
const mainEl = new FakeNode("main"); mainEl.id = "main";
const fixedMap = {{
  "theme": new FakeNode("select"),
  "main": mainEl,
  "who": new FakeNode("span"),
  "fresh": new FakeNode("span"),
  "announce": new FakeNode("div"),
  "card-back": new FakeNode("dialog"),
  "why": new FakeNode("dialog"),
  "launch-confirm": new FakeNode("dialog"),
  "onboard-wizard": new FakeNode("dialog"),
  "tower-machine": {{ textContent: {json.dumps(tower_json)} }},
  "network-words": {{ textContent: {json.dumps(words_json)} }},
}};
global.document = {{
  documentElement: htmlRoot,
  body: new FakeNode("body"),
  createElement: (tag) => new FakeNode(tag),
  createElementNS: (_ns, tag) => new FakeNode(tag),
  createTextNode: (t) => ({{ text: String(t) }}),
  getElementById: (id) => fixedMap[id] || idRegistry.get(id) || null,
  querySelectorAll: () => [],
  querySelector: () => null,
  addEventListener: () => {{}},
  get activeElement() {{ return activeEl; }},
}};
global.Node = FakeNode;
global.CSS = {{ escape: (s) => String(s) }};
global.HTMLDialogElement = function() {{}};
global.HTMLDialogElement.prototype = {{}};
global.location = {{ hash: "#overview" }};
global.history = {{ replaceState: () => {{}} }};
global.matchMedia = () => ({{ matches: false }});
global.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
global.EventSource = FakeEventSource;
global.setInterval = () => 1;
global.clearInterval = () => {{}};
global.setTimeout = () => 1;
global.clearTimeout = () => {{}};
global.window = global;

const fetchLog = [];
let mockConsoleResp = {{ mode: "live", read_at: "2026-11-24T10:00:00Z", read_error: null }};
let mockLogbookStatus = 304;
let mockLogbookTag = '"tag-1"';
let mockLogbookBody = JSON.parse(fs.readFileSync("fixtures/logbook/golden-1.8.json", "utf8"));
global.fetch = async (url, opts) => {{
  fetchLog.push({{ url, opts: opts || {{}} }});
  if (url === "/v1/console.json") {{
    return {{ status: 200, ok: true, json: async () => mockConsoleResp }};
  }}
  if (url === "/v1/logbook.json") {{
    const inm = (opts && opts.headers && opts.headers["If-None-Match"]) || null;
    if (inm === mockLogbookTag && mockLogbookStatus === 304) {{
      return {{ status: 304, ok: false, headers: {{ get: () => mockLogbookTag }}, json: async () => ({{}}) }};
    }}
    return {{
      status: 200,
      ok: true,
      headers: {{ get: (k) => (k.toLowerCase() === "etag" ? mockLogbookTag : null) }},
      json: async () => mockLogbookBody,
    }};
  }}
  throw new Error("unexpected fetch url: " + url);
}};
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
            timeout=45,
            check=False,
        )
    finally:
        os.unlink(tf_path)
    if res.returncode != 0:
        raise AssertionError(
            f"Node probe failed ({res.returncode}):\nSTDOUT:{res.stdout}\nSTDERR:{res.stderr}"
        )
    return res.stdout.strip()


class ModularBuildUnitTest(unittest.TestCase):
    def test_adr_0124_passes_adr_gov_validation(self) -> None:
        self.assertTrue(ADR_0124.is_file(), "docs/adr/0124-modular-console-build-and-sse-streaming.md must exist")
        defects = adr_gov.validate_adr(ADR_0124.read_text(encoding="utf-8"))
        self.assertEqual(defects, [])

    def test_console_src_manifest_and_check_passes(self) -> None:
        self.assertTrue(BUILD_PY.is_file(), "console/build.py must exist")
        self.assertTrue(os.access(BUILD_PY, os.X_OK), "console/build.py must be executable (+x)")
        self.assertTrue(SRC_DIR.is_dir(), "console/src/ directory must exist")
        present = tuple(sorted(p.name for p in SRC_DIR.iterdir() if p.is_file()))
        self.assertEqual(present, EXPECTED_MODULES)

        res = subprocess.run(
            ["python3", str(BUILD_PY), "--check"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_build_py_deterministic_write_and_drift_detection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tmp_src = tmp / "src"
            shutil.copytree(SRC_DIR, tmp_src)
            out1 = tmp / "index1.html"
            out2 = tmp / "index2.html"

            for out_path in (out1, out2):
                res = subprocess.run(
                    ["python3", str(BUILD_PY), "--src", str(tmp_src), "--out", str(out_path), "--write"],
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

            bytes1 = out1.read_bytes()
            bytes2 = out2.read_bytes()
            self.assertEqual(bytes1, bytes2, "console/build.py output must be byte-for-byte deterministic")
            self.assertEqual(bytes1, INDEX_HTML.read_bytes(), "committed console/index.html must match build output")

            # Introduce drift in out1 and verify --check exits 1 with diff hint
            out1.write_text(out1.read_text(encoding="utf-8") + "<!-- drifted -->\n", encoding="utf-8")
            drift = subprocess.run(
                ["python3", str(BUILD_PY), "--src", str(tmp_src), "--out", str(out1), "--check"],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(drift.returncode, 1)
            combined = drift.stdout + drift.stderr
            self.assertIn("drifted", combined)
            self.assertIn("console/build.py", combined)

    def test_build_py_rejects_unlisted_orphan_module_and_missing_module(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tmp_src = tmp / "src"
            shutil.copytree(SRC_DIR, tmp_src)
            out = tmp / "index.html"

            rogue = tmp_src / "98-rogue.js"
            rogue.write_text("// unlisted\n", encoding="utf-8")
            res_rogue = subprocess.run(
                ["python3", str(BUILD_PY), "--src", str(tmp_src), "--out", str(out), "--write"],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(res_rogue.returncode, 1)
            self.assertIn("98-rogue.js", res_rogue.stdout + res_rogue.stderr)

            rogue.unlink()
            (tmp_src / "06-hive-wizard.js").unlink()
            res_missing = subprocess.run(
                ["python3", str(BUILD_PY), "--src", str(tmp_src), "--out", str(out), "--write"],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(res_missing.returncode, 1)
            self.assertIn("06-hive-wizard.js", res_missing.stdout + res_missing.stderr)

    def test_console_sources_preserve_static_perimeter_and_wcag_tokens(self) -> None:
        checked_files = 0
        for path in sorted(CONSOLE_DIR.rglob("*")):
            if not path.is_file() or path.suffix == ".md":
                continue
            if "snapshot" in path.relative_to(CONSOLE_DIR).parts or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            checked_files += 1
            low = text.lower()
            for banned in ("adapters/", ".crew/", "firestore", "state_store"):
                self.assertNotIn(banned, low, f"{path} contains forbidden substring {banned!r}")

        self.assertGreaterEqual(checked_files, len(EXPECTED_MODULES) + 3)

        html = INDEX_HTML.read_text(encoding="utf-8")
        for banned_html in ("xmlhttprequest", "sendbeacon", "<form"):
            self.assertNotIn(banned_html, html.lower())
        self.assertNotRegex(html, r'(?i)\b(?:src|href)\s*=\s*["\']https?:')
        for marker in (
            'id="tower-machine"',
            'id="network-words"',
            'id: "your-turn"',
            'id: "journey"',
            'href="#hive"',
            'id="onboard-wizard"',
            'new EventSource("/v1/events")',
            "--bg: #f6f7f9;",
            "--bg: #0f1419;",
        ):
            self.assertIn(marker, html)


class ConsoleSseClientNodeTest(unittest.TestCase):
    def test_snapshot_mode_opens_zero_eventsource_connections(self) -> None:
        if shutil.which("node") is None:
            self.fail("node is not on PATH: this test runs in the workbench, ./go ci")
        out = _run_sse_node_probe("""
(async () => {
  book = mockLogbookBody;
  con = { mode: "snapshot", read_at: null, read_error: null };
  etag = '"snap-tag"';
  syncEventStream();
  if (eventSources.length !== 0) throw new Error("snapshot opened EventSource: " + eventSources.length);
  if (htmlRoot.dataset.sse !== "off") throw new Error("expected sse=off, got " + htmlRoot.dataset.sse);
  if (fetchLog.length !== 0) throw new Error("unexpected fetch in snapshot sync: " + fetchLog.length);
  console.log("SNAPSHOT_ZERO_SSE_OK");
})().catch((e) => { console.error(e); process.exit(1); });
""")
        self.assertEqual(out, "SNAPSHOT_ZERO_SSE_OK")

    def test_live_mode_sse_triggers_conditional_fetch_only_on_etag_or_error_change(self) -> None:
        if shutil.which("node") is None:
            self.fail("node is not on PATH: this test runs in the workbench, ./go ci")
        out = _run_sse_node_probe("""
(async () => {
  mockLogbookStatus = 200;
  await load();
  render();
  syncEventStream();
  if (eventSources.length !== 1) throw new Error("expected 1 EventSource, got " + eventSources.length);
  const es = eventSources[0];
  if (es.url !== "/v1/events") throw new Error("unexpected SSE url: " + es.url);
  es.emit("open");
  if (htmlRoot.dataset.sse !== "live") throw new Error("expected sse=live after open, got " + htmlRoot.dataset.sse);

  fetchLog.length = 0;
  mockLogbookStatus = 304;
  // Matching ETag & matching null read_error -> zero HTTP fetches
  es.emit("logbook", { etag: '"tag-1"', mode: "live", read_at: "2026-11-24T10:00:00Z", read_error: null });
  await new Promise((r) => setImmediate(r));
  if (fetchLog.length !== 0) throw new Error("unchanged etag triggered fetch: " + JSON.stringify(fetchLog));

  // Changed ETag -> immediate conditional fetch with If-None-Match: "tag-1"
  mockLogbookTag = '"tag-2"';
  mockLogbookStatus = 200;
  mockConsoleResp = { mode: "live", read_at: "2026-11-24T10:00:02Z", read_error: null };
  es.emit("logbook", { etag: '"tag-2"', mode: "live", read_at: "2026-11-24T10:00:02Z", read_error: null });
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  const lbCalls = fetchLog.filter((c) => c.url === "/v1/logbook.json");
  if (lbCalls.length !== 1) throw new Error("expected 1 logbook fetch on etag change, got " + lbCalls.length);
  if (lbCalls[0].opts.headers["If-None-Match"] !== '"tag-1"') {
    throw new Error("expected If-None-Match 'tag-1', got " + JSON.stringify(lbCalls[0].opts.headers));
  }
  if (etag !== '"tag-2"') throw new Error("etag did not update to tag-2: " + etag);

  // Changed read_error with same ETag -> triggers tick() to surface error banner
  fetchLog.length = 0;
  mockLogbookStatus = 304;
  mockConsoleResp = { mode: "live", read_at: "2026-11-24T10:00:02Z", read_error: "reader timeout" };
  es.emit("logbook", { etag: '"tag-2"', mode: "live", read_at: "2026-11-24T10:00:02Z", read_error: "reader timeout" });
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  if (fetchLog.length !== 2) throw new Error("expected console+logbook fetch on read_error change, got " + fetchLog.length);
  if (con.read_error !== "reader timeout") throw new Error("con.read_error not updated: " + con.read_error);
  console.log("LIVE_SSE_FETCH_OK");
})().catch((e) => { console.error(e); process.exit(1); });
""")
        self.assertEqual(out, "LIVE_SSE_FETCH_OK")

    def test_eventsource_fallback_replay_pause_and_simulated_snapshot_close(self) -> None:
        if shutil.which("node") is None:
            self.fail("node is not on PATH: this test runs in the workbench, ./go ci")
        out = _run_sse_node_probe("""
(async () => {
  mockLogbookStatus = 200;
  await load();
  render();
  syncEventStream();
  const es = eventSources[0];

  // Error event transitions state to fallback without closing or throwing; 2s tick() still works
  es.emit("error");
  if (htmlRoot.dataset.sse !== "fallback") throw new Error("expected sse=fallback on error, got " + htmlRoot.dataset.sse);
  mockLogbookTag = '"tag-3"';
  await tick();
  if (etag !== '"tag-3"') throw new Error("fallback tick() failed to advance etag: " + etag);

  // Starting network replay closes SSE stream so zero requests occur during replay
  startReplay("0010", false);
  if (!es.closed || htmlRoot.dataset.sse !== "off") {
    throw new Error("startReplay must close EventSource; closed=" + es.closed + " sse=" + htmlRoot.dataset.sse);
  }
  replay = null;

  // Simulated live page hitting a snapshot server frame closes SSE cleanly without fetching
  syncEventStream();
  const es2 = eventSources[1];
  fetchLog.length = 0;
  es2.emit("logbook", { etag: '"other-snap"', mode: "snapshot", read_at: null, read_error: null });
  await new Promise((r) => setImmediate(r));
  if (!es2.closed || fetchLog.length !== 0) {
    throw new Error("snapshot frame on simulated live page must close stream without fetching");
  }

  // Environment without EventSource degrades cleanly to fallback
  delete global.EventSource;
  syncEventStream();
  if (htmlRoot.dataset.sse !== "fallback") {
    throw new Error("missing EventSource must set sse=fallback, got " + htmlRoot.dataset.sse);
  }
  console.log("FALLBACK_AND_REPLAY_OK");
})().catch((e) => { console.error(e); process.exit(1); });
""")
        self.assertEqual(out, "FALLBACK_AND_REPLAY_OK")


if __name__ == "__main__":
    unittest.main()
