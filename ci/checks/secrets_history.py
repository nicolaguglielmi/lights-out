# SPDX-License-Identifier: Apache-2.0
"""ci-secrets-history: prove attached-repo history secrets rules (docs/adr/0049 §4).

Runs real gitleaks via ci/secrets.sh and core/history-findings.sh on a
throwaway git repository:
  (a) a new leak in the range is NO-GO;
  (b) a finding excused only at HEAD (in .lights-out/.gitleaksignore or root
      .gitleaksignore) is still NO-GO;
  (c) a finding excused at the base is GO, with a stderr line saying
      "excused by base";
  (d) core/history-findings.sh lists the history finding on stderr and drafts
      no ignore file.

The synthetic key material is assembled at runtime so this source holds none.
Run by ci/secrets-history.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _synthetic_rsa_key() -> str:
    # Assembled dynamically so gitleaks does not match a literal key header in source.
    hdr = "-" * 5 + "BEGIN RSA " + "PRIVATE KEY" + "-" * 5
    ftr = "-" * 5 + "END RSA " + "PRIVATE KEY" + "-" * 5
    chunk = "MIIEowIBAAKCAQEA" * 6
    return f"{hdr}\n{chunk}\n{chunk}\n{ftr}\n"


def _git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "ci",
        "GIT_AUTHOR_EMAIL": "ci@localhost",
        "GIT_COMMITTER_NAME": "ci",
        "GIT_COMMITTER_EMAIL": "ci@localhost",
    }


def _commit(repo: Path, message: str, env: dict[str, str]) -> str:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], env=env, stdin=subprocess.DEVNULL, capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-q", "-m", message],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    )
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def judge(root: Path | None = None) -> tuple[str, bool]:
    """Run all four attached-history gitleaks cases; return (verdict_text, ok)."""
    repo_root = Path(root) if root is not None else Path.cwd()
    env = _git_env()

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        repo = t / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "-C", str(repo), "init", "-q", "-b", "main"],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
        )
        (repo / "app.py").write_text('print("hello")\n', encoding="utf-8")
        c1 = _commit(repo, "clean", env)

        (repo / "id_rsa").write_text(_synthetic_rsa_key(), encoding="utf-8")
        c2 = _commit(repo, "a leaked key", env)

        def run_gate(base_sha: str, head_sha: str) -> tuple[str, str]:
            gate_env = {**env, "WORKLOAD_ATTACHED": "1", "WORKLOAD": str(repo), "BASE": base_sha, "HEAD": head_sha}
            proc = subprocess.run(
                ["sh", "ci/secrets.sh"],
                cwd=repo_root,
                env=gate_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
            return proc.stdout.strip(), proc.stderr

        def fail_case(case: str, detail: str) -> tuple[str, bool]:
            print(f"ci-secrets-history: {case}: {detail}", file=sys.stderr)
            return f"case {case} disagrees with docs/adr/0049 §4", False

        line_a, _ = run_gate(c1, c2)
        if not line_a.startswith("NO-GO: secrets — "):
            return fail_case("a-new-leak", f"a new leak in the range gave '{line_a}', want NO-GO")

        found_json = t / "found.json"
        subprocess.run(
            [
                "gitleaks",
                "git",
                "-c",
                "ci/gitleaks-attached.toml",
                "--ignore-gitleaks-allow",
                "-i",
                str(t),
                f"--log-opts={c2}",
                "--no-banner",
                "--no-color",
                "--redact",
                "--report-format",
                "json",
                "--report-path",
                str(found_json),
                str(repo / ".git"),
            ],
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        fp = ""
        if found_json.is_file() and found_json.stat().st_size > 0:
            items = json.loads(found_json.read_text(encoding="utf-8"))
            if items and isinstance(items, list):
                fp = str(items[0].get("Fingerprint", ""))
        if not fp:
            return fail_case("a-fingerprint", "gitleaks found nothing to fingerprint in the fake key")

        (repo / ".lights-out").mkdir()
        (repo / ".lights-out" / ".gitleaksignore").write_text(f"{fp}\n", encoding="utf-8")
        (repo / ".gitleaksignore").write_text(f"{fp}\n", encoding="utf-8")
        c3 = _commit(repo, "excuse it, at HEAD only", env)

        line_b, _ = run_gate(c2, c3)
        if not line_b.startswith("NO-GO: secrets — "):
            return fail_case("b-head-only", f"a finding excused only at HEAD gave '{line_b}', want NO-GO")

        with (repo / "app.py").open("a", encoding="utf-8") as fh:
            fh.write('print("bye")\n')
        c4 = _commit(repo, "a clean change on a base that excuses it", env)

        line_c, err_c = run_gate(c3, c4)
        if line_c != "GO: secrets":
            return fail_case("c-base", f"a finding excused at the base gave '{line_c}', want GO: secrets")
        expected_prefix = f"secrets: {fp} excused by base "
        if not any(ln.startswith(expected_prefix) for ln in err_c.splitlines()):
            return fail_case("c-stderr", f"no 'excused by base' line on stderr for {fp}")

        # (d) init's history-findings listing must report fp on stderr and draft no .gitleaksignore
        draft = t / "draft"
        draft.mkdir()
        hf_env = {**env, "WORKLOAD": str(repo), "WORKLOAD_CONTRACT": str(draft)}
        hf = subprocess.run(
            ["sh", "core/history-findings.sh"],
            cwd=repo_root,
            env=hf_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if fp not in hf.stderr:
            return fail_case("d-init-lists", f"core/history-findings.sh did not list {fp} on stderr")
        if (draft / ".gitleaksignore").exists():
            return fail_case("d-init-drafts", "an ignore file was drafted")

    return "", True


def main() -> int:
    text, ok = judge()
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
