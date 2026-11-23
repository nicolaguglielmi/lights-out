"""Tests for M69 workflow wiring and docs alignment (ADR 0099), written from
the ADR and never from the implementation.

Kept under ci/tests/ (rather than core/tests/) so core/ remains free of
CodeHost vendor names (ci/vendor-free.sh) and workload paths (ci/app-free.sh),
and run alongside core/tests/ by ci/core-tests.sh.
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class PackageWorkflowTests(unittest.TestCase):
    """ADR 0099 §4: `package.yml`'s `dist` job verifies `lights-out` inside and from the wheel."""

    def test_dist_checks_wrapper_inside_wheel_and_runs_both_executables(self):
        doc = yaml.safe_load((ROOT / ".github/workflows/package.yml").read_text(encoding="utf-8"))
        dist = doc["jobs"]["dist"]
        run_text = "\n".join(str(step.get("run", "")) for step in dist.get("steps") or [])

        self.assertIn("uv build", run_text)
        self.assertIn(".data/scripts/lights-out", run_text)
        self.assertIn("lights-out-gates version", run_text)
        self.assertIn("lights-out --version", run_text)
        self.assertRegex(run_text, r"uv tool run\b[^\n]*lights-out --version")

        pypi_if = str(doc["jobs"]["pypi"].get("if", ""))
        self.assertIn("refs/tags/v", pypi_if)
        self.assertNotIn("workflow_dispatch", pypi_if)


class StrangerWorkflowTests(unittest.TestCase):
    """ADR 0099 §4: `stranger.yml`'s `package` job installs the built wheel via `uv tool install`."""

    def test_package_job_builds_wheel_and_runs_both_installed_commands(self):
        doc = yaml.safe_load((ROOT / ".github/workflows/stranger.yml").read_text(encoding="utf-8"))
        pkg = doc["jobs"]["package"]
        run_text = "\n".join(str(step.get("run", "")) for step in pkg.get("steps") or [])

        self.assertIn("UV_VERSION", run_text)
        self.assertIn("UV_SHA256_AMD64", run_text)
        self.assertIn("workbench/fetch.sh", run_text)
        self.assertRegex(run_text, r"uv build\b[^\n]*--out-dir")
        self.assertRegex(run_text, r"uv tool install\b[^\n]*--from\b[^\n]*lights-out-gates")
        self.assertIn("uv tool dir --bin", run_text)
        self.assertIn("GITHUB_PATH", run_text)
        self.assertIn("lights-out --version", run_text)
        self.assertIn("lights-out-gates version", run_text)
        self.assertIn("lights-out doctor", run_text)
        self.assertIn("lights-out init", run_text)
        self.assertIn("lights-out gates", run_text)
        self.assertIn("lights-out-gates --light", run_text)
        self.assertIn("workloads/minimal/", run_text)


class ReleaseNotesAndDocsTests(unittest.TestCase):
    """ADR 0099 §5: release notes and docs present `uv tool install lights-out-gates` as primary."""

    def test_release_notes_name_one_install_command(self):
        text = (ROOT / "package/release-notes.sh").read_text(encoding="utf-8")
        self.assertIn("uv tool install lights-out-gates", text)
        self.assertIn("lights-out", text)

    def test_docs_and_site_show_one_install_with_curl_alternative(self):
        for rel in ("README.md", "docs/use-on-your-repo.md", "docs/tutorial.md", "docs/reference.md", "site/index.html"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("uv tool install lights-out-gates", text, rel)
            self.assertIn("curl ", text, rel)
