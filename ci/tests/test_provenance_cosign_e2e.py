"""End-to-end cosign test for signed agent provenance (docs/adr/0117).

Runs inside the Podman workbench container where cosign is installed:
1. Generates an ephemeral passwordless cosign keypair (`COSIGN_PASSWORD= cosign generate-key-pair`).
2. Creates a real git repository with an ADR, PR/merge commits, stub state store, and release log.
3. Builds, signs, and verifies `.intoto.json` + `.intoto.json.bundle` via `core/provenance.sh` (`GO`).
4. Tampers one byte in `.intoto.json` and proves both `cosign verify-blob` and `core/provenance.sh verify`
   fail (`NO-GO`).
5. Tampers one byte in the attested ADR file and proves `core/provenance.sh verify` fails (`NO-GO`).
6. Verifies `core/evidence.sh` links the signed provenance bundle into `.crew/evidence/<v>.json`.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class ProvenanceCosignE2ETests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(shutil.which("cosign"), "cosign is not on PATH: this test runs in the workbench, ./go ci")
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        for rel in ("core", "ci", "console"):
            (self.root / rel).symlink_to(REPO / rel)
        (self.root / "adapters" / "stub-state").mkdir(parents=True)
        get_sh = self.root / "adapters" / "stub-state" / "get.sh"
        get_sh.write_text(
            '#!/bin/sh\nset -eu\n[ "$1" = tickets ] || exit 1\n'
            'f="state/$2.json"\n[ -f "$f" ] || exit 1\ncat "$f"\n',
            encoding="utf-8",
        )
        get_sh.chmod(0o755)
        (self.root / "state").mkdir()
        (self.root / ".crew").mkdir()
        (self.root / "keys").mkdir()
        (self.root / "work").mkdir()

        # Ephemeral passwordless cosign keypair
        gen_env = dict(os.environ, COSIGN_PASSWORD="")
        gen = subprocess.run(
            ["cosign", "generate-key-pair"],
            cwd=self.root / "keys",
            env=gen_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(gen.returncode, 0, gen.stderr)
        self.key = self.root / "keys" / "cosign.key"
        self.pub = self.root / "keys" / "cosign.pub"
        self.assertTrue(self.key.is_file())
        self.assertTrue(self.pub.is_file())

        # Scratch git repository with an ADR
        self.work = self.root / "work"
        self._git("init", "-q")
        self._git("config", "user.email", "e2e@example.invalid")
        self._git("config", "user.name", "E2E")
        (self.work / "README.md").write_text("service\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-q", "-m", "Initial commit")
        base_sha = self._git("rev-parse", "HEAD").strip()

        (self.work / "docs" / "adr").mkdir(parents=True)
        self.adr = self.work / "docs" / "adr" / "0001-signed-receipts.md"
        self.adr.write_text("# ADR 0001 — Signed receipts\n", encoding="utf-8")
        self._git("add", "docs/adr/0001-signed-receipts.md")
        self._git("commit", "-q", "-m", "Add ADR-0001 for signed receipts")
        merge_sha = self._git("rev-parse", "HEAD").strip()

        img_digest = "d" * 64
        doc = {
            "id": "0001",
            "state": "held",
            "attempt": 1,
            "spec": {"sha256": "e" * 64, "title": "Implement ADR-0001 receipts"},
            "tasks": [{
                "attempt": 1,
                "role": "dev-worker",
                "harness": "replay",
                "provider": "synthetic",
                "model": "worker-e2e",
                "thinking": "high",
                "turns": 3,
                "input_sha256": "f" * 64,
                "tokens": {"input": 1000, "output": 300},
            }],
            "spend": [{
                "step": "assign",
                "attempt": 1,
                "role": "dev-worker",
                "harness": "replay",
                "provider": "synthetic",
                "model": "worker-e2e",
                "tokens": {"input": 1000, "output": 300},
                "cost_usd": 0.0042,
                "cost_unknown": None,
                "at": "2026-11-24T12:00:00Z",
            }],
            "pr": {"number": 1, "url": "http://host/pr/1", "head_sha": merge_sha, "base_sha": base_sha},
            "ci": {
                "verdict": "GO",
                "failed": None,
                "risk_level": "low",
                "decision": "auto-merge",
                "head_sha": merge_sha,
                "line": "GO: gates — risk low, auto-merge",
                "details": [],
                "lines": [
                    "GO: risk — low, auto-merge",
                    "GO: secrets",
                    "GO: test-weakening",
                    "GO: deps",
                    "GO: sast",
                    "GO: test",
                    "GO: mutation",
                    "GO: test-proof",
                ],
                "seconds": {
                    "risk": 0, "secrets": 1, "test-weakening": 0, "deps": 1,
                    "sast": 2, "test": 3, "mutation": 8, "test-proof": 1,
                },
            },
            "merge": {"sha": merge_sha, "head_sha": merge_sha},
            "release": {"version": "1.0.0", "verdict": "held", "image": f"registry.local/app-svc@sha256:{img_digest}"},
        }
        (self.root / "state" / "0001.json").write_text(json.dumps(doc), encoding="utf-8")
        releases = [
            {
                "kind": "release",
                "at": "2026-11-24T12:10:00Z",
                "ticket": "0001",
                "record": {
                    "version": "1.0.0",
                    "stable": "0.9.0",
                    "stages": [{"pct": 5, "verdict": "GO", "numbers": ["slo: ok"],
                                "started_at": "2026-11-24T12:05:00Z", "judged_at": "2026-11-24T12:10:00Z"}],
                    "verdict": "held",
                    "held_at": 25,
                    "image": f"registry.local/app-svc@sha256:{img_digest}",
                    "sbom": "sha256:" + ("c" * 64),
                    "dast": "staging: 0 high",
                },
            },
            {
                "kind": "answer",
                "at": "2026-11-24T12:20:00Z",
                "version": "1.0.0",
                "answer": "GO",
                "by": "operator",
                "via": "cli",
                "brief_sha256": "b" * 64,
                "brief_author": "release",
                "reason": "all stages green",
            },
        ]
        (self.root / ".crew" / "releases.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in releases), encoding="utf-8"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args):
        proc = subprocess.run(
            ["git", *args], cwd=self.work, capture_output=True, text=True, check=True
        )
        return proc.stdout

    def _run_sh(self, script, *args, env_extra=None):
        env = dict(
            os.environ,
            PYTHONPATH=str(REPO),
            VERSION="1.0.0",
            STATE_STORE="stub-state",
            RELEASES_LOG=".crew/releases.jsonl",
            WORKLOAD=str(self.work),
            COSIGN_KEY=str(self.key),
        )
        if env_extra:
            for k, v in env_extra.items():
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = v
        return subprocess.run(
            ["sh", script, *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_sign_verify_tamper_and_evidence_pack_integration(self):
        # 1. Build, sign, and verify via core/provenance.sh
        res = self._run_sh("core/provenance.sh")
        self.assertEqual(res.returncode, 0, res.stderr)
        lines = res.stdout.splitlines()
        self.assertEqual(len(lines), 1, res.stdout)
        self.assertTrue(lines[0].startswith("GO: provenance — "), lines[0])
        self.assertIn("signed and verified", lines[0])

        att = self.root / ".crew" / "provenance" / "1.0.0.intoto.json"
        bundle = self.root / ".crew" / "provenance" / "1.0.0.intoto.json.bundle"
        self.assertTrue(att.is_file())
        self.assertTrue(bundle.is_file())

        # Verify cleanly via core/provenance.sh verify
        ver = self._run_sh("core/provenance.sh", "verify", str(att))
        self.assertEqual(ver.returncode, 0, ver.stderr)
        self.assertTrue(ver.stdout.startswith("GO: provenance — "), ver.stdout)

        # Evidence pack links the signed provenance bundle
        ev = self._run_sh("core/evidence.sh")
        self.assertEqual(ev.returncode, 0, ev.stderr)
        self.assertTrue(ev.stdout.startswith("GO: evidence — "), ev.stdout)
        ev_pack = json.loads((self.root / ".crew" / "evidence" / "1.0.0.json").read_text(encoding="utf-8"))
        self.assertIn("provenance", ev_pack["artefacts"])
        self.assertIn("value", ev_pack["artefacts"]["provenance"])

        # 2. Tamper one byte in .intoto.json -> cosign verify-blob AND core/provenance.sh verify MUST fail (NO-GO)
        original_bytes = att.read_bytes()
        tampered_bytes = bytearray(original_bytes)
        idx = original_bytes.index(b'"GO"') + 1
        tampered_bytes[idx] = ord("g")  # valid JSON, 1 byte altered
        att.write_bytes(bytes(tampered_bytes))

        cosign_check = subprocess.run(
            [
                "cosign", "verify-blob",
                "--key", str(self.pub),
                "--insecure-ignore-tlog",
                "--bundle", str(bundle),
                str(att),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(cosign_check.returncode, 0)

        tampered_ver = self._run_sh("core/provenance.sh", "verify", str(att))
        self.assertEqual(tampered_ver.returncode, 1)
        self.assertEqual(len(tampered_ver.stdout.splitlines()), 1, tampered_ver.stdout)
        self.assertTrue(tampered_ver.stdout.startswith("NO-GO: provenance — "), tampered_ver.stdout)

        # 3. Restore statement, tamper one byte in attested ADR -> core/provenance.sh verify MUST fail (NO-GO)
        att.write_bytes(original_bytes)
        self.adr.write_bytes(self.adr.read_bytes() + b"\n")
        adr_tamper = self._run_sh("core/provenance.sh", "verify", str(att))
        self.assertEqual(adr_tamper.returncode, 1)
        self.assertTrue(adr_tamper.stdout.startswith("NO-GO: provenance — "), adr_tamper.stdout)

        # 4. Missing cosign key on build -> NO-GO, leaves unsigned .intoto.json and no stale .bundle
        no_key = self._run_sh(
            "core/provenance.sh",
            env_extra={"COSIGN_KEY": str(self.root / "keys" / "absent.key")},
        )
        self.assertEqual(no_key.returncode, 1)
        self.assertTrue(no_key.stdout.startswith("NO-GO: provenance — "), no_key.stdout)
        self.assertTrue(att.is_file())
        self.assertFalse(bundle.exists())


if __name__ == "__main__":
    unittest.main()
