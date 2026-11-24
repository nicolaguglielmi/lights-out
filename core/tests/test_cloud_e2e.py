"""Tests for Registry and Flags conformance suites and outside admission (ADR 0107).

Strictly vendor-free, app-free, and model-free: drives core.gates.registry,
core.gates.flags, core.adapters, and core.gates.readiness against in-memory
fakes and temporary directories.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from core import adapters
from core.gates import NoGo, flags, readiness, registry


class FakeRegistry:
    """In-memory Registry port fake, honest unless brk names one defect."""

    def __init__(self, brk: str | None = None, has_sbom: bool = True):
        self.brk = brk
        self.has_sbom = has_sbom
        self.tags: dict[str, str] = {}
        self.sboms: dict[str, str] = {}
        self.repo = "registry.example.invalid/service"

    def __call__(self, name: str, *args: str, data: bytes | None = None):
        if name == "ready":
            if self.brk == "ready-fails":
                return 1, "", "registry unreachable\n"
            if self.brk == "ready-stdout":
                return 0, "ok\n", ""
            return 0, "", ""
        if name == "publish":
            tag = args[0] if args else ""
            if not tag:
                if self.brk == "empty-publish-ok":
                    return 0, f"{self.repo}@sha256:{'a' * 64}\n", ""
                return 1, "", "usage: publish.sh <tag>\n"
            digest = "sha256:" + hashlib.sha256(tag.encode()).hexdigest()
            ref = f"{self.repo}@{digest}"
            self.tags[tag] = ref
            if self.has_sbom:
                self.sboms[ref] = "sha256:" + hashlib.sha256(f"sbom:{tag}".encode()).hexdigest()
            if self.brk == "publish-mutable-tag":
                return 0, f"{self.repo}:{tag}\n", ""
            if self.brk == "publish-multi-line":
                return 0, f"building...\n{ref}\n", ""
            return 0, ref + "\n", ""
        if name == "verify":
            tag = args[0] if args else ""
            if not tag:
                if self.brk == "empty-verify-ok":
                    return 0, f"{self.repo}@sha256:{'b' * 64}\n", ""
                return 1, "", "usage: verify.sh <tag>\n"
            if tag not in self.tags:
                if self.brk == "accept-unsigned":
                    return 0, f"{self.repo}@sha256:{'c' * 64}\n", ""
                if self.brk == "unsigned-stdout":
                    return 1, f"{self.repo}:{tag}\n", "unsigned\n"
                return 1, "", "unsigned or absent\n"
            ref = self.tags[tag]
            if self.brk == "verify-mismatch":
                return 0, f"{self.repo}@sha256:{'d' * 64}\n", ""
            if self.brk == "verify-mutable-tag":
                return 0, f"{self.repo}:{tag}\n", ""
            return 0, ref + "\n", ""
        if name == "sbom":
            ref = args[0] if args else ""
            if ref not in self.sboms:
                if self.brk == "sbom-absent-ok":
                    return 0, f"sha256:{'e' * 64}\n", ""
                if self.brk == "sbom-absent-stdout":
                    return 1, "not found\n", "missing\n"
                return 1, "", "no sbom\n"
            if self.brk == "sbom-bad-format":
                return 0, "not-a-digest\n", ""
            return 0, self.sboms[ref] + "\n", ""
        raise AssertionError(name)


class FakeFlags:
    """In-memory Flags port fake, honest unless brk names one defect."""

    def __init__(self, brk: str | None = None, initial: str = "off"):
        self.brk = brk
        self.flags: dict[str, dict] = {
            "release-freeze": {
                "variants": {"on": True, "off": False},
                "defaultVariant": initial,
            }
        }

    def __call__(self, name: str, *args: str, data: bytes | None = None):
        if name == "ready":
            if self.brk == "ready-fails":
                return 1, "", "flags not ready\n"
            if self.brk == "ready-stdout":
                return 0, "ready\n", ""
            return 0, "", ""
        if name == "evaluate":
            flag = args[0] if args else ""
            if not flag:
                if self.brk == "empty-eval-ok":
                    return 0, "false\n", ""
                return 1, "", "missing flag\n"
            if flag not in self.flags:
                if self.brk == "absent-is-false":
                    return 0, "false\n", ""
                if self.brk == "absent-stdout":
                    return 1, "null\n", "no such flag\n"
                return 1, "", "no such flag\n"
            spec = self.flags[flag]
            val = spec["variants"][spec["defaultVariant"]]
            if self.brk == "bad-value":
                return 0, "maybe\n", ""
            return 0, ("true" if val else "false") + "\n", ""
        if name == "set":
            flag = args[0] if len(args) > 0 else ""
            variant = args[1] if len(args) > 1 else ""
            if not flag or not variant:
                if self.brk == "empty-set-ok":
                    return 0, "", ""
                return 1, "", "usage: set.sh <flag> <variant>\n"
            if flag not in self.flags:
                if self.brk == "set-absent-ok":
                    return 0, "", ""
                return 1, "", "unknown flag\n"
            if variant not in self.flags[flag]["variants"]:
                if self.brk == "set-bad-variant-ok":
                    return 0, "", ""
                return 1, "", "unknown variant\n"
            if self.brk == "set-stdout":
                self.flags[flag]["defaultVariant"] = variant
                return 0, "updated\n", ""
            if self.brk != "set-noop":
                self.flags[flag]["defaultVariant"] = variant
            return 0, "", ""
        raise AssertionError(name)


class RegistryConformanceTest(unittest.TestCase):
    def run_suite(self, reg: FakeRegistry, extra_env: dict[str, str] | None = None):
        env = {"REGISTRY": "fake-reg", **(extra_env or {})}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                return "GO", registry.judge([], env=env, verb=reg)
            except NoGo as no:
                return "NO-GO", no.reason

    def test_fair_registry_passes_with_and_without_sbom(self):
        for has_sbom in (True, False):
            with self.subTest(has_sbom=has_sbom):
                reg = FakeRegistry(has_sbom=has_sbom)
                verdict, detail = self.run_suite(reg)
                self.assertEqual(verdict, "GO", detail)
                self.assertIn("fake-reg", detail)
                self.assertIn("6 cases", detail)

    def test_require_sbom_fails_when_sbom_missing(self):
        reg = FakeRegistry(has_sbom=False)
        verdict, reason = self.run_suite(reg, {"CONFORMANCE_REQUIRE_SBOM": "1"})
        self.assertEqual(verdict, "NO-GO")
        self.assertTrue(reason.startswith("case6 sbom:"), reason)

    def test_each_break_names_its_case(self):
        cases = (
            ("ready-fails", "case1 ready"),
            ("ready-stdout", "case1 ready"),
            ("accept-unsigned", "case2 unsigned-refused"),
            ("unsigned-stdout", "case2 unsigned-refused"),
            ("publish-mutable-tag", "case3 publish-digest"),
            ("publish-multi-line", "case3 publish-digest"),
            ("verify-mismatch", "case4 verify-roundtrip"),
            ("verify-mutable-tag", "case4 verify-roundtrip"),
            ("empty-publish-ok", "case5 missing-arg-refused"),
            ("empty-verify-ok", "case5 missing-arg-refused"),
            ("sbom-absent-ok", "case6 sbom"),
            ("sbom-absent-stdout", "case6 sbom"),
            ("sbom-bad-format", "case6 sbom"),
        )
        for brk, case in cases:
            with self.subTest(brk=brk):
                verdict, reason = self.run_suite(FakeRegistry(brk=brk))
                self.assertEqual(verdict, "NO-GO", reason)
                self.assertTrue(reason.startswith(case + ":"), reason)
                self.assertNotIn("\n", reason)

    def test_unset_registry_and_missing_verbs(self):
        with self.assertRaises(NoGo):
            registry.judge([], env={})
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            os.chdir(td)
            try:
                Path("adapters/incomplete").mkdir(parents=True)
                Path("adapters/incomplete/publish.sh").write_text("#!/bin/sh\n", encoding="utf-8")
                with self.assertRaises(NoGo) as ctx:
                    registry.judge([], env={"REGISTRY": "incomplete"})
                self.assertIn("verify.sh", ctx.exception.reason)
            finally:
                os.chdir(cwd)


class FlagsConformanceTest(unittest.TestCase):
    def run_suite(self, flg: FakeFlags, extra_env: dict[str, str] | None = None):
        env = {"FLAGS": "fake-flags", **(extra_env or {})}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                return "GO", flags.judge([], env=env, verb=flg)
            except NoGo as no:
                return "NO-GO", no.reason

    def test_fair_flags_passes_and_restores_initial_state(self):
        for initial in ("off", "on"):
            with self.subTest(initial=initial):
                flg = FakeFlags(initial=initial)
                verdict, detail = self.run_suite(flg)
                self.assertEqual(verdict, "GO", detail)
                self.assertIn("fake-flags", detail)
                self.assertIn("6 cases", detail)
                self.assertEqual(flg.flags["release-freeze"]["defaultVariant"], initial)

    def test_readonly_mode_skips_mutation_cleanly(self):
        flg = FakeFlags(brk="set-noop", initial="off")
        verdict, detail = self.run_suite(flg, {"CONFORMANCE_READONLY": "1"})
        self.assertEqual(verdict, "GO", detail)
        self.assertIn("CONFORMANCE_READONLY", detail)

    def test_each_break_names_its_case_and_restores(self):
        cases = (
            ("ready-fails", "case1 ready"),
            ("ready-stdout", "case1 ready"),
            ("absent-is-false", "case2 absent-flag-refused"),
            ("absent-stdout", "case2 absent-flag-refused"),
            ("bad-value", "case3 evaluate-known"),
            ("set-noop", "case4 set-and-evaluate"),
            ("set-stdout", "case4 set-and-evaluate"),
            ("set-absent-ok", "case5 invalid-set-refused"),
            ("set-bad-variant-ok", "case5 invalid-set-refused"),
            ("empty-eval-ok", "case6 missing-arg-refused"),
            ("empty-set-ok", "case6 missing-arg-refused"),
        )
        for brk, case in cases:
            with self.subTest(brk=brk):
                flg = FakeFlags(brk=brk, initial="on")
                verdict, reason = self.run_suite(flg)
                self.assertEqual(verdict, "NO-GO", reason)
                self.assertTrue(reason.startswith(case + ":"), reason)
                self.assertNotIn("\n", reason)
                if brk != "set-noop":
                    self.assertEqual(flg.flags["release-freeze"]["defaultVariant"], "on", brk)

    def test_unset_flags_and_missing_verbs(self):
        with self.assertRaises(NoGo):
            flags.judge([], env={})
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            os.chdir(td)
            try:
                Path("adapters/incomplete").mkdir(parents=True)
                Path("adapters/incomplete/ready.sh").write_text("#!/bin/sh\n", encoding="utf-8")
                with self.assertRaises(NoGo) as ctx:
                    flags.judge([], env={"FLAGS": "incomplete"})
                self.assertIn("evaluate.sh", ctx.exception.reason)
            finally:
                os.chdir(cwd)


class ReadinessProxyDerivationTest(unittest.TestCase):
    def test_derives_count_query_without_target_info(self):
        with tempfile.TemporaryDirectory() as td:
            promql = Path(td) / "proxy.promql"
            promql.write_text(
                "# name: traffic\n"
                "sum by (cluster) (label_replace(rate(ingress_requests_total[1m]), \"cluster\", \"$1\", \"svc\", \".*\"))\n",
                encoding="utf-8",
            )
            metric, expr = readiness._readiness_query({"SLO_PROMQL": str(promql)})
            self.assertEqual(metric, "ingress_requests_total")
            self.assertEqual(expr, "count(ingress_requests_total)")


class AdaptersRegistryFlagsWiringTest(unittest.TestCase):
    def test_registry_and_flags_in_suites_and_absent_from_no_suite(self):
        self.assertEqual(adapters.SUITES.get("REGISTRY"), "registry-conformance")
        self.assertEqual(adapters.SUITES.get("FLAGS"), "flags-conformance")
        self.assertEqual(adapters.SUITES.get("TRACKER"), "tracker-conformance")
        self.assertEqual(adapters.SUITES.get("CHARTS"), "charts-conformance")
        self.assertEqual(adapters.NO_SUITE, ("ORCHESTRATOR", "METRICS"))


if __name__ == "__main__":
    unittest.main()
