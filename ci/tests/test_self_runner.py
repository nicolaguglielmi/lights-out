"""self.yml's gates job and the runner it runs on (docs/adr/0074, amendment 2026-11-28).

Spec: a push may run on the self-hosted runner the SELF_RUNNER variable
names, with a longer timeout, for a first judgement too large for GitHub's
6 h; a pull request always runs on ubuntu-latest within 360 minutes, so no
fork's code reaches a self-hosted runner; with the variable unset nothing
changes.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _gates() -> dict:
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "self.yml").read_text(encoding="utf-8"))
    return wf["jobs"]["gates"]


class SelfRunnerTest(unittest.TestCase):
    def test_only_a_push_reaches_the_self_hosted_runner(self) -> None:
        self.assertEqual(
            _gates()["runs-on"],
            "${{ github.event_name == 'push' && vars.SELF_RUNNER || 'ubuntu-latest' }}",
        )

    def test_the_longer_timeout_needs_a_push_and_the_variable(self) -> None:
        self.assertEqual(
            _gates()["timeout-minutes"],
            "${{ github.event_name == 'push' && vars.SELF_RUNNER && 720 || 360 }}",
        )

    def test_the_plan_job_stays_on_github(self) -> None:
        wf = yaml.safe_load((ROOT / ".github" / "workflows" / "self.yml").read_text(encoding="utf-8"))
        self.assertEqual(wf["jobs"]["plan"]["runs-on"], "ubuntu-latest")


if __name__ == "__main__":
    unittest.main()
