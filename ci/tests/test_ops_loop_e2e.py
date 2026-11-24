"""End-to-end subprocess & surface workflow tests for Continuous Ops Loop (ADR 0109).

Exercises live ThreadingHTTPServer bearer auth & fail-closed startup via subprocess,
rollback -> auto-incident via core/releases.py subprocess, runner.py --once and
graceful SIGTERM shutdown against a stub EventBus, and surface recipe validation
including actionlint on surfaces/github-actions/tower-orchestrator.yml.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_CHECKOUT_PIN = "3d3c42e5aac5ba805825da76410c181273ba90b1"


class WebhookE2ETest(unittest.TestCase):
    def test_non_loopback_without_token_fails_closed_on_startup(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "ALERT_WEBHOOK_TOKEN"}
        proc = subprocess.run(
            [sys.executable, str(ROOT / "core" / "webhook.py"), "--serve", "--bind", "0.0.0.0", "--port", "0"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ALERT_WEBHOOK_TOKEN", proc.stderr)

    def test_webhook_subprocess_http_auth_cap_and_normalized_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            alerts_dir = Path(tmp) / "alerts"
            env = dict(os.environ, ALERT_WEBHOOK_TOKEN="e2e-webhook-token", PYTHONPATH=str(ROOT))
            with subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "core" / "webhook.py"),
                    "--serve",
                    "--bind",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--out",
                    str(alerts_dir),
                ],
                cwd=tmp,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as proc:
                try:
                    port = None
                    deadline = time.time() + 10.0
                    while time.time() < deadline:
                        assert proc.stderr is not None
                        line = proc.stderr.readline()
                        if not line and proc.poll() is not None:
                            break
                        m = re.search(r"http://127\.0\.0\.1:(\d+)", line)
                        if m:
                            port = int(m.group(1))
                            break
                    self.assertIsNotNone(port, "webhook server did not report listening port")
                    url = f"http://127.0.0.1:{port}/webhook"

                    prometheus_payload = json.dumps(
                        {
                            "status": "firing",
                            "alerts": [
                                {
                                    "status": "firing",
                                    "labels": {"alertname": "Canary5xx", "severity": "critical", "version": "0.9.1"},
                                    "annotations": {"summary": "Canary 5xx rate high"},
                                }
                            ],
                        }
                    ).encode("utf-8")

                    # Unauthenticated -> 401
                    req_401 = urllib.request.Request(
                        url,
                        data=prometheus_payload,
                        method="POST",
                        headers={"Content-Type": "application/json"},
                    )
                    with self.assertRaises(urllib.error.HTTPError) as err_401:
                        urllib.request.urlopen(req_401, timeout=5)
                    self.assertEqual(err_401.exception.code, 401)

                    # Oversized (> 64 KiB) -> 413
                    oversized = json.dumps({"headline": "z" * 66000, "version": "0.9.1"}).encode("utf-8")
                    req_413 = urllib.request.Request(
                        url,
                        data=oversized,
                        method="POST",
                        headers={"Content-Type": "application/json", "Authorization": "Bearer e2e-webhook-token"},
                    )
                    with self.assertRaises(urllib.error.HTTPError) as err_413:
                        urllib.request.urlopen(req_413, timeout=5)
                    self.assertEqual(err_413.exception.code, 413)

                    # Authorized valid payload -> 200
                    req_200 = urllib.request.Request(
                        url,
                        data=prometheus_payload,
                        method="POST",
                        headers={"Content-Type": "application/json", "Authorization": "Bearer e2e-webhook-token"},
                    )
                    with urllib.request.urlopen(req_200, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        body = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(body["schema"], "lights-out.alert/1")
                    saved = Path(body["written"])
                    self.assertTrue(saved.is_file())
                    saved_doc = json.loads(saved.read_text(encoding="utf-8"))
                    self.assertEqual(saved_doc["alerts"][0]["version"], "0.9.1")
                finally:
                    proc.terminate()
                    proc.wait(timeout=5)


class RollbackAutoIncidentE2ETest(unittest.TestCase):
    def test_releases_cli_writes_rollback_incident_in_temp_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "adapters" / "stub-state").mkdir(parents=True)
            sha = "b" * 40
            (work / "ticket-doc.json").write_text(
                json.dumps(
                    {
                        "id": "0005",
                        "state": "rolled-back",
                        "release": {"version": "1.2.3-rc1"},
                        "pr": {"number": 42},
                        "merge": {"sha": sha},
                    }
                ),
                encoding="utf-8",
            )
            (work / "adapters" / "stub-state" / "get.sh").write_text(
                '#!/bin/sh\n[ "$1 $2" = "tickets 0005" ] || exit 1\ncat ticket-doc.json\n',
                encoding="utf-8",
            )
            stages = json.dumps(
                [
                    {
                        "pct": 5,
                        "verdict": "NO-GO: slo — error_rate 0.25",
                        "numbers": ["slo: error_rate canary(1.2.3-rc1)=0.250 stable(1.2.2)=0.000"],
                        "started_at": "2026-11-24T06:00:00Z",
                        "judged_at": "2026-11-24T06:01:00Z",
                    }
                ]
            )
            env = dict(
                os.environ,
                PYTHONPATH=str(ROOT),
                AUTO_INCIDENT_ON_ROLLBACK="on",
                AUTO_POSTMORTEM_ON_ROLLBACK="off",
                CANARY_VERSION="1.2.3-rc1",
                STABLE_VERSION="1.2.2",
                RELEASE_TICKET="0005",
                STATE_STORE="stub-state",
                RELEASE_REASON="5%: error_rate 0.25",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "core" / "releases.py"),
                    "record",
                    "rolled-back",
                    "",
                    stages,
                    "registry.local/img@sha256:99",
                ],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            incidents = list((work / ".crew" / "incidents").glob("rollback-1-2-3-rc1-*.json"))
            self.assertEqual(len(incidents), 1)
            inc = json.loads(incidents[0].read_text(encoding="utf-8"))
            self.assertEqual(inc["kind"], "incident")
            self.assertEqual(inc["chain"], {"ticket": "0005", "pr": 42, "merge": sha})
            self.assertEqual(inc["release"]["verdict"], "rolled-back")


class RunnerE2ETest(unittest.TestCase):
    def test_runner_once_and_sigterm_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            bus_dir = work / "adapters" / "stub-bus"
            bus_dir.mkdir(parents=True)
            (work / "core").mkdir()
            queue = work / "queue.txt"
            queue.write_text('m-1 {"ticket":"0007","step":"assign"}\n', encoding="utf-8")
            log_file = work / "runner-trace.log"

            (bus_dir / "pull.sh").write_text(
                f'#!/bin/sh\nset -eu\n[ -s "{queue}" ] || exit 0\nhead -n 1 "{queue}"\n'
                f'tail -n +2 "{queue}" > "{queue}.next" && mv "{queue}.next" "{queue}"\n',
                encoding="utf-8",
            )
            (bus_dir / "ack.sh").write_text(
                f'#!/bin/sh\nset -eu\necho "ack:$1" >> "{log_file}"\n',
                encoding="utf-8",
            )
            (work / "core" / "tower.sh").write_text(
                f'#!/bin/sh\nset -eu\necho "tower:$2:$3" >> "{log_file}"\n',
                encoding="utf-8",
            )

            env = dict(os.environ, PYTHONPATH=str(ROOT), EVENT_BUS="stub-bus", STATE_STORE="sqlite")
            once_proc = subprocess.run(
                [sys.executable, str(ROOT / "core" / "runner.py"), "--once", "--lockfile", ".crew/runner.lock"],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(once_proc.returncode, 0, once_proc.stderr)
            self.assertEqual(log_file.read_text(encoding="utf-8").splitlines(), ["tower:0007:assign", "ack:m-1"])

            with subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "core" / "runner.py"),
                    "--poll-s",
                    "30",
                    "--lockfile",
                    ".crew/runner.lock",
                ],
                cwd=work,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as poll_proc:
                try:
                    deadline = time.time() + 5.0
                    while time.time() < deadline:
                        assert poll_proc.stderr is not None
                        line = poll_proc.stderr.readline()
                        if '"cycle"' in line or '"start"' in line:
                            break
                    t0 = time.time()
                    poll_proc.send_signal(signal.SIGTERM)
                    rc = poll_proc.wait(timeout=5)
                    elapsed = time.time() - t0
                    self.assertEqual(rc, 0)
                    self.assertLess(elapsed, 4.0)
                finally:
                    if poll_proc.poll() is None:
                        poll_proc.kill()
                        poll_proc.wait()


class SurfaceRecipesTest(unittest.TestCase):
    def test_github_actions_tower_orchestrator_recipe_and_actionlint(self) -> None:
        wf = ROOT / "surfaces" / "github-actions" / "tower-orchestrator.yml"
        self.assertTrue(wf.is_file(), f"missing {wf}")
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        triggers = doc.get("on") if "on" in doc else doc.get(True)
        self.assertIsInstance(triggers, dict)
        for ev in ("issues", "issue_comment", "pull_request"):
            self.assertIn(ev, triggers)

        top_perms = doc.get("permissions")
        self.assertIsInstance(top_perms, dict)
        self.assertNotEqual(top_perms, "write-all")

        jobs = doc.get("jobs") or {}
        self.assertTrue(jobs)
        for job_name, job in jobs.items():
            self.assertIn("github.server_url == 'https://github.com'", str(job.get("if", "")), job_name)
            perms = job.get("permissions") or top_perms
            self.assertNotIn("packages", perms)
            self.assertNotIn("id-token", perms)
            for step in job.get("steps") or []:
                if "uses" in step:
                    uses_val = str(step["uses"])
                    self.assertRegex(uses_val, r"@[0-9a-f]{40}(\s|$)", f"unpinned action: {uses_val}")
                    if uses_val.startswith("actions/checkout@"):
                        self.assertIn(EXPECTED_CHECKOUT_PIN, uses_val)

        if not shutil.which("actionlint"):
            self.skipTest("actionlint not installed outside workbench")
        lint = subprocess.run(
            ["actionlint", "-no-color", str(wf)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_gitlab_ci_tower_orchestrator_recipe(self) -> None:
        recipe = ROOT / "surfaces" / "gitlab-ci" / "tower-orchestrator.yml"
        self.assertTrue(recipe.is_file(), f"missing {recipe}")
        text = recipe.read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
        self.assertIsInstance(doc, dict)
        self.assertIn("core/runner.py", text)
        self.assertIn("--once", text)
        self.assertIn("resource_group", text)

    def test_alerts_readme_documents_integrations(self) -> None:
        readme = ROOT / "surfaces" / "alerts" / "README.md"
        self.assertTrue(readme.is_file(), f"missing {readme}")
        text = readme.read_text(encoding="utf-8")
        for needle in ("Alertmanager", "core/webhook.py", "triage", "postmortem", "ALERT_WEBHOOK_TOKEN"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
