"""Spec-derived tests for core/provenance.py (docs/adr/0117).

Written from ADR 0117 before implementation:
- SLSA v1.0 / in-toto Statement/v1 envelope and https://lights-out.dev/provenance/v1 predicate.
- Strict subject digest normalization (64-hex sha256 for images, 40/64-hex gitCommit for commits).
- Full lineage binding: ticket, intake, changed and cited ADRs (paths + sha256), agents
  (product / tasks / review runs with role, harness, provider, model, thinking, turns,
  input_sha256, tokens, usd), 8 PR gate verdicts and durations, merge, and go-for-launch human gate.
- Honest gaps (null + explicit predicate.missing list, <path>\\t<missing_count> stdout) and
  strict secret scrubbing.
- Structural and repository ADR digest verification (tamper detection).
"""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.adr_gov import validate_adr

REPO = Path(__file__).resolve().parents[2]
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://lights-out.dev/provenance/v1"

IMG_DIGEST_UPPER = "A" * 64
IMG_DIGEST = "a" * 64
BASE_SHA = "1" * 40
HEAD_SHA = "2" * 40
MERGE_SHA = "3" * 40
SPEC_SHA = "4" * 64
BRIEF_SHA = "5" * 64
IN_SHA_PRODUCT = "6" * 64
IN_SHA_TASK1 = "7" * 64
IN_SHA_TASK2 = "8" * 64
IN_SHA_REVIEW = "9" * 64

EIGHT_GATE_LINES = [
    "GO: risk — low, review",
    "GO: secrets",
    "GO: test-weakening",
    "GO: deps — 0 vulnerabilities",
    "GO: sast — 0 new findings",
    "GO: test — 24 passed",
    "GO: mutation — 88% >= 80%",
    "GO: test-proof",
]

EIGHT_GATE_SECONDS = {
    "risk": 0,
    "secrets": 1,
    "test-weakening": 0,
    "deps": 2,
    "sast": 3,
    "test": 4,
    "mutation": 11,
    "test-proof": 2,
}


def full_ticket_doc(version="0.4.0", intake_id="0010"):
    return {
        "id": "0001",
        "state": "held",
        "attempt": 2,
        "intake": intake_id,
        "spec": {"sha256": SPEC_SHA, "title": "Enforce decision record ADR-0002 in checkout"},
        "product": {
            "role": "product",
            "consulted": True,
            "harness": "replay",
            "provider": "synthetic",
            "model": "planner-v1",
            "thinking": "medium",
            "turns": 2,
            "input_sha256": IN_SHA_PRODUCT,
            "tokens": {"input": 500, "output": 120},
        },
        "tasks": [
            {
                "attempt": 1,
                "role": "dev-worker",
                "harness": "replay",
                "provider": "synthetic",
                "model": "worker-v1",
                "thinking": "high",
                "turns": 5,
                "input_sha256": IN_SHA_TASK1,
                "tokens": {"input": 1500, "output": 400},
                "files": [{"path": "docs/adr/0001-storage-layout.md", "added": 12, "removed": 0}],
            },
            {
                "attempt": 2,
                "role": "dev-worker",
                "harness": "replay",
                "provider": "synthetic",
                "model": "worker-v1",
                "thinking": "high",
                "turns": 3,
                "input_sha256": IN_SHA_TASK2,
                "tokens": {"input": 900, "output": 250},
                "files": [{"path": "docs/adr/0001-storage-layout.md", "added": 14, "removed": 0}],
            },
        ],
        "review": {
            "role": "test-reviewer",
            "verdict": "approved",
            "reason": "Tests cover edge cases.",
            "harness": "replay",
            "provider": "synthetic",
            "model": "reviewer-v1",
            "thinking": "medium",
            "turns": 1,
            "input_sha256": IN_SHA_REVIEW,
            "tokens": {"input": 800, "output": 100},
        },
        "spend": [
            {"step": "clarify", "attempt": None, "role": "product", "harness": "replay",
             "provider": "synthetic", "model": "planner-v1", "tokens": {"input": 500, "output": 120},
             "cost_usd": 0.0012, "cost_unknown": None, "at": "2026-11-24T10:00:00Z"},
            {"step": "assign", "attempt": 1, "role": "dev-worker", "harness": "replay",
             "provider": "synthetic", "model": "worker-v1", "tokens": {"input": 1500, "output": 400},
             "cost_usd": 0.0095, "cost_unknown": None, "at": "2026-11-24T10:05:00Z"},
            {"step": "rework", "attempt": 2, "role": "dev-worker", "harness": "replay",
             "provider": "synthetic", "model": "worker-v1", "tokens": {"input": 900, "output": 250},
             "cost_usd": 0.0058, "cost_unknown": None, "at": "2026-11-24T10:15:00Z"},
            {"step": "review", "attempt": 2, "role": "test-reviewer", "harness": "replay",
             "provider": "synthetic", "model": "reviewer-v1", "tokens": {"input": 800, "output": 100},
             "cost_usd": 0.0021, "cost_unknown": None, "at": "2026-11-24T10:20:00Z"},
        ],
        "pr": {"number": 7, "url": "http://host/pr/7", "head_sha": HEAD_SHA, "base_sha": BASE_SHA},
        "ci": {
            "verdict": "GO",
            "failed": None,
            "risk_level": "medium",
            "decision": "review",
            "head_sha": HEAD_SHA,
            "line": "GO: gates — risk medium, review",
            "details": [],
            "lines": EIGHT_GATE_LINES,
            "seconds": EIGHT_GATE_SECONDS,
        },
        "merge": {"sha": MERGE_SHA, "head_sha": HEAD_SHA},
        "release": {
            "version": version,
            "verdict": "held",
            "image": f"registry.local/service@sha256:{IMG_DIGEST_UPPER}",
        },
    }


def intake_doc(intake_id="0010"):
    return {
        "id": intake_id,
        "kind": "intake",
        "state": "confirmed",
        "intake": {"by": "alice", "via": "console", "at": "2026-11-24T09:30:00Z"},
        "confirmation": {"by": "alice", "via": "console", "at": "2026-11-24T09:45:00Z"},
        "tickets": ["0001"],
    }


def release_entry(version="0.4.0", ticket="0001", image=None, verdict="held"):
    return {
        "kind": "release",
        "at": "2026-11-24T11:00:00Z",
        "ticket": ticket,
        "record": {
            "version": version,
            "stable": "0.3.0",
            "stages": [{"pct": 5, "verdict": "GO", "numbers": ["slo: ok"]}],
            "verdict": verdict,
            "held_at": 25 if verdict == "held" else None,
            "image": f"registry.local/service@sha256:{IMG_DIGEST_UPPER}" if image is None else image,
            "sbom": "sha256:" + ("b" * 64),
            "dast": None,
        },
    }


def answer_entry(version="0.4.0", answer="GO", brief_sha256=BRIEF_SHA, reason="canary clean"):
    return {
        "kind": "answer",
        "at": "2026-11-24T11:30:00Z",
        "version": version,
        "answer": answer,
        "by": "nicola",
        "via": "console",
        "brief_sha256": brief_sha256,
        "reason": reason,
    }


class ProvenanceTestCase(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
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
        (self.root / "docs" / "adr").mkdir(parents=True)
        self.adr1 = self.root / "docs" / "adr" / "0001-storage-layout.md"
        self.adr2 = self.root / "docs" / "adr" / "0002-checkout-rules.md"
        self.adr1.write_text("# ADR 0001 — Storage layout\n", encoding="utf-8")
        self.adr2.write_text("# ADR 0002 — Checkout rules\n", encoding="utf-8")
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def write_doc(self, doc, ticket=None):
        tid = ticket or doc["id"]
        (self.root / "state" / f"{tid}.json").write_text(json.dumps(doc), encoding="utf-8")

    def write_releases(self, *entries):
        log = self.root / ".crew" / "releases.jsonl"
        log.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    def run_cli(self, *args, env_extra=None):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": str(REPO),
            "STATE_STORE": "stub-state",
            "RELEASES_LOG": ".crew/releases.jsonl",
            "WORKLOAD": str(self.root),
            "PROVENANCE_DIR": ".crew/provenance",
        }
        if env_extra:
            for k, v in env_extra.items():
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = v
        return subprocess.run(
            [sys.executable, str(REPO / "core" / "provenance.py"), *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


class EnvelopeAndFullLineageTests(ProvenanceTestCase):
    def test_adr_0117_has_zero_defects(self):
        adr_path = REPO / "docs" / "adr" / "0117-signed-agent-provenance-attestations.md"
        self.assertTrue(adr_path.is_file())
        self.assertEqual(validate_adr(adr_path.read_text(encoding="utf-8")), [])

    def test_build_full_release_and_ticket_provenance(self):
        self.write_doc(full_ticket_doc())
        self.write_doc(intake_doc())
        self.write_releases(release_entry(), answer_entry())

        proc = self.run_cli("build", env_extra={"VERSION": "0.4.0"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.count("\n"), 1, proc.stdout)
        att_rel, missing_str = proc.stdout.rstrip("\n").split("\t")
        self.assertEqual(att_rel, ".crew/provenance/0.4.0.intoto.json")
        self.assertEqual(missing_str, "0")
        self.assertEqual(proc.stderr, "")

        stmt = json.loads((self.root / att_rel).read_text(encoding="utf-8"))
        self.assertEqual(stmt["_type"], STATEMENT_TYPE)
        self.assertEqual(stmt["predicateType"], PREDICATE_TYPE)
        self.assertEqual(
            stmt["subject"],
            [{
                "name": f"registry.local/service@sha256:{IMG_DIGEST_UPPER}",
                "digest": {"sha256": IMG_DIGEST},
            }],
        )

        pred = stmt["predicate"]
        self.assertEqual(pred["missing"], [])
        self.assertEqual(
            pred["ticket"],
            {
                "id": "0001",
                "state": "held",
                "spec_sha256": SPEC_SHA,
                "spec_title": "Enforce decision record ADR-0002 in checkout",
            },
        )
        self.assertEqual(pred["intake"]["id"], "0010")
        self.assertEqual(pred["intake"]["by"], "alice")
        self.assertEqual(pred["intake"]["confirmed_by"], "alice")

        # Both changed ADR (0001 in task.files) and cited ADR (0002 in spec.title) hashed
        want_adrs = [
            {
                "path": "docs/adr/0001-storage-layout.md",
                "sha256": hashlib.sha256(self.adr1.read_bytes()).hexdigest(),
            },
            {
                "path": "docs/adr/0002-checkout-rules.md",
                "sha256": hashlib.sha256(self.adr2.read_bytes()).hexdigest(),
            },
        ]
        self.assertEqual(pred["adrs"], want_adrs)

        # Agents: product + 2 task attempts + review = 4 runs
        self.assertEqual(len(pred["agents"]), 4)
        roles = [a["role"] for a in pred["agents"]]
        self.assertEqual(roles, ["product", "dev-worker", "dev-worker", "test-reviewer"])
        self.assertEqual([a["usd"] for a in pred["agents"]], [0.0012, 0.0095, 0.0058, 0.0021])
        for agent in pred["agents"]:
            for key in ("role", "harness", "provider", "model", "thinking", "turns",
                        "input_sha256", "tokens", "usd"):
                self.assertIn(key, agent)
                self.assertIsNotNone(agent[key], key)

        # Gates: all 8 PR gates and durations
        gates = pred["gates"]
        self.assertEqual(gates["verdict"], "GO")
        self.assertEqual(len(gates["checks"]), 8)
        self.assertEqual(
            [(c["gate"], c["verdict"], c["duration_s"]) for c in gates["checks"]],
            [
                ("risk", "GO", 0),
                ("secrets", "GO", 1),
                ("test-weakening", "GO", 0),
                ("deps", "GO", 2),
                ("sast", "GO", 3),
                ("test", "GO", 4),
                ("mutation", "GO", 11),
                ("test-proof", "GO", 2),
            ],
        )

        # Merge & human gate
        self.assertEqual(pred["merge"]["sha"], MERGE_SHA)
        self.assertEqual(pred["merge"]["pr"]["number"], 7)
        self.assertEqual(pred["merge"]["risk_level"], "medium")
        self.assertEqual(
            pred["human_gate"],
            {
                "gate": "go-for-launch",
                "answer": "GO",
                "by": "nicola",
                "via": "console",
                "at": "2026-11-24T11:30:00Z",
                "brief_sha256": BRIEF_SHA,
                "reason": "canary clean",
            },
        )


class SubjectNormalizationTests(ProvenanceTestCase):
    def test_ticket_merge_without_image_normalizes_git_commit(self):
        doc = full_ticket_doc()
        del doc["release"]
        doc["state"] = "merged"
        doc["merge"]["sha"] = "ABCDEF0123" * 4
        self.write_doc(doc)

        proc = self.run_cli("build", env_extra={"TICKET": "0001"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        att_rel, _ = proc.stdout.rstrip("\n").split("\t")
        self.assertEqual(att_rel, ".crew/provenance/0001.intoto.json")
        stmt = json.loads((self.root / att_rel).read_text(encoding="utf-8"))
        self.assertEqual(
            stmt["subject"],
            [{"name": "abcdef0123" * 4, "digest": {"gitCommit": "abcdef0123" * 4}}],
        )

    def test_non_hex_image_falls_back_to_git_commit_digest(self):
        doc = full_ticket_doc()
        doc["release"]["image"] = "registry.local/service:0.4.0"
        self.write_doc(doc)
        self.write_doc(intake_doc())
        self.write_releases(release_entry(image="registry.local/service:0.4.0"), answer_entry())

        proc = self.run_cli("build", env_extra={"VERSION": "0.4.0"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        stmt = json.loads((self.root / ".crew/provenance/0.4.0.intoto.json").read_text(encoding="utf-8"))
        self.assertEqual(stmt["subject"], [{"name": MERGE_SHA, "digest": {"gitCommit": MERGE_SHA}}])

    def test_git_repo_only_build_binds_head_commit_and_changed_adr(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("init\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "docs/adr/0001-storage-layout.md"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "Add storage decision"], cwd=self.root, check=True)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True, check=True
        ).stdout.strip()

        proc = self.run_cli("build", env_extra={"STATE_STORE": None})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        att_rel, _ = proc.stdout.rstrip("\n").split("\t")
        self.assertEqual(att_rel, f".crew/provenance/{head}.intoto.json")
        stmt = json.loads((self.root / att_rel).read_text(encoding="utf-8"))
        self.assertEqual(stmt["subject"], [{"name": head, "digest": {"gitCommit": head}}])
        self.assertEqual(
            stmt["predicate"]["adrs"],
            [{
                "path": "docs/adr/0001-storage-layout.md",
                "sha256": hashlib.sha256(self.adr1.read_bytes()).hexdigest(),
            }],
        )

    def test_refuses_when_no_valid_digest_or_malformed_subject_id(self):
        doc = full_ticket_doc()
        del doc["release"]
        doc["merge"]["sha"] = "not-a-hex-sha"
        doc["ci"]["head_sha"] = "short"
        doc["pr"]["head_sha"] = "short"
        self.write_doc(doc)

        proc = self.run_cli("build", env_extra={"TICKET": "0001"})
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(len(proc.stdout.splitlines()), 1)
        self.assertFalse((self.root / ".crew" / "provenance").exists())

        for bad in ("../escape", ".hidden", "a/b"):
            with self.subTest(bad=bad):
                p = self.run_cli("build", env_extra={"VERSION": bad})
                self.assertEqual(p.returncode, 1)
                self.assertFalse((self.root / ".crew" / "provenance").exists())


class MissingDataAndSecretHygieneTests(ProvenanceTestCase):
    def test_never_fabricates_missing_fields_and_reports_exact_missing_count(self):
        doc = full_ticket_doc()
        doc["intake"] = None
        doc["product"] = {"role": "product", "consulted": False}
        doc["tasks"] = [{
            "attempt": 1,
            "role": "dev-worker",
            "harness": "replay",
            "provider": "unpriced-provider",
            "model": "unpriced-worker",
            "thinking": None,
            "turns": None,
            "input_sha256": None,
            "tokens": None,
        }]
        doc["review"] = None
        doc["spend"] = []
        doc["ci"]["seconds"] = None
        doc["spec"]["title"] = "Cites missing ADR-9999"
        del doc["release"]
        self.write_doc(doc)

        proc = self.run_cli("build", env_extra={"TICKET": "0001"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        att_rel, count_str = proc.stdout.rstrip("\n").split("\t")
        stmt = json.loads((self.root / att_rel).read_text(encoding="utf-8"))
        pred = stmt["predicate"]

        self.assertIsNone(pred["intake"])
        self.assertEqual(len(pred["agents"]), 1)
        self.assertEqual(pred["agents"][0]["role"], "dev-worker")
        self.assertIsNone(pred["agents"][0]["usd"])
        self.assertIsNone(pred["agents"][0]["tokens"])
        self.assertIsNone(pred["agents"][0]["input_sha256"])
        self.assertIsNone(pred["gates"]["durations_s"])
        self.assertTrue(all(c["duration_s"] is None for c in pred["gates"]["checks"]))
        self.assertIsNone(pred["human_gate"])

        self.assertEqual(int(count_str), len(pred["missing"]))
        for expected_gap in ("adrs:ADR-9999", "agents[0].usd", "gates.durations_s", "human_gate"):
            self.assertIn(expected_gap, pred["missing"])
            self.assertIn(f"missing: {expected_gap}", proc.stderr)

    def test_scrubs_secrets_and_never_emits_secret_env(self):
        tok_val = "tok" + ("1" * 14)
        pw_val = "pass" + ("2" * 14)
        sec_val = "sec" + ("3" * 14)
        jwt_val = "jwt" + ("4" * 14)
        env_sec_val = "env" + ("5" * 14)
        env_tok_val = "env" + ("6" * 14)

        doc = full_ticket_doc()
        doc["spec"]["title"] = "Fix webhook AUTH_" + "TOKEN=" + tok_val
        doc["pr"]["url"] = f"https://builder:{pw_val}@host/pr/7"
        doc["ci"]["lines"] = list(EIGHT_GATE_LINES) + ["GO: custom — VENDOR_" + "SECRET=" + sec_val]
        self.write_doc(doc)
        self.write_doc(intake_doc())
        self.write_releases(
            release_entry(),
            answer_entry(reason="Approved with Authorization: Bearer " + jwt_val),
        )

        proc = self.run_cli(
            "build",
            env_extra={
                "VERSION": "0.4.0",
                "SIGNING_" + "SECRET": env_sec_val,
                "WEBHOOK_" + "TOKEN": env_tok_val,
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        raw = (self.root / ".crew/provenance/0.4.0.intoto.json").read_text(encoding="utf-8")
        for leaked in (tok_val, pw_val, sec_val, jwt_val, env_sec_val, env_tok_val):
            self.assertNotIn(leaked, raw)
        self.assertIn("[REDACTED]", raw)


class VerifyAndTamperTests(ProvenanceTestCase):
    def setUp(self):
        super().setUp()
        self.write_doc(full_ticket_doc())
        self.write_doc(intake_doc())
        self.write_releases(release_entry(), answer_entry())
        proc = self.run_cli("build", env_extra={"VERSION": "0.4.0"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.att = self.root / ".crew" / "provenance" / "0.4.0.intoto.json"

    def test_verify_accepts_valid_statement_and_repo_adrs(self):
        proc = self.run_cli("verify", "--statement", str(self.att), "--repo", str(self.root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith("OK"), proc.stdout)

    def test_verify_detects_single_byte_adr_tampering_and_deletion(self):
        original = self.adr1.read_bytes()
        self.adr1.write_bytes(original + b" ")
        proc = self.run_cli("verify", "--statement", str(self.att), "--repo", str(self.root))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("0001-storage-layout.md", proc.stdout)

        self.adr1.unlink()
        proc_missing = self.run_cli("verify", "--statement", str(self.att), "--repo", str(self.root))
        self.assertEqual(proc_missing.returncode, 1)
        self.assertIn("0001-storage-layout.md", proc_missing.stdout)

    def test_verify_rejects_malformed_envelope_digests_traversal_and_corrupt_bundle(self):
        valid = json.loads(self.att.read_text(encoding="utf-8"))
        mutations = []

        m1 = copy.deepcopy(valid)
        m1["_type"] = "https://in-toto.io/Statement/v0.1"
        mutations.append(("wrong _type", m1))

        m2 = copy.deepcopy(valid)
        m2["predicateType"] = "https://example.invalid/other/v1"
        mutations.append(("wrong predicateType", m2))

        m3 = copy.deepcopy(valid)
        m3["subject"] = []
        mutations.append(("empty subject", m3))

        m4 = copy.deepcopy(valid)
        m4["subject"][0]["digest"] = {"sha256": "A" * 64}
        mutations.append(("uppercase sha256", m4))

        m5 = copy.deepcopy(valid)
        m5["subject"][0]["digest"] = {"gitCommit": "abc123"}
        mutations.append(("short gitCommit", m5))

        m6 = copy.deepcopy(valid)
        m6["predicate"]["adrs"] = [{"path": "../outside.md", "sha256": "a" * 64}]
        mutations.append(("adr path traversal", m6))

        m7 = copy.deepcopy(valid)
        del m7["predicate"]["gates"]
        mutations.append(("missing predicate key", m7))

        bad_file = self.root / "bad.intoto.json"
        for label, broken in mutations:
            with self.subTest(label=label):
                bad_file.write_text(json.dumps(broken), encoding="utf-8")
                proc = self.run_cli("verify", "--statement", str(bad_file), "--repo", str(self.root))
                self.assertEqual(proc.returncode, 1, f"{label} unexpectedly passed: {proc.stdout}")

        bad_bundle = self.root / "corrupt.bundle"
        bad_bundle.write_text("{not-json", encoding="utf-8")
        proc_bundle = self.run_cli("verify", "--statement", str(self.att), "--bundle", str(bad_bundle))
        self.assertEqual(proc_bundle.returncode, 1)


if __name__ == "__main__":
    unittest.main()
