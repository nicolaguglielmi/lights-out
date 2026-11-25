"""The gates image's entrypoint never clones over the tree it runs from.

Spec (package/entrypoint.sh, `attach_dir`): the repository is cloned into
/tmp/lights-out/attached, unless the current directory or the entrypoint's
own checkout is inside that directory; then it is cloned into a fresh
directory beside it, so the running checkout is never deleted. Inside the
gates image the self-checks run in /tmp/lights-out/attached itself.

The function is taken from the script as it is written and run with sh.
"""

import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "package" / "entrypoint.sh"
ATTACHED = Path("/tmp/lights-out/attached")


def helper() -> str:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = lines.index("attach_dir() {")
    end = lines.index("}", start)
    return "\n".join(lines[start:end + 1]) + "\nattach_dir\n"


class AttachDir(unittest.TestCase):
    def setUp(self):
        # Only a directory of this test's own under ATTACHED, never ATTACHED
        # itself: in the gates image ATTACHED is the running checkout.
        self.mine = ATTACHED / f"test-{uuid.uuid4().hex}"
        (self.mine / "package").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.mine, ignore_errors=True)
        self.outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)
        (self.outside / "package").mkdir()

    def run_from(self, cwd: Path, script_dir: Path) -> str:
        script = script_dir / "package" / "attach-dir.sh"
        script.write_text(helper(), encoding="utf-8")
        out = subprocess.run(["sh", str(script)], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()
        if out != str(ATTACHED):
            self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        return out

    def test_from_elsewhere_the_clone_goes_to_the_usual_place(self):
        self.assertEqual(self.run_from(self.outside, self.outside), str(ATTACHED))

    def test_from_inside_the_usual_place_the_clone_goes_beside_it(self):
        out = self.run_from(self.mine, self.outside)
        self.assertNotEqual(out, str(ATTACHED))
        self.assertTrue(Path(out).is_dir())
        self.assertFalse(str(Path(out).resolve()).startswith(str(ATTACHED.resolve()) + "/"))

    def test_a_script_checked_out_inside_the_usual_place_never_clones_over_it(self):
        out = self.run_from(self.outside, self.mine)
        self.assertNotEqual(out, str(ATTACHED))
        self.assertTrue(self.mine.is_dir(), "the running checkout was touched")


if __name__ == "__main__":
    unittest.main()
