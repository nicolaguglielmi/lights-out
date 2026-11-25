# SPDX-License-Identifier: Apache-2.0
"""ci-prompts: roles/*/*.md prompts never name gates, gate tools, or targets.

Implements ticket 0004 / docs/adr/0020 §6 and docs/adr/0090 §3:
  - Self-checks HAND_TOOLS against gates/*.sh, ci/secrets.sh,
    ci/checks/secrets_check.py, stacks/*/runners/*, */workload.env, and
    workloads/*/.lights-out/workload.env so the list cannot silently drift.
  - Derives gate basenames dynamically from gates/*.sh (excluding `test`).
  - Derives Makefile targets dynamically from `make --no-print-directory help`.
  - Proves the matcher rejects a planted prompt naming "mutation" and accepts
    an allowed-text prompt before scanning roles/*/*.md.

Run by ci/prompts.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

HAND_TOOLS = (
    "osv-scanner",
    "gitleaks",
    "cosmic-ray",
    "bandit",
    "stryker",
    "eslint",
    "tests/mutation",
    "tests/sast",
)


def _hand_tool_sources(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    files.extend(sorted((repo_root / "gates").glob("*.sh")))
    for rel in ("ci/secrets.sh", "ci/checks/secrets_check.py"):
        p = repo_root / rel
        if p.is_file():
            files.append(p)
    files.extend(sorted(repo_root.glob("stacks/*/runners/*")))
    files.extend(sorted(repo_root.glob("*/workload.env")))
    files.extend(sorted(repo_root.glob("workloads/*/.lights-out/workload.env")))
    return [p for p in files if p.is_file()]


def find_tool_drift(repo_root: Path, tools: tuple[str, ...] = HAND_TOOLS) -> list[str]:
    """Return any tool in TOOLS no longer named by any gate, secret check, runner, or workload.env."""
    sources = _hand_tool_sources(repo_root)
    blobs = [p.read_text(encoding="utf-8", errors="replace") for p in sources]
    return [tool for tool in tools if not any(tool in blob for blob in blobs)]


def read_make_targets(repo_root: Path) -> list[str]:
    """Return targets listed by `make --no-print-directory help`."""
    env = dict(os.environ)
    for key in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL"):
        env.pop(key, None)
    proc = subprocess.run(
        ["make", "--no-print-directory", "help"],
        cwd=repo_root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stdout + proc.stderr).strip() or f"exit {proc.returncode}")
    targets: list[str] = []
    for line in proc.stdout.splitlines():
        if re.match(r"^  [a-z]", line):
            targets.append(line.split()[0])
    return targets


def build_rules(gate_basenames: list[str], targets: list[str], hand_tools: tuple[str, ...] = HAND_TOOLS) -> list[tuple[str, str, bool]]:
    """Build (label, literal, slash_exempt) checks matching ADR 0020 §6."""
    rules: list[tuple[str, str, bool]] = [("gate", "gate", False), ("gates", "gates", True)]
    for base in gate_basenames:
        rules.append((base, base, False))
    for target in targets:
        rules.append((f"make {target}", f"make {target}", False))
        rules.append((f"./go {target}", f"./go {target}", False))
    for tool in hand_tools:
        rules.append((tool, tool, False))
    rules.append(("make", "make", False))
    rules.append(("./go", "./go", False))
    return rules


def check_text(text: str, rules: list[tuple[str, str, bool]]) -> str | None:
    """Return the first forbidden label found in TEXT, or None."""
    for label, literal, slash_exempt in rules:
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(literal) + r"(?![A-Za-z0-9_])", re.IGNORECASE)
        for match in pattern.finditer(text):
            if slash_exempt and text[match.end() : match.end() + 1] == "/":
                continue
            return label
    return None


def self_test(rules: list[tuple[str, str, bool]]) -> str | None:
    """Prove the checker rejects a planted prompt naming 'mutation' and accepts clean prose."""
    bad = "Do not run the mutation suite yourself.\n"
    good = "Read the ticket. Change only what it asks for. Run the app tests from the checkout, then write the pull request text.\n"
    if check_text(bad, rules) is None:
        return 'self-test failed: a planted prompt naming "mutation" was not caught'
    got = check_text(good, rules)
    if got is not None:
        return f'self-test failed: an allowed-text prompt was flagged (names "{got}")'
    return None


def judge(root: Path | None = None) -> tuple[str, bool]:
    """Check hand tool drift, run planted self-tests, and scan roles/*/*.md."""
    repo_root = Path(root) if root is not None else Path.cwd()
    roles_dir = repo_root / "roles"
    if not roles_dir.is_dir():
        return "roles/ not found", False

    drift = find_tool_drift(repo_root)
    if drift:
        return (
            "hand tool list names a tool no gates/*.sh, ci/secrets.sh, stacks/*/runners/* or workload.env invokes any more "
            "(drift; add the replacement by hand): " + " ".join(drift),
            False,
        )

    gates_dir = repo_root / "gates"
    gate_basenames = sorted(p.stem for p in gates_dir.glob("*.sh") if p.is_file() and p.stem != "test")
    try:
        targets = read_make_targets(repo_root)
    except RuntimeError as err:
        return f"make --no-print-directory help exited nonzero\n{err}", False

    rules = build_rules(gate_basenames, targets)
    st_err = self_test(rules)
    if st_err is not None:
        return st_err, False

    findings: list[str] = []
    first_file = ""
    first_word = ""
    for path in sorted(roles_dir.glob("*/*.md")):
        if not path.is_file():
            continue
        label = check_text(path.read_text(encoding="utf-8"), rules)
        if label is None:
            continue
        rel = path.relative_to(repo_root).as_posix()
        findings.append(f'{rel} names "{label}"')
        if not first_file:
            first_file = rel
            first_word = label

    if findings:
        return "\n".join([f'{first_file} names "{first_word}"', *findings]), False
    return "", True


def main() -> int:
    text, ok = judge()
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
