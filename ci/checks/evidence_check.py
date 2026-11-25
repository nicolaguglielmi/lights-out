# SPDX-License-Identifier: Apache-2.0
"""ci-evidence: proves core/evidence.sh, the evidence pack per release.

Cold check (no plane, no registry, no network), implementing docs/adr/0081
§1-§5, §7 and docs/adr/0089 §5:
  (a) rolled-back release 0.2.0: GO, signed pack, gaps, PR/rework/gate lines,
      top-level missing list completeness, and (a') no-logbook snapshot message.
  (b) cosign verify-blob passes; 1 appended byte fails verification.
  (c) held + answered release 0.3.0: answer packed, PR gap, verifies.
  (d) rebuilding 0.2.0 replaces a stale bundle; (d') with golden logbook,
      frozen console HTML is signed, verified, and tamper-rejected.
  (e) refusals (unknown version, unset VERSION, slash, leading dot, missing
      key writing unsigned pack) all return 1-line NO-GO and exit 1.
  (f) release log and state store remain unmodified.

Run by ci/evidence.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_STUB_STATE_GET = """\
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
[ "$1" = tickets ] && [ -f "$d/state/$2.json" ] || exit 1
cat "$d/state/$2.json"
"""

_STATE_0001 = json.dumps(
    {
        "id": "0001",
        "state": "rolled-back",
        "spec": {"sha256": "5pec", "title": "Add a reminder"},
        "pr": {"number": 7, "url": "http://host/pr/7", "head_sha": "h2", "base_sha": "b1"},
        "ci": {
            "verdict": "GO: gates",
            "failed": None,
            "risk_level": "low",
            "decision": "auto-merge",
            "head_sha": "h2",
            "line": "GO: gates",
            "details": [],
            "lines": ["GO: secrets", "GO: lint", "GO: gates"],
        },
        "rework": [
            {
                "attempt": 1,
                "judge": "gates",
                "head_sha": "h1",
                "line": "NO-GO: gates — tests: 1 failed",
                "details": ["tests: 1 failed"],
                "lines": ["GO: secrets", "NO-GO: gates — tests: 1 failed"],
                "at": "2026-11-12T10:00:00Z",
            }
        ],
        "merge": {"sha": "m1", "head_sha": "h2"},
        "release": {"version": "0.2.0", "verdict": "rolled-back", "image": "repo@sha256:aaa"},
    }
)

_RELEASES_JSONL = "\n".join(
    [
        json.dumps(
            {
                "kind": "release",
                "at": "2026-11-12T09:00:00Z",
                "ticket": "0001",
                "record": {
                    "version": "0.2.0",
                    "stable": "0.1.0",
                    "stages": [{"pct": 5, "verdict": "NO-GO", "numbers": ["slo: error_rate 0.3"]}],
                    "verdict": "rolled-back",
                    "held_at": None,
                    "image": "repo@sha256:old",
                    "sbom": None,
                    "dast": None,
                },
            }
        ),
        json.dumps(
            {
                "kind": "release",
                "at": "2026-11-12T11:00:00Z",
                "ticket": "0001",
                "record": {
                    "version": "0.2.0",
                    "stable": "0.1.0",
                    "stages": [{"pct": 5, "verdict": "NO-GO", "numbers": ["slo: error_rate 0.3"]}],
                    "verdict": "rolled-back",
                    "held_at": None,
                    "image": "repo@sha256:aaa",
                    "sbom": None,
                    "dast": None,
                },
            }
        ),
        json.dumps(
            {
                "kind": "release",
                "at": "2026-11-12T12:00:00Z",
                "ticket": None,
                "record": {
                    "version": "0.3.0",
                    "stable": "0.1.0",
                    "stages": [
                        {"pct": 5, "verdict": "GO", "numbers": ["slo: ok"]},
                        {"pct": 25, "verdict": "GO", "numbers": ["slo: ok"]},
                    ],
                    "verdict": "held",
                    "held_at": 25,
                    "image": "repo@sha256:bbb",
                    "sbom": "sha256:sbom",
                    "dast": None,
                },
            }
        ),
        json.dumps({"kind": "answer", "at": "2026-11-12T12:30:00Z", "version": "0.3.0", "answer": "GO", "by": "nicola", "via": "console"}),
    ]
) + "\n"


def _count_missing_leaves(node: object) -> int:
    if isinstance(node, dict):
        count = 1 if ("source" in node and "missing" in node) else 0
        return count + sum(_count_missing_leaves(v) for v in node.values())
    if isinstance(node, list):
        return sum(_count_missing_leaves(v) for v in node)
    return 0


def _verify_blob(pub_key: Path, target: Path) -> bool:
    proc = subprocess.run(
        [
            "cosign",
            "verify-blob",
            "--key",
            str(pub_key),
            "--insecure-ignore-tlog",
            "--bundle",
            f"{target}.bundle",
            str(target),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _one_line(stdout: str, label: str) -> str | None:
    if stdout.count("\n") != 1:
        return f"{label}: stdout is not exactly one line\n{stdout}"
    return None


def _run_evidence(root: Path, version: str | None, key_path: Path, logbook: str = "") -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("VERSION", None)
    env.pop("LOGBOOK_FILE", None)
    if version is not None:
        env["VERSION"] = version
    env.update(
        {
            "EVIDENCE_LOGBOOK": logbook,
            "RELEASES_LOG": ".crew/releases.jsonl",
            "STATE_STORE": "stub-state",
            "EVIDENCE_DIR": str(root / "out"),
            "COSIGN_KEY": str(key_path),
        }
    )
    return subprocess.run(
        ["sh", "core/evidence.sh"],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def judge(root: Path | None = None) -> tuple[str, bool]:
    """Run all evidence pack and frozen snapshot checks; return (verdict_text, ok)."""
    repo_root = Path(root) if root is not None else Path.cwd()
    if not (repo_root / "core" / "evidence.sh").is_file():
        return "core/evidence.sh not found", False
    if not shutil.which("cosign"):
        return "cosign not found: this check runs in the workbench", False

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for sub in ("adapters/stub-state", "state", ".crew", "keys"):
            (t / sub).mkdir(parents=True)
        for link in ("core", "ci", "console"):
            (t / link).symlink_to(repo_root / link)

        stub_get = t / "adapters" / "stub-state" / "get.sh"
        stub_get.write_text(_STUB_STATE_GET, encoding="utf-8")
        stub_get.chmod(0o755)
        (t / "state" / "0001.json").write_text(_STATE_0001, encoding="utf-8")
        (t / ".crew" / "releases.jsonl").write_text(_RELEASES_JSONL, encoding="utf-8")

        kg = subprocess.run(
            ["cosign", "generate-key-pair", "--output-key-prefix", "k"],
            cwd=t / "keys",
            env={**os.environ, "COSIGN_PASSWORD": ""},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if kg.returncode != 0:
            return "cosign could not generate a throwaway key pair", False
        key = t / "keys" / "k.key"
        pub = t / "keys" / "k.pub"

        # (a) 0.2.0 rolled back
        res_a = _run_evidence(t, "0.2.0", key)
        err = _one_line(res_a.stdout, "0.2.0")
        if err:
            return err, False
        out_a = res_a.stdout.strip()
        if not out_a.startswith("GO: evidence"):
            return f"0.2.0 is not a GO\n{out_a}\n{res_a.stderr.strip()}", False
        if res_a.returncode != 0:
            return f"0.2.0: GO but exit {res_a.returncode}", False

        pack_020 = t / "out" / "0.2.0.json"
        bundle_020 = t / "out" / "0.2.0.json.bundle"
        if not pack_020.is_file() or pack_020.stat().st_size == 0:
            return "0.2.0: no pack at EVIDENCE_DIR/0.2.0.json", False
        if not bundle_020.is_file() or bundle_020.stat().st_size == 0:
            return "0.2.0: no bundle beside the pack", False

        doc_a = json.loads(pack_020.read_text(encoding="utf-8"))
        ver_val = doc_a["version"]["value"] if isinstance(doc_a["version"], dict) else doc_a["version"]
        if doc_a.get("schema") != "lights-out.evidence/1" or ver_val != "0.2.0":
            return "0.2.0: schema", False
        img = doc_a["artefacts"]["image"]
        if img.get("value") != "repo@sha256:aaa" or not re.search(r":2", str(img.get("source", ""))):
            return "0.2.0: not the last release line", False
        earlier = doc_a["earlier_releases"]["value"] if isinstance(doc_a["earlier_releases"], dict) else doc_a["earlier_releases"]
        if earlier != [1]:
            return "0.2.0: the earlier release is not listed by line", False
        for art_key in ("sbom", "dast"):
            art = doc_a["artefacts"][art_key]
            if "missing" not in art or "value" in art:
                return "0.2.0: sbom and dast are not missing entries", False
        ans = doc_a["answer"]
        if "missing" not in ans or "rolled back" not in ans["missing"]:
            return "0.2.0: answer is not one missing entry saying rolled back", False
        prs = doc_a["prs"]
        if len(prs) != 1 or prs[0]["pr"]["number"]["value"] != 7 or prs[0]["merge"]["sha"]["value"] != "m1":
            return "0.2.0: the PR is not packed", False
        rework = prs[0]["rework"]["value"]
        if len(rework) != 1 or rework[0]["attempt"] != 1:
            return "0.2.0: the rework is not kept", False
        if len(prs[0]["gates"]["lines"]["value"]) != 3:
            return "0.2.0: the gate lines are not kept", False
        rev = prs[0]["review"]
        if "missing" not in rev or "auto-merge" not in rev["missing"]:
            return "0.2.0: the review is not missing with the decision named", False
        if "missing" not in doc_a["stages"][0]["started_at"]:
            return "0.2.0: the stage times are not missing", False
        missing_n = _count_missing_leaves(doc_a)
        if len(doc_a.get("missing", [])) != missing_n:
            return f"0.2.0: the top-level missing does not list every missing entry ({missing_n})", False

        # (a') no logbook message
        if not re.search(r"; no console snapshot: .+", out_a):
            return f"0.2.0 with no logbook: the line does not say there is no console snapshot\n{out_a}", False
        if (t / "out" / "0.2.0.console.html").exists():
            return "0.2.0 with no logbook: a console snapshot was written", False

        # (b) verify and tamper detection
        if not _verify_blob(pub, pack_020):
            return "0.2.0: the bundle does not verify with cosign verify-blob", False
        tampered = t / "tampered.json"
        tampered_bundle = t / "tampered.json.bundle"
        tampered.write_bytes(pack_020.read_bytes() + b" ")
        tampered_bundle.write_bytes(bundle_020.read_bytes())
        if _verify_blob(pub, tampered):
            return "a pack with one byte appended still verifies: the signature binds nothing", False

        # (c) 0.3.0 held and answered
        res_c = _run_evidence(t, "0.3.0", key)
        err = _one_line(res_c.stdout, "0.3.0")
        if err:
            return err, False
        if not res_c.stdout.strip().startswith("GO: evidence"):
            return f"0.3.0 is not a GO\n{res_c.stdout.strip()}\n{res_c.stderr.strip()}", False
        pack_030 = t / "out" / "0.3.0.json"
        doc_c = json.loads(pack_030.read_text(encoding="utf-8"))
        ans_c = doc_c["answer"]
        if (
            ans_c["answer"]["value"] != "GO"
            or ans_c["by"]["value"] != "nicola"
            or ans_c["via"]["value"] != "console"
        ):
            return "0.3.0: the answer is not packed", False
        if "missing" not in ans_c["reason"]:
            return "0.3.0: the answer's reason is not missing", False
        if doc_c["artefacts"]["sbom"].get("value") != "sha256:sbom":
            return "0.3.0: the sbom is not a value", False
        if "missing" not in doc_c["artefacts"]["dast"]:
            return "0.3.0: dast is not missing", False
        prs_c = doc_c["prs"][0] if isinstance(doc_c["prs"], list) else doc_c["prs"]
        if "missing" not in prs_c or "value" in prs_c:
            return "0.3.0: the PR is not one missing entry", False
        earlier_c = doc_c["earlier_releases"]["value"] if isinstance(doc_c["earlier_releases"], dict) else doc_c["earlier_releases"]
        if len(earlier_c) != 0:
            return "0.3.0: the release is not the held one", False
        if not _verify_blob(pub, pack_030):
            return "0.3.0: the bundle does not verify with cosign verify-blob", False

        # (d) stale bundle replaced on rebuild
        bundle_020.write_text("stale", encoding="utf-8")
        _run_evidence(t, "0.2.0", key)
        if not _verify_blob(pub, pack_020):
            return "0.2.0 rebuilt: the old bundle was not replaced by a new signature", False

        # (d') with golden logbook -> frozen console signed & verified & tamper rejected
        golden = str(repo_root / "fixtures" / "logbook" / "golden-1.5.json")
        res_s = _run_evidence(t, "0.2.0", key, logbook=golden)
        err = _one_line(res_s.stdout, "0.2.0 with a logbook")
        if err:
            return err, False
        out_s = res_s.stdout.strip()
        if not (out_s.startswith("GO: evidence") and out_s.endswith("; console snapshot signed")):
            return f"0.2.0 with a logbook: the line does not end in the signed snapshot\n{out_s}\n{res_s.stderr.strip()}", False
        snap = t / "out" / "0.2.0.console.html"
        snap_bundle = t / "out" / "0.2.0.console.html.bundle"
        if not (snap.is_file() and snap.stat().st_size > 0 and snap_bundle.is_file() and snap_bundle.stat().st_size > 0):
            return "0.2.0 with a logbook: no console snapshot and bundle beside the pack", False
        snap_text = snap.read_text(encoding="utf-8")
        if 'id="frozen-logbook"' not in snap_text:
            return "0.2.0 with a logbook: the snapshot does not inline the logbook", False
        if "evidence pack 0.2.0, logbook of " not in snap_text:
            return "0.2.0 with a logbook: the snapshot's label does not name the pack", False
        if not _verify_blob(pub, snap):
            return "0.2.0 with a logbook: the snapshot's bundle does not verify with cosign verify-blob", False
        tampered_html = t / "tampered.html"
        tampered_html_bundle = t / "tampered.html.bundle"
        tampered_html.write_bytes(snap.read_bytes() + b" ")
        tampered_html_bundle.write_bytes(snap_bundle.read_bytes())
        if _verify_blob(pub, tampered_html):
            return "a snapshot with one byte appended still verifies", False

        # (e) refusals
        def check_refusal(version: str | None, key_arg: Path, label: str) -> str | None:
            res = _run_evidence(t, version, key_arg)
            line_err = _one_line(res.stdout, label)
            if line_err:
                return line_err
            if not res.stdout.strip().startswith("NO-GO: evidence"):
                return f"{label}: not a NO-GO\n{res.stdout.strip()}"
            if res.returncode != 1:
                return f"{label}: NO-GO but exit {res.returncode}"
            return None

        for ver, desc in (("9.9.9", "an unknown version"), (None, "an unset VERSION"), ("../x", "a VERSION with a slash"), (".hidden", "a VERSION that starts with a dot")):
            ref_err = check_refusal(ver, key, desc)
            if ref_err:
                return ref_err, False

        if (t / "out" / "9.9.9.json").exists():
            return "an unknown version still wrote a pack", False

        pack_030.unlink(missing_ok=True)
        (t / "out" / "0.3.0.json.bundle").unlink(missing_ok=True)
        res_nokey = _run_evidence(t, "0.3.0", t / "keys" / "absent.key")
        line_err = _one_line(res_nokey.stdout, "a missing key")
        if line_err:
            return line_err, False
        if not res_nokey.stdout.strip().startswith("NO-GO: evidence") or res_nokey.returncode != 1:
            return f"a missing key: not a NO-GO\n{res_nokey.stdout.strip()}", False
        if not (pack_030.is_file() and pack_030.stat().st_size > 0):
            return "a missing key: the pack is not written to be read", False
        if (t / "out" / "0.3.0.json.bundle").exists():
            return "a missing key: a bundle sits beside an unsigned pack", False
        if "./go up" not in res_nokey.stdout:
            return f"a missing key: the line does not name ./go up\n{res_nokey.stdout.strip()}", False

        # (f) read-only proof
        if len((t / ".crew" / "releases.jsonl").read_text(encoding="utf-8").splitlines()) != 4:
            return "the release log was changed", False
        if (t / "state" / "0002.json").exists():
            return "the state store was written", False

    return "pack for a rolled-back and an answered release, and a frozen console, signed, verified, tamper rejected", True


def main() -> int:
    text, ok = judge()
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
