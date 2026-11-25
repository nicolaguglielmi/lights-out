# SPDX-License-Identifier: Apache-2.0
"""ci-map: proves docs/adr/0054 end to end, cold.

No engine, no network; temp git repositories and the real `make map`,
core/map.py, and the file Tracker:
  (a) `make map` builds this repository at HEAD and says so (§2).
  (b) `make map` builds workloads/minimal as an attached repository of
      its own: its tickets are .lights-out/tickets (§1, ADR 0037 §2).
  (c) a map built at one commit is refused at another, naming both (§2).
  (d) after a merge that changes a path a page cites, after-merge builds
      at the merge and files one docs ticket whose perimeter is that
      page; a re-run files nothing new (§4).
  (e) a merge that changes no cited path files nothing (§4).

Run by ci/map.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_GIT_ENV_STRIP = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
)
_MAKE_ENV_STRIP = ("WORKLOAD", "MAKEFLAGS", "MFLAGS", "MAKELEVEL")


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    for key in _GIT_ENV_STRIP:
        env.pop(key, None)
    env.update(
        {
            "GIT_AUTHOR_NAME": "ci",
            "GIT_AUTHOR_EMAIL": "ci@localhost",
            "GIT_COMMITTER_NAME": "ci",
            "GIT_COMMITTER_EMAIL": "ci@localhost",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if extra:
        env.update(extra)
    return env


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)


def _git_commit(repo: Path, env: dict[str, str], *args: str) -> str:
    proc = _run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-q", *args], cwd=repo, env=env)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or f"git commit failed ({proc.returncode})")
    sha = _run(["git", "-C", str(repo), "rev-parse", "HEAD"], cwd=repo, env=env)
    if sha.returncode != 0:
        raise RuntimeError(sha.stderr or "git rev-parse HEAD failed")
    return sha.stdout.strip()


def _make_map(repo_root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    mk_env = dict(env)
    for key in _MAKE_ENV_STRIP:
        mk_env.pop(key, None)
    return _run(["make", "--no-print-directory", "map", *args], cwd=repo_root, env=mk_env)


def _extract_perimeter(ticket_text: str) -> list[str]:
    lines: list[str] = []
    in_section = False
    for raw in ticket_text.splitlines():
        if raw.startswith("## Perimeter"):
            in_section = True
            continue
        if in_section and raw.startswith("## "):
            break
        if in_section and raw.startswith("- "):
            lines.append(raw)
    return lines


def judge(root: Path | None = None) -> tuple[str, bool]:
    """Run all five end-to-end map checks; return (verdict_text, ok)."""
    repo_root = Path(root) if root is not None else Path.cwd()
    env = _clean_env()

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)

        # (a) make map on this repository
        head_proc = _run(["git", "rev-parse", "HEAD"], cwd=repo_root, env=env)
        if head_proc.returncode != 0:
            return "git rev-parse HEAD failed", False
        head = head_proc.stdout.strip()
        self_db = t / "self.sqlite"
        proc = _make_map(repo_root, env, f"MAP_FILE={self_db}")
        combined = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            return f"make map on this repository failed\n{combined}", False
        line = proc.stdout.strip()
        if not (line.startswith(f"GO: map — {head} ") and line.endswith(f" → {self_db}")):
            return f"make map on this repository: {combined}", False

        # (b) make map on workloads/minimal as an attached repository
        minimal = t / "minimal"
        shutil.copytree(repo_root / "workloads" / "minimal", minimal)
        _run(["git", "-C", str(minimal), "init", "-q", "-b", "main"], cwd=repo_root, env=env)
        _run(["git", "-C", str(minimal), "add", "-A"], cwd=repo_root, env=env)
        min_head = _git_commit(minimal, env, "-m", "the user's repository")
        min_db = t / "minimal.sqlite"
        proc_b = _make_map(repo_root, env, f"ATTACHED={minimal}", f"MAP_FILE={min_db}")
        combined_b = (proc_b.stdout + proc_b.stderr).strip()
        if proc_b.returncode != 0:
            return f"make map on workloads/minimal failed\n{combined_b}", False
        line_b = proc_b.stdout.strip()
        if not (line_b.startswith(f"GO: map — {min_head} ") and "ticket=1" in line_b):
            return f"make map on workloads/minimal did not map its one ticket: {combined_b}", False

        # (c) stale map refused naming both commits and ./go map
        readme = minimal / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        later = _git_commit(minimal, env, "-am", "a later commit")
        slice_proc = _run(
            [
                sys.executable,
                "core/map.py",
                "slice",
                "--map",
                str(min_db),
                "--base",
                later,
                "--ticket",
                str(minimal / ".lights-out" / "tickets" / "0001.md"),
            ],
            cwd=repo_root,
            env=env,
        )
        if slice_proc.returncode == 0:
            return f"a map of an earlier commit was read at {later}", False
        err = slice_proc.stderr
        if not (f"built at {min_head}" in err and f"the base is {later}" in err and "./go map" in err):
            return f"a stale map was refused without naming both commits: {err.strip()}", False

        # (d) after-merge files one docs ticket whose perimeter is .lights-out/worker.md; re-run files nothing
        _run(["git", "-C", str(minimal), "checkout", "-q", "-b", "ticket/0001"], cwd=repo_root, env=env)
        sast_run = minimal / "tests" / "sast" / "run.sh"
        sast_run.write_text(sast_run.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
        _git_commit(minimal, env, "-am", "fix")
        _run(["git", "-C", str(minimal), "checkout", "-q", "main"], cwd=repo_root, env=env)
        merge_proc = _run(
            [
                "git",
                "-C",
                str(minimal),
                "-c",
                "commit.gpgsign=false",
                "merge",
                "-q",
                "--no-ff",
                "-m",
                "Merge pull request 'fix' (#1) from ticket/0001 into main",
                "ticket/0001",
            ],
            cwd=repo_root,
            env=env,
        )
        if merge_proc.returncode != 0:
            return f"git merge failed: {merge_proc.stderr.strip()}", False
        merge_sha = _run(["git", "-C", str(minimal), "rev-parse", "HEAD"], cwd=repo_root, env=env).stdout.strip()

        tickets_dir = t / "tickets"
        merged_db = t / "merged.sqlite"

        def run_after_merge(sha: str) -> subprocess.CompletedProcess[str]:
            am_env = {**env, "TRACKER": "file", "TICKETS_DIR": str(tickets_dir)}
            return _run(
                [
                    sys.executable,
                    "core/map.py",
                    "after-merge",
                    "--clone-url",
                    str(minimal),
                    "--merge-sha",
                    sha,
                    "--tickets",
                    ".lights-out/tickets",
                    "--targets",
                    "/dev/null",
                    "--out",
                    str(merged_db),
                ],
                cwd=repo_root,
                env=am_env,
            )

        am1 = run_after_merge(merge_sha)
        if am1.returncode != 0:
            return f"after-merge failed\n{(am1.stdout + am1.stderr).strip()}", False
        try:
            doc1 = json.loads(am1.stdout)
        except ValueError:
            return f"after-merge returned non-JSON: {am1.stdout.strip()}", False
        if doc1.get("map", {}).get("sha") != merge_sha or doc1.get("map", {}).get("docs_ticket") != "0001":
            return f"after-merge did not record the merge and docs ticket 0001: {am1.stdout.strip()}", False

        ticket_file = tickets_dir / "0001.md"
        ticket_lines = ticket_file.read_text(encoding="utf-8").splitlines()
        if f"- Origin: docs merge {merge_sha}" not in ticket_lines:
            return "the docs ticket carries no origin", False
        perimeter = _extract_perimeter(ticket_file.read_text(encoding="utf-8"))
        if perimeter != ["- `.lights-out/worker.md`"]:
            return f"the docs ticket's perimeter is not the page alone: {' '.join(perimeter)}", False

        am2 = run_after_merge(merge_sha)
        if am2.returncode != 0:
            return f"after-merge re-run failed\n{(am2.stdout + am2.stderr).strip()}", False
        ticket_files = sorted(p.name for p in tickets_dir.iterdir())
        if ticket_files != ["0001.md"]:
            return f"a re-run filed a second docs ticket: {' '.join(ticket_files)}", False

        # (e) merge changing no cited path files nothing
        (minimal / "NOTES.txt").write_text("notes\n", encoding="utf-8")
        _run(["git", "-C", str(minimal), "add", "NOTES.txt"], cwd=repo_root, env=env)
        uncited_sha = _git_commit(minimal, env, "-m", "uncited")
        am3 = run_after_merge(uncited_sha)
        if am3.returncode != 0:
            return f"after-merge on an uncited change failed\n{(am3.stdout + am3.stderr).strip()}", False
        doc3 = json.loads(am3.stdout)
        if doc3.get("map", {}).get("docs_ticket") is not None:
            return f"a change no page cites filed a docs ticket: {am3.stdout.strip()}", False

    return "", True


def main() -> int:
    text, ok = judge()
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
