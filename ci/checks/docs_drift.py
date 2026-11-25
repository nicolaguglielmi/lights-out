# SPDX-License-Identifier: Apache-2.0
"""ci-docs-drift: cited paths, targets, and NAME= keys stay aligned with code.

Implements docs/adr/0054 §3 and its 2026-11-09 landing-page amendment:
  1. Runs `core/map.py drift` against a planted probe repository (missing path,
     unknown `./go` target, unknown `NAME=` key) and verifies all 3 findings.
  2. Runs `core/map.py drift` on `.` with `MAP_EXCLUDE` and `--known
     CONTAINER_CONNECTION` (ADR 0051).
  3. Checks `site/index.html` against `lights-out`'s `version=`, main blob/tree
     links, and `./go <target>` commands, after proving a planted landing page
     reports all three defect classes.

Run by ci/docs-drift.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_VERSION_LINE_RE = re.compile(r"version=([0-9]+\.[0-9]+\.[0-9]+)\s*$")
_MAIN_LINK_RE = re.compile(r"https://github\.com/nicolaguglielmi/lights-out/(?:blob|tree)/main/([^\"'#?<>\s]+)")
_V_RE = re.compile(r"(?<![\w.])v([0-9]+\.[0-9]+\.[0-9]+)\b")
_IMG_TAG_RE = re.compile(r"ghcr\.io/nicolaguglielmi/[a-z0-9-]+:([0-9]+\.[0-9]+\.[0-9]+)\b")
_GO_CMD_RE = re.compile(r"\./go ([a-z][a-z0-9-]*)")

_PROBE_PAGE = "# Page\n\n`docs/real.sh`, `docs/gone.sh`, `./go no-such-target`, `NO_SUCH_KEY=1`.\n"
_PROBE_MAP_WANT = "\n".join(
    [
        "docs/page.md:3: command ./go no-such-target names nothing",
        "docs/page.md:3: key NO_SUCH_KEY names nothing",
        "docs/page.md:3: path docs/gone.sh names nothing",
    ]
)

_PROBE_SITE_HTML = """\
<p>v1.2.3 and v1.2.2, ghcr.io/nicolaguglielmi/lights-out-gates:1.2.3</p>
<a href="https://github.com/nicolaguglielmi/lights-out/blob/main/docs/real.md#x">ok</a><a href="https://github.com/nicolaguglielmi/lights-out/tree/main/docs">ok</a>
<a href="https://github.com/nicolaguglielmi/lights-out/blob/main/docs/gone.md">gone</a> <code>./go help</code> <code>./go no-such-target</code>
"""
_PROBE_SITE_WANT = [
    "site/index.html:1: version v1.2.2 is not the wrapper's 1.2.3",
    "site/index.html:3: link to docs/gone.md names nothing",
    "site/index.html:3: command ./go no-such-target names nothing",
]


def _read_make_targets(repo_root: Path) -> list[str]:
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
    return [line.split()[0] for line in proc.stdout.splitlines() if re.match(r"^  [a-z]", line)]


def site_drift(repo: Path, known_targets: set[str]) -> list[str]:
    """Return alignment defects for site/index.html against lights-out's version and repo files."""
    page = "site/index.html"
    version: str | None = None
    for line in (repo / "lights-out").read_text(encoding="utf-8").splitlines():
        match = _VERSION_LINE_RE.match(line)
        if match:
            version = match.group(1)
            break
    if not version:
        return ["lights-out: no version=<major>.<minor>.<patch> line"]

    findings: list[str] = []
    for lineno, line in enumerate((repo / page).read_text(encoding="utf-8").splitlines(), 1):
        for v in sorted(set(_V_RE.findall(line))):
            if v != version:
                findings.append(f"{page}:{lineno}: version v{v} is not the wrapper's {version}")
        for v in sorted(set(_IMG_TAG_RE.findall(line))):
            if v != version:
                findings.append(f"{page}:{lineno}: image tag {v} is not the wrapper's {version}")
        for path in sorted(set(_MAIN_LINK_RE.findall(line))):
            if not (repo / path.rstrip("/")).exists():
                findings.append(f"{page}:{lineno}: link to {path} names nothing")
        for target in sorted(set(_GO_CMD_RE.findall(line))):
            if target not in known_targets:
                findings.append(f"{page}:{lineno}: command ./go {target} names nothing")
    return findings


def _run_map_drift_self_test(repo_root: Path, targets_file: Path, tmp: Path) -> str | None:
    probe = tmp / "probe"
    (probe / "docs").mkdir(parents=True)
    subprocess.run(["git", "-C", str(probe), "init", "-q"], stdin=subprocess.DEVNULL, capture_output=True, check=True)
    (probe / "docs" / "real.sh").write_text("x\n", encoding="utf-8")
    (probe / "docs" / "page.md").write_text(_PROBE_PAGE, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "core/map.py", "drift", "--repo", str(probe), "--targets", str(targets_file)],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return "a page citing a missing path, target and key passed"
    got = (proc.stdout + proc.stderr).strip()
    if got != _PROBE_MAP_WANT:
        return f"the probe page did not give its three findings\n{got}"
    return None


def _run_site_drift_self_test(known_targets: set[str], tmp: Path) -> str | None:
    sp = tmp / "site-probe"
    (sp / "site").mkdir(parents=True)
    (sp / "docs").mkdir(parents=True)
    (sp / "lights-out").write_text("#!/bin/sh\nversion=1.2.3\n", encoding="utf-8")
    (sp / "docs" / "real.md").write_text("x\n", encoding="utf-8")
    (sp / "site" / "index.html").write_text(_PROBE_SITE_HTML, encoding="utf-8")

    got = site_drift(sp, known_targets)
    if got != _PROBE_SITE_WANT:
        return "\n".join(["the probe landing page did not give its three findings", *got])
    return None


def judge(root: Path | None = None, *, map_exclude: str | None = None) -> tuple[str, bool]:
    """Validate docs and landing-page drift after proving probes fail; return (verdict_text, ok)."""
    repo_root = Path(root) if root is not None else Path.cwd()
    exclude = os.environ.get("MAP_EXCLUDE", "") if map_exclude is None else map_exclude
    if not exclude:
        return "MAP_EXCLUDE not set; run this via ./go ci", False

    try:
        targets = _read_make_targets(repo_root)
    except RuntimeError as err:
        return f"make --no-print-directory help exited nonzero\n{err}", False
    known_targets = set(targets)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        targets_file = tmp_path / "targets"
        targets_file.write_text("\n".join(targets) + "\n", encoding="utf-8")

        st_map = _run_map_drift_self_test(repo_root, targets_file, tmp_path)
        if st_map is not None:
            return st_map, False

        real_proc = subprocess.run(
            [
                sys.executable,
                "core/map.py",
                "drift",
                "--repo",
                ".",
                "--targets",
                str(targets_file),
                "--exclude",
                exclude,
                "--known",
                "CONTAINER_CONNECTION",
            ],
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if real_proc.returncode != 0:
            lines = (real_proc.stdout + real_proc.stderr).strip().splitlines()
            return "\n".join([f"{len(lines)} citation(s) name nothing", *lines]), False

        st_site = _run_site_drift_self_test(known_targets, tmp_path)
        if st_site is not None:
            return st_site, False

        site_findings = site_drift(repo_root, known_targets)
        if site_findings:
            return "\n".join([f"the landing page: {len(site_findings)} finding(s)", *site_findings]), False

    return "", True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ci.checks.docs_drift")
    parser.add_argument("--exclude", default=None)
    args = parser.parse_args(argv)
    text, ok = judge(map_exclude=args.exclude)
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
