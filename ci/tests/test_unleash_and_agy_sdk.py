# SPDX-License-Identifier: Apache-2.0
"""Integration and conformance proofs for adapters/unleash and adapters/antigravity-sdk.

Kept under ci/tests/ so core/ remains strictly vendor-free per ci/vendor-free.sh.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTH_UNLEASH_TOKEN = "unleash-" + "synthetic" + "-tok"
SYNTH_GEMINI_KEY = "AIza" + "Synth" + "AgySdk0001"


def _make_unleash_server():
    state = {
        "health_mode": False,
        "frontend_only": set(),
        "auth_headers": [],
        "features": {
            "canary": {"name": "canary", "enabled": False, "strategies": [{"name": "default"}]},
            "constrained": {
                "name": "constrained",
                "enabled": True,
                "strategies": [
                    {
                        "name": "flexibleRollout",
                        "constraints": [
                            {"contextName": "version", "operator": "IN", "values": ["v1.2.3", "v2.0.0"]},
                        ],
                    }
                ],
            },
            "ci-match": {
                "name": "ci-match",
                "enabled": True,
                "strategies": [
                    {
                        "name": "default",
                        "constraints": [
                            {
                                "contextName": "appVersion",
                                "operator": "STR_STARTS_WITH",
                                "values": ["rel-"],
                                "caseInsensitive": True,
                            }
                        ],
                    }
                ],
            },
            "negated": {
                "name": "negated",
                "enabled": True,
                "strategies": [
                    {
                        "name": "default",
                        "constraints": [
                            {"contextName": "version", "operator": "IN", "values": ["bad"], "inverted": True},
                        ],
                    }
                ],
            },
            "front-only": {"name": "front-only", "enabled": True, "strategies": [{"name": "default"}]},
        },
        "toggles_requests": 0,
    }
    lock = threading.Lock()

    class UnleashHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            _ = format, args

        def _send_json(self, code: int, payload) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _check_auth(self) -> bool:
            auth = self.headers.get("Authorization", "")
            with lock:
                state["auth_headers"].append(auth)
            if auth != SYNTH_UNLEASH_TOKEN:
                self._send_json(401, {"message": "unauthorized"})
                return False
            return True

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                if not self._check_auth():
                    return
                self._send_json(200, {"health": "GOOD"})
                return
            if not self._check_auth():
                return
            with lock:
                if path == "/api/client/features":
                    if state["health_mode"]:
                        self._send_json(404, {"message": "not found"})
                        return
                    self._send_json(200, {"version": 2, "features": list(state["features"].values())})
                    return
                if path.startswith("/api/client/features/"):
                    flag = unquote(path[len("/api/client/features/"):])
                    if flag in state["frontend_only"] or flag not in state["features"]:
                        self._send_json(404, {"message": f"feature {flag} not found"})
                        return
                    self._send_json(200, state["features"][flag])
                    return
                if path == "/api/frontend":
                    toggles = [
                        {"name": f["name"], "enabled": f["enabled"]}
                        for f in state["features"].values()
                        if f["name"] in state["frontend_only"]
                    ]
                    if not toggles:
                        self._send_json(404, {"message": "none"})
                        return
                    self._send_json(200, {"toggles": toggles})
                    return
                self._send_json(404, {"message": "unknown path"})

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._check_auth():
                return
            prefix = "/api/admin/projects/"
            if path.startswith(prefix):
                segs = [unquote(p) for p in path[len(prefix):].split("/")]
                # projects/<proj>/features/<flag>/environments/<env>/{on|off}
                if len(segs) == 6 and segs[1] == "features" and segs[3] == "environments" and segs[5] in ("on", "off"):
                    flag = segs[2]
                    action = segs[5]
                    with lock:
                        if flag not in state["features"]:
                            self._send_json(404, {"message": "unknown flag"})
                            return
                        state["features"][flag]["enabled"] = (action == "on")
                        state["toggles_requests"] += 1
                    self._send_json(200, {"enabled": action == "on"})
                    return
            self._send_json(404, {"message": "bad admin endpoint"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), UnleashHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, state


class UnleashAdapterTest(unittest.TestCase):
    def setUp(self):
        self.server, self.thread, self.state = _make_unleash_server()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.env = dict(
            os.environ,
            UNLEASH_URL=self.url,
            UNLEASH_API_TOKEN=SYNTH_UNLEASH_TOKEN,
            UNLEASH_PROJECT="default",
            UNLEASH_ENVIRONMENT="development",
            READY_TIMEOUT="2",
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _run(self, script: str, *args: str, env=None):
        return subprocess.run(
            ["sh", str(REPO_ROOT / "adapters" / "unleash" / script), *args],
            cwd=REPO_ROOT,
            env=self.env if env is None else env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_flags_conformance_all_6_cases_pass(self):
        env = dict(self.env, CONFORMANCE="1", FLAGS="unleash", CONFORMANCE_FLAG="canary")
        proc = subprocess.run(
            ["sh", "gates/flags-conformance.sh"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout.strip(), "GO: flags-conformance — unleash, 6 cases")

    def test_token_never_on_curl_argv_and_stdin_auth_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            spy_log = Path(tmp) / "curl_argv.log"
            real_curl = "/usr/bin/curl"
            for candidate in ("/usr/bin/curl", "/bin/curl"):
                if os.path.exists(candidate):
                    real_curl = candidate
                    break
            spy = Path(tmp) / "curl"
            spy.write_text(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{spy_log}"\n'
                f'exec "{real_curl}" "$@"\n',
                encoding="utf-8",
            )
            spy.chmod(spy.stat().st_mode | stat.S_IXUSR)
            env = dict(self.env, PATH=f"{tmp}:{self.env['PATH']}")
            self.assertEqual(self._run("ready.sh", env=env).returncode, 0)
            self.assertEqual(self._run("set.sh", "canary", "on", env=env).returncode, 0)
            eval_proc = self._run("evaluate.sh", "canary", env=env)
            self.assertEqual(eval_proc.returncode, 0)
            self.assertEqual(eval_proc.stdout.strip(), "true")
            logged = spy_log.read_text(encoding="utf-8")
            self.assertIn("-H @-", logged)
            self.assertNotIn(SYNTH_UNLEASH_TOKEN, logged)
            self.assertTrue(all(h == SYNTH_UNLEASH_TOKEN for h in self.state["auth_headers"]))

    def test_env_validation_and_ready_fallback(self):
        bad_envs = [
            {"UNLEASH_URL": "", "UNLEASH_API_TOKEN": SYNTH_UNLEASH_TOKEN},
            {"UNLEASH_URL": "replace-me", "UNLEASH_API_TOKEN": SYNTH_UNLEASH_TOKEN},
            {"UNLEASH_URL": "ftp://example.invalid", "UNLEASH_API_TOKEN": SYNTH_UNLEASH_TOKEN},
            {"UNLEASH_URL": "http://user:pass@127.0.0.1:9999", "UNLEASH_API_TOKEN": SYNTH_UNLEASH_TOKEN},
            {"UNLEASH_URL": self.url, "UNLEASH_API_TOKEN": ""},
            {"UNLEASH_URL": self.url, "UNLEASH_API_TOKEN": "replace-me"},
        ]
        for overrides in bad_envs:
            e = dict(self.env, **overrides)
            p = self._run("ready.sh", env=e)
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(p.stdout, "")

        self.state["health_mode"] = True
        p_health = self._run("ready.sh")
        self.assertEqual(p_health.returncode, 0, p_health.stderr)
        self.assertEqual(p_health.stdout, "")

    def test_evaluate_constraints_frontend_fallback_and_set_errors(self):
        p1 = self._run("evaluate.sh", "constrained", "v1.2.3")
        self.assertEqual((p1.returncode, p1.stdout.strip()), (0, "true"))
        p2 = self._run("evaluate.sh", "constrained", "v9.9.9")
        self.assertEqual((p2.returncode, p2.stdout.strip()), (0, "false"))
        p3 = self._run("evaluate.sh", "ci-match", "REL-42")
        self.assertEqual((p3.returncode, p3.stdout.strip()), (0, "true"))
        p4 = self._run("evaluate.sh", "negated", "ok")
        self.assertEqual((p4.returncode, p4.stdout.strip()), (0, "true"))
        p5 = self._run("evaluate.sh", "negated", "bad")
        self.assertEqual((p5.returncode, p5.stdout.strip()), (0, "false"))

        self.state["frontend_only"].add("front-only")
        p_front = self._run("evaluate.sh", "front-only")
        self.assertEqual((p_front.returncode, p_front.stdout.strip()), (0, "true"))

        p_absent = self._run("evaluate.sh", "no-such-flag")
        self.assertNotEqual(p_absent.returncode, 0)
        self.assertEqual(p_absent.stdout, "")

        before_toggles = self.state["toggles_requests"]
        p_bad_var = self._run("set.sh", "canary", "maybe")
        self.assertNotEqual(p_bad_var.returncode, 0)
        self.assertEqual(p_bad_var.stdout, "")
        self.assertEqual(self.state["toggles_requests"], before_toggles)

        p_bad_flag = self._run("set.sh", "no-such-flag", "on")
        self.assertNotEqual(p_bad_flag.returncode, 0)
        self.assertEqual(p_bad_flag.stdout, "")


FAKE_AGY_SDK = '''
from __future__ import annotations
import json
import os
from pathlib import Path


class _BuiltinTools:
    SEARCH_WEB = "search_web"
    READ_URL_CONTENT = "read_url_content"
    GENERATE_IMAGE = "generate_image"
    START_SUBAGENT = "start_subagent"


class _AgentBehavior:
    AUTONOMOUS = "AUTONOMOUS"


class _RunCommandConfig:
    def __init__(self, enable_sandbox: bool = True):
        self.enable_sandbox = enable_sandbox


class _CapabilitiesConfig:
    def __init__(self, agent_behavior=None, disabled_tools=None, run_command_config=None):
        self.agent_behavior = agent_behavior
        self.disabled_tools = disabled_tools or []
        self.run_command_config = run_command_config


class _BudgetConfig:
    def __init__(self, max_model_calls=None, max_total_tokens=None):
        self.max_model_calls = max_model_calls
        self.max_total_tokens = max_total_tokens


class _GeminiModelOptions:
    def __init__(self, thinking_level="medium"):
        self.thinking_level = thinking_level


class _StopReason:
    MAX_TOTAL_TOKENS_EXCEEDED = "MAX_TOTAL_TOKENS_EXCEEDED"
    MAX_MODEL_CALLS_EXCEEDED = "MAX_MODEL_CALLS_EXCEEDED"


class _HookResult:
    def __init__(self, allow: bool, reason: str | None = None):
        self.allow = allow
        self.reason = reason


class types:
    BuiltinTools = _BuiltinTools
    AgentBehavior = _AgentBehavior
    RunCommandConfig = _RunCommandConfig
    CapabilitiesConfig = _CapabilitiesConfig
    BudgetConfig = _BudgetConfig
    GeminiModelOptions = _GeminiModelOptions
    StopReason = _StopReason
    HookResult = _HookResult


class GeminiAPIEndpoint:
    def __init__(self, base_url=None, url=None, options=None):
        self.base_url = base_url or url
        self.options = options


class LocalAgentConfig:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _HooksNS:
    @staticmethod
    def pre_tool_call_decide(fn):
        return fn


hooks = _HooksNS()


class _PolicyNS:
    @staticmethod
    def allow_all():
        return {"kind": "allow_all"}

    @staticmethod
    def deny(*names):
        return {"kind": "deny", "names": list(names)}


policy = _PolicyNS()


class _Resp:
    def __init__(self, text: str, usage: dict, model_version: str, stop_reason=None):
        self._text = text
        self.usage_metadata = usage
        self.model_version = model_version
        self.stop_reason = stop_reason

    def text(self):
        return self._text


class Agent:
    def __init__(self, config: LocalAgentConfig):
        self.config = config
        self.conversation = type("Conv", (), {"turns": 1, "total_usage": None, "last_turn_stop_reason": None})()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def chat(self, prompt: str):
        _ = prompt
        scenario = os.environ.get("TEST_AGY_SCENARIO", "coding")
        hook = (self.config.hooks or [None])[0]
        if scenario == "coding":
            ws = Path(self.config.workspaces[0])
            res_write = await hook({"name": "write_file", "args": {"TargetFile": "feature.txt", "Content": "hello"}})
            assert res_write.allow is True, f"expected write_file allowed, got {res_write.reason}"
            res_cmd = await hook({"name": "run_command", "args": {"CommandLine": "python3 --version"}})
            assert res_cmd.allow is True, f"expected safe cmd allowed, got {res_cmd.reason}"
            res_deny = await hook({"name": "run_command", "args": {"CommandLine": "git push origin main"}})
            assert res_deny.allow is False, "expected git push denied"
            res_builtin = await hook({"name": "search_web", "args": {"query": "test"}})
            assert res_builtin.allow is False, "expected search_web denied"
            (ws / "feature.txt").write_text("implemented via sdk\\n", encoding="utf-8")
            usage = {"prompt_token_count": 120, "candidates_token_count": 30, "total_token_count": 150}
            self.conversation.total_usage = usage
            return _Resp("feat: implement via sdk\\n\\nAdded feature.txt.", usage, self.config.model)
        if scenario == "budget":
            usage = {"prompt_token_count": 900, "candidates_token_count": 300, "total_token_count": 1200}
            self.conversation.total_usage = usage
            self.conversation.last_turn_stop_reason = types.StopReason.MAX_TOTAL_TOKENS_EXCEEDED
            return _Resp("", usage, self.config.model, stop_reason=types.StopReason.MAX_TOTAL_TOKENS_EXCEEDED)
        if scenario == "review":
            assert any(isinstance(p, dict) and "run_command" in p.get("names", []) for p in (self.config.policies or []))
            usage = {"prompt_token_count": 80, "candidates_token_count": 20, "total_token_count": 100}
            self.conversation.total_usage = usage
            return _Resp("APPROVED\\nChecked via sdk.", usage, self.config.model)
        if scenario == "conversational":
            assert self.config.tools, "expected conversational tools"
            self.config.tools[0](title="Synthetic incident", hypotheses=[{"cause": "pool", "confidence": 0.9}])
            usage = {"prompt_token_count": 50, "candidates_token_count": 15, "total_token_count": 65}
            self.conversation.total_usage = usage
            return _Resp("Filed incident.", usage, self.config.model)
        if scenario == "conversational_nocalls":
            usage = {"prompt_token_count": 50, "candidates_token_count": 5, "total_token_count": 55}
            self.conversation.total_usage = usage
            return _Resp("Did nothing.", usage, self.config.model)
        raise AssertionError(f"unknown TEST_AGY_SCENARIO={scenario}")
'''


class AntigravitySdkAdapterTest(unittest.TestCase):
    def _init_git_ws(self, root: Path):
        ws = root / "ws"
        ws.mkdir()
        subprocess.run(["git", "-C", str(ws), "init", "-q", "-b", "main"], check=True)
        (ws / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(ws), "add", "README.md"], check=True)
        subprocess.run(
            ["git", "-C", str(ws), "-c", "user.name=ci", "-c", "user.email=ci@localhost",
             "commit", "-q", "-m", "initial"],
            check=True,
        )
        base = subprocess.check_output(["git", "-C", str(ws), "rev-parse", "HEAD"], text=True).strip()
        ticket = root / "ticket.md"
        ticket.write_text("Ticket 0042: test antigravity-sdk harness.\n", encoding="utf-8")
        return ws, base, ticket, hashlib.sha256(ticket.read_bytes()).hexdigest()

    def test_no_cli_container_files_and_ci_checks_pass(self):
        adapter_dir = REPO_ROOT / "adapters" / "antigravity-sdk"
        for forbidden in ("Containerfile", "package.json", "package-lock.json", "release.env"):
            self.assertFalse((adapter_dir / forbidden).exists(), f"{forbidden} would misclassify SDK as container CLI")
        for script in ("ci/harness-shape.sh", "ci/hooks.sh"):
            proc = subprocess.run(["sh", script], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, f"{script}: {proc.stdout}\n{proc.stderr}")

    def test_missing_sdk_package_fails_loudly_without_hidden_mocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws, base, ticket, _ = self._init_git_ws(root)
            out = root / "out"
            env = dict(
                os.environ,
                ROLE="dev-worker",
                TICKET="0042",
                WORKSPACE=str(ws),
                BASE=base,
                TICKET_FILE=str(ticket),
                OUT=str(out),
                THINKING="medium",
                MAX_TURNS="4",
                TOKEN_BUDGET="2000",
                TIMEOUT_S="20",
                MODEL_PROVIDER="gemini",
                MODEL="gemini-3.8-flash",
                GEMINI_API_KEY=SYNTH_GEMINI_KEY,
                CONFORMANCE="1",
            )
            env.pop("AGY_SDK_STUB", None)
            proc = subprocess.run(
                ["sh", "adapters/antigravity-sdk/run.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "")
            self.assertIn("google-antigravity", proc.stderr)
            self.assertEqual(len(proc.stderr.strip().splitlines()), 1, proc.stderr)

            # Refusing AGY_SDK_STUB=1 when CONFORMANCE!=1 even if package path exists.
            env_stub = dict(env, CONFORMANCE="0", AGY_SDK_STUB="1")
            p_stub = subprocess.run(
                ["sh", "adapters/antigravity-sdk/run.sh"],
                cwd=REPO_ROOT,
                env=env_stub,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(p_stub.returncode, 0)
            self.assertIn("AGY_SDK_STUB=1 requires CONFORMANCE=1", p_stub.stderr)

            # Refusing non-gemini provider and placeholder key.
            p_prov = subprocess.run(
                ["sh", "adapters/antigravity-sdk/run.sh"],
                cwd=REPO_ROOT,
                env=dict(env, MODEL_PROVIDER="openai"),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(p_prov.returncode, 0)
            self.assertIn("gemini only", p_prov.stderr)

            p_key = subprocess.run(
                ["sh", "adapters/antigravity-sdk/run.sh"],
                cwd=REPO_ROOT,
                env=dict(env, GEMINI_API_KEY="replace-me"),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(p_key.returncode, 0)
            self.assertIn("GEMINI_API_KEY", p_key.stderr)

    def test_sdk_stub_coding_budget_review_and_conversational_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "stub_pkg" / "google" / "antigravity"
            pkg.mkdir(parents=True)
            (pkg / "__init__.py").write_text(FAKE_AGY_SDK, encoding="utf-8")
            (pkg / "types.py").write_text("from . import types as _t\n__getattr__ = lambda n: getattr(_t, n)\n", encoding="utf-8")
            (pkg / "hooks.py").write_text("from . import hooks, policy\n", encoding="utf-8")

            ws, base, ticket, in_sha = self._init_git_ws(root)
            base_env = dict(
                os.environ,
                PYTHONPATH=str(root / "stub_pkg"),
                CONFORMANCE="1",
                AGY_SDK_STUB="1",
                MODEL_PROVIDER="gemini",
                MODEL="gemini-3.8-flash",
                GEMINI_API_KEY=SYNTH_GEMINI_KEY,
                THINKING="high",
                TIMEOUT_S="20",
            )

            # 1. Coding role with guard.sh ALLOW + DENY + harness-check.sh verification.
            out_code = root / "out-coding"
            p_code = subprocess.run(
                ["sh", "adapters/antigravity-sdk/run.sh"],
                cwd=REPO_ROOT,
                env=dict(
                    base_env,
                    TEST_AGY_SCENARIO="coding",
                    ROLE="dev-worker",
                    TICKET="0042",
                    WORKSPACE=str(ws),
                    BASE=base,
                    TICKET_FILE=str(ticket),
                    OUT=str(out_code),
                    MAX_TURNS="5",
                    TOKEN_BUDGET="5000",
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(p_code.returncode, 0, p_code.stdout + p_code.stderr)
            chk = subprocess.run(
                ["sh", "core/harness-check.sh", str(out_code), str(ws), base],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(chk.returncode, 0, chk.stdout + chk.stderr)
            run_json = json.loads((out_code / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["harness"], "antigravity-sdk")
            self.assertEqual(run_json["input_sha256"], in_sha)
            self.assertEqual(run_json["exit_code"], 0)
            self.assertNotIn(SYNTH_GEMINI_KEY, (out_code / "run.json").read_text(encoding="utf-8"))
            hooks_log = (out_code / "hooks.log").read_text(encoding="utf-8")
            self.assertIn("ALLOW write_file -", hooks_log)
            self.assertIn("ALLOW run_command -", hooks_log)
            self.assertIn("DENY run_command ", hooks_log)
            self.assertIn("DENY search_web not-offered", hooks_log)

            # 2. Budget exhaustion emits OUT/run.json + OUT/stop before failing (ADR 0087).
            out_budget = root / "out-budget"
            p_budget = subprocess.run(
                ["sh", "adapters/antigravity-sdk/run.sh"],
                cwd=REPO_ROOT,
                env=dict(
                    base_env,
                    TEST_AGY_SCENARIO="budget",
                    ROLE="dev-worker",
                    TICKET="0043",
                    WORKSPACE=str(ws),
                    BASE=base,
                    TICKET_FILE=str(ticket),
                    OUT=str(out_budget),
                    MAX_TURNS="5",
                    TOKEN_BUDGET="100",
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(p_budget.returncode, 0)
            self.assertIn("TOKEN_BUDGET", p_budget.stderr)
            self.assertEqual((out_budget / "stop").read_text(encoding="utf-8").strip(), "TOKEN_BUDGET")
            budget_run = json.loads((out_budget / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(budget_run["exit_code"], 1)
            self.assertEqual(budget_run["stop"], "TOKEN_BUDGET")
            self.assertFalse((out_budget / "pr.json").exists())

            # 3. Review role writes OUT/final.md without OUT/pr.json.
            out_rev = root / "out-review"
            p_rev = subprocess.run(
                ["sh", "adapters/antigravity-sdk/run.sh"],
                cwd=REPO_ROOT,
                env=dict(
                    base_env,
                    TEST_AGY_SCENARIO="review",
                    ROLE="test-reviewer",
                    TICKET="0042",
                    WORKSPACE=str(ws),
                    BASE=base,
                    TICKET_FILE=str(ticket),
                    OUT=str(out_rev),
                    THINKING="low",
                    MAX_TURNS="3",
                    TOKEN_BUDGET="2000",
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(p_rev.returncode, 0, p_rev.stdout + p_rev.stderr)
            self.assertTrue((out_rev / "final.md").read_text(encoding="utf-8").startswith("APPROVED\n"))
            self.assertFalse((out_rev / "pr.json").exists())

            # 4. Conversational role records tool calls into OUT/calls.json, fails on 0 calls.
            inp = root / "input.md"
            inp.write_text("Canary error rate spiked 5x.\n", encoding="utf-8")
            out_conv = root / "out-conv"
            p_conv = subprocess.run(
                ["sh", "adapters/antigravity-sdk/run.sh"],
                cwd=REPO_ROOT,
                env=dict(
                    base_env,
                    TEST_AGY_SCENARIO="conversational",
                    ROLE="ops-triage",
                    INPUT=str(inp),
                    OUT=str(out_conv),
                    THINKING="medium",
                    ADK_MAX_TURNS="4",
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(p_conv.returncode, 0, p_conv.stdout + p_conv.stderr)
            calls = json.loads((out_conv / "calls.json").read_text(encoding="utf-8"))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["name"], "file_incident")

            out_nocall = root / "out-nocall"
            p_nocall = subprocess.run(
                ["sh", "adapters/antigravity-sdk/run.sh"],
                cwd=REPO_ROOT,
                env=dict(
                    base_env,
                    TEST_AGY_SCENARIO="conversational_nocalls",
                    ROLE="ops-triage",
                    INPUT=str(inp),
                    OUT=str(out_nocall),
                    THINKING="medium",
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(p_nocall.returncode, 0)
            self.assertIn("made no tool call", p_nocall.stderr)


if __name__ == "__main__":
    unittest.main()
