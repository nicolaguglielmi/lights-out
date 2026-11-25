"""Tests for ADR 0135 logbook enrichment, TUI view 6, and Console #thresholds / jury / provenance UI.

Strictly vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import adr_gov, logbook, tui

REPO = Path(__file__).resolve().parents[2]
CONSOLE_HTML = REPO / "console" / "index.html"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
CONTRACT_DIRNAME = "." + "lights" + "-out"


def _write_rich_workspace(root: Path) -> dict[str, str]:
    (root / "policy").mkdir(parents=True)
    (root / "policy" / "merge.env").write_text(
        "RISK_HIGH_MIGRATIONS=\n"
        "RISK_HIGH_AUTH=\n"
        "RISK_HIGH_PAYMENTS=\n"
        "RISK_SIZE_EXCLUDE=*.lock\n"
        "RISK_AUTO_MERGE_LEVELS=low\n"
        "# Provenance, external: 200 lines median review guidance.\n"
        "RISK_SIZE_MEDIUM_LINES=200\n"
        "# Provenance, decision: one score quantum.\n"
        "MUTATION_MARGIN=0.10\n"
        "# Provenance, decision: verify error delta.\n"
        "ERROR_RATE_MAX_DELTA=0.01\n"
        "# Provenance, decision: verify latency delta.\n"
        "LATENCY_P95_MAX_DELTA=0.25\n"
        "RISK_REQUIRE_ADR=off\n",
        encoding="utf-8",
    )
    (root / "policy" / "rework.env").write_text(
        "# Provenance, decision: max 4 attempts.\n"
        "REWORK_MAX_ATTEMPTS=4\n"
        "# Provenance, decision: default open budget.\n"
        "SPEND_BUDGET_TICKET_USD=\n"
        "SPEND_BUDGET_DAY_USD=\n",
        encoding="utf-8",
    )
    (root / "policy" / "mainline.env").write_text(
        "# Provenance: owner decision for mainline grouping.\n"
        "MAINLINE_GROUP_MAX=4\n",
        encoding="utf-8",
    )
    for role in ("dev-worker", "architect", "product", "release", "test-reviewer", "ops-triage"):
        rdir = root / "roles" / role
        rdir.mkdir(parents=True)
        (rdir / "role.env").write_text("THINKING=low\nTIMEOUT_S=900\nTOKEN_BUDGET=100000\n", encoding="utf-8")

    contract = root / CONTRACT_DIRNAME
    contract.mkdir(parents=True)
    (contract / "workload.env").write_text(
        "# Provenance, measurement: 100 mutants, 80 killed.\n"
        "MUTATION_BASELINE=0.80\n",
        encoding="utf-8",
    )
    (contract / "policy.env").write_text(
        "# Provenance, decision: tighter local mutation margin.\n"
        "MUTATION_MARGIN=0.05\n",
        encoding="utf-8",
    )

    crew = root / ".crew"
    crew.mkdir(parents=True)
    (crew / "start-plan.env").write_text("SPEND_BUDGET_TICKET_USD=5.00\n", encoding="utf-8")

    (root / "tickets").mkdir()
    (root / "tickets" / "0001.md").write_text(
        "# Feature one\n\n- Feature: core\n- Priority: P1\n\n## Perimeter\n- core/*\n",
        encoding="utf-8",
    )

    doc_0001 = {
        "id": "0001",
        "state": "held",
        "attempt": 1,
        "history": [
            {"step": "open", "state": "opened", "at": "2026-11-25T08:00:00Z"},
            {"step": "assign", "state": "pr-opened", "attempt": 1, "at": "2026-11-25T08:10:00Z"},
            {"step": "review", "state": "ci-passed", "at": "2026-11-25T08:20:00Z"},
            {"step": "release", "state": "held", "at": "2026-11-25T08:30:00Z"},
        ],
        "release": {"version": "0.9.1-rc1", "image": "img@sha256:" + "a" * 64, "verdict": "held"},
        "review": {
            "role": "test-reviewer",
            "verdict": "approved",
            "jury": {
                "mode": "cross-vendor",
                "cross_vendor": True,
                "author_provider": "alpha",
                "reviewer_provider": "beta",
                "reviewer_model": "beta-large",
                "reviewer_harness": "beta-cli",
                "reason": "different provider selected by jury",
            },
        },
        "spend": [
            {
                "role": "dev-worker",
                "step": "assign",
                "attempt": 1,
                "harness": "alpha-cli",
                "provider": "alpha",
                "model": "alpha-v1",
                "replayed": False,
                "cost_usd": 1.25,
                "duration_s": 30,
                "at": "2026-11-25T08:10:00Z",
            }
        ],
    }

    (crew / "releases.jsonl").write_text(
        json.dumps({
            "kind": "release",
            "at": "2026-11-25T08:30:00Z",
            "ticket": "0001",
            "record": {"version": "0.9.1-rc1", "stable": "0.9.0", "verdict": "held", "held_at": 25, "stages": []},
            "brief_run": {
                "role": "release",
                "harness": "alpha-cli",
                "provider": "alpha",
                "model": "alpha-v1",
                "replayed": False,
                "tokens": {"input": 100_000, "output": 10_000},
                "duration_s": 5,
                "at": "2026-11-25T08:32:00Z",
            },
        })
        + "\n",
        encoding="utf-8",
    )
    (crew / "release.json").write_text(
        json.dumps({"version": "0.9.1-rc1", "stable": "0.9.0", "verdict": "held", "held_at": 25, "stages": []}),
        encoding="utf-8",
    )

    prov_dir = crew / "provenance"
    prov_dir.mkdir(parents=True)
    stmt = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": "0.9.1-rc1", "digest": {"sha256": "a" * 64}}],
        "predicateType": "https://lights-out.dev/provenance/v1",
        "predicate": {
            "adrs": [{"path": "docs/adr/0135-m17-thresholds-and-complete-worker-ledger.md", "sha256": "b" * 64}],
            "agents": [{"role": "dev-worker"}],
            "gates": [{"gate": "risk", "verdict": "GO"}],
            "review": {"jury": doc_0001["review"]["jury"]},
            "missing": [],
        },
    }
    stmt_path = prov_dir / "0.9.1-rc1.intoto.json"
    stmt_bytes = json.dumps(stmt).encode("utf-8")
    stmt_path.write_bytes(stmt_bytes)
    (prov_dir / "0.9.1-rc1.intoto.json.bundle").write_text('{"mediaType":"sigstore"}', encoding="utf-8")

    (root / "adapters" / "stub-state").mkdir(parents=True)
    (root / "adapters" / "stub-state" / "get.sh").write_text(
        f"""#!/bin/sh
set -eu
if [ "$1" = "tickets" ] && [ "$2" = "0001" ]; then
  cat <<'JSON'
{json.dumps(doc_0001)}
JSON
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    (root / "adapters" / "stub-delivery").mkdir(parents=True)
    (root / "adapters" / "stub-delivery" / "status.sh").write_text(
        '#!/bin/sh\necho \'{"stable":"0.9.0","canary":"0.9.1-rc1","weight":25}\'\n',
        encoding="utf-8",
    )
    (root / "adapters" / "stub-delivery" / "promote.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(root / "adapters" / "stub-state" / "get.sh", 0o755)
    os.chmod(root / "adapters" / "stub-delivery" / "status.sh", 0o755)

    (root / "profiles").mkdir(exist_ok=True)
    (root / "profiles" / "prices.json").write_text(
        json.dumps({
            "prices": [
                {"provider": "alpha", "model": "alpha-v1", "input": 1.0, "output": 4.0, "source": "https://example.invalid/alpha", "checked": "2026-11-25"}
            ]
        }),
        encoding="utf-8",
    )
    return {"stmt_sha": hashlib.sha256(stmt_bytes).hexdigest()}


class LogbookTuiThresholdsTests(unittest.TestCase):
    def test_adr_0135_valid(self) -> None:
        adr = REPO / "docs" / "adr" / "0135-m17-thresholds-and-complete-worker-ledger.md"
        self.assertEqual(adr_gov.validate_adr(adr.read_text(encoding="utf-8")), [])

    def test_logbook_deterministic_with_enriched_thresholds_jury_provenance_and_release_spend(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            old = Path.cwd()
            try:
                root = Path(td)
                meta = _write_rich_workspace(root)
                os.chdir(root)
                env = {
                    **os.environ,
                    "STATE_STORE": "stub-state",
                    "DELIVERY": "stub-delivery",
                    "WORKLOAD_CONTRACT": CONTRACT_DIRNAME,
                    "TICKETS_DIR": "tickets",
                    "RELEASES_LOG": ".crew/releases.jsonl",
                    "RELEASE_RECORD": ".crew/release.json",
                    "PROVENANCE_DIR": ".crew/provenance",
                    "CREW_DIR": ".crew",
                    "ROLES_DIR": "roles",
                }
                with mock.patch.dict(os.environ, env, clear=True):
                    self.assertEqual(logbook.main(["--out", "out1.json"]), 0)
                    self.assertEqual(logbook.main(["--out", "out2.json"]), 0)
                raw1 = (root / "out1.json").read_bytes()
                raw2 = (root / "out2.json").read_bytes()
                self.assertEqual(raw1, raw2, "equal inputs must emit byte-identical logbook JSON")
                book = json.loads(raw1)
                self.assertIsInstance(book["thresholds"], list, book["thresholds"])

                by_id = {t["id"]: t for t in book["thresholds"]}
                self.assertEqual(by_id["MUTATION_MARGIN"]["default"], "0.10")
                self.assertEqual(by_id["MUTATION_MARGIN"]["active"], "0.05")
                self.assertTrue(by_id["MUTATION_MARGIN"]["changed"])
                self.assertEqual(by_id["SPEND_BUDGET_TICKET_USD"]["active"], "5.00")
                self.assertEqual(by_id["SPEND_BUDGET_TICKET_USD"]["source"], ".crew/start-plan.env")

                t1 = book["tickets"][0]
                self.assertTrue(t1["jury"]["cross_vendor"])
                self.assertEqual(t1["jury"]["reviewer_provider"], "beta")
                ps = t1["provenance_summary"]
                self.assertTrue(ps["complete"])
                self.assertTrue(ps["signed"])
                self.assertEqual(ps["statement_sha256"], meta["stmt_sha"])

                self.assertIn("release", book["spend"]["roles"])
                self.assertEqual(book["spend"]["roles"]["release"]["runs"], 1)

                # Verify TUI view 6 (thresholds) in 80x24 and NO_COLOR=1
                out_tui = io.StringIO()
                with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
                    rc = tui.main(
                        ["--logbook", str(root / "out1.json"), "--once", "--view", "6", "--width", "80", "--height", "24"],
                        stdin=io.StringIO(""),
                        stdout=out_tui,
                        stderr=io.StringIO(),
                    )
                self.assertEqual(rc, 0)
                rendered = out_tui.getvalue()
                self.assertIsNone(ANSI_RE.search(rendered))
                self.assertEqual(len(rendered.splitlines()), 24)
                for line in rendered.splitlines():
                    self.assertLessEqual(len(line), 80)
                self.assertIn("6:Thresholds", rendered)
                self.assertIn("MUTATION_MARGIN", rendered)
                self.assertIn("cross-vendor", rendered)
                self.assertIn("complete/signed", rendered)

                # Validate summary JSON includes thresholds_governance
                out_json = io.StringIO()
                rc_j = tui.main(
                    ["--logbook", str(root / "out1.json"), "--json", "--view", "thresholds"],
                    stdin=io.StringIO(""),
                    stdout=out_json,
                    stderr=io.StringIO(),
                )
                self.assertEqual(rc_j, 0)
                summary = json.loads(out_json.getvalue())
                self.assertEqual(summary["view"], "thresholds")
                self.assertEqual(summary["thresholds_governance"]["jury_tickets"], 1)
                self.assertEqual(summary["thresholds_governance"]["provenance_tickets"], 1)
            finally:
                os.chdir(old)


class ConsoleBuildAndDomThresholdsTests(unittest.TestCase):
    def test_console_build_check_passes(self) -> None:
        proc = subprocess.run(
            ["python3", "console/build.py", "--check"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_console_thresholds_and_ticket_jury_provenance_dom(self) -> None:
        if shutil.which("node") is None:
            self.fail("node is not on PATH: this test runs in the workbench, ./go ci")
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
    this.classList = {{ toggle: () => {{}}, add: () => {{}}, remove: () => {{}}, contains: () => false }};
    this.value = "";
    this.open = false;
    this.returnValue = "";
    this.hidden = false;
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
  focus() {{}}
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
const fixed = {{
  "theme": new FakeNode("select"), "main": mainEl, "who": new FakeNode("span"), "fresh": new FakeNode("span"),
  "sse-pill": new FakeNode("span"), "role-pill": new FakeNode("span"),
  "persona-junior": new FakeNode("button"), "persona-senior": new FakeNode("button"),
  "cmd-k-btn": new FakeNode("button"), "announce": new FakeNode("div"),
  "card-back": new FakeNode("dialog"), "why": new FakeNode("dialog"), "launch-confirm": new FakeNode("dialog"),
  "onboard-wizard": new FakeNode("dialog"), "cmd-palette": new FakeNode("dialog"), "ops-dialog": new FakeNode("dialog"),
  "tower-machine": {{ textContent: {json.dumps(tower_m.group(1))} }},
  "network-words": {{ textContent: {json.dumps(words_m.group(1))} }},
}};
global.document = {{
  documentElement: new FakeNode("html"), body: new FakeNode("body"), activeElement: fixed["cmd-k-btn"],
  createElement: (t) => new FakeNode(t), createElementNS: (_ns, t) => new FakeNode(t),
  createTextNode: (t) => ({{ text: String(t) }}),
  getElementById: (id) => fixed[id] || idMap.get(id) || null,
  querySelectorAll: (s) => mainEl.querySelectorAll(s), querySelector: (s) => mainEl.querySelector(s),
  addEventListener: () => {{}},
}};
global.Node = FakeNode;
global.CSS = {{ escape: (s) => String(s) }};
global.HTMLDialogElement = function() {{}};
global.HTMLDialogElement.prototype = {{}};
global.location = {{ hash: "#thresholds" }};
global.history = {{ replaceState: () => {{}} }};
global.matchMedia = () => ({{ matches: false }});
global.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
global.fetch = () => Promise.reject(new Error("no fetch"));
global.setInterval = () => 1;
global.clearInterval = () => {{}};
global.window = global;
""" + core_js + """
const golden = JSON.parse(fs.readFileSync("fixtures/logbook/golden-1.8.json", "utf8"));
book = Object.assign({}, golden, {
  thresholds: [
    {
      id: "MUTATION_MARGIN", key: "MUTATION_MARGIN", value: "0.05", default: "0.10", active: "0.05",
      changed: true, overridden: true, file: "contract/policy.env", source: "contract/policy.env",
      judge: "gates", scope: "gates", provenance: { kind: "decision", text: "tighter local margin" },
      last_commit: { short: "abc123def456", subject: "tighten margin", at: "2026-11-25T08:00:00Z" },
      history: [{ short: "abc123def456", subject: "tighten margin", at: "2026-11-25T08:00:00Z" }]
    },
    {
      id: "REWORK_MAX_ATTEMPTS", key: "REWORK_MAX_ATTEMPTS", value: "4", default: "4", active: "4",
      changed: false, overridden: false, file: "policy/rework.env", source: "policy/rework.env",
      judge: "rework", scope: "rework", provenance: { kind: "decision", text: "four attempts" },
      last_commit: null, history: []
    }
  ]
});
con = { mode: "snapshot", default_persona: "junior", can_launch: true };
render();
if (!mainEl.textContent.includes("Modified vs default")) throw new Error("missing threshold summary");
if (!mainEl.textContent.includes("tighter local margin")) throw new Error("missing provenance");
if (!document.getElementById("agents")) throw new Error("missing #agents on #thresholds");

// Cockpit mode shows git commit History column & commits
setPersona("senior", false);
render();
if (!mainEl.textContent.includes("abc123def456")) throw new Error("missing commit sha in Cockpit thresholds");

// Legacy golden-1.8 thresholds work too + filter buttons
book = JSON.parse(fs.readFileSync("fixtures/logbook/golden-1.8.json", "utf8"));
book.tickets[0].jury = {
  mode: "cross-vendor", cross_vendor: true, author_provider: "alpha",
  reviewer_provider: "beta", reviewer_model: "beta-large", reason: "cross provider review"
};
book.tickets[0].provenance_summary = {
  subject: "0.9.1-rc1", statement: "prov/0.9.1-rc1.intoto.json",
  statement_sha256: "a".repeat(64), bundle_sha256: "b".repeat(64),
  signed: true, complete: true, missing: [], missing_count: 0,
  adrs_count: 1, agents_count: 2, gates_count: 8
};
render();
if (!document.getElementById("thresholds-table")) throw new Error("golden thresholds table failed");

// Ticket screen & drawer show jury badge and provenance receipt summary
const tid = book.tickets[0].id;
location.hash = "#ticket/" + tid;
render();
const att = document.getElementById("ticket-attestation");
if (!att || !att.textContent.includes("cross-provider")) throw new Error("missing #ticket-attestation jury");
if (!att.textContent.includes("aaaaaaaaaaaa")) throw new Error("missing statement sha in #ticket-attestation");

const drw = drawer(book.tickets[0]);
if (!drw.querySelector(".jury-badge") || !drw.querySelector(".prov-badge")) throw new Error("missing drawer jury/prov badges");
console.log("CONSOLE_THRESHOLDS_DOM_OK");
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as tf:
            tf.write(probe)
            tf_path = tf.name
        try:
            res = subprocess.run(
                ["node", tf_path],
                cwd=REPO,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        finally:
            os.unlink(tf_path)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout.strip(), "CONSOLE_THRESHOLDS_DOM_OK")


if __name__ == "__main__":
    unittest.main()
