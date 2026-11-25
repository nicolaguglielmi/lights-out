"""Tests for core/jury.py, core/jury.sh, ADR 0130, and additive provenance jury linkage.

Kept strictly vendor-free, app-free, and model-free using synthetic provider
and credential variable names so core/tests/ satisfies ci/vendor-free.sh,
ci/app-free.sh, and ci/model-free.sh.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import jury, provenance
from core.adr_gov import validate_adr

REPO = Path(__file__).resolve().parents[2]

SYNTH_PROVIDERS = {
    "providers": {
        "replay": {
            "label": "Local replay",
            "harnesses": ["replay"],
            "default_harness": "replay",
            "env_var": "",
            "default_model": "",
        },
        "alpha": {
            "label": "Alpha Vendor",
            "harnesses": ["alpha-cli"],
            "default_harness": "alpha-cli",
            "env_var": "SYNTH_ALPHA_CRED",
            "default_model": "alpha-model-v1",
        },
        "beta": {
            "label": "Beta Vendor",
            "harnesses": ["beta-cli"],
            "default_harness": "beta-cli",
            "env_var": "SYNTH_BETA_CRED",
            "default_model": "beta-model-v2",
        },
        "gamma": {
            "label": "Gamma Vendor",
            "harnesses": ["gamma-cli"],
            "default_harness": "gamma-cli",
            "env_var": "SYNTH_GAMMA_CRED",
            "default_model": "gamma-model-v3",
        },
    }
}


class AdrAndConstantsTests(unittest.TestCase):
    def test_adr_0130_passes_governance(self):
        adr = REPO / "docs" / "adr" / "0130-cross-provider-jury-and-outside-harness.md"
        self.assertTrue(adr.is_file())
        text = adr.read_text(encoding="utf-8")
        self.assertEqual(validate_adr(text), [])
        for heading in ("## Context", "## Decision", "## Alternatives", "## Consequences", "## Provenance"):
            self.assertIn(heading, text)

    def test_constants_and_shell_wrapper_line_budget(self):
        self.assertEqual(jury.JURY_MIN_PROVIDERS, 2)
        self.assertEqual(jury.JURY_DEFAULT_MODE, "auto")
        sh_path = REPO / "core" / "jury.sh"
        self.assertTrue(sh_path.is_file())
        code_lines = [
            ln for ln in sh_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        self.assertLessEqual(len(code_lines), 20)


class SelectReviewerTests(unittest.TestCase):
    def test_medium_and_high_risk_selects_alternate_vendor(self):
        env = {"SYNTH_ALPHA_CRED": "configured-alpha", "SYNTH_BETA_CRED": "configured-beta"}
        for risk in ("medium", "high"):
            with self.subTest(risk=risk):
                res = jury.select_reviewer("alpha", "alpha-cli", risk, SYNTH_PROVIDERS, env)
                self.assertEqual(
                    set(res.keys()),
                    {"mode", "cross_vendor", "author_provider", "reviewer_provider",
                     "reviewer_model", "reviewer_harness", "reason"},
                )
                self.assertEqual(res["mode"], "auto")
                self.assertTrue(res["cross_vendor"])
                self.assertEqual(res["author_provider"], "alpha")
                self.assertEqual(res["reviewer_provider"], "beta")
                self.assertEqual(res["reviewer_model"], "beta-model-v2")
                self.assertEqual(res["reviewer_harness"], "beta-cli")
                self.assertIn("beta", res["reason"])

    def test_jury_provider_preference_and_fallback(self):
        env_three = {
            "SYNTH_ALPHA_CRED": "configured-alpha",
            "SYNTH_BETA_CRED": "configured-beta",
            "SYNTH_GAMMA_CRED": "configured-gamma",
            "JURY_PROVIDER": "gamma",
        }
        res = jury.select_reviewer("alpha", "alpha-cli", "high", SYNTH_PROVIDERS, env_three)
        self.assertTrue(res["cross_vendor"])
        self.assertEqual(res["reviewer_provider"], "gamma")
        self.assertEqual(res["reviewer_model"], "gamma-model-v3")
        self.assertEqual(res["reviewer_harness"], "gamma-cli")

        # Preferred JURY_PROVIDER missing its key: auto falls back deterministically, strict refuses.
        env_two = {
            "SYNTH_ALPHA_CRED": "configured-alpha",
            "SYNTH_BETA_CRED": "configured-beta",
            "JURY_PROVIDER": "gamma",
        }
        fb = jury.select_reviewer("alpha", "alpha-cli", "medium", SYNTH_PROVIDERS, env_two)
        self.assertTrue(fb["cross_vendor"])
        self.assertEqual(fb["reviewer_provider"], "beta")

        with self.assertRaises(jury.Refused):
            jury.select_reviewer(
                "alpha", "alpha-cli", "medium", SYNTH_PROVIDERS,
                {**env_two, "JURY_CROSS_PROVIDER": "strict"},
            )

    def test_low_risk_and_single_key_preserve_default_or_refuse_strict(self):
        two_keys = {"SYNTH_ALPHA_CRED": "configured-alpha", "SYNTH_BETA_CRED": "configured-beta"}
        low = jury.select_reviewer("alpha", "alpha-cli", "low", SYNTH_PROVIDERS, two_keys)
        self.assertFalse(low["cross_vendor"])
        self.assertEqual(low["reviewer_provider"], "alpha")
        self.assertEqual(low["reviewer_model"], "alpha-model-v1")
        self.assertEqual(low["reviewer_harness"], "alpha-cli")
        self.assertIn("low", low["reason"])

        with self.assertRaises(jury.Refused):
            jury.select_reviewer(
                "alpha", "alpha-cli", "low", SYNTH_PROVIDERS,
                {**two_keys, "JURY_CROSS_PROVIDER": "strict"},
            )

        one_key = {"SYNTH_ALPHA_CRED": "configured-alpha", "SYNTH_BETA_CRED": "replace-me"}
        auto_one = jury.select_reviewer("alpha", "alpha-cli", "medium", SYNTH_PROVIDERS, one_key)
        self.assertFalse(auto_one["cross_vendor"])
        self.assertEqual(auto_one["reviewer_provider"], "alpha")
        self.assertIn("2", auto_one["reason"])

        with self.assertRaises(jury.Refused):
            jury.select_reviewer(
                "alpha", "alpha-cli", "high", SYNTH_PROVIDERS,
                {**one_key, "JURY_CROSS_PROVIDER": "strict"},
            )

    def test_replay_at_l0_and_off_mode(self):
        two_keys = {"SYNTH_ALPHA_CRED": "configured-alpha", "SYNTH_BETA_CRED": "configured-beta"}
        rep = jury.select_reviewer("replay", "replay", "high", SYNTH_PROVIDERS, two_keys)
        self.assertFalse(rep["cross_vendor"])
        self.assertEqual(rep["author_provider"], "replay")
        self.assertEqual(rep["reviewer_provider"], "replay")
        self.assertEqual(rep["reviewer_harness"], "replay")
        self.assertEqual(rep["reviewer_model"], "")
        self.assertIn("replay", rep["reason"])

        with self.assertRaises(jury.Refused):
            jury.select_reviewer(
                "replay", "replay", "high", SYNTH_PROVIDERS,
                {**two_keys, "JURY_CROSS_PROVIDER": "strict"},
            )

        off = jury.select_reviewer(
            "alpha", "alpha-cli", "high", SYNTH_PROVIDERS,
            {**two_keys, "JURY_CROSS_PROVIDER": "off"},
        )
        self.assertFalse(off["cross_vendor"])
        self.assertEqual(off["mode"], "off")
        self.assertEqual(off["reviewer_provider"], "alpha")

        with self.assertRaises(jury.Refused):
            jury.select_reviewer("alpha", "alpha-cli", "high", SYNTH_PROVIDERS, {"JURY_CROSS_PROVIDER": "invalid"})

    def test_secret_values_never_exposed_in_results_or_errors(self):
        secret_a = "sentinel-alpha-val-9911"
        secret_b = "sentinel-beta-val-8822"
        env = {"SYNTH_ALPHA_CRED": secret_a, "SYNTH_BETA_CRED": secret_b}
        res = jury.select_reviewer("alpha", "alpha-cli", "high", SYNTH_PROVIDERS, env)
        serialized = json.dumps(res)
        self.assertNotIn(secret_a, serialized)
        self.assertNotIn(secret_b, serialized)


class JuryCliAndProvenanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "profiles").mkdir(parents=True)
        (self.root / "profiles" / "providers.json").write_text(json.dumps(SYNTH_PROVIDERS), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_select_env_and_inspect_cli_with_run_json_and_doc(self):
        run_dir = self.root / ".crew" / "0042" / "out"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "harness": "alpha-cli",
            "provider": "alpha",
            "model": "alpha-model-v1",
            "exit_code": 0,
        }), encoding="utf-8")
        doc_path = self.root / "doc.json"
        doc_path.write_text(json.dumps({
            "id": "0042",
            "ci": {"risk_level": "medium"},
            "tasks": [{"attempt": 1, "harness": "alpha-cli", "provider": "alpha"}],
        }), encoding="utf-8")

        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": str(REPO),
            "SYNTH_ALPHA_CRED": "secret-alpha-dont-leak",
            "SYNTH_BETA_CRED": "secret-beta-dont-leak",
            "TICKET": "0042",
            "DOC_FILE": str(doc_path),
        }

        sel = subprocess.run(
            ["sh", str(REPO / "core" / "jury.sh"), "select-env", "--role", "architect"],
            cwd=self.root, env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(sel.returncode, 0, sel.stderr)
        sel_data = json.loads(sel.stdout)
        self.assertTrue(sel_data["cross_vendor"])
        self.assertEqual(sel_data["author_provider"], "alpha")
        self.assertEqual(sel_data["reviewer_provider"], "beta")
        self.assertNotIn("secret-alpha-dont-leak", sel.stdout + sel.stderr)
        self.assertNotIn("secret-beta-dont-leak", sel.stdout + sel.stderr)

        insp = subprocess.run(
            ["sh", str(REPO / "core" / "jury.sh"), "inspect", "--ticket", "0042"],
            cwd=self.root, env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(insp.returncode, 0, insp.stderr)
        insp_data = json.loads(insp.stdout)
        self.assertEqual(insp_data["reviewer_provider"], "beta")

        # Strict refusal exits 1 with one-line NO-GO when < 2 keys exist
        strict_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": str(REPO),
            "SYNTH_ALPHA_CRED": "secret-alpha-dont-leak",
            "JURY_CROSS_PROVIDER": "strict",
            "TICKET": "0042",
            "DOC_FILE": str(doc_path),
        }
        strict = subprocess.run(
            ["sh", str(REPO / "core" / "jury.sh"), "select-env"],
            cwd=self.root, env=strict_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(strict.returncode, 1)
        self.assertTrue(strict.stdout.startswith("NO-GO: jury — "), strict.stdout)

    def test_provenance_includes_jury_additively_and_preserves_legacy(self):
        (self.root / "adapters" / "stub-state").mkdir(parents=True)
        get_sh = self.root / "adapters" / "stub-state" / "get.sh"
        get_sh.write_text('#!/bin/sh\ncat "state/$2.json"\n', encoding="utf-8")
        get_sh.chmod(0o755)
        (self.root / "state").mkdir()

        base_doc = {
            "id": "0007",
            "state": "merged",
            "attempt": 1,
            "spec": {"sha256": "a" * 64, "title": "Legacy ticket"},
            "tasks": [{
                "attempt": 1, "role": "dev-worker", "harness": "alpha-cli",
                "provider": "alpha", "model": "alpha-model-v1", "thinking": "high",
                "turns": 2, "input_sha256": "b" * 64, "tokens": {"input": 100, "output": 50},
                "usd": 0.001,
            }],
            "review": {
                "role": "test-reviewer", "verdict": "approved", "harness": "beta-cli",
                "provider": "beta", "model": "beta-model-v2", "thinking": "medium",
                "turns": 1, "input_sha256": "c" * 64, "tokens": {"input": 80, "output": 20},
                "usd": 0.001,
            },
            "ci": {
                "verdict": "GO", "decision": "review", "risk_level": "medium",
                "head_sha": "1" * 40, "line": "GO: gates", "lines": ["GO: risk — medium, review"],
                "seconds": {"risk": 0},
            },
            "merge": {"sha": "2" * 40, "head_sha": "1" * 40},
            "pr": {"number": 1, "url": "http://localhost/pr/1", "head_sha": "1" * 40, "base_sha": "0" * 40},
        }

        prev = os.getcwd()
        os.chdir(self.root)
        try:
            # Legacy statement (no jury metadata) -> no predicate.review, no agent.jury
            (self.root / "state" / "0007.json").write_text(json.dumps(base_doc), encoding="utf-8")
            _, legacy_stmt = provenance.statement({"TICKET": "0007", "STATE_STORE": "stub-state", "WORKLOAD": str(self.root)})
            self.assertNotIn("review", legacy_stmt["predicate"])
            for ag in legacy_stmt["predicate"]["agents"]:
                self.assertNotIn("jury", ag)

            # With jury metadata -> included in both predicate.review and reviewer agent entry
            jury_doc = dict(base_doc)
            jury_doc["review"] = dict(base_doc["review"], jury={
                "mode": "auto",
                "cross_vendor": True,
                "author_provider": "alpha",
                "reviewer_provider": "beta",
                "reviewer_model": "beta-model-v2",
                "reviewer_harness": "beta-cli",
                "reason": "medium-risk PR: selected cross-vendor reviewer beta",
            })
            (self.root / "state" / "0007.json").write_text(json.dumps(jury_doc), encoding="utf-8")
            out_path, jury_stmt = provenance.build({
                "TICKET": "0007",
                "STATE_STORE": "stub-state",
                "WORKLOAD": str(self.root),
                "PROVENANCE_DIR": str(self.root / ".crew" / "provenance"),
            })
            pred = jury_stmt["predicate"]
            self.assertIn("review", pred)
            self.assertTrue(pred["review"]["jury"]["cross_vendor"])
            self.assertEqual(pred["review"]["jury"]["reviewer_provider"], "beta")
            rev_agent = [a for a in pred["agents"] if a["kind"] == "review"][0]
            self.assertEqual(rev_agent["jury"]["author_provider"], "alpha")
            self.assertEqual(rev_agent["jury"]["reviewer_provider"], "beta")
            verified = provenance.verify_statement(out_path, repo_dir=str(self.root))
            self.assertEqual(verified["predicate"]["review"]["jury"]["reviewer_harness"], "beta-cli")
        finally:
            os.chdir(prev)


class StepReviewJuryWiringTests(unittest.TestCase):
    """Verify core/step_review.py routes medium/high risk reviews via core.jury."""

    def test_step_review_selects_cross_vendor_and_attaches_run_json_jury(self):
        from core import cmd_util, step_review
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "profiles").mkdir(parents=True)
            (root / "profiles" / "providers.json").write_text(
                json.dumps(SYNTH_PROVIDERS), encoding="utf-8"
            )
            (root / "roles" / "test-reviewer").mkdir(parents=True)
            (root / "roles" / "test-reviewer" / "prompt.md").write_text("Review.\n", encoding="utf-8")
            (root / "roles" / "test-reviewer" / "role.env").write_text(
                "THINKING=high\nMAX_TURNS=60\nTIMEOUT_S=900\nTOKEN_BUDGET=1000\nTOKEN_BUDGET_BETA=2222\n",
                encoding="utf-8",
            )
            doc_path = root / "doc.json"
            doc_path.write_text(
                json.dumps({
                    "id": "0042",
                    "attempt": 1,
                    "tasks": [{"attempt": 1, "provider": "alpha", "harness": "alpha-cli"}],
                    "ci": {"risk_level": "medium"},
                }),
                encoding="utf-8",
            )
            seen = {}

            def fake_sh(script, *args, **kwargs):
                s = str(script)
                env = kwargs.get("env") or {}
                if s.endswith("clone-url.sh"):
                    return subprocess.CompletedProcess([], 0, stdout="file:///repo\n", stderr="")
                if s.endswith("get.sh"):
                    return subprocess.CompletedProcess([], 0, stdout="Ticket 0042\n", stderr="")
                if s.endswith("run.sh"):
                    seen["script"] = s
                    seen["provider"] = env.get("MODEL_PROVIDER")
                    seen["model"] = env.get("MODEL")
                    seen["budget"] = env.get("TOKEN_BUDGET")
                    out_dir = Path(env["OUT"])
                    (out_dir / "final.md").write_text("APPROVED\n\nCross-vendor verified.\n", encoding="utf-8")
                    (out_dir / "run.json").write_text(
                        json.dumps({"harness": "beta-cli", "provider": "beta", "model": "beta-model-v2"}),
                        encoding="utf-8",
                    )
                    return subprocess.CompletedProcess([], 0, stdout="", stderr="")
                return subprocess.CompletedProcess([], 0, stdout="", stderr="")

            def fake_py(script, *args, **kwargs):
                s = str(script)
                if s.endswith("agents.py"):
                    Path(args[args.index("--out") + 1]).write_text("review input", encoding="utf-8")
                    return subprocess.CompletedProcess([], 0, stdout="null", stderr="")
                if s.endswith("spend.py"):
                    prov = ((kwargs.get("env") or {}).get("MODEL_PROVIDER") or "").upper()
                    return subprocess.CompletedProcess([], 0, stdout=("2222\n" if prov == "BETA" else "1000\n"), stderr="")
                if s.endswith("tower.py"):
                    rj = json.loads(Path(args[-1]).read_text(encoding="utf-8"))
                    return subprocess.CompletedProcess(
                        [], 0, stdout=json.dumps({"harness": rj["harness"], "provider": rj["provider"]}), stderr=""
                    )
                return subprocess.CompletedProcess([], 0, stdout="", stderr="")

            prev = os.getcwd()
            os.chdir(root)
            try:
                import contextlib, io
                out = io.StringIO()
                env = {
                    "TICKET": "0042",
                    "PR_NUMBER": "12",
                    "BASE_SHA": "b1",
                    "HEAD_SHA": "h1",
                    "CODE_HOST": "stub-ch",
                    "TRACKER": "stub-tr",
                    "AGENT_HARNESS": "alpha-cli",
                    "MODEL_PROVIDER": "alpha",
                    "DOC_FILE": str(doc_path),
                    "SYNTH_ALPHA_CRED": "k-alpha-val",
                    "SYNTH_BETA_CRED": "k-beta-val",
                }
                with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh), \
                     mock.patch.object(cmd_util, "run_py", side_effect=fake_py), \
                     mock.patch.object(cmd_util, "git_cmd", return_value=subprocess.CompletedProcess([], 0, stdout="h1\n", stderr="")), \
                     mock.patch.object(cmd_util, "run_cmd", return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")), \
                     contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    rc = step_review.main([], env=env)
                self.assertEqual(rc, 0)
                self.assertEqual(seen["script"], "adapters/beta-cli/run.sh")
                self.assertEqual(seen["provider"], "beta")
                self.assertEqual(seen["model"], "beta-model-v2")
                self.assertEqual(seen["budget"], "2222")
                res = json.loads(out.getvalue())
                self.assertTrue(res["review"]["jury"]["cross_vendor"])
                self.assertEqual(res["review"]["jury"]["reviewer_provider"], "beta")
                run_json = json.loads((root / ".crew" / "0042" / "review" / "out" / "run.json").read_text(encoding="utf-8"))
                self.assertEqual(run_json["jury"]["reviewer_harness"], "beta-cli")

                # Strict mode in replay fails closed
                with self.assertRaises(SystemExit) as cm:
                    with contextlib.redirect_stderr(io.StringIO()):
                        step_review.select_review_jury(
                            {**env, "AGENT_HARNESS": "replay", "JURY_CROSS_PROVIDER": "strict"},
                            ticket="0042", role="test-reviewer", doc={"ci": {"risk_level": "high"}}, default_harness="replay",
                        )
                self.assertEqual(cm.exception.code, 1)
            finally:
                os.chdir(prev)


if __name__ == "__main__":
    unittest.main()
