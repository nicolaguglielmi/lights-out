# SPDX-License-Identifier: Apache-2.0
"""ci-corpus: verify the unknown-repository corpus (docs/adr/0106).

Drives core.corpus with --contract-rel=.lights-out, validates every positive
drafted repository against ci/workloads.sh and core.light, and runs a self-test
proving broken shapes and corrupted contracts fail NO-GO.

Run by ci/corpus.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from core import corpus as core_corpus

DEFAULT_WORKBENCH_IMAGE = "localhost/lights-out-workbench:test"


def verify_workloads(
    shape: core_corpus.Shape,
    repo_dir: Path,
    contract_dir: Path,
    _draft_info: dict,
    *,
    root: Path | None = None,
) -> str | None:
    """Validate a drafted repository contract using ci/workloads.sh."""
    repo_root = root or Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "WORKBENCH_IMAGE": os.environ.get("WORKBENCH_IMAGE") or DEFAULT_WORKBENCH_IMAGE,
        "HARNESS_BASE_IMAGE": os.environ.get("HARNESS_BASE_IMAGE") or DEFAULT_WORKBENCH_IMAGE,
        "WORKLOAD_CONTRACT": str(contract_dir),
    }
    proc = subprocess.run(
        ["sh", "ci/workloads.sh", str(repo_dir)],
        cwd=repo_root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    out = proc.stdout.strip()
    if proc.returncode != 0 or out != "GO: workloads":
        detail = out or proc.stderr.strip() or f"exit {proc.returncode}"
        return f"ci/workloads.sh refused {shape.name}: {detail}"
    return None


def self_test(root: Path | None = None) -> str | None:
    """Prove planted corpus and contract defects fail deterministically."""
    repo_root = root or Path(__file__).resolve().parents[2]
    stacks = repo_root / "stacks"
    shapes = core_corpus.default_shapes()

    go_shape = next(s for s in shapes if s.name == "go-http")
    broken_port = core_corpus.Shape(
        name="self-test-wrong-port",
        kind="positive",
        expected_stack=go_shape.expected_stack,
        expected_has_plane=True,
        expected_port=9999,
        expected_probe=go_shape.expected_probe,
        expected_lockfiles=go_shape.expected_lockfiles,
        expected_test_cmd_substr=go_shape.expected_test_cmd_substr,
        expected_toolchain=go_shape.expected_toolchain,
        files=go_shape.files,
    )
    passed, total, _ = core_corpus.run(
        contract_rel=".lights-out",
        stacks_dir=stacks,
        report_path=None,
        verify_light=False,
        shapes=[broken_port],
    )
    if (passed, total) != (0, 1):
        return "corpus accepted a shape with mismatched expected port"

    pinned_shape = next(s for s in shapes if s.name == "python-requirements")
    broken_neg = core_corpus.Shape(
        name="self-test-neg-succeeds",
        kind="negative",
        expected_error_tokens=("unpinned",),
        files=pinned_shape.files,
    )
    passed, total, _ = core_corpus.run(
        contract_rel=".lights-out",
        stacks_dir=stacks,
        report_path=None,
        verify_light=False,
        shapes=[broken_neg],
    )
    if (passed, total) != (0, 1):
        return "corpus accepted a negative shape that drafted cleanly"

    lib_shape = next(s for s in shapes if s.name == "python-uv-lib")

    def tamper_workload(shape: core_corpus.Shape, repo_dir: Path, contract_dir: Path, info: dict) -> str | None:
        env_path = contract_dir / "workload.env"
        env_path.write_text(env_path.read_text(encoding="utf-8") + "EXTRA_PLANTED_KEY=1\n", encoding="utf-8")
        return verify_workloads(shape, repo_dir, contract_dir, info, root=repo_root)

    passed, total, _ = core_corpus.run(
        contract_rel=".lights-out",
        stacks_dir=stacks,
        report_path=None,
        verify_light=False,
        on_draft=tamper_workload,
        shapes=[lib_shape],
    )
    if (passed, total) != (0, 1):
        return "ci/workloads.sh accepted a workload.env with an extra planted key"

    return None


def judge(
    root: Path | None = None,
    *,
    report_path: str | Path | None = None,
    full: bool | None = None,
) -> tuple[str, bool]:
    """Run corpus self-test and verification; return ``(verdict_text, ok)``."""
    repo_root = root or Path(__file__).resolve().parents[2]
    is_full = (
        full
        if full is not None
        else os.environ.get("CORPUS_FULL", "").strip().lower() in ("1", "true", "yes")
    )
    resolved_report: str | Path
    if report_path is not None:
        resolved_report = report_path
    else:
        env_report = os.environ.get("CORPUS_REPORT", "").strip()
        resolved_report = env_report if env_report else (repo_root / core_corpus.DEFAULT_REPORT)

    st_err = self_test(repo_root)
    if st_err is not None:
        return f"self-test failed: {st_err}", False

    passed, total, details = core_corpus.run(
        contract_rel=".lights-out",
        stacks_dir=repo_root / "stacks",
        report_path=resolved_report,
        verify_light=True,
        on_draft=lambda s, r, c, i: verify_workloads(s, r, c, i, root=repo_root),
        full=is_full,
    )
    if passed != total:
        failed = [item for item in details if item["status"] != "PASS"]
        names = ", ".join(item["name"] for item in failed)
        lines = [f"{len(failed)} of {total} repository shapes failed ({names})"]
        for item in failed:
            lines.append(f"{item['name']}: {'; '.join(item['errors'])}")
        return "\n".join(lines), False
    return f"{passed}/{total} repository shapes verified", True


def main() -> int:
    text, ok = judge()
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
