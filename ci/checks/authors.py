# SPDX-License-Identifier: Apache-2.0
"""ci-authors: every commit reachable from HEAD is attributed to its account.

Implements docs/adr/0074 §5 (2026-11-12 amendment):
  - no author, committer, or Co-authored-by / Signed-off-by trailer address is
    noreply@anthropic.com;
  - no address at users.noreply.github.com lacks its numeric account id
    (<id>+<login>@users.noreply.github.com).
Runs a planted-history self-test (3 accepted, 6 refused) before returning GO.

Run by ci/authors.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ACCOUNT_ID_RE = re.compile(r"^[0-9]+\+")
_TRAILER_RE = re.compile(r"^(?:co-authored-by|signed-off-by):", re.IGNORECASE)
_OWN_EMAIL = "45848139+nicolaguglielmi@users.noreply.github.com"


def bad_email_reason(email: str) -> str:
    """Return the misattribution reason for EMAIL, or empty string if valid."""
    lower = email.strip().lower()
    if lower == "noreply@anthropic.com":
        return "an Anthropic address"
    if lower.endswith("@users.noreply.github.com"):
        local = lower.split("@", 1)[0]
        if not _ACCOUNT_ID_RE.match(local):
            return "a noreply address with no account id"
    return ""


def _extract_trailer_email(line: str) -> str:
    email = re.sub(r"^[^<]*<", "", line)
    return re.sub(r">.*$", "", email)


def scan_history(repo: Path) -> list[str]:
    """Return one formatted finding per misattributed author, committer, or trailer."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%x01%H%x09%ae%x09%ce%n%B", "HEAD"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git log failed (exit {proc.returncode})")

    findings: list[str] = []
    short_sha = ""
    for line in proc.stdout.splitlines():
        if line.startswith("\x01"):
            parts = line[1:].split("\t")
            full_sha = parts[0]
            author_email = parts[1] if len(parts) > 1 else ""
            committer_email = parts[2] if len(parts) > 2 else ""
            short_sha = full_sha[:7]
            reason = bad_email_reason(author_email)
            if reason:
                findings.append(f"{short_sha}: author {author_email}, {reason}")
            reason = bad_email_reason(committer_email)
            if reason:
                findings.append(f"{short_sha}: committer {committer_email}, {reason}")
            continue
        if _TRAILER_RE.match(line):
            trailer_email = _extract_trailer_email(line)
            reason = bad_email_reason(trailer_email)
            if reason:
                findings.append(f'{short_sha}: trailer "{line}", {reason}')
    return findings


def self_test() -> str | None:
    """Verify planted valid histories pass and planted misattributions fail."""
    with tempfile.TemporaryDirectory() as tmp:
        base_env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        }

        def plant(case: str, author_email: str, committer_email: str, message: str = "x") -> Path:
            repo = Path(tmp) / case
            subprocess.run(
                ["git", "init", "-q", str(repo)],
                env=base_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
            )
            env = {
                **base_env,
                "GIT_AUTHOR_NAME": "a",
                "GIT_AUTHOR_EMAIL": author_email,
                "GIT_COMMITTER_NAME": "c",
                "GIT_COMMITTER_EMAIL": committer_email,
            }
            subprocess.run(
                ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", message],
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
            )
            return repo

        ok_cases = {
            "ok-own": plant("ok-own", _OWN_EMAIL, _OWN_EMAIL, f"x\n\nSigned-off-by: N <{_OWN_EMAIL}>"),
            "ok-stranger": plant("ok-stranger", "dev@example.org", "noreply@github.com"),
            "ok-bot": plant("ok-bot", "49699333+dependabot[bot]@users.noreply.github.com", "noreply@github.com"),
        }
        bad_cases = {
            "bad-author": plant("bad-author", "noreply@anthropic.com", _OWN_EMAIL),
            "bad-committer": plant("bad-committer", _OWN_EMAIL, "Noreply@Anthropic.com"),
            "bad-coauthor": plant("bad-coauthor", _OWN_EMAIL, _OWN_EMAIL, "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"),
            "bad-signoff": plant("bad-signoff", _OWN_EMAIL, _OWN_EMAIL, "x\n\nSigned-off-by: Claude <noreply@anthropic.com>"),
            "bad-bare-login": plant("bad-bare-login", "nicola@users.noreply.github.com", _OWN_EMAIL),
            "bad-bare-coauthor": plant(
                "bad-bare-coauthor",
                _OWN_EMAIL,
                _OWN_EMAIL,
                "x\n\nCo-authored-by: N <nicola@users.noreply.github.com>",
            ),
        }

        for name, repo in ok_cases.items():
            got = scan_history(repo)
            if got:
                return f"the planted case {name} was refused: {got[0]}"
        for name, repo in bad_cases.items():
            if not scan_history(repo):
                return f"the planted case {name} was accepted"
    return None


def judge(root: Path | None = None) -> tuple[str, bool]:
    """Check git history attribution and run planted-history self-test."""
    repo_root = Path(root) if root is not None else Path.cwd()
    if not shutil.which("git"):
        return "git is not installed", False

    head_proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "-q", "--verify", "HEAD"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if head_proc.returncode != 0:
        return "no HEAD: run it in a clone", False

    shallow_proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--is-shallow-repository"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if shallow_proc.stdout.strip() != "false":
        return "a shallow clone: fetch the whole history (fetch-depth: 0)", False

    found = scan_history(repo_root)
    if found:
        reason = f"{len(found)} misattribution(s) in HEAD's history, first: {found[0]}"
        return "\n".join([reason, *found]), False

    st_err = self_test()
    if st_err is not None:
        return st_err, False

    count_proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-list", "--count", "HEAD"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    commit_count = count_proc.stdout.strip()
    return f"{commit_count} commits, each attributed to its own account", True


def main() -> int:
    text, ok = judge()
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
