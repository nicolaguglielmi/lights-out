"""Tests for M69 (One install via lights-out-gates, ADR 0099), written from
the ADR and never from the implementation.

Verifies:
- `lights-out --version` matches `lights-out version`, works with no container
  engine on PATH, never invokes `podman` or `docker`, and matches both
  `version=` in `lights-out` and `[project].version` in `pyproject.toml`;
- `pyproject.toml` configures `tool.setuptools.script-files = ["lights-out"]`
  beside `[project.scripts]` (`lights-out-gates`), keeps `name = "lights-out-gates"`
  and `dependencies = []`, and describes both commands;
- `uv build` puts the byte-identical `lights-out` script into both sdist and
  wheel (`.data/scripts/lights-out`), excludes tests, and `uv tool install`
  installs both executables (`lights-out`, `lights-out-gates`) working end-to-end.
"""

import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

from core.tests.test_light import CALC_PLUS, Repo, clean_env

ROOT = Path(__file__).resolve().parents[2]
POSIX_TOOLS = (
    "sh", "awk", "sed", "grep", "tr", "sort", "head", "tail", "cut",
    "cat", "uname", "basename", "dirname", "mkdir", "rm", "mktemp", "env", "wc",
)


def parse_wrapper_version(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.fullmatch(r"version=([0-9]+\.[0-9]+\.[0-9]+)", line)
        if m:
            return m.group(1)
    raise AssertionError(f"{path} has no semver version= line")


def load_pyproject():
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


class OneInstallCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="one-install-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make_engine_free_path(self):
        tools = self.tmp / "tools"
        tools.mkdir()
        for tool in POSIX_TOOLS:
            found = shutil.which(tool)
            if found:
                (tools / tool).symlink_to(found)
        trip = self.tmp / "trip"
        trip.mkdir()
        tripped = self.tmp / "tripped"
        for engine in ("podman", "docker"):
            script = trip / engine
            script.write_text(f"#!/bin/sh\n: > {str(tripped)!r}\nexit 0\n", encoding="utf-8")
            script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return tools, trip, tripped


class WrapperVersionTests(OneInstallCase):
    """ADR 0099 §2: `--version` aliases `version`, needs no engine, never touches one."""

    def test_flag_version_matches_subcommand_without_engine(self):
        tools, trip, tripped = self.make_engine_free_path()
        wrapper = ROOT / "lights-out"
        wrapper_ver = parse_wrapper_version(wrapper)
        pyproject_ver = load_pyproject()["project"]["version"]
        self.assertEqual(wrapper_ver, pyproject_ver)

        env_no_engine = clean_env(PATH=str(tools), HOME=str(self.tmp))
        env_trip = clean_env(PATH=f"{trip}{os.pathsep}{tools}", HOME=str(self.tmp))

        res_sub = subprocess.run(
            ["sh", str(wrapper), "version"],
            cwd=self.tmp, env=env_no_engine, capture_output=True, text=True, check=False,
        )
        res_flag = subprocess.run(
            ["sh", str(wrapper), "--version"],
            cwd=self.tmp, env=env_no_engine, capture_output=True, text=True, check=False,
        )
        res_trip = subprocess.run(
            ["sh", str(wrapper), "--version"],
            cwd=self.tmp, env=env_trip, capture_output=True, text=True, check=False,
        )

        self.assertEqual(res_sub.returncode, 0, res_sub.stderr)
        self.assertEqual(res_flag.returncode, 0, res_flag.stderr)
        self.assertEqual(res_trip.returncode, 0, res_trip.stderr)
        self.assertFalse(tripped.exists(), "--version invoked an engine")
        self.assertEqual(res_flag.stderr, "")
        self.assertEqual(res_flag.stdout, res_sub.stdout)
        self.assertTrue(
            res_flag.stdout.startswith(f"lights-out {wrapper_ver} ("),
            f"unexpected --version output: {res_flag.stdout!r}",
        )


class PyprojectMetadataTests(unittest.TestCase):
    """ADR 0099 §1: `lights-out-gates` ships both `lights-out` and `lights-out-gates`."""

    def test_pyproject_ships_wrapper_beside_light_tier(self):
        data = load_pyproject()
        project = data["project"]
        setuptools = data["tool"]["setuptools"]

        self.assertEqual(project["name"], "lights-out-gates")
        self.assertEqual(project["dependencies"], [])
        self.assertEqual(project["scripts"], {"lights-out-gates": "lights_out.__main__:gates_main"})
        self.assertEqual(setuptools.get("script-files"), ["lights-out"])
        desc = project.get("description", "")
        self.assertIn("lights-out", desc)
        self.assertIn("lights-out-gates", desc)


class WheelAndToolInstallTests(OneInstallCase):
    """ADR 0099 §1-§2: `uv build` and `uv tool install` put both executables on PATH."""

    def test_uv_build_and_tool_install_ship_both_executables(self):
        uv = shutil.which("uv")
        self.assertIsNotNone(uv, "uv must be on PATH in the workbench")

        pkg_src = self.tmp / "src"
        pkg_src.mkdir()
        for rel in ("pyproject.toml", "LICENSE", "NOTICE", "README.md", "lights-out"):
            shutil.copy2(ROOT / rel, pkg_src / rel)
        for d in ("core", "policy"):
            shutil.copytree(ROOT / d, pkg_src / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

        dist = self.tmp / "dist"
        build = subprocess.run(
            [uv, "build", "--out-dir", str(dist)],
            cwd=pkg_src, env=clean_env(HOME=str(self.tmp)), capture_output=True, text=True, check=False,
        )
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

        version = load_pyproject()["project"]["version"]
        sdist = dist / f"lights_out_gates-{version}.tar.gz"
        wheel = dist / f"lights_out_gates-{version}-py3-none-any.whl"
        self.assertTrue(sdist.is_file(), f"missing sdist: {list(dist.iterdir())}")
        self.assertTrue(wheel.is_file(), f"missing wheel: {list(dist.iterdir())}")

        repo_wrapper = (ROOT / "lights-out").read_bytes()

        with tarfile.open(sdist, "r:gz") as tf:
            sdist_member = f"lights_out_gates-{version}/lights-out"
            self.assertIn(sdist_member, tf.getnames())
            self.assertEqual(tf.extractfile(sdist_member).read(), repo_wrapper)

        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()
            wheel_script = f"lights_out_gates-{version}.data/scripts/lights-out"
            self.assertIn(wheel_script, names)
            self.assertEqual(zf.read(wheel_script), repo_wrapper)
            self.assertFalse(any("/tests/" in n or n.startswith("tests/") for n in names), names)

        tool_dir = self.tmp / "uv-tools"
        bin_dir = self.tmp / "uv-bin"
        install_env = clean_env(HOME=str(self.tmp), UV_TOOL_DIR=str(tool_dir), UV_TOOL_BIN_DIR=str(bin_dir))
        install = subprocess.run(
            [uv, "tool", "install", "--from", str(wheel), "lights-out-gates"],
            cwd=self.tmp, env=install_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(install.returncode, 0, install.stdout + install.stderr)

        inst_wrapper = bin_dir / "lights-out"
        inst_gates = bin_dir / "lights-out-gates"
        self.assertTrue(inst_wrapper.is_file(), f"installed bins: {list(bin_dir.iterdir())}")
        self.assertTrue(inst_gates.is_file(), f"installed bins: {list(bin_dir.iterdir())}")
        self.assertEqual(inst_wrapper.read_bytes(), repo_wrapper)

        tools, trip, tripped = self.make_engine_free_path()
        run_env = clean_env(PATH=f"{bin_dir}{os.pathsep}{trip}{os.pathsep}{tools}", HOME=str(self.tmp))

        w_flag = subprocess.run([str(inst_wrapper), "--version"], env=run_env, capture_output=True, text=True, check=False)
        w_sub = subprocess.run([str(inst_wrapper), "version"], env=run_env, capture_output=True, text=True, check=False)
        self.assertEqual(w_flag.returncode, 0, w_flag.stderr)
        self.assertEqual(w_flag.stdout, w_sub.stdout)
        self.assertTrue(w_flag.stdout.startswith(f"lights-out {version} ("), w_flag.stdout)
        self.assertFalse(tripped.exists(), "installed lights-out --version invoked an engine")

        g_ver = subprocess.run([str(inst_gates), "version"], env=run_env, capture_output=True, text=True, check=False)
        self.assertEqual(g_ver.returncode, 0, g_ver.stderr)
        self.assertEqual(g_ver.stdout.strip(), f"lights-out-gates {version} (the light tier: --light)")

        iso = subprocess.run(
            [uv, "tool", "run", "--isolated", "--from", str(wheel), "lights-out", "--version"],
            cwd=self.tmp, env=clean_env(HOME=str(self.tmp)), capture_output=True, text=True, check=False,
        )
        self.assertEqual(iso.returncode, 0, iso.stderr)
        self.assertEqual(iso.stdout, w_sub.stdout)

        r = Repo(self.tmp / "scratch-repo")
        r.populate(contract=True)
        r.commit("base")
        r.write(r.calc, CALC_PLUS)
        r.commit("change")
        g_light = subprocess.run(
            [str(inst_gates), "--light"],
            cwd=r.path, env=clean_env(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", HOME=str(self.tmp)),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(g_light.returncode, 0, g_light.stdout + g_light.stderr)
        self.assertTrue(g_light.stdout.startswith("GO: gates-light "), g_light.stdout)
