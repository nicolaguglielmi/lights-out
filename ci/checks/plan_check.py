# SPDX-License-Identifier: Apache-2.0
"""ci-plan: the plan job of the workflows (docs/adr/0065).

Two halves:
  1. The classifier (`sh .github/plan.sh classify`) prints `docs` when every
     changed path on stdin is documentation (§3), and `code` otherwise
     (including empty stdin).
  2. Every job of ci.yml, stranger.yml, self.yml, podman.yml, and package.yml
     except `plan`, `publish`, and package.yml's release jobs (dist, pypi,
     actions, release, wiki: docs/adr/0075 §1, 0077 §4) needs `plan` and runs
     only on its outputs (`run == 'true'`, plus `full == 'true'` except for
     ci.yml/ci and self.yml/gates).

Proves each half can fail on planted cases before judging the real repo.
Run by ci/plan.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

DOCS_PATHS = (
    "README.md",
    "ROADMAP.md",
    "docs/PLAN.md",
    "docs/adr/0065-one-proof-per-commit.md",
    "docs/launch/checklist.md",
    "site/index.html",
    "site/render.py",
    ".claude/skills/milestone/SKILL.md",
    "actions/test-proof/README.md",
    "console/README.md",
)
CODE_PATHS = (
    "Makefile",
    "ci/lib.sh",
    "core/map.py",
    "docs/assets/lights-out.jpg",
    "tickets/0001.md",
    "roles/dev-worker/prompt.md",
    "workloads/minimal/README.md",
    ".lights-out/worker.md",
    "fixtures/logbook/README.md",
    "console/index.html",
    "actions/test-proof/action.yml",
    ".github/workflows/ci.yml",
    "lights-out",
    "action.yml",
)

WORKFLOW_FILES = ("ci.yml", "stranger.yml", "self.yml", "podman.yml", "package.yml")
FULL_KEEP = {("ci.yml", "ci"), ("self.yml", "gates")}
RELEASE_JOBS = {("package.yml", n) for n in ("dist", "pypi", "actions", "release", "wiki")}

_PLANTED_WORKFLOW = """\
jobs:
  plan: {permissions: {actions: read, contents: read}, outputs: {run: x, full: y}}
  free: {if: "github.server_url == 'https://github.com'"}
  half: {needs: plan, if: "needs.plan.outputs.run == 'true'"}
  dist: {if: "github.server_url == 'https://github.com'"}
"""
_PLANTED_WANT = [
    "w.yml: job free does not need plan and run only when run is true",
    "w.yml: job half runs on a docs-only push",
    "w.yml: job dist does not need plan and run only when run is true",
]


def classify_paths(repo_root: Path, *paths: str) -> str:
    """Invoke `.github/plan.sh classify` with PATHS on stdin."""
    payload = ("\n".join(paths) + "\n") if paths else ""
    proc = subprocess.run(
        ["sh", ".github/plan.sh", "classify"],
        cwd=repo_root,
        input=payload,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def workflow_flaws(root: Path, names: list[str] | tuple[str, ...]) -> list[str]:
    """Yield gating defects across workflow files under ROOT/.github/workflows/."""
    findings: list[str] = []
    for fname in names:
        wf_path = root / ".github" / "workflows" / fname
        doc = yaml.safe_load(wf_path.read_text(encoding="utf-8")) or {}
        jobs = doc.get("jobs") or {}
        plan_job = jobs.get("plan")
        if not plan_job:
            findings.append(f"{fname}: no plan job")
            continue
        if set(plan_job.get("outputs") or {}) != {"run", "full"}:
            findings.append(f"{fname}: plan's outputs are not run and full")
        if plan_job.get("permissions") != {"actions": "read", "contents": "read"}:
            findings.append(
                f"{fname}: plan's permissions are {plan_job.get('permissions')}, "
                "want actions: read and contents: read"
            )
        for job_name, job in jobs.items():
            if job_name in ("plan", "publish") or (fname, job_name) in RELEASE_JOBS:
                continue
            needs = job.get("needs") or []
            needs_list = [needs] if isinstance(needs, str) else list(needs)
            cond = str(job.get("if", ""))
            if "plan" not in needs_list or "needs.plan.outputs.run == 'true'" not in cond:
                findings.append(f"{fname}: job {job_name} does not need plan and run only when run is true")
            elif (fname, job_name) not in FULL_KEEP and "needs.plan.outputs.full == 'true'" not in cond:
                findings.append(f"{fname}: job {job_name} runs on a docs-only push")
    return findings


def self_test(repo_root: Path) -> str | None:
    """Prove classifier cases and planted un-gated workflow defects are judged accurately."""
    for path in DOCS_PATHS:
        got = classify_paths(repo_root, path)
        if got != "docs":
            return f"{path} is documentation (docs/adr/0065 §3); the classifier says {got}"
    for path in CODE_PATHS:
        got = classify_paths(repo_root, path)
        if got != "code":
            return f"{path} is not documentation (docs/adr/0065 §3); the classifier says {got}"
    if classify_paths(repo_root, "README.md", "docs/PLAN.md") != "docs":
        return "two documentation paths are not docs"
    if classify_paths(repo_root, "README.md", "Makefile") != "code":
        return "a documentation path and the Makefile are not code"
    if classify_paths(repo_root) != "code":
        return "no paths at all is not code: an empty compare proves nothing"

    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe"
        wf_dir = probe / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "w.yml").write_text(_PLANTED_WORKFLOW, encoding="utf-8")
        got = workflow_flaws(probe, ("w.yml",))
        if not got:
            return "a job not gated on plan passed"
        if got != _PLANTED_WANT:
            return "\n".join(["the probe workflow did not give its three findings", *got])
    return None


def judge(root: Path | None = None) -> tuple[str, bool]:
    """Validate classifier and workflow gating under ROOT; return (verdict_text, ok)."""
    repo_root = Path(root) if root is not None else Path.cwd()
    if not (repo_root / ".github" / "plan.sh").is_file():
        return ".github/plan.sh is missing", False

    st_err = self_test(repo_root)
    if st_err is not None:
        return st_err, False

    found = workflow_flaws(repo_root, WORKFLOW_FILES)
    if found:
        return "\n".join([f"{len(found)} job(s) escape the plan job", *found]), False
    return "", True


def main() -> int:
    text, ok = judge()
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
