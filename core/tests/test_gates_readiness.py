"""Tests for core/gates/readiness.py, written from docs/adr/0057 §9-§10 and
gates/production-readiness.sh's header, never from the implementation.

GO only when delivery, metrics, secrets and the human gate all hold; one
NO-GO naming every item that fails. The record's hash is the one the
delivery suite writes and ci/lib.sh's adapter_hash computes.
"""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest

from core.gates import NoGo, main
from core.gates import readiness

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def promql(*values):
    return json.dumps({"status": "success", "data": {"result": [{"value": [0, v]} for v in values]}})


class Tree:
    """A working directory with adapters/<d>/ and a record, for one case."""

    def __init__(self, test):
        self.dir = tempfile.mkdtemp()
        test.addCleanup(lambda: subprocess.run(["rm", "-rf", self.dir], check=False))
        cwd = os.getcwd()
        os.chdir(self.dir)
        test.addCleanup(os.chdir, cwd)

    def write(self, rel, text):
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        with open(rel, "w") as fh:
            fh.write(text)


GOOD_ENV = {
    "DELIVERY": "stagehand",
    "METRICS": "promql",
    "METRICS_URL": "http://prom:9090",
    "CONSOLE_AUTH": "proxy",
    "CONSOLE_IDENTITY_HEADER": "X-User",
    "CONSOLE_LAUNCHERS": "ada,grace",
    "READINESS_ENV_FILE": "prod.env",
    "CONFORMANCE_DIR": "records",
}


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        self.t = Tree(self)
        self.t.write("adapters/stagehand/rollback.sh", "echo rollback\n")
        self.t.write("adapters/stagehand/deploy.sh", "echo deploy\n")
        self.t.write("verify/slo.promql", "# name: traffic\nsum by (service_version) (rate(http_server_request_duration_seconds_count[1m]) * on (job, instance) group_left (service_version) target_info)\n")
        self.t.write("prod.env", "APP_PORT=9000\nSERVICE_KEY=\nVCS_TOKEN=replace-me\n")
        self.record()

    def record(self, digest=None, at="2026-11-13T10:00:00Z"):
        digest = readiness.adapter_hash("adapters/stagehand") if digest is None else digest
        self.t.write("records/delivery-stagehand.env", f"ADAPTER=stagehand\nHASH={digest}\nAT={at}\n")

    def run_gate(self, env=None, answer=promql("2")):
        env = dict(GOOD_ENV if env is None else env)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                return "GO", readiness.judge([], env=env, query=lambda e: answer), err.getvalue()
            except NoGo as no:
                return "NO-GO", no.reason, err.getvalue()

    def assert_fails(self, items, env=None, answer=promql("2")):
        verdict, text, _ = self.run_gate(env, answer)
        self.assertEqual(verdict, "NO-GO", text)
        named = [i for i in ("delivery", "metrics", "secrets", "human gate") if f"{i} (" in text]
        self.assertEqual(named, items, text)
        return text

    def test_all_four_hold_is_go_naming_each(self):
        verdict, text, err = self.run_gate()
        self.assertEqual(verdict, "GO", text)
        self.assertIn("delivery stagehand, conformance passed 2026-11-13T10:00:00Z", text)
        self.assertIn("2 target_info series", text)
        self.assertIn("launchers ada,grace", text)
        self.assertEqual(err.count("ok, "), 4, err)

    def test_stderr_line_per_item(self):
        _, _, err = self.run_gate(dict(GOOD_ENV, DELIVERY=""))
        self.assertIn("production-readiness: delivery   FAIL, DELIVERY is not set", err)
        self.assertIn("production-readiness: human gate ok, launchers ada,grace", err)

    def test_no_env_file_is_no_secret(self):
        os.remove("prod.env")
        self.assertEqual(self.run_gate()[0], "GO")

    def test_delivery_unset(self):
        self.assertIn("DELIVERY is not set", self.assert_fails(["delivery"], dict(GOOD_ENV, DELIVERY="")))

    def test_delivery_without_rollback(self):
        os.remove("adapters/stagehand/rollback.sh")
        self.record()
        self.assertIn("no adapters/stagehand/rollback.sh", self.assert_fails(["delivery"]))

    def test_delivery_naming_no_adapter(self):
        self.assert_fails(["delivery"], dict(GOOD_ENV, DELIVERY="nosuch"))

    def test_delivery_without_record(self):
        os.remove("records/delivery-stagehand.env")
        self.assertIn("has not passed here", self.assert_fails(["delivery"]))

    def test_record_of_another_hash(self):
        self.record("0" * 64)
        self.assertIn("changed since its conformance passed at 2026-11-13T10:00:00Z", self.assert_fails(["delivery"]))

    def test_record_without_hash(self):
        self.t.write("records/delivery-stagehand.env", "ADAPTER=stagehand\nAT=x\n")
        self.assert_fails(["delivery"])

    def test_adapter_changed_after_its_record(self):
        self.t.write("adapters/stagehand/deploy.sh", "echo deploy\n# changed\n")
        self.assert_fails(["delivery"])

    def test_metrics_not_promql(self):
        self.assertIn("METRICS=prometheus is not promql", self.assert_fails(["metrics"], dict(GOOD_ENV, METRICS="prometheus")))

    def test_metrics_url_empty(self):
        self.assertIn("METRICS_URL is not set", self.assert_fails(["metrics"], dict(GOOD_ENV, METRICS_URL="")))

    def test_metrics_no_sample_no_answer_or_garbage(self):
        for answer in (promql(), None, "not json", promql("0"), promql("1.5"), promql("-3")):
            with self.subTest(answer=answer):
                self.assert_fails(["metrics"], answer=answer)

    def test_metrics_samples_add_up(self):
        self.assertIn("5 target_info series", self.run_gate(answer=promql("2", "3"))[1])

    def test_each_secret_suffix_and_the_auth_header(self):
        for key in ("SERVICE_KEY", "VCS_TOKEN", "APP_SECRET", "DB_PASSWORD", "METRICS_AUTH_HEADER"):
            with self.subTest(key=key):
                self.t.write("prod.env", f"APP_PORT=9000\n{key}=real-value\n")
                self.assertIn(f"prod.env holds {key};", self.assert_fails(["secrets"]))

    def test_a_secret_value_never_reaches_the_verdict(self):
        self.t.write("prod.env", "VCS_TOKEN=ghp_very_secret\n")
        verdict, text, err = self.run_gate()
        self.assertNotIn("ghp_very_secret", text + err)

    def test_secrets_listed_together(self):
        self.t.write("prod.env", "A_KEY=1\nB_TOKEN=2\nKEYRING=3\n")
        self.assertIn("holds A_KEY B_TOKEN;", self.assert_fails(["secrets"]))

    def test_go_for_launch_preset_in_environment_or_file(self):
        self.assert_fails(["human gate"], dict(GOOD_ENV, GO_FOR_LAUNCH="GO"))
        self.t.write("prod.env", "GO_FOR_LAUNCH=GO\n")
        self.assertIn("GO_FOR_LAUNCH is preset", self.assert_fails(["human gate"]))

    def test_console_auth_not_proxy(self):
        self.assertIn("CONSOLE_AUTH=loopback, not proxy", self.assert_fails(["human gate"], dict(GOOD_ENV, CONSOLE_AUTH="")))

    def test_no_identity_header(self):
        self.assertIn("without CONSOLE_IDENTITY_HEADER",
                      self.assert_fails(["human gate"], dict(GOOD_ENV, CONSOLE_IDENTITY_HEADER="")))

    def test_launchers_only_commas_and_spaces(self):
        for value in ("", " , ,"):
            with self.subTest(value=value):
                self.assert_fails(["human gate"], dict(GOOD_ENV, CONSOLE_LAUNCHERS=value))

    def test_every_failure_named_in_one_line(self):
        env = dict(GOOD_ENV, DELIVERY="", METRICS="prometheus", CONSOLE_AUTH="")
        self.t.write("prod.env", "X_KEY=v\n")
        text = self.assert_fails(["delivery", "metrics", "secrets", "human gate"], env)
        self.assertNotIn("\n", text)

    def test_defaults_are_dot_env_and_crew_conformance(self):
        env = {k: v for k, v in GOOD_ENV.items() if k not in ("READINESS_ENV_FILE", "CONFORMANCE_DIR")}
        self.t.write(".env", "Z_TOKEN=v\n")
        os.makedirs(".crew", exist_ok=True)
        os.rename("records", ".crew/conformance")
        self.assertIn(".env holds Z_TOKEN", self.assert_fails(["secrets"], env))


class HashTest(unittest.TestCase):
    """The digest is ci/lib.sh's adapter_hash, so a record either writes is
    read the same by the other (docs/adr/0057 §9)."""

    def shell_hash(self, d):
        return subprocess.run(["sh", "-c", '. ci/lib.sh && adapter_hash "$1"', "t", d], cwd=REPO,
                              capture_output=True, text=True, check=True).stdout.strip()

    def test_equal_to_ci_lib_on_every_adapter(self):
        for d in sorted(os.listdir(os.path.join(REPO, "adapters"))):
            path = os.path.join(REPO, "adapters", d)
            if os.path.isdir(path):
                with self.subTest(adapter=d):
                    self.assertEqual(readiness.adapter_hash(path), self.shell_hash(path))

    def test_paths_contents_and_subdirectories_count_pycache_does_not(self):
        d = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ["rm", "-rf", d], check=False)
        os.makedirs(f"{d}/sub/__pycache__")
        for rel, text in (("a.sh", "a"), ("B.sh", "b"), ("sub/c.py", "c"), ("sub/__pycache__/c.pyc", "x")):
            with open(f"{d}/{rel}", "w") as fh:
                fh.write(text)
        h0 = readiness.adapter_hash(d)
        self.assertEqual(h0, self.shell_hash(d))
        with open(f"{d}/sub/__pycache__/c.pyc", "w") as fh:
            fh.write("y")
        self.assertEqual(readiness.adapter_hash(d), h0)
        os.rename(f"{d}/sub/c.py", f"{d}/sub/d.py")
        self.assertNotEqual(readiness.adapter_hash(d), h0)
        self.assertEqual(readiness.adapter_hash(d), self.shell_hash(d))


class MainTest(unittest.TestCase):
    def test_verdict_lines(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(lambda a: "fine"), 0)
            self.assertEqual(main(lambda a: None), 0)

            def no(a):
                raise NoGo("why\nsplit", "d1\nd2", "")
            self.assertEqual(main(no), 1)
        self.assertEqual(out.getvalue(), "fine\nwhy split\nd1\nd2\n")


if __name__ == "__main__":
    unittest.main()
