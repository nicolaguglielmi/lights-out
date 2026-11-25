"""Vendor-free tests for core/provenance.py attach and build --attach (docs/adr/0134).

Uses only synthetic temporary adapters (adapters/fakehost/attest.sh) with zero
vendor names so core/tests/ satisfies ci/vendor-free.sh, ci/app-free.sh, and
ci/model-free.sh.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.adr_gov import validate_adr
from core.tests.test_provenance import ProvenanceTestCase, full_ticket_doc, intake_doc, release_entry, answer_entry

REPO = Path(__file__).resolve().parents[2]
MERGE_SHA = "3" * 40


def _write_fake_adapter(root: Path, name: str, body: str) -> Path:
    script = root / "adapters" / name / "attest.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


class GovernanceAndBoundaryTests(unittest.TestCase):
    def test_adr_0134_passes_governance_and_fields(self) -> None:
        adr = REPO / "docs" / "adr" / "0134-codehost-merge-attestations.md"
        self.assertTrue(adr.is_file())
        text = adr.read_text(encoding="utf-8")
        self.assertEqual(validate_adr(text), [])
        self.assertIn("- Status: accepted", text)
        self.assertIn("- Date: 2026-11-25", text)
        for heading in ("## Context", "## Decision", "## Alternatives", "## Consequences", "## Provenance"):
            self.assertIn(heading, text)

    def test_provenance_sh_stays_within_25_code_lines(self) -> None:
        sh_path = REPO / "core" / "provenance.sh"
        self.assertTrue(sh_path.is_file())
        code_lines = [
            ln for ln in sh_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        self.assertLessEqual(len(code_lines), 25, f"core/provenance.sh has {len(code_lines)} code lines")


class ProvenanceAttachTests(ProvenanceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_doc(full_ticket_doc())
        self.write_doc(intake_doc())
        self.write_releases(release_entry(), answer_entry())
        (self.root / "ci").symlink_to(REPO / "ci")
        (self.root / "core").symlink_to(REPO / "core")
        self.calls_log = self.root / "attest-calls.log"
        _write_fake_adapter(
            self.root,
            "fakehost",
            f'''#!/bin/sh
set -eu
printf '%s\\t%s\\n' "$1" "$2" >> {str(self.calls_log)!r}
printf '{{"stored":"remote","id":"att-101","url":"https://fakehost.invalid/attestations/101","sha":"%s","verify_cmd":"fakehost verify --id att-101"}}\\n' "$1"
''',
        )
        _write_fake_adapter(
            self.root,
            "fakehost-two",
            '''#!/bin/sh
set -eu
printf '{"stored":"remote","id":"att-202","url":"https://fakehost-two.invalid/attestations/202","sha":"%s","note":"second host"}\\n' "$1"
''',
        )

    def test_attach_subcommand_invokes_dynamic_adapter_and_writes_additive_receipt(self) -> None:
        built = self.run_cli("build", env_extra={"VERSION": "0.4.0"})
        self.assertEqual(built.returncode, 0, built.stderr)
        att_path = self.root / ".crew" / "provenance" / "0.4.0.intoto.json"
        bundle_path = self.root / ".crew" / "provenance" / "0.4.0.intoto.json.bundle"
        bundle_path.write_text(json.dumps({"mediaType": "application/vnd.dev.sigstore.bundle+json;version=0.2"}),
                               encoding="utf-8")
        before_bytes = att_path.read_bytes()

        receipt_path = self.root / ".crew" / "provenance" / "0.4.0.receipt.json"
        receipt_path.write_text(json.dumps({"custom_meta": "keep-me"}), encoding="utf-8")

        res = self.run_cli(
            "attach",
            "--statement",
            str(att_path),
            "--bundle",
            str(bundle_path),
            "--out-receipt",
            str(receipt_path),
            env_extra={"CODE_HOST": "fakehost"},
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(res.stdout.startswith("OK: "), res.stdout)
        self.assertIn("fakehost", res.stdout)
        self.assertIn("stored=remote", res.stdout)
        self.assertIn("fakehost verify --id att-101", res.stderr)
        self.assertIn("https://fakehost.invalid/attestations/101", res.stderr)
        self.assertIn("core/provenance.py verify", res.stderr)

        # Original signed statement bytes must remain untouched
        self.assertEqual(att_path.read_bytes(), before_bytes)

        # Adapter received resolved merge SHA and bundle path
        logged = self.calls_log.read_text(encoding="utf-8").strip().split("\t")
        self.assertEqual(logged, [MERGE_SHA, str(bundle_path)])

        # Second attach appends additively without losing custom keys or first entry
        res2 = self.run_cli(
            "attach",
            "--statement",
            str(att_path),
            "--sha",
            "abcdef0123" * 4,
            "--out-receipt",
            str(receipt_path),
            env_extra={"CODE_HOST": "fakehost-two"},
        )
        self.assertEqual(res2.returncode, 0, res2.stderr)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["custom_meta"], "keep-me")
        self.assertEqual(receipt["code_host"], "fakehost-two")
        self.assertEqual(receipt["receipt"]["id"], "att-202")
        self.assertEqual(len(receipt["attachments"]), 2)
        self.assertEqual(receipt["attachments"][0]["code_host"], "fakehost")
        self.assertEqual(receipt["attachments"][0]["receipt"]["id"], "att-101")
        self.assertEqual(receipt["attachments"][1]["code_host"], "fakehost-two")
        self.assertEqual(receipt["attachments"][1]["sha"], "abcdef0123" * 4)

    def test_build_with_attach_flag_succeeds_and_writes_sidecar_receipt(self) -> None:
        proc = self.run_cli("build", "--attach", env_extra={"VERSION": "0.4.0", "CODE_HOST": "fakehost"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), ".crew/provenance/0.4.0.intoto.json\t0")
        self.assertIn("fakehost verify --id att-101", proc.stderr)

        sidecar = self.root / ".crew" / "provenance" / "0.4.0.intoto.json.attestation-receipt.json"
        self.assertTrue(sidecar.is_file())
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "attached")
        self.assertEqual(data["code_host"], "fakehost")
        self.assertEqual(data["sha"], MERGE_SHA)

        logged = self.calls_log.read_text(encoding="utf-8").strip().split("\t")
        self.assertEqual(logged, [MERGE_SHA, ".crew/provenance/0.4.0.intoto.json"])

    def test_missing_attest_adapter_skips_cleanly_unless_required(self) -> None:
        (self.root / "adapters" / "noattest").mkdir(parents=True)
        for ch in ("", "noattest"):
            with self.subTest(code_host=ch):
                proc = self.run_cli("build", "--attach", env_extra={"VERSION": "0.4.0", "CODE_HOST": ch})
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.strip(), ".crew/provenance/0.4.0.intoto.json\t0")
                self.assertIn("attach skipped", proc.stderr)
                sidecar = self.root / ".crew" / "provenance" / "0.4.0.intoto.json.attestation-receipt.json"
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                self.assertEqual(data["status"], "skipped")

                req = self.run_cli(
                    "build",
                    "--attach",
                    "--attach-required",
                    env_extra={"VERSION": "0.4.0", "CODE_HOST": ch},
                )
                self.assertEqual(req.returncode, 1)
                self.assertEqual(len(req.stdout.splitlines()), 1)

        att_path = self.root / ".crew" / "provenance" / "0.4.0.intoto.json"
        attach_skip = self.run_cli("attach", "--statement", str(att_path), env_extra={"CODE_HOST": "noattest"})
        self.assertEqual(attach_skip.returncode, 0, attach_skip.stderr)
        self.assertIn("attach skipped", attach_skip.stdout)

        attach_req = self.run_cli(
            "attach", "--statement", str(att_path), "--required", env_extra={"CODE_HOST": "noattest"}
        )
        self.assertEqual(attach_req.returncode, 1)

    def test_attach_scrubs_secrets_and_rejects_bad_adapter_outputs(self) -> None:
        built = self.run_cli("build", env_extra={"VERSION": "0.4.0"})
        self.assertEqual(built.returncode, 0, built.stderr)
        att_path = self.root / ".crew" / "provenance" / "0.4.0.intoto.json"

        secret_val = "syn" + "secret" + "778899"
        _write_fake_adapter(
            self.root,
            "leaky-fail",
            f'''#!/bin/sh
printf 'API_TOKEN=%s failed\\n' {secret_val!r} >&2
exit 1
''',
        )
        fail_res = self.run_cli("attach", "--statement", str(att_path), env_extra={"CODE_HOST": "leaky-fail"})
        self.assertEqual(fail_res.returncode, 1)
        self.assertNotIn(secret_val, fail_res.stdout + fail_res.stderr)
        self.assertIn("[REDACTED]", fail_res.stdout)

        _write_fake_adapter(
            self.root,
            "bad-json",
            '''#!/bin/sh
printf 'not-json\\n'
''',
        )
        bad_res = self.run_cli("attach", "--statement", str(att_path), env_extra={"CODE_HOST": "bad-json"})
        self.assertEqual(bad_res.returncode, 1)

        trav = self.run_cli("attach", "--statement", str(att_path), env_extra={"CODE_HOST": "../escape"})
        self.assertEqual(trav.returncode, 1)

    def test_provenance_sh_attach_subcommand_honors_contract(self) -> None:
        built = self.run_cli("build", env_extra={"VERSION": "0.4.0"})
        self.assertEqual(built.returncode, 0, built.stderr)
        att_path = self.root / ".crew" / "provenance" / "0.4.0.intoto.json"

        proc = subprocess.run(
            ["sh", "core/provenance.sh", "attach", "--statement", str(att_path)],
            cwd=self.root,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "PYTHONPATH": str(REPO),
                "CODE_HOST": "fakehost",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 1, proc.stdout)
        self.assertTrue(lines[0].startswith("GO: provenance — "), lines[0])
        self.assertIn("fakehost", lines[0])


if __name__ == "__main__":
    unittest.main()
