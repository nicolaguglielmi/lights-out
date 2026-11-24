"""Specification tests for Continuous Ops Loop (ADR 0109).

Strictly vendor-free, app-free, and model-free: covers core/ops_loop.py
(rollback auto-incident and opt-in postmortem), core/releases.py opt-in hook,
core/webhook.py (structural alert normalization, fail-closed auth, payload cap),
and core/runner.py (continuous orchestrator drain, lockfile, signal handling).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from core import ops_loop, releases, runner, webhook

SAMPLE_RECORD = {
    "version": "0.4.0-rc1",
    "stable": "0.3.9",
    "stages": [
        {
            "pct": 5,
            "verdict": "GO",
            "numbers": ["slo: traffic canary(0.4.0-rc1)=1.200 stable(0.3.9)=4.100"],
            "started_at": "2026-11-24T04:00:00Z",
            "judged_at": "2026-11-24T04:01:00Z",
        },
        {
            "pct": 25,
            "verdict": "NO-GO: slo — error_rate 0.500 > 0.010",
            "numbers": ["slo: error_rate canary(0.4.0-rc1)=0.500 stable(0.3.9)=0.000"],
            "started_at": "2026-11-24T04:01:05Z",
            "judged_at": "2026-11-24T04:02:05Z",
        },
    ],
    "verdict": "rolled-back",
    "held_at": None,
    "image": "registry.local/service@sha256:1111",
    "sbom": None,
    "dast": None,
    "reason": "25%: error_rate 0.500 > 0.010",
}


class TempWorkspaceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.chdir(self.root)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._tmp.cleanup()


class AutoIncidentTests(TempWorkspaceCase):
    def test_on_rollback_creates_deterministic_incident_without_fabricating_logs(self) -> None:
        rec_path = self.root / ".crew" / "release.json"
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(json.dumps(SAMPLE_RECORD), encoding="utf-8")

        err1 = io.StringIO()
        with contextlib.redirect_stderr(err1):
            res1 = ops_loop.on_rollback(str(rec_path), {"INCIDENT_OUT": ".crew/incidents"})
        inc_path = Path(res1["incident_path"])
        self.assertTrue(inc_path.is_file())
        self.assertRegex(inc_path.name, r"^rollback-0-4-0-rc1-20261124t040205z\.json$")
        doc = json.loads(inc_path.read_text(encoding="utf-8"))
        self.assertEqual(doc["kind"], "incident")
        self.assertEqual(doc["window"], {"from": "2026-11-24T04:00:00Z", "to": "2026-11-24T04:02:05Z"})
        self.assertEqual(doc["release"], SAMPLE_RECORD)
        self.assertEqual(len(doc["alerts"]), 1)
        self.assertEqual(doc["alerts"][0]["headline"], "NO-GO: slo — error_rate 0.500 > 0.010")
        self.assertEqual(doc["logs"], [])
        self.assertIsNone(doc["chain"])

        first_bytes = inc_path.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            res2 = ops_loop.on_rollback(str(rec_path), {"INCIDENT_OUT": ".crew/incidents"})
        self.assertEqual(res2["incident_path"], res1["incident_path"])
        self.assertEqual(inc_path.read_bytes(), first_bytes)

    def test_on_rollback_anchors_chain_and_copies_real_logs_when_ticket_present(self) -> None:
        rec_path = self.root / ".crew" / "release.json"
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(json.dumps(SAMPLE_RECORD), encoding="utf-8")

        log_path = self.root / ".crew" / "0012" / "release.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("[launchpad] 25%: error_rate 0.500\nslo: error_rate canary(0.4.0-rc1)=0.500\n", encoding="utf-8")

        store_dir = self.root / "adapters" / "stub-state"
        store_dir.mkdir(parents=True)
        sha = "a" * 40
        ticket_doc = {
            "id": "0012",
            "state": "rolled-back",
            "release": {"version": "0.4.0-rc1"},
            "pr": {"number": 17},
            "merge": {"sha": sha},
        }
        (self.root / "state.json").write_text(json.dumps(ticket_doc), encoding="utf-8")
        (store_dir / "get.sh").write_text(
            '#!/bin/sh\n[ "$1 $2" = "tickets 0012" ] || exit 1\ncat state.json\n',
            encoding="utf-8",
        )

        with contextlib.redirect_stderr(io.StringIO()):
            res = ops_loop.on_rollback(
                str(rec_path),
                {
                    "RELEASE_TICKET": "0012",
                    "STATE_STORE": "stub-state",
                    "INCIDENT_OUT": ".crew/incidents",
                },
            )
        doc = json.loads(Path(res["incident_path"]).read_text(encoding="utf-8"))
        self.assertEqual(doc["chain"], {"ticket": "0012", "pr": 17, "merge": sha})
        self.assertEqual(
            doc["logs"],
            ["[launchpad] 25%: error_rate 0.500", "slo: error_rate canary(0.4.0-rc1)=0.500"],
        )

    def test_on_rollback_refuses_missing_or_held_record(self) -> None:
        with self.assertRaises(ops_loop.Refused):
            ops_loop.on_rollback("missing.json", {})
        held = dict(SAMPLE_RECORD, verdict="held", held_at=25)
        Path("held.json").write_text(json.dumps(held), encoding="utf-8")
        with self.assertRaises(ops_loop.Refused):
            ops_loop.on_rollback("held.json", {})
        self.assertFalse((self.root / ".crew" / "incidents").exists())

    def test_releases_hook_is_inert_by_default_and_active_when_enabled(self) -> None:
        base_env = {
            "RELEASE_RECORD": ".crew/release.json",
            "RELEASES_LOG": ".crew/releases.jsonl",
            "CANARY_VERSION": "0.4.0-rc1",
            "STABLE_VERSION": "0.3.9",
            "RELEASE_REASON": "5%: error_rate 1.000",
        }
        stages_json = json.dumps(SAMPLE_RECORD["stages"])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = releases.main(["record", "rolled-back", "", stages_json, "img@sha"], dict(base_env))
        self.assertEqual(code, 0)
        self.assertFalse((self.root / ".crew" / "incidents").exists())

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = releases.main(
                ["record", "held", "25", stages_json, "img@sha"],
                {**base_env, "AUTO_INCIDENT_ON_ROLLBACK": "on"},
            )
        self.assertEqual(code, 0)
        self.assertFalse((self.root / ".crew" / "incidents").exists())

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = releases.main(
                ["record", "rolled-back", "", stages_json, "img@sha"],
                {**base_env, "AUTO_INCIDENT_ON_ROLLBACK": "on", "TICKET": "0001", "STATE_STORE": "missing-store"},
            )
        self.assertEqual(code, 0)
        created = list((self.root / ".crew" / "incidents").glob("rollback-0-4-0-rc1-*.json"))
        self.assertEqual(len(created), 1)
        inc_doc = json.loads(created[0].read_text(encoding="utf-8"))
        self.assertIsNone(inc_doc["chain"])

    def test_auto_postmortem_opt_in_reports_missing_recording_without_failing(self) -> None:
        rec_path = self.root / ".crew" / "release.json"
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(json.dumps(SAMPLE_RECORD), encoding="utf-8")

        (self.root / "core").mkdir()
        (self.root / "core" / "postmortem.sh").write_text(
            '#!/bin/sh\necho "harness-adk: ops-triage: NoRecording: unrecorded" >&2\n'
            'echo "NO-GO: postmortem — the ops-triage role did not finish (adk)"\nexit 1\n',
            encoding="utf-8",
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = ops_loop.on_rollback(
                str(rec_path),
                {
                    "INCIDENT_OUT": ".crew/incidents",
                    "AUTO_POSTMORTEM_ON_ROLLBACK": "on",
                    "AGENT_HARNESS": "replay",
                    "TRACKER": "file",
                },
            )
        self.assertTrue(Path(res["incident_path"]).is_file())
        self.assertIsNotNone(res["postmortem"])
        self.assertNotEqual(res["postmortem"]["status"], "ok")
        self.assertIn("recording", err.getvalue().lower())

        (self.root / "core" / "postmortem.sh").write_text(
            '#!/bin/sh\necho "GO: postmortem — 1 action item(s): tickets 0008"\nexit 0\n',
            encoding="utf-8",
        )
        with contextlib.redirect_stderr(io.StringIO()):
            res_ok = ops_loop.on_rollback(
                str(rec_path),
                {
                    "INCIDENT_OUT": ".crew/incidents",
                    "AUTO_POSTMORTEM_ON_ROLLBACK": "on",
                    "AGENT_HARNESS": "replay",
                    "TRACKER": "file",
                },
            )
        res_ok_check = res_ok["postmortem"]["status"]
        self.assertEqual(res_ok_check, "ok")
        self.assertIn("GO: postmortem", res_ok["postmortem"]["line"])

    def test_ops_loop_cli_on_rollback(self) -> None:
        rec_path = self.root / ".crew" / "release.json"
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(json.dumps(SAMPLE_RECORD), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = ops_loop.main(["on-rollback", "--record", str(rec_path)], env={"INCIDENT_OUT": ".crew/incidents"})
        self.assertEqual(rc, 0)
        self.assertTrue(Path(out.getvalue().strip()).is_file())

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            bad_rc = ops_loop.main(["on-rollback", "--record", "no-such-file.json"], env={})
        self.assertEqual(bad_rc, 1)


class WebhookTests(TempWorkspaceCase):
    def test_normalize_alerts_array_envelope(self) -> None:
        payload = {
            "status": "firing",
            "commonLabels": {"service": "checkout"},
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "HighErrorRate", "severity": "critical", "version": "0.5.0"},
                    "annotations": {"summary": "5xx spike on canary", "description": "error_rate=0.45"},
                    "startsAt": "2026-11-24T05:00:00Z",
                }
            ],
        }
        norm = webhook.normalize_payload(payload, received_at="2026-11-24T05:01:00Z")
        self.assertEqual(norm["schema"], "lights-out.alert/1")
        self.assertEqual(norm["received_at"], "2026-11-24T05:01:00Z")
        self.assertEqual(len(norm["alerts"]), 1)
        alert = norm["alerts"][0]
        self.assertEqual(alert["headline"], "5xx spike on canary")
        self.assertEqual(alert["severity"], "critical")
        self.assertEqual(alert["status"], "firing")
        self.assertEqual(alert["version"], "0.5.0")
        self.assertEqual(alert["summary"], "error_rate=0.45")
        self.assertEqual(alert["labels"]["service"], "checkout")

    def test_normalize_incident_envelope(self) -> None:
        payload = {
            "incident": {
                "incident_id": "inc-42",
                "state": "open",
                "summary": "Latency p95 above 500ms",
                "condition_name": "p95-latency-breach",
                "started_at": 1763960400,
                "severity": "ERROR",
                "metadata": {"user_labels": {"version": "0.6.0-rc2", "stable": "0.5.9"}},
            }
        }
        norm = webhook.normalize_payload(payload, received_at="2026-11-24T05:01:00Z")
        self.assertEqual(norm["schema"], "lights-out.alert/1")
        self.assertEqual(len(norm["alerts"]), 1)
        alert = norm["alerts"][0]
        self.assertEqual(alert["headline"], "Latency p95 above 500ms")
        self.assertEqual(alert["status"], "open")
        self.assertEqual(alert["severity"], "error")
        self.assertEqual(alert["version"], "0.6.0-rc2")
        self.assertEqual(alert["stable"], "0.5.9")
        self.assertRegex(alert["starts_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_normalize_flat_object_preserves_triage_fields(self) -> None:
        payload = {
            "headline": "canary error rate 1.0",
            "severity": "page",
            "version": "0.2.0-canary",
            "stable": "0.1.0",
            "summary": "100% 5xx at 5%",
            "stage": {"pct": 5, "verdict": "NO-GO: slo", "numbers": ["slo: error_rate 1.0"]},
            "chain": {"ticket": "0001", "pr": 2, "merge": "a" * 40},
        }
        norm = webhook.normalize_payload(payload, received_at="2026-11-24T05:01:00Z")
        alert = norm["alerts"][0]
        self.assertEqual(alert["headline"], "canary error rate 1.0")
        self.assertEqual(alert["severity"], "page")
        self.assertEqual(alert["version"], "0.2.0-canary")
        self.assertEqual(alert["stable"], "0.1.0")
        self.assertEqual(alert["stage"], payload["stage"])
        self.assertEqual(alert["chain"], payload["chain"])

    def test_normalize_rejects_unknown_shapes(self) -> None:
        for bad in ({}, [], "str", {"alerts": ["bad"]}, {"incident": "not-dict"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    webhook.normalize_payload(bad)

    def test_cli_normalize_writes_output_file(self) -> None:
        src = self.root / "raw.json"
        src.write_text(json.dumps({"headline": "disk full", "version": "1.0.0", "severity": "high"}), encoding="utf-8")
        out_dir = self.root / "normalized"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = webhook.main(["--normalize", str(src), "--out", str(out_dir)])
        self.assertEqual(rc, 0)
        written = Path(buf.getvalue().strip())
        self.assertTrue(written.is_file())
        data = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "lights-out.alert/1")
        self.assertEqual(data["alerts"][0]["version"], "1.0.0")

    def test_serve_fails_closed_on_non_loopback_without_token(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = webhook.main(["--serve", "--bind", "0.0.0.0", "--port", "0"], env={})
        self.assertNotEqual(rc, 0)
        self.assertIn("ALERT_WEBHOOK_TOKEN", err.getvalue())

    def test_server_bearer_auth_size_cap_and_persistence(self) -> None:
        alerts_dir = self.root / ".crew" / "alerts"
        server = webhook.create_server(
            bind="127.0.0.1",
            port=0,
            out_dir=str(alerts_dir),
            secret="test-webhook-secret",
            dispatch=False,
            env={},
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            url = f"http://127.0.0.1:{port}/webhook"
            body = json.dumps({"headline": "5xx spike", "version": "0.7.0", "severity": "critical"}).encode("utf-8")

            # Missing auth -> 401
            req_no_auth = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as ctx1:
                urllib.request.urlopen(req_no_auth, timeout=5)
            self.assertEqual(ctx1.exception.code, 401)

            # Wrong auth -> 401
            req_bad_auth = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": "Bearer wrong-secret"},
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx2:
                urllib.request.urlopen(req_bad_auth, timeout=5)
            self.assertEqual(ctx2.exception.code, 401)

            # Oversized payload (> 64 KiB) -> 413
            big_body = json.dumps({"headline": "x" * 70000, "version": "0.7.0"}).encode("utf-8")
            req_big = urllib.request.Request(
                url,
                data=big_body,
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-webhook-secret"},
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx3:
                urllib.request.urlopen(req_big, timeout=5)
            self.assertEqual(ctx3.exception.code, 413)

            # Valid bearer POST -> 200 and writes normalized file
            req_ok = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-webhook-secret"},
            )
            with urllib.request.urlopen(req_ok, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                resp_json = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(resp_json["schema"], "lights-out.alert/1")
            self.assertEqual(resp_json["count"], 1)
            written = Path(resp_json["written"])
            self.assertTrue(written.is_file())
            self.assertEqual(written.parent.resolve(), alerts_dir.resolve())
        finally:
            server.shutdown()
            server.server_close()

    def test_dispatch_calls_triage_when_enabled_and_handles_missing_recording(self) -> None:
        (self.root / "core").mkdir()
        log_file = self.root / "triage.calls"
        (self.root / "core" / "triage.sh").write_text(
            f'#!/bin/sh\necho "$ALERT" >> "{log_file}"\n'
            'echo "NO-GO: triage — no recording" >&2\nexit 1\n',
            encoding="utf-8",
        )
        alerts_dir = self.root / ".crew" / "alerts"
        server = webhook.create_server(
            bind="127.0.0.1",
            port=0,
            out_dir=str(alerts_dir),
            secret="test-webhook-secret",
            dispatch=True,
            env={"AGENT_HARNESS": "replay"},
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            url = f"http://127.0.0.1:{port}/webhook"
            body = json.dumps({"headline": "5xx spike", "version": "0.8.0", "severity": "high"}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-webhook-secret"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                payload = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(len(payload["dispatched"]), 1)
            self.assertTrue(log_file.is_file())

            # Alert without version -> skipped without invoking triage.sh, still 200
            log_file.unlink()
            body_no_ver = json.dumps({"headline": "disk warning", "severity": "low"}).encode("utf-8")
            req2 = urllib.request.Request(
                url,
                data=body_no_ver,
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-webhook-secret"},
            )
            with urllib.request.urlopen(req2, timeout=5) as resp2:
                self.assertEqual(resp2.status, 200)
                payload2 = json.loads(resp2.read().decode("utf-8"))
            self.assertEqual(payload2["dispatched"][0]["status"], "skipped")
            self.assertFalse(log_file.exists())

            # Health endpoint -> 200; wrong Content-Type -> 415
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as hresp:
                self.assertEqual(hresp.status, 200)
            req_415 = urllib.request.Request(
                url,
                data=b"plain text",
                method="POST",
                headers={"Content-Type": "text/plain", "Authorization": "Bearer test-webhook-secret"},
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx_415:
                urllib.request.urlopen(req_415, timeout=5)
            self.assertEqual(ctx_415.exception.code, 415)
        finally:
            server.shutdown()
            server.server_close()


class RunnerTests(TempWorkspaceCase):
    def _setup_stub_bus(self, messages: list[str], tower_fail_ticket: str = "") -> tuple[Path, Path]:
        bus_dir = self.root / "adapters" / "stub-bus"
        bus_dir.mkdir(parents=True)
        (self.root / "core").mkdir(exist_ok=True)
        queue_file = self.root / "queue.txt"
        queue_file.write_text("".join(f"{m}\n" for m in messages), encoding="utf-8")
        events_log = self.root / "events.log"

        (bus_dir / "pull.sh").write_text(
            f'#!/bin/sh\nset -eu\n'
            f'if [ ! -s "{queue_file}" ]; then exit 0; fi\n'
            f'head -n 1 "{queue_file}"\n'
            f'tail -n +2 "{queue_file}" > "{queue_file}.tmp" && mv "{queue_file}.tmp" "{queue_file}"\n',
            encoding="utf-8",
        )
        (bus_dir / "ack.sh").write_text(
            f'#!/bin/sh\nset -eu\necho "ack:$1" >> "{events_log}"\n',
            encoding="utf-8",
        )
        (self.root / "core" / "tower.sh").write_text(
            f'#!/bin/sh\nset -eu\necho "step:$2:$3" >> "{events_log}"\n'
            f'if [ "$2" = "{tower_fail_ticket}" ]; then exit 1; fi\n',
            encoding="utf-8",
        )
        return queue_file, events_log

    def test_runner_once_drains_and_acks_all_messages_and_drops_malformed(self) -> None:
        _, events_log = self._setup_stub_bus([
            'ack-1 {"ticket":"0001","step":"assign"}',
            'ack-2 {"not_a_ticket":true}',
            'ack-3 {"ticket":"0002","step":"ci"}',
        ])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = runner.main(
                ["--once", "--lockfile", str(self.root / ".crew" / "runner.lock")],
                env={"EVENT_BUS": "stub-bus", "STATE_STORE": "sqlite"},
            )
        self.assertEqual(rc, 0)
        lines = events_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            lines,
            ["step:0001:assign", "ack:ack-1", "ack:ack-2", "step:0002:ci", "ack:ack-3"],
        )
        log_entries = [json.loads(line) for line in err.getvalue().splitlines() if line.strip().startswith("{")]
        self.assertTrue(any(e.get("event") == "drop" for e in log_entries))
        self.assertTrue(any(e.get("event") == "cycle" and e.get("processed") == 3 for e in log_entries))

    def test_runner_once_step_failure_still_acks_and_exits_one(self) -> None:
        _, events_log = self._setup_stub_bus(
            [
                'ack-1 {"ticket":"0001","step":"assign"}',
                'ack-2 {"ticket":"0002","step":"ci"}',
            ],
            tower_fail_ticket="0001",
        )
        with contextlib.redirect_stderr(io.StringIO()):
            rc = runner.main(
                ["--once", "--lockfile", str(self.root / ".crew" / "runner.lock")],
                env={"EVENT_BUS": "stub-bus", "STATE_STORE": "sqlite"},
            )
        self.assertEqual(rc, 1)
        self.assertEqual(
            events_log.read_text(encoding="utf-8").splitlines(),
            ["step:0001:assign", "ack:ack-1", "step:0002:ci", "ack:ack-2"],
        )

    def test_runner_lockfile_prevents_concurrent_runs(self) -> None:
        self._setup_stub_bus([])
        lock_path = self.root / ".crew" / "runner.lock"
        with runner.acquire_lock(str(lock_path)):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = runner.main(
                    ["--once", "--lockfile", str(lock_path)],
                    env={"EVENT_BUS": "stub-bus"},
                )
            self.assertNotEqual(rc, 0)
            self.assertIn("lock", err.getvalue().lower())

    def test_runner_requires_event_bus(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = runner.main(["--once"], env={})
        self.assertNotEqual(rc, 0)
        self.assertIn("EVENT_BUS", err.getvalue())


if __name__ == "__main__":
    unittest.main()
