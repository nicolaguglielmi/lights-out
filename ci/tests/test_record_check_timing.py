"""record_check names each check's duration on stderr, never on stdout.

Spec (ci/lib.sh, record_check; docs/adr/0062, docs/adr/0093 §6): under
`./go ci` (CI_RECORDS set), once a check has ended, one line
`[ci] NAME: N s` goes to stderr, so a demo over its budget shows which
check took the time. Stdout stays the check's own, byte for byte, and its
exit code is returned. Run alone (no CI_RECORDS) nothing is added.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RecordCheckTiming(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.check = self.tmp / "check.sh"
        self.check.write_text("echo 'NO-GO: probe — planted'\necho noise >&2\nexit 1\n", encoding="utf-8")

    def run_check(self, records: str | None):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        if records is not None:
            env["CI_RECORDS"] = records
        return subprocess.run(["sh", "-c", f'. ci/lib.sh; record_check "{self.check}" probe'],
                              cwd=ROOT, env=env, capture_output=True, text=True)

    def test_under_ci_the_duration_goes_to_stderr(self):
        r = self.run_check(str(self.tmp / "records"))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "NO-GO: probe — planted\n")
        self.assertIn("noise", r.stderr)
        self.assertRegex(r.stderr.splitlines()[-1], r"^\[ci\] probe: [0-9]+ s$")

    def test_alone_nothing_is_added(self):
        r = self.run_check(None)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "NO-GO: probe — planted\n")
        self.assertIsNone(re.search(r"\[ci\]", r.stderr))


if __name__ == "__main__":
    unittest.main()
