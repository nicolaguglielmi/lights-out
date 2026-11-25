# SPDX-License-Identifier: Apache-2.0
"""ci-secrets: no committed or uncommitted secret.

Covers both modes (docs/adr/0025, 0037 §3, 0042 §4, 0049 §4, 0083 §6):
  1. Attached repository range mode (WORKLOAD_ATTACHED, BASE, HEAD all set):
     scans the attached repository's git directory reachable from HEAD using
     ci/gitleaks-attached.toml and --ignore-gitleaks-allow, honoring only
     fingerprints excused in BASE's .lights-out/.gitleaksignore (never HEAD's).
  2. Default checkout mode:
     verifies the five local allowlisted paths (.crew/cosign.key, .env,
     .attached, .crew/cache, .crew/logbook.json) are untracked and gitignored,
     scans HEAD git history with default gitleaks rules (no allowlist), and
     scans the working tree with ci/gitleaks-worktree.toml.

Run by ci/secrets.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXEMPT_PATHS = (
    ".crew/cosign.key",
    ".env",
    ".attached",
    ".crew/cache",
    ".crew/logbook.json",
)


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)


def _forward_to_stderr(proc: subprocess.CompletedProcess[str]) -> None:
    if proc.stdout:
        sys.stderr.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    sys.stderr.flush()


def _require_range(workload: Path, base_ref: str, head_ref: str) -> tuple[str, str] | tuple[None, str]:
    cat = _run(["git", "-C", str(workload), "cat-file", "-t", base_ref])
    if cat.returncode == 0 and cat.stdout.strip() == "tree":
        base = base_ref
    else:
        mb = _run(["git", "-C", str(workload), "merge-base", base_ref, head_ref])
        if mb.returncode != 0:
            detail = (mb.stdout or mb.stderr).strip()
            msg = f"git merge-base {base_ref} {head_ref} failed"
            if detail:
                msg = f"{msg}\n{detail}"
            return None, msg
        base = mb.stdout.strip()
    hp = _run(["git", "-C", str(workload), "rev-parse", "-q", "--verify", f"{head_ref}^{{commit}}"])
    if hp.returncode != 0:
        return None, f"HEAD={head_ref} names no commit"
    return base, hp.stdout.strip()


def _load_fingerprints(report_path: Path) -> list[str]:
    if not report_path.is_file() or report_path.stat().st_size == 0:
        return []
    try:
        data = json.loads(report_path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item["Fingerprint"]) for item in data if isinstance(item, dict) and item.get("Fingerprint")]


def _judge_attached(repo_root: Path, env: dict[str, str]) -> tuple[str, bool]:
    workload = Path(env.get("WORKLOAD") or ".")
    base_ref = env["BASE"]
    head_ref = env["HEAD"]
    base, head_or_err = _require_range(workload, base_ref, head_ref)
    if base is None:
        return head_or_err, False
    head = head_or_err

    git_dir_proc = _run(["git", "-C", str(workload), "rev-parse", "--absolute-git-dir"])
    if git_dir_proc.returncode != 0:
        return f"{workload} is not a git repository", False
    git_dir = git_dir_proc.stdout.strip()
    attached_cfg = str(repo_root / "ci" / "gitleaks-attached.toml")

    with tempfile.TemporaryDirectory() as tmp:
        scan_tmp = Path(tmp)
        none_dir = scan_tmp / "none"
        base_dir = scan_tmp / "base"
        none_dir.mkdir()
        base_dir.mkdir()

        base_type = _run(["git", "-C", str(workload), "cat-file", "-t", base])
        if base_type.returncode == 0 and base_type.stdout.strip() == "commit":
            spec = f"{base}:./.lights-out/.gitleaksignore"
            exists = _run(["git", "-C", str(workload), "cat-file", "-e", spec])
            if exists.returncode == 0:
                show = _run(["git", "-C", str(workload), "show", spec])
                if show.returncode == 0:
                    (base_dir / ".gitleaksignore").write_text(show.stdout, encoding="utf-8")

        def run_scan(ignore_dir: Path, report_path: Path) -> int:
            proc = _run(
                [
                    "gitleaks",
                    "git",
                    "-c",
                    attached_cfg,
                    "--ignore-gitleaks-allow",
                    "-i",
                    str(ignore_dir),
                    f"--log-opts={head}",
                    "--no-banner",
                    "--no-color",
                    "--redact",
                    "--report-format",
                    "json",
                    "--report-path",
                    str(report_path),
                    git_dir,
                ],
                cwd=repo_root,
            )
            _forward_to_stderr(proc)
            return proc.returncode

        judged_report = scan_tmp / "judged.json"
        rc = run_scan(base_dir, judged_report)
        ignore_file = base_dir / ".gitleaksignore"
        if ignore_file.is_file() and ignore_file.stat().st_size > 0:
            all_report = scan_tmp / "all.json"
            run_scan(none_dir, all_report)
            kept = set(_load_fingerprints(judged_report))
            for fp in _load_fingerprints(all_report):
                if fp not in kept:
                    print(f"secrets: {fp} excused by base {base[:7]} (.lights-out/.gitleaksignore)", file=sys.stderr)

        if rc != 0:
            return (
                "gitleaks found a secret in the attached repository's history "
                "that the base's .lights-out/.gitleaksignore does not excuse",
                False,
            )
    return "", True


def judge(root: Path | None = None, env: dict[str, str] | None = None) -> tuple[str, bool]:
    """Run secrets scan in attached-range or repo-checkout mode; return (verdict_text, ok)."""
    repo_root = Path(root) if root is not None else Path.cwd()
    cur_env = dict(os.environ) if env is None else env

    if cur_env.get("WORKLOAD_ATTACHED") and cur_env.get("BASE") and cur_env.get("HEAD"):
        return _judge_attached(repo_root, cur_env)

    for exempt in EXEMPT_PATHS:
        tracked = _run(["git", "ls-files", "--error-unmatch", "--", exempt], cwd=repo_root)
        if tracked.returncode == 0:
            return (
                f"{exempt} is tracked or staged; a real secret must never be committed — "
                "fix .gitignore and unstage it (git rm --cached)",
                False,
            )
        ignored = _run(["git", "check-ignore", "-q", "--", exempt], cwd=repo_root)
        if ignored.returncode != 0:
            return (
                f".gitignore does not ignore {exempt}; fix .gitignore before "
                "ci/gitleaks-worktree.toml's allowlist can be trusted",
                False,
            )

    head_ok = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).returncode == 0
    if head_ok:
        hist = _run(["gitleaks", "git", "--log-opts=HEAD", "--no-banner", "--no-color", "--redact", "."], cwd=repo_root)
        _forward_to_stderr(hist)
        if hist.returncode != 0:
            return "gitleaks found a secret in git history", False
    else:
        print("secrets: empty repository, no commits yet; scanning the working tree only", file=sys.stderr)

    wt_cfg = str(repo_root / "ci" / "gitleaks-worktree.toml")
    wt = _run(
        ["gitleaks", "dir", "-c", wt_cfg, "--no-banner", "--no-color", "--redact", "."],
        cwd=repo_root,
    )
    _forward_to_stderr(wt)
    if wt.returncode != 0:
        return "gitleaks found a secret in the working tree", False

    return "", True


def main() -> int:
    text, ok = judge()
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
