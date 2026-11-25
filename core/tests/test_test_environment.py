"""The test packages drop what `make gates` exports about the checkout.

Spec (core/tests/__init__.py, ci/tests/__init__.py): once either package is
imported, WORKLOAD, ATTACHED, BASE, HEAD and the other variables naming the
checkout under judgment, and make's own MAKEFLAGS, are gone from the
environment, whatever the parent set.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("WORKLOAD", "ATTACHED", "BASE", "HEAD", "GATES_OUT", "MAKEFLAGS")


class TestEnvironment(unittest.TestCase):
    def test_each_package_drops_the_checkout_variables(self):
        for pkg in ("core.tests", "ci.tests"):
            with self.subTest(pkg=pkg):
                env = {**os.environ, **{n: "/the/checkout/under/judgment" for n in NAMES}}
                out = subprocess.run(
                    [sys.executable, "-c", f"import os, {pkg}; print(sorted(n for n in {NAMES!r} if n in os.environ))"],
                    cwd=ROOT, env=env, capture_output=True, text=True, check=True,
                ).stdout.strip()
                self.assertEqual(out, "[]")


if __name__ == "__main__":
    unittest.main()
