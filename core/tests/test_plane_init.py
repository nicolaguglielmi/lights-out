"""Unit tests for core/init_plane.py and plane hooks in core/init.py (ADR 0100).

Strictly vendor-free and app-free: tests only generic container build files,
contract outputs, and workload keys without naming delivery/flag/code-host
vendors or bundled workloads.
"""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from core import init
from core import init_plane


def _make_python_repo(root: Path, container_name: str | None = None, container_text: str = "") -> None:
    (root / "pyproject.toml").write_text(
        '[project]\nname = "svc"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "svc").mkdir(parents=True, exist_ok=True)
    (root / "svc" / "__init__.py").write_text("def ok(): return True\n", encoding="utf-8")
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "test_svc.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    if container_name is not None:
        (root / container_name).write_text(container_text, encoding="utf-8")


class DetectContainerTest(unittest.TestCase):
    def test_returns_none_when_no_container_file_at_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            (root / "sub" / "Dockerfile").write_text("FROM python:3.13-slim\n", encoding="utf-8")
            self.assertIsNone(init_plane.detect_container(root))

    def test_prefers_containerfile_over_dockerfile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Containerfile").write_text("FROM python:3.13-slim\nEXPOSE 5000\n", encoding="utf-8")
            (root / "Dockerfile").write_text("FROM node:24\nEXPOSE 3000\n", encoding="utf-8")
            info = init_plane.detect_container(root)
            self.assertIsNotNone(info)
            self.assertEqual(info["dockerfile"], "Containerfile")
            self.assertEqual(info["port"], 5000)

    def test_uses_dockerfile_when_containerfile_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Dockerfile").write_text("FROM node:24\nEXPOSE 3000/tcp 9090\n", encoding="utf-8")
            info = init_plane.detect_container(root)
            self.assertIsNotNone(info)
            self.assertEqual(info["dockerfile"], "Dockerfile")
            self.assertEqual(info["port"], 3000)
            self.assertTrue(info["port_explicit"])

    def test_defaults_port_to_8000_and_never_fabricates_healthz(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Containerfile").write_text("FROM python:3.13-slim\nEXPOSE $PORT\n", encoding="utf-8")
            info = init_plane.detect_container(root)
            self.assertIsNotNone(info)
            self.assertEqual(info["port"], 8000)
            self.assertFalse(info["port_explicit"])
            self.assertEqual(info["health_probe"], "tcp:8000")
            self.assertIsNone(info["health_path"])

    def test_extracts_healthcheck_http_paths_across_forms(self) -> None:
        cases = [
            (
                "shell_curl",
                "FROM python:3.13-slim\nEXPOSE 4000\nHEALTHCHECK --interval=5s CMD curl -f http://localhost:4000/health || exit 1\n",
                4000,
                "http:/health",
                "/health",
            ),
            (
                "exec_json",
                'FROM node:24\nEXPOSE 3000\nHEALTHCHECK CMD ["curl", "-fsS", "http://127.0.0.1:3000/readyz"]\n',
                3000,
                "http:/readyz",
                "/readyz",
            ),
            (
                "line_continuation",
                "FROM python:3.13-slim\nEXPOSE 8080\nHEALTHCHECK CMD \\\n  wget -q --spider http://localhost:8080/ok || exit 1\n",
                8080,
                "http:/ok",
                "/ok",
            ),
            (
                "root_url_without_slash",
                "FROM python:3.13-slim\nEXPOSE 8000\nHEALTHCHECK CMD curl -f http://localhost:8000 || exit 1\n",
                8000,
                "http:/",
                "/",
            ),
        ]
        for label, content, want_port, want_probe, want_path in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "Containerfile").write_text(content, encoding="utf-8")
                info = init_plane.detect_container(root)
                self.assertEqual(info["port"], want_port)
                self.assertEqual(info["health_probe"], want_probe)
                self.assertEqual(info["health_path"], want_path)

    def test_falls_back_to_honest_tcp_when_healthcheck_unclear_or_none(self) -> None:
        cases = [
            ("none", "FROM python:3.13-slim\nEXPOSE 7000\nHEALTHCHECK NONE\n", 7000),
            ("non_http", "FROM python:3.13-slim\nEXPOSE 5432\nHEALTHCHECK CMD pg_isready\n", 5432),
            ("shell_var", "FROM python:3.13-slim\nEXPOSE 3000\nHEALTHCHECK CMD curl -f http://localhost:3000/$PATH || exit 1\n", 3000),
            ("hash_char", "FROM python:3.13-slim\nEXPOSE 3000\nHEALTHCHECK CMD curl -f http://localhost:3000/ok#frag\n", 3000),
        ]
        for label, content, want_port in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "Dockerfile").write_text(content, encoding="utf-8")
                info = init_plane.detect_container(root)
                self.assertEqual(info["health_probe"], f"tcp:{want_port}")
                self.assertIsNone(info["health_path"])

    def test_detects_runtime_and_test_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Containerfile").write_text(
                "FROM --platform=$BUILDPLATFORM python:3.13-slim AS runtime\n"
                "FROM runtime AS test\n",
                encoding="utf-8",
            )
            info = init_plane.detect_container(root)
            self.assertTrue(info["has_runtime"])
            self.assertTrue(info["has_test"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Dockerfile").write_text("FROM node:24 AS builder\nFROM node:24-slim\n", encoding="utf-8")
            info = init_plane.detect_container(root)
            self.assertFalse(info["has_runtime"])
            self.assertFalse(info["has_test"])


class DraftPlaneTest(unittest.TestCase):
    def test_writes_all_plane_files_and_returns_inferred_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            out = Path(tmp) / "contract"
            root.mkdir()
            (root / "Dockerfile").write_text(
                "FROM python:3.13-slim AS runtime\nEXPOSE 3000\nHEALTHCHECK CMD curl -f http://localhost:3000/health || exit 1\n",
                encoding="utf-8",
            )
            info = init_plane.detect_container(root)
            keys = init_plane.draft_plane(root, out, info, {})

            compose_text = (out / "compose.yaml").read_text(encoding="utf-8")
            self.assertIn("backend:", compose_text)
            self.assertIn("image: localhost:5050/lights-out-backend:${APP_TAG:-latest}", compose_text)
            self.assertIn("context: ${WORKLOAD}", compose_text)
            self.assertIn("dockerfile: Dockerfile", compose_text)
            self.assertIn("target: runtime", compose_text)
            self.assertIn("backend-canary:", compose_text)
            self.assertIn("image: ${CANARY_IMAGE:-localhost:5050/lights-out-backend:latest}", compose_text)
            self.assertIn('profiles: ["canary"]', compose_text)
            self.assertIn("backend-test:", compose_text)
            self.assertIn('profiles: ["test"]', compose_text)
            self.assertNotIn("target: test", compose_text)
            # backend-canary must have no build: section.
            canary_block = compose_text.split("backend-canary:")[1].split("backend-test:")[0]
            self.assertNotIn("build:", canary_block)

            flags_data = json.loads((out / "flags" / "flags.json").read_text(encoding="utf-8"))
            self.assertEqual(set(flags_data.get("flags", {})), {"release-freeze"})
            rf = flags_data["flags"]["release-freeze"]
            self.assertEqual(rf["variants"], {"on": True, "off": False})
            self.assertEqual(rf["defaultVariant"], "off")

            smoke = out / "smoke.sh"
            load = out / "load.sh"
            self.assertTrue(smoke.is_file())
            self.assertTrue(load.is_file())
            self.assertTrue(smoke.stat().st_mode & stat.S_IXUSR)
            self.assertTrue(load.stat().st_mode & stat.S_IXUSR)
            smoke_text = smoke.read_text(encoding="utf-8")
            self.assertIn("$APP_URL/health", smoke_text)
            self.assertIn("2??|3??", smoke_text)
            load_text = load.read_text(encoding="utf-8")
            self.assertIn("$APP_URL$WORKLOAD_SAMPLE_PATH", load_text)

            self.assertEqual(keys["CANARY_VERSION"][0], "0.2.0-canary")
            self.assertEqual(keys["WORKLOAD_KILL_SWITCH"][0], "")
            self.assertEqual(keys["WORKLOAD_HEALTH_PROBE"][0], "http:/health")
            self.assertEqual(keys["WORKLOAD_SAMPLE_PATH"][0], "/")
            self.assertEqual(keys["WORKLOAD_PORT"][0], "3000")
            for k, (val, prov) in keys.items():
                self.assertTrue(prov.startswith("inferred:"), f"{k}: {prov}")
                self.assertNotIn("$", val)
                self.assertNotIn("#", val)

    def test_omits_workload_port_when_8000_and_omits_missing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            out = Path(tmp) / "contract"
            root.mkdir()
            (root / "Containerfile").write_text("FROM python:3.13-slim\nEXPOSE 8000\n", encoding="utf-8")
            info = init_plane.detect_container(root)
            keys = init_plane.draft_plane(root, out, info, {})
            self.assertNotIn("WORKLOAD_PORT", keys)
            self.assertEqual(keys["WORKLOAD_HEALTH_PROBE"][0], "tcp:8000")
            compose_text = (out / "compose.yaml").read_text(encoding="utf-8")
            self.assertNotIn("target:", compose_text)
            self.assertIn("$APP_URL/", (out / "smoke.sh").read_text(encoding="utf-8"))

    def test_preserves_existing_plane_files_in_contract_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            out = Path(tmp) / "contract"
            root.mkdir()
            (out / "flags").mkdir(parents=True)
            (root / "Containerfile").write_text("FROM python:3.13-slim\nEXPOSE 8000\n", encoding="utf-8")
            (out / "compose.yaml").write_text("# custom compose\n", encoding="utf-8")
            (out / "flags" / "flags.json").write_text('{"flags": {"custom": {}}}\n', encoding="utf-8")
            (out / "smoke.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (out / "load.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

            info = init_plane.detect_container(root)
            init_plane.draft_plane(root, out, info, {})

            self.assertEqual((out / "compose.yaml").read_text(encoding="utf-8"), "# custom compose\n")
            self.assertEqual((out / "flags" / "flags.json").read_text(encoding="utf-8"), '{"flags": {"custom": {}}}\n')
            self.assertEqual((out / "smoke.sh").read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n")
            self.assertEqual((out / "load.sh").read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n")


class InitHooksTest(unittest.TestCase):
    def test_write_draft_with_dockerfile_drafts_plane_and_workload_env(self) -> None:
        stacks = Path(__file__).resolve().parents[2] / "stacks"
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            out = Path(tmp) / "out"
            repo.mkdir()
            _make_python_repo(repo, "Dockerfile", "FROM python:3.13-slim\nEXPOSE 5000\n")

            err = io.StringIO()
            with redirect_stderr(err):
                stack = init.write_draft(repo, out, stacks, "2026-11-24", "abc1234")
            self.assertEqual(stack, "python")
            self.assertTrue((out / "compose.yaml").is_file())
            self.assertTrue((out / "flags" / "flags.json").is_file())
            self.assertTrue((out / "smoke.sh").is_file())
            self.assertTrue((out / "load.sh").is_file())

            env_text = (out / "workload.env").read_text(encoding="utf-8")
            self.assertIn("CANARY_VERSION=0.2.0-canary\n", env_text)
            self.assertIn("WORKLOAD_KILL_SWITCH=\n", env_text)
            self.assertIn("WORKLOAD_HEALTH_PROBE=tcp:5000\n", env_text)
            self.assertIn("WORKLOAD_SAMPLE_PATH=/\n", env_text)
            self.assertIn("WORKLOAD_PORT=5000\n", env_text)
            self.assertIn("# Provenance, inferred:", env_text)

    def test_write_draft_without_container_file_stays_gates_only_with_stderr_note(self) -> None:
        stacks = Path(__file__).resolve().parents[2] / "stacks"
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            out = Path(tmp) / "out"
            repo.mkdir()
            _make_python_repo(repo)

            err = io.StringIO()
            with redirect_stderr(err):
                init.write_draft(repo, out, stacks, "2026-11-24", "abc1234")
            self.assertFalse((out / "compose.yaml").exists())
            self.assertFalse((out / "flags").exists())
            self.assertIn("gates half only", err.getvalue())
            env_text = (out / "workload.env").read_text(encoding="utf-8")
            self.assertNotIn("WORKLOAD_PORT=", env_text)
            self.assertNotIn("WORKLOAD_HEALTH_PROBE=", env_text)

    def test_render_workload_env_preserves_committed_plane_and_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _make_python_repo(repo)
            d = init.infer(repo)
            plane = {
                "CANARY_VERSION": ("0.2.0-canary", "inferred: default"),
                "WORKLOAD_KILL_SWITCH": ("", "inferred: empty"),
                "WORKLOAD_HEALTH_PROBE": ("tcp:3000", "inferred: tcp"),
                "WORKLOAD_SAMPLE_PATH": ("/", "inferred: root"),
                "WORKLOAD_PORT": ("3000", "inferred: EXPOSE 3000"),
            }
            committed = {
                "CANARY_VERSION": "0.9.0-rc1",
                "WORKLOAD_KILL_SWITCH": "freeze-it",
                "WORKLOAD_SAMPLE_PATH": "/api",
                "WORKLOAD_HEALTH_PROBE": "http:/ready",
                "WORKLOAD_PORT": "9099",
            }
            out = init.render_workload_env(d, committed, "2026-11-24", "deadbeef", plane=plane)
            self.assertIn("CANARY_VERSION=0.9.0-rc1\n", out)
            self.assertIn("WORKLOAD_KILL_SWITCH=freeze-it\n", out)
            self.assertIn("WORKLOAD_SAMPLE_PATH=/api\n", out)
            self.assertIn("WORKLOAD_HEALTH_PROBE=http:/ready\n", out)
            self.assertIn("WORKLOAD_PORT=9099\n", out)
            self.assertIn("# Provenance, kept:", out)

    def test_render_workload_env_upgrades_empty_gates_only_committed_keys_when_plane_drafted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _make_python_repo(repo)
            d = init.infer(repo)
            plane = {
                "CANARY_VERSION": ("0.2.0-canary", "inferred: default"),
                "WORKLOAD_KILL_SWITCH": ("", "inferred: empty"),
                "WORKLOAD_HEALTH_PROBE": ("tcp:3000", "inferred: tcp"),
                "WORKLOAD_SAMPLE_PATH": ("/", "inferred: root"),
                "WORKLOAD_PORT": ("3000", "inferred: EXPOSE 3000"),
            }
            committed = {
                "CANARY_VERSION": "",
                "WORKLOAD_KILL_SWITCH": "",
                "WORKLOAD_SAMPLE_PATH": "",
            }
            out = init.render_workload_env(d, committed, "2026-11-24", "deadbeef", plane=plane)
            self.assertIn("CANARY_VERSION=0.2.0-canary\n", out)
            self.assertIn("WORKLOAD_HEALTH_PROBE=tcp:3000\n", out)
            self.assertIn("WORKLOAD_SAMPLE_PATH=/\n", out)
            self.assertIn("WORKLOAD_PORT=3000\n", out)


if __name__ == "__main__":
    unittest.main()
