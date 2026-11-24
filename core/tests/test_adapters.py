"""Tests for core/adapters.py, written from docs/adr/0058 §1-§4 (an
adapter outside the repository, admitted by the exact hash its port's
conformance suite passed on), never from the implementation.

The module's commands, as the ADR and the Makefile glue use them:

    adapters.py hash <dir>                  the adapter's sha256, one line
    adapters.py admit                       nothing and exit 0, or the one refusal line on stdout and exit 1
    adapters.py trial <PORT>                link the adapter for its suite and record the hash on trial
    adapters.py register <PORT> <suite>     record the proof, only if the bytes are those on trial
    adapters.py untrial <PORT>              the suite ended: the trial record goes
"""

import contextlib
import hashlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path

from core import adapters

GOOD = {"get.sh": "#!/bin/sh\necho get\n", "put.sh": "#!/bin/sh\necho put\n", "lib/x.py": "print(1)\n"}


class AdaptersCase(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        os.chdir(self.checkout)
        Path("gates").mkdir()
        for suite in adapters.SUITES.values():
            Path(f"gates/{suite}.sh").write_text(f"# {suite}\n")
        self.outside = self.root / "outside"
        self.make_adapter("json-files")
        self.env = {"ADAPTERS_PATH": str(self.outside), "STATE_STORE": "outside/json-files",
                    "CODE_HOST": "ours-a", "DELIVERY": "ours-b", "TRACKER": "file"}

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def make_adapter(self, name, files=None):
        d = self.outside / name
        for rel, text in (GOOD if files is None else files).items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        return d

    def call(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = adapters.main(list(argv), dict(self.env if env is None else env))
        return code, out.getvalue(), err.getvalue()

    def admitted(self, env=None):
        code, out, _ = self.call("admit", env=env)
        return code == 0 and out == ""

    def refusal(self, env=None):
        code, out, _ = self.call("admit", env=env)
        self.assertEqual(code, 1, out)
        lines = out.splitlines()
        self.assertEqual(len(lines), 1, f"want one refusal line, got {out!r}")
        return lines[0]

    def prove(self, port="STATE_STORE", suite="statestore-conformance", env=None):
        self.assertEqual(self.call("trial", port, env=env)[0], 0)
        self.assertEqual(self.call("register", port, suite, env=env)[0], 0)


class HashTest(AdaptersCase):
    def digest(self, name="json-files"):
        code, out, _ = self.call("hash", str(self.outside / name))
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^[0-9a-f]{64}\n$")
        return out.strip()

    def test_same_bytes_same_hash_wherever_and_whenever(self):
        self.make_adapter("copy")
        os.utime(self.outside / "copy" / "get.sh", (1, 1))
        self.assertEqual(self.digest(), self.digest("copy"))

    def test_one_byte_changes_it(self):
        before = self.digest()
        (self.outside / "json-files" / "lib" / "x.py").write_text("print(2)\n")
        self.assertNotEqual(before, self.digest())

    def test_the_executable_bit_changes_it(self):
        before = self.digest()
        f = self.outside / "json-files" / "get.sh"
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
        self.assertNotEqual(before, self.digest())

    def test_a_rename_changes_it(self):
        before = self.digest()
        (self.outside / "json-files" / "put.sh").rename(self.outside / "json-files" / "set.sh")
        self.assertNotEqual(before, self.digest())

    def test_a_new_empty_file_changes_it(self):
        before = self.digest()
        (self.outside / "json-files" / ".hidden").write_text("")
        self.assertNotEqual(before, self.digest())

    def test_a_symlink_in_the_tree_is_refused(self):
        os.symlink("/etc/passwd", self.outside / "json-files" / "link.sh")
        code, out, err = self.call("hash", str(self.outside / "json-files"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("link.sh", err)


class AdmitTest(AdaptersCase):
    def test_nothing_outside_is_nothing_to_do(self):
        env = dict(self.env, STATE_STORE="sqlite")
        env.pop("ADAPTERS_PATH")
        self.assertTrue(self.admitted(env))
        self.assertFalse(Path("adapters/outside").exists())

    def test_without_a_proof_it_is_refused_in_one_line(self):
        line = self.refusal()
        self.assertIn("STATE_STORE=outside/json-files", line)
        self.assertIn("refused", line)
        self.assertIn("statestore-conformance", line)
        self.assertIn("./go statestore-conformance STATE_STORE=outside/json-files", line)
        self.assertFalse(Path("adapters/outside/json-files").exists())

    def test_after_its_suite_it_is_admitted_and_linked(self):
        self.prove()
        self.assertTrue(self.admitted())
        link = Path("adapters/outside/json-files")
        self.assertTrue(link.is_symlink())
        self.assertEqual(Path(os.readlink(link)), self.outside / "json-files")
        self.assertTrue((link / "get.sh").is_file())

    def test_one_changed_byte_is_refused_again(self):
        self.prove()
        (self.outside / "json-files" / "get.sh").write_text("#!/bin/sh\necho changed\n")
        self.assertIn("refused", self.refusal())

    def test_a_proof_against_another_suite_file_admits_nothing(self):
        self.prove()
        Path("gates/statestore-conformance.sh").write_text("# a ninth case\n")
        self.assertIn("refused", self.refusal())

    def test_a_refusal_removes_the_link(self):
        self.prove()
        self.assertTrue(self.admitted())
        (self.outside / "json-files" / "get.sh").write_text("#!/bin/sh\necho changed\n")
        self.refusal()
        self.assertFalse(Path("adapters/outside/json-files").is_symlink())

    def test_the_proof_is_of_its_port(self):
        self.prove()
        env = dict(self.env, STATE_STORE="sqlite", EVENT_BUS="outside/json-files")
        self.assertIn("EVENT_BUS=outside/json-files", self.refusal(env))

    def test_a_port_without_a_suite_is_refused(self):
        for port in ("ORCHESTRATOR", "METRICS"):
            env = dict(self.env, STATE_STORE="sqlite", **{port: "outside/json-files"})
            line = self.refusal(env)
            self.assertIn(f"{port}=outside/json-files", line)
            self.assertIn("no conformance suite", line)

    def test_every_port_with_a_suite_can_be_admitted(self):
        suites = {"CODE_HOST": "codehost-conformance", "DELIVERY": "delivery-conformance",
                  "STATE_STORE": "statestore-conformance", "EVENT_BUS": "eventbus-conformance",
                  "TRACKER": "tracker-conformance", "AGENT_HARNESS": "harness-conformance",
                  "REGISTRY": "registry-conformance", "FLAGS": "flags-conformance"}
        for port, suite in suites.items():
            env = dict(self.env, STATE_STORE="sqlite")
            env[port] = "outside/json-files"
            self.assertIn(suite, self.refusal(env))
            self.prove(port, suite, env)
            self.assertTrue(self.admitted(env), port)

    def test_adapters_path_must_be_an_absolute_directory(self):
        for value in (None, "", "relative/dir", str(self.root / "missing")):
            env = dict(self.env)
            if value is None:
                env.pop("ADAPTERS_PATH")
            else:
                env["ADAPTERS_PATH"] = value
            self.assertIn("ADAPTERS_PATH", self.refusal(env))

    def test_a_name_that_is_not_a_plain_name_is_refused(self):
        for value in ("outside/../json-files", "outside/Json", "outside/", "outside/a/b", "outside/-x"):
            self.assertIn("refused", self.refusal(dict(self.env, STATE_STORE=value)))

    def test_a_missing_adapter_is_refused(self):
        self.assertIn("refused", self.refusal(dict(self.env, STATE_STORE="outside/absent")))

    def test_a_symlink_inside_the_adapter_is_refused_even_when_it_was_proven(self):
        self.prove()
        os.symlink("/etc/passwd", self.outside / "json-files" / "link.sh")
        self.assertIn("refused", self.refusal())

    def test_a_directory_in_the_link_place_is_refused(self):
        self.prove()
        Path("adapters/outside/json-files").unlink()
        Path("adapters/outside/json-files").mkdir()
        self.assertIn("adapters/outside/json-files", self.refusal())

    def test_a_stale_link_is_replaced(self):
        self.prove()
        Path("adapters/outside/json-files").unlink()
        os.symlink(str(self.root), "adapters/outside/json-files")
        self.assertTrue(self.admitted())
        self.assertEqual(Path(os.readlink("adapters/outside/json-files")), self.outside / "json-files")

    def test_the_first_refusal_is_the_one_line(self):
        env = dict(self.env, CODE_HOST="outside/json-files")
        self.assertEqual(len(self.refusal(env).splitlines()), 1)

    def test_the_register_is_where_ADAPTERS_REGISTER_says(self):
        env = dict(self.env, ADAPTERS_REGISTER=str(self.root / "elsewhere" / "register"))
        self.prove(env=env)
        self.assertTrue((self.root / "elsewhere" / "register").is_file())
        self.assertTrue(self.admitted(env))
        self.assertIn("refused", self.refusal())

    def test_a_hand_written_register_line_for_other_bytes_admits_nothing(self):
        reg = Path(".crew/adapters/register")
        reg.parent.mkdir(parents=True)
        reg.write_text("STATE_STORE\tjson-files\t" + "0" * 64 + "\tstatestore-conformance\t2026-11-13T00:00:00Z\n")
        self.assertIn("refused", self.refusal())


class TrialTest(AdaptersCase):
    def test_trial_links_without_a_proof_and_admit_still_refuses_outside_the_trial(self):
        self.assertEqual(self.call("trial", "STATE_STORE")[0], 0)
        self.assertTrue(Path("adapters/outside/json-files").is_symlink())
        self.assertIn("refused", self.refusal())

    def test_during_the_trial_its_own_port_is_admitted(self):
        self.assertEqual(self.call("trial", "STATE_STORE")[0], 0)
        self.assertTrue(self.admitted(dict(self.env, ADAPTERS_TRIAL="STATE_STORE")))
        other = dict(self.env, ADAPTERS_TRIAL="STATE_STORE", CODE_HOST="outside/json-files")
        self.assertIn("CODE_HOST=outside/json-files", self.refusal(other))

    def test_a_trial_on_changed_bytes_admits_nothing(self):
        self.assertEqual(self.call("trial", "STATE_STORE")[0], 0)
        (self.outside / "json-files" / "get.sh").write_text("#!/bin/sh\necho changed\n")
        self.assertIn("refused", self.refusal(dict(self.env, ADAPTERS_TRIAL="STATE_STORE")))

    def test_untrial_ends_the_trial(self):
        self.assertEqual(self.call("trial", "STATE_STORE")[0], 0)
        self.assertEqual(self.call("untrial", "STATE_STORE")[:2], (0, ""))
        self.assertIn("refused", self.refusal(dict(self.env, ADAPTERS_TRIAL="STATE_STORE")))
        self.assertEqual(self.call("register", "STATE_STORE", "statestore-conformance")[0], 1)

    def test_register_without_a_trial_records_nothing(self):
        code, out, err = self.call("register", "STATE_STORE", "statestore-conformance")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())
        self.assertIn("refused", self.refusal())

    def test_bytes_changed_during_the_suite_are_not_registered(self):
        self.assertEqual(self.call("trial", "STATE_STORE")[0], 0)
        (self.outside / "json-files" / "get.sh").write_text("#!/bin/sh\necho changed\n")
        self.assertEqual(self.call("register", "STATE_STORE", "statestore-conformance")[0], 1)
        self.assertIn("refused", self.refusal())

    def test_register_names_the_suite_and_the_hash(self):
        self.prove()
        _, digest, _ = self.call("hash", str(self.outside / "json-files"))
        fields = Path(".crew/adapters/register").read_text().splitlines()[-1].split("\t")
        suite = hashlib.sha256(Path("gates/statestore-conformance.sh").read_bytes()).hexdigest()[:12]
        self.assertEqual(fields[:4], ["STATE_STORE", "json-files", digest.strip(), f"statestore-conformance@{suite}"])
        self.assertRegex(fields[4], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_register_with_another_ports_suite_records_nothing(self):
        self.assertEqual(self.call("trial", "STATE_STORE")[0], 0)
        self.assertEqual(self.call("register", "STATE_STORE", "codehost-conformance")[0], 1)
        self.assertIn("refused", self.refusal())

    def test_an_adapter_of_ours_is_nothing_to_try_or_register(self):
        env = dict(self.env, STATE_STORE="sqlite")
        self.assertEqual(self.call("trial", "STATE_STORE", env=env)[:2], (0, ""))
        self.assertEqual(self.call("register", "STATE_STORE", "statestore-conformance", env=env)[:2], (0, ""))
        self.assertFalse(Path(".crew/adapters/register").exists())

    def test_trial_of_a_port_without_a_suite_is_refused(self):
        env = dict(self.env, ORCHESTRATOR="outside/json-files")
        code, out, err = self.call("trial", "ORCHESTRATOR", env=env)
        self.assertEqual(code, 1)
        self.assertIn("no conformance suite", err)

    def test_tracker_can_be_tried_registered_and_admitted(self):
        env = dict(self.env, STATE_STORE="sqlite", TRACKER="outside/json-files")
        self.assertIn("tracker-conformance", self.refusal(env))
        self.prove("TRACKER", "tracker-conformance", env)
        self.assertTrue(self.admitted(env))


if __name__ == "__main__":
    unittest.main()
