# SPDX-License-Identifier: Apache-2.0
"""ci-roles: the number of roles the repo states is the number it has.

A role is a directory under roles/ with a role.env. Every "<number> agents",
"<number> roles", or "<number> agent roles" in the living text (README.md,
CONTRIBUTING.md, ROADMAP.md, board/index.html, docs/*.md excluding
docs/PLAN.md, and roles/*/prompt.md) must equal that number. ADRs are records
of a decision at their date and are not read.

Run by ci/roles.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")
LIVING_TEXT_FILES = ("README.md", "CONTRIBUTING.md", "ROADMAP.md", "board/index.html")
CLAIM_RE = re.compile(r"\b(" + "|".join(WORDS) + r"|\d+) (?:agent roles|agents|roles)\b", re.IGNORECASE)


def find_mismatches(path_label: str, text: str, expected_count: int) -> list[str]:
    """Return '<path>:<lineno>: says <match>' entries where stated count != expected_count."""
    bad: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in CLAIM_RE.finditer(line):
            raw = match.group(1).lower()
            said = int(raw) if raw.isdigit() else WORDS.index(raw)
            if said != expected_count:
                bad.append(f"{path_label}:{lineno}: says {match.group(0)!r}")
    return bad


def self_test() -> str | None:
    """Prove the role-count scanner rejects a stale role count and accepts the real count."""
    if not find_mismatches("probe.md", "There are seven agent roles.\n", 6):
        return "self-test failed: a stale claim ('seven agent roles' vs 6) was accepted"
    if find_mismatches("probe.md", "There are six roles and 6 agents.\n", 6):
        return "self-test failed: a matching claim ('six roles and 6 agents' vs 6) was rejected"
    return None


def judge(root: Path | None = None) -> tuple[str, bool]:
    """Validate living-text role counts under ROOT; return (verdict_text, ok)."""
    repo_root = Path(root) if root is not None else Path.cwd()
    roles_dir = repo_root / "roles"
    if not roles_dir.is_dir():
        return "roles/ not found", False

    st_err = self_test()
    if st_err is not None:
        return st_err, False

    roles = sorted(p.parent.name for p in roles_dir.glob("*/role.env") if p.is_file())
    if not roles:
        return "a stated role count disagrees with roles/\nno roles/*/role.env", False

    targets = [repo_root / rel for rel in LIVING_TEXT_FILES]
    docs_dir = repo_root / "docs"
    if docs_dir.is_dir():
        targets.extend(p for p in sorted(docs_dir.glob("*.md")) if p.name != "PLAN.md")
    targets.extend(sorted(roles_dir.glob("*/prompt.md")))

    bad: list[str] = []
    for path in targets:
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root).as_posix()
        bad.extend(find_mismatches(rel, path.read_text(encoding="utf-8"), len(roles)))

    if bad:
        header = f"roles/ has {len(roles)}: {', '.join(roles)}"
        return "\n".join(["a stated role count disagrees with roles/", header, *bad]), False
    return "", True


def main() -> int:
    text, ok = judge()
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
