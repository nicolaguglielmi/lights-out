"""No check of `make ci` prunes the repository's git worktrees.

Spec (docs/adr/0093 §3 and §6): under ./go demo the checks run in a
container of their own, beside the plane, on the same checkout. The tower's
worktrees live in the demo container's /tmp, which the checks' container
cannot see, so a `git worktree prune` there deletes their metadata while a
ticket is in flight. A check removes only the worktrees it added.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def ci_checks() -> list[str]:
    m = re.search(r"^CI_CHECKS\s*:=\s*(.+)$", (ROOT / "Makefile").read_text(encoding="utf-8"), re.MULTILINE)
    assert m, "no CI_CHECKS := line in the Makefile"
    return m.group(1).split()


class ChecksNeverPrune(unittest.TestCase):
    def test_no_check_prunes_worktrees(self):
        offenders = []
        for name in ci_checks():
            script = ROOT / "ci" / f"{name}.sh"
            if not script.is_file():
                continue
            code = [ln for ln in script.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
            if any(re.search(r"\bworktree\s+prune\b", ln) for ln in code):
                offenders.append(name)
        self.assertEqual(offenders, [], "these checks prune every worktree of the checkout")

    def test_gates_light_removes_its_own_worktrees(self):
        text = (ROOT / "ci" / "gates-light.sh").read_text(encoding="utf-8")
        self.assertRegex(text, r"own_worktrees_gone\(\) \{\n[^}]*git worktree remove --force")
        self.assertIn("trap own_worktrees_gone EXIT", text)


if __name__ == "__main__":
    unittest.main()
