"""GitLab's open-pr waits out a branch the merge request API does not know yet.

Spec (adapters/gitlab/open-pr.sh, docs/adr/0017 §1): GitLab takes a push in
the background, so for a moment after it a merge request from the new branch
is refused with a 400 naming the source branch. That answer is retried, once
a second, READY_TIMEOUT times; then the open-pr prints "<iid> <url>". Any
other refusal fails at once, naming the code and GitLab's body.

curl is a fake on PATH: the listing answers [], the creation answers what
the test scripts, one answer per call.
"""

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "adapters" / "gitlab" / "open-pr.sh"

FAKE_CURL = r"""#!/bin/sh
out=""; post=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out=$2; shift ;;
    -X) [ "$2" = POST ] && post=1; shift ;;
  esac
  shift
done
if [ -z "$post" ]; then echo '[]'; exit 0; fi
n=$(cat "$ANSWERS.n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$ANSWERS.n"
line=$(sed -n "${n}p" "$ANSWERS"); [ -n "$line" ] || line=$(tail -n 1 "$ANSWERS")
printf '%s' "${line#* }" > "$out"; printf '%s' "${line%% *}"
"""

BRANCH_400 = '400 {"message":["Source branch \\"t/1\\" does not exist"]}'
CREATED = '201 {"iid":7,"web_url":"http://gitlab.invalid/root/workload/-/merge_requests/7"}'


class OpenPrRetry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        curl = bin_dir / "curl"
        curl.write_text(FAKE_CURL, encoding="utf-8")
        curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
        sleep = bin_dir / "sleep"
        sleep.write_text("#!/bin/sh\n:\n", encoding="utf-8")
        sleep.chmod(sleep.stat().st_mode | stat.S_IEXEC)
        self.path = f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"
        self.pr = self.tmp / "pr.json"
        self.pr.write_text(json.dumps({"title": "t", "body": "b"}), encoding="utf-8")
        self.answers = self.tmp / "answers"

    def run_open_pr(self, *answers: str, timeout: int = 5):
        self.answers.write_text("\n".join(answers) + "\n", encoding="utf-8")
        (self.tmp / "answers.n").unlink(missing_ok=True)
        env = {"PATH": self.path, "ANSWERS": str(self.answers), "READY_TIMEOUT": str(timeout)}
        r = subprocess.run(["sh", str(SCRIPT), "t/1", str(self.pr)], env=env, capture_output=True, text=True)
        calls = int((self.tmp / "answers.n").read_text() or 0) if (self.tmp / "answers.n").exists() else 0
        return r, calls

    def test_the_source_branch_400_is_waited_out(self):
        r, calls = self.run_open_pr(BRANCH_400, BRANCH_400, CREATED)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "7 http://gitlab.invalid/root/workload/-/merge_requests/7\n")
        self.assertEqual(calls, 3)

    def test_it_gives_up_after_ready_timeout_naming_the_answer(self):
        r, calls = self.run_open_pr(BRANCH_400, timeout=3)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(calls, 3)
        self.assertIn("400", r.stderr)
        self.assertIn("does not exist", r.stderr)

    def test_any_other_refusal_fails_at_once_with_its_body(self):
        r, calls = self.run_open_pr('400 {"message":["Title is too long"]}', CREATED)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(calls, 1)
        self.assertIn("Title is too long", r.stderr)
        r, calls = self.run_open_pr('403 {"message":"403 Forbidden"}', CREATED)
        self.assertEqual(r.returncode, 1)
        self.assertIn("403", r.stderr)


if __name__ == "__main__":
    unittest.main()
