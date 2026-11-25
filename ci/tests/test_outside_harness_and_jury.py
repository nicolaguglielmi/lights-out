"""End-to-end tests for M37 cross-provider jury, provenance linkage, outside/* AgentHarness, and orchestrator recipes."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import adapters, jury, provenance

REPO = Path(__file__).resolve().parents[2]


class RealProvidersJuryTests(unittest.TestCase):
    def setUp(self):
        self.providers = jury.load_providers(str(REPO / "profiles" / "providers.json"))

    def test_cross_vendor_selection_against_real_providers_json(self):
        env_two = {"GEMINI_API_KEY": "present-gemini", "ANTHROPIC_API_KEY": "present-anthropic"}
        res = jury.select_reviewer("gemini", "gemini-cli", "medium", self.providers, env_two)
        self.assertTrue(res["cross_vendor"])
        self.assertEqual(res["author_provider"], "gemini")
        self.assertEqual(res["reviewer_provider"], "anthropic")
        self.assertEqual(res["reviewer_model"], "claude-sonnet-5-5")
        self.assertEqual(res["reviewer_harness"], "claude-code")

        res_rev = jury.select_reviewer("anthropic", "claude-code", "high", self.providers, env_two)
        self.assertTrue(res_rev["cross_vendor"])
        self.assertEqual(res_rev["reviewer_provider"], "gemini")
        self.assertEqual(res_rev["reviewer_model"], "gemini-3.8-flash")
        self.assertEqual(res_rev["reviewer_harness"], "gemini-cli")

    def test_low_risk_single_key_and_l0_replay_against_real_providers(self):
        env_two = {"GEMINI_API_KEY": "present-gemini", "ANTHROPIC_API_KEY": "present-anthropic"}
        low = jury.select_reviewer("gemini", "gemini-cli", "low", self.providers, env_two)
        self.assertFalse(low["cross_vendor"])
        self.assertEqual(low["reviewer_provider"], "gemini")

        env_one = {"GEMINI_API_KEY": "present-gemini"}
        single = jury.select_reviewer("gemini", "gemini-cli", "medium", self.providers, env_one)
        self.assertFalse(single["cross_vendor"])
        self.assertEqual(single["reviewer_provider"], "gemini")

        l0 = jury.select_reviewer("replay", "replay", "high", self.providers, env_two)
        self.assertFalse(l0["cross_vendor"])
        self.assertEqual(l0["reviewer_provider"], "replay")
        self.assertEqual(l0["reviewer_harness"], "replay")

        with self.assertRaises(jury.Refused):
            jury.select_reviewer(
                "gemini", "gemini-cli", "high", self.providers,
                {**env_one, "JURY_CROSS_PROVIDER": "strict"},
            )


class OutsideFileHarnessE2ETests(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        (self.checkout / "gates").mkdir()
        shutil.copy(REPO / "gates" / "harness-conformance.sh", self.checkout / "gates" / "harness-conformance.sh")
        self.ext = self.root / "ext"
        self.ext.mkdir()
        shutil.copytree(REPO / "fixtures" / "outside-adapters" / "file-harness", self.ext / "file-harness")
        self.reg = self.root / "register"
        os.chdir(self.checkout)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _init_git_ws(self, ws: Path) -> str:
        ws.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
        subprocess.run(["git", "config", "user.email", "ci@localhost"], cwd=ws, check=True)
        subprocess.run(["git", "config", "user.name", "ci"], cwd=ws, check=True)
        (ws / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=ws, check=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws, capture_output=True, text=True, check=True).stdout.strip()

    def test_admission_execution_budgets_and_tamper_refusal(self):
        env = {
            "ADAPTERS_PATH": str(self.ext),
            "ADAPTERS_REGISTER": str(self.reg),
            "AGENT_HARNESS": "outside/file-harness",
        }
        with self.assertRaises(adapters.Refused) as ref:
            adapters.admit(env)
        self.assertIn("harness-conformance", str(ref.exception))

        adapters.trial(env, "AGENT_HARNESS")
        with self.assertRaises(adapters.Refused):
            adapters.admit(env)
        adapters.admit({**env, "ADAPTERS_TRIAL": "AGENT_HARNESS"})

        linked = self.checkout / "adapters" / "outside" / "file-harness"
        self.assertTrue(linked.is_symlink())

        ver = subprocess.run(["sh", str(linked / "version.sh"), "--json"], capture_output=True, text=True, check=False)
        self.assertEqual(ver.returncode, 0, ver.stderr)
        self.assertEqual(json.loads(ver.stdout)["harness"], "outside/file-harness")

        ws = self.root / "ws"
        base = self._init_git_ws(ws)
        ticket = self.root / "ticket.md"
        ticket.write_text("Ticket 0099: verify outside file-harness.\n", encoding="utf-8")
        out_w = self.root / "out-write"
        run_env = {
            **os.environ,
            "ROLE": "dev-worker",
            "TICKET": "0099",
            "WORKSPACE": str(ws),
            "BASE": base,
            "OUT": str(out_w),
            "TICKET_FILE": str(ticket),
            "THINKING": "high",
            "MAX_TURNS": "4",
            "TIMEOUT_S": "30",
            "TOKEN_BUDGET": "2500",
        }
        w_proc = subprocess.run(["sh", str(linked / "run.sh")], env=run_env, capture_output=True, text=True, check=False)
        self.assertEqual(w_proc.returncode, 0, w_proc.stderr)
        hc = subprocess.run(
            ["sh", str(REPO / "core" / "harness-check.sh"), str(out_w), str(ws), base],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(hc.returncode, 0, hc.stderr)
        run_meta = json.loads((out_w / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_meta["harness"], "outside/file-harness")
        self.assertEqual(run_meta["exit_code"], 0)

        # Reviewer mode leaves workspace untouched and writes APPROVED
        head_after = subprocess.run(["git", "-C", str(ws), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        out_r = self.root / "out-review"
        r_proc = subprocess.run(
            ["sh", str(linked / "run.sh")],
            env={**run_env, "ROLE": "test-reviewer", "BASE": head_after, "OUT": str(out_r)},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(r_proc.returncode, 0, r_proc.stderr)
        self.assertEqual((out_r / "final.md").read_text(encoding="utf-8").splitlines()[0], "APPROVED")

        # Over-budget run records stop + partial run.json without pr.json
        script_over = self.root / "over_budget.json"
        script_over.write_text(json.dumps({"tokens": {"input": 2000, "output": 1000, "total": 3000}}), encoding="utf-8")
        out_b = self.root / "out-budget"
        b_proc = subprocess.run(
            ["sh", str(linked / "run.sh")],
            env={**run_env, "OUT": str(out_b), "FILE_HARNESS_SCRIPT": str(script_over)},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(b_proc.returncode, 1)
        self.assertIn("over TOKEN_BUDGET", b_proc.stderr)
        self.assertEqual((out_b / "stop").read_text(encoding="utf-8").strip(), "TOKEN_BUDGET")
        self.assertTrue((out_b / "run.json").is_file())
        self.assertFalse((out_b / "pr.json").exists())

        # Unsafe write path denied
        script_bad = self.root / "bad_write.json"
        script_bad.write_text(json.dumps({"writes": [{"path": "../escape.txt", "content": "x"}]}), encoding="utf-8")
        out_bad = self.root / "out-bad"
        bad_proc = subprocess.run(
            ["sh", str(linked / "run.sh")],
            env={**run_env, "OUT": str(out_bad), "FILE_HARNESS_SCRIPT": str(script_bad)},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(bad_proc.returncode, 1)
        self.assertIn("DENY write", (out_bad / "hooks.log").read_text(encoding="utf-8"))

        # Register, admit, tamper refuse, symlink refuse
        adapters.register(env, "AGENT_HARNESS", "harness-conformance")
        linked.unlink()
        adapters.admit(env)
        self.assertTrue(linked.is_symlink())

        with open(self.ext / "file-harness" / "version.sh", "a", encoding="utf-8") as fh:
            fh.write("\n")
        with self.assertRaises(adapters.Refused):
            adapters.admit(env)

        linked_tree = self.ext / "file-harness-linked"
        shutil.copytree(REPO / "fixtures" / "outside-adapters" / "file-harness", linked_tree)
        os.symlink("/etc/hostname", linked_tree / "extra.sh")
        with self.assertRaises(adapters.Refused) as sym_ref:
            adapters.admit({**env, "AGENT_HARNESS": "outside/file-harness-linked"})
        self.assertIn("symlink", str(sym_ref.exception))


class ProvenanceAndOrchestratorRecipesTests(unittest.TestCase):
    def test_orchestrator_recipes_exist_and_keep_surface_boundary(self):
        orch_dir = REPO / "surfaces" / "orchestrators"
        for name in ("README.md", "openhands.md", "paperclip.md", "gastown.md"):
            p = orch_dir / name
            self.assertTrue(p.is_file(), f"missing {p}")
            text = p.read_text(encoding="utf-8")
            self.assertIn("lights-out gates", text)
            self.assertIn("lights-out-gates --light", text)

    def test_provenance_crew_run_jury_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".crew" / "0088" / "review" / "out").mkdir(parents=True)
            (root / ".crew" / "0088" / "review" / "out" / "run.json").write_text(json.dumps({
                "harness": "claude-code",
                "provider": "anthropic",
                "model": "claude-sonnet-5-5",
                "jury": {
                    "mode": "auto",
                    "cross_vendor": True,
                    "author_provider": "gemini",
                    "reviewer_provider": "anthropic",
                    "reviewer_model": "claude-sonnet-5-5",
                    "reviewer_harness": "claude-code",
                    "reason": "high-risk PR: selected cross-vendor reviewer anthropic",
                },
            }), encoding="utf-8")
            prev = os.getcwd()
            os.chdir(root)
            try:
                stmt_path, stmt = provenance.build({
                    "WORKLOAD": str(REPO),
                    "SUBJECT_SHA256": "a" * 64,
                    "SUBJECT_NAME": "outside-jury-e2e",
                    "TICKET": "0088",
                    "CREW_DIR": str(root / ".crew"),
                    "PROVENANCE_DIR": str(root / "out-prov-1"),
                })
                self.assertEqual(stmt["predicate"]["review"]["jury"]["reviewer_provider"], "anthropic")
                self.assertEqual(stmt["predicate"]["review"]["jury"]["reviewer_harness"], "claude-code")
                self.assertEqual(provenance.verify_statement(stmt_path, repo_dir=str(REPO))["subject"][0]["digest"]["sha256"], "a" * 64)

                (root / "adapters" / "stub-state").mkdir(parents=True)
                get_sh = root / "adapters" / "stub-state" / "get.sh"
                get_sh.write_text('#!/bin/sh\ncat "state/$2.json"\n', encoding="utf-8")
                get_sh.chmod(0o755)
                (root / "state").mkdir()
                (root / "state" / "0088.json").write_text(json.dumps({
                    "id": "0088",
                    "state": "REVIEWED",
                    "spec": {"sha256": "b" * 64, "title": "Cross-vendor jury check"},
                    "review": {
                        "harness": "claude-code",
                        "provider": "anthropic",
                        "model": "claude-sonnet-5-5",
                    },
                }), encoding="utf-8")
                stmt_doc_path, stmt_doc = provenance.build({
                    "WORKLOAD": str(REPO),
                    "SUBJECT_SHA256": "a" * 64,
                    "SUBJECT_NAME": "outside-jury-e2e",
                    "TICKET": "0088",
                    "STATE_STORE": "stub-state",
                    "CREW_DIR": str(root / ".crew"),
                    "PROVENANCE_DIR": str(root / "out-prov-2"),
                })
                self.assertEqual(stmt_doc["predicate"]["agents"][0]["jury"]["reviewer_harness"], "claude-code")
                self.assertEqual(stmt_doc["predicate"]["review"]["jury"]["reviewer_provider"], "anthropic")
                self.assertEqual(provenance.verify_statement(stmt_doc_path, repo_dir=str(REPO))["predicate"]["review"]["jury"]["reviewer_provider"], "anthropic")
            finally:
                os.chdir(prev)


if __name__ == "__main__":
    unittest.main()
