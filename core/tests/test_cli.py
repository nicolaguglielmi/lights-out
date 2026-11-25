"""Spec-derived tests for the unified power-user CLI (docs/adr/0112).

Written from the ADR, before core/cli.py: the grammar table, flag translation,
exit-code taxonomy, `--json`/`--jsonl` envelopes, the secret rules and the
completion scripts. Stdlib only, no engine, no network; the dispatch target is
always an injected fake runner or `--dry-run`.

Vendor-free, app-free and model-free (ci/vendor-free.sh, ci/app-free.sh,
ci/model-free.sh scan core/tests too): the only secret-shaped names used here
are synthetic.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from core import cli

ROOT = Path(__file__).resolve().parents[2]

SPEC = {
    "repo": ["init", "try", "gates", "test-proof", "test-integrity", "adr-check", "migration-safety",
             "doctor", "map", "corpus", "genesis", "map-brief-eval"],
    "ticket": ["intake", "request", "answer", "intake-confirm", "assign", "resume", "network", "audit", "epics",
               "spec-split"],
    "release": ["up", "down", "smoke", "deploy", "promote", "evidence", "provenance", "production-readiness"],
    "ops": ["watch", "alert-webhook", "triage", "auto-incident", "incident", "postmortem", "dora", "spend", "top",
            "charts-conformance", "metrics-conformance", "orchestrator-conformance", "m24-check", "demo-loop",
            "reviewer-eval", "portfolio"],
    "config": ["start", "next", "cloud-setup", "scoreboard", "agents", "tracker-conformance", "console-build",
               "jury", "live-preflight", "thresholds"],
}
SHELLS = ["bash", "zsh", "fish"]
GATE_SHAPED = {"init", "try", "gates", "test-proof", "test-integrity", "adr-check", "migration-safety",
               "doctor", "map", "corpus", "production-readiness", "tracker-conformance", "charts-conformance",
               "metrics-conformance", "orchestrator-conformance"}
SECRET = "canary-secret-value-90210"


class FakeRunner:
    """Stands in for the subprocess: records argv/env, replays given stdout lines."""

    def __init__(self, lines=(), rc=0):
        self.lines = list(lines)
        self.rc = rc
        self.calls = []

    def __call__(self, argv, env, on_line):
        self.calls.append((list(argv), dict(env)))
        for line in self.lines:
            on_line(line)
        return self.rc


def run_main(argv, env=None, runner=None, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    rc = cli.main(argv, env=dict(env or {}), runner=runner or FakeRunner(["GO: fake"]),
                  stdin=io.StringIO(stdin), stdout=out, stderr=err)
    return rc, out.getvalue(), err.getvalue()


class GrammarTests(unittest.TestCase):
    """ADR 0112 §1: six domains, each verb one flat command."""

    def test_domains_and_verbs_are_the_adr_table(self):
        self.assertEqual(list(cli.DOMAINS), [*SPEC, "completion"])
        for domain, verbs in SPEC.items():
            got = [c.verb for c in cli.COMMANDS if c.domain == domain]
            self.assertEqual(got, verbs, domain)

    def test_every_verb_parses_to_its_flat_command(self):
        for domain, verbs in SPEC.items():
            for verb in verbs:
                plan = cli.parse([domain, verb])
                flat = "release" if (domain, verb) == ("release", "deploy") else verb
                self.assertEqual(plan.command.flat, flat, f"{domain} {verb}")
                self.assertEqual(plan.command.domain, domain)

    def test_flat_commands_stay_valid_and_equal_the_domain_spelling(self):
        for domain, verbs in SPEC.items():
            for verb in verbs:
                flat = "release" if (domain, verb) == ("release", "deploy") else verb
                self.assertEqual(cli.parse([flat]).command, cli.parse([domain, verb]).command, flat)

    def test_release_is_a_domain_only_when_a_verb_follows(self):
        self.assertEqual(cli.parse(["release"]).command.verb, "deploy")
        self.assertEqual(cli.parse(["release", "CANARY_VERSION=2"]).command.verb, "deploy")
        self.assertEqual(cli.parse(["release", "--canary-version", "2"]).tail, ["CANARY_VERSION=2"])
        self.assertEqual(cli.parse(["release", "up"]).command.flat, "up")
        self.assertEqual(cli.parse(["release", "deploy"]).command.flat, "release")

    def test_domain_without_or_with_unknown_verb_is_usage_naming_the_verbs(self):
        for argv in (["ticket"], ["ticket", "frobnicate"]):
            with self.assertRaises(cli.UsageError) as cm:
                cli.parse(argv)
            for verb in SPEC["ticket"]:
                self.assertIn(verb, str(cm.exception))

    def test_unknown_command_is_usage(self):
        with self.assertRaises(cli.UsageError):
            cli.parse(["frobnicate"])

    def test_domain_help_lists_verbs_on_stdout_and_exits_zero(self):
        rc, out, err = run_main(["ops", "--help"])
        self.assertEqual(rc, 0)
        for verb in SPEC["ops"]:
            self.assertIn(verb, out)

    def test_top_level_help_names_every_domain(self):
        for argv in ([], ["help"], ["--help"]):
            rc, out, _ = run_main(argv)
            self.assertEqual(rc, 0)
            for domain in cli.DOMAINS:
                self.assertIn(domain, out)

    def test_clone_only_commands_are_marked(self):
        clone_only = {c.flat for c in cli.COMMANDS if c.surface == "clone"}
        self.assertEqual(
            clone_only,
            {"corpus", "agents", "tracker-conformance", "charts-conformance", "metrics-conformance",
             "orchestrator-conformance", "console-build"},
        )


class FlagTranslationTests(unittest.TestCase):
    """ADR 0112 §2: POSIX flags become KEY=VALUE; natives pass through."""

    def test_flag_forms_become_key_value(self):
        self.assertEqual(cli.parse(["ticket", "assign", "--ticket", "0001"]).tail, ["TICKET=0001"])
        self.assertEqual(cli.parse(["ticket", "assign", "--ticket=0001"]).tail, ["TICKET=0001"])
        self.assertEqual(cli.parse(["assign", "TICKET=0001"]).tail, ["TICKET=0001"])

    def test_value_may_contain_equals_and_spaces(self):
        plan = cli.parse(["ticket", "answer", "--ticket", "7", "--answer=a=b c"])
        self.assertEqual(plan.tail, ["TICKET=7", "ANSWER=a=b c"])

    def test_dashes_become_underscores_and_order_is_kept(self):
        plan = cli.parse(["ops", "watch", "--poll-s", "5", "--once"])
        self.assertEqual(plan.tail, ["POLL_S=5", "ONCE=1"])
        plan = cli.parse(["repo", "gates", "BASE=a", "--head", "b", "--staged"])
        self.assertEqual(plan.tail, ["BASE=a", "HEAD=b", "STAGED=1"])
        self.assertEqual(plan.pairs, {"BASE": "a", "HEAD": "b", "STAGED": "1"})

    def test_native_flags_pass_through_verbatim(self):
        plan = cli.parse(["config", "start", "--non-interactive", "--preset", "junior",
                          "--provider=replay", "--ticket-budget-usd", "2"])
        self.assertEqual(plan.tail, ["--non-interactive", "--preset", "junior",
                                     "--provider=replay", "--ticket-budget-usd", "2"])
        self.assertEqual(cli.parse(["config", "next", "--mode", "expert"]).tail, ["--mode", "expert"])
        self.assertEqual(cli.parse(["next", "MODE=junior"]).tail, ["MODE=junior"])
        self.assertEqual(cli.parse(["repo", "doctor", "--level", "l1"]).tail, ["--level", "l1"])

    def test_profile_is_native_for_start_and_a_key_elsewhere(self):
        self.assertEqual(cli.parse(["config", "start", "--profile", "l1"]).tail, ["--profile", "l1"])
        self.assertEqual(cli.parse(["ops", "triage", "--profile", "l1"]).tail, ["PROFILE=l1"])

    def test_init_and_flags_and_extension_keys(self):
        plan = cli.parse([
            "repo", "init", "../r", "--apply", "--branch", "init/lo", "--commit", "--force",
            "--baseline-mode", "incremental", "--baseline-max-files", "5",
        ])
        self.assertEqual(plan.positional, ["../r"])
        self.assertEqual(
            plan.tail,
            ["--apply", "--branch", "init/lo", "--commit", "--force",
             "--baseline-mode", "incremental", "--baseline-max-files", "5"],
        )
        with self.assertRaises(cli.UsageError):
            cli.parse(["repo", "gates", "--apply"])
        with self.assertRaises(cli.UsageError):
            cli.parse(["repo", "init", "--apply=1"])

        prov = cli.parse(["release", "provenance", "--version", "0.8.1", "--ticket", "0001", "--provenance-dir", ".crew/p"])
        self.assertEqual(prov.tail, ["VERSION=0.8.1", "TICKET=0001", "PROVENANCE_DIR=.crew/p"])
        self.assertEqual(cli.parse(["config", "console-build", "--check"]).tail, ["--check"])
        self.assertEqual(cli.parse(["config", "tracker-conformance", "--tracker", "file"]).tail, ["TRACKER=file"])
        self.assertEqual(cli.parse(["ops", "charts-conformance", "--charts", "grafana"]).tail, ["CHARTS=grafana"])
        self.assertEqual(cli.parse(["ops", "metrics-conformance", "--metrics", "promql"]).tail, ["METRICS=promql"])
        self.assertEqual(
            cli.parse(["ops", "orchestrator-conformance", "--orchestrator", "inproc"]).tail,
            ["ORCHESTRATOR=inproc"],
        )

    def test_engine_is_kept_for_the_gate_tier(self):
        for flat in ("gates", "adr-check", "migration-safety"):
            self.assertEqual(cli.parse(["repo", flat, "--engine"]).tail, ["--engine"], flat)
        with self.assertRaises(cli.UsageError):
            cli.parse(["release", "up", "--engine"])
        with self.assertRaises(cli.UsageError):
            cli.parse(["ops", "top", "--engine"])

    def test_unknown_flag_is_usage_naming_allowed_keys(self):
        with self.assertRaises(cli.UsageError) as cm:
            cli.parse(["ticket", "assign", "--bogus", "x"])
        self.assertIn("--bogus", str(cm.exception))
        self.assertIn("--ticket", str(cm.exception))

    def test_missing_value_is_usage(self):
        for argv in (["ticket", "assign", "--ticket"], ["config", "start", "--preset"]):
            with self.assertRaises(cli.UsageError):
                cli.parse(argv)

    def test_extension_keys_belong_to_their_commands_only(self):
        self.assertEqual(cli.parse(["ticket", "request", "--request-by", "ann"]).tail, ["REQUEST_BY=ann"])
        self.assertEqual(cli.parse(["ticket", "network", "--attempt", "2"]).tail, ["ATTEMPT=2"])
        self.assertEqual(cli.parse(["ops", "alert-webhook", "--alert-file", "a.json"]).tail, ["ALERT_FILE=a.json"])
        self.assertEqual(cli.parse(["ops", "auto-incident", "--release-record", "r"]).tail, ["RELEASE_RECORD=r"])
        self.assertEqual(cli.parse(["repo", "map", "--map-file", "m.json"]).tail, ["MAP_FILE=m.json"])
        self.assertEqual(cli.parse(["config", "cloud-setup", "--cloud", "x", "--slo-mode", "otel"]).tail,
                         ["CLOUD=x", "SLO_MODE=otel"])
        plan = cli.parse(["ops", "incident", "--name", "n", "--from", "a", "--to", "b", "--logs", "l"])
        self.assertEqual(plan.tail, ["NAME=n", "FROM=a", "TO=b", "LOGS=l"])
        self.assertEqual(
            cli.parse(["repo", "migration-safety", "--ticket-file", "tickets/0001.md"]).tail,
            ["TICKET_FILE=tickets/0001.md"],
        )
        top_plan = cli.parse(["ops", "top", "--logbook-file", "l.json", "--url", "http://127.0.0.1:8098",
                              "--view", "canary", "--width", "100", "--height", "30", "--once"])
        self.assertEqual(top_plan.tail, [
            "LOGBOOK_FILE=l.json", "URL=http://127.0.0.1:8098", "VIEW=canary", "WIDTH=100", "HEIGHT=30", "ONCE=1",
        ])
        with self.assertRaises(cli.UsageError):
            cli.parse(["ticket", "assign", "--request-by", "ann"])

    def test_global_keys_are_the_29_of_adr_0105(self):
        self.assertEqual(len(cli.GLOBAL_KEYS), 29)
        self.assertEqual(len(set(cli.GLOBAL_KEYS)), 29)
        for key in ("BASE", "HEAD", "TICKET", "SPEND_BUDGET_DAY_USD", "ADAPTERS_PATH"):
            self.assertIn(key, cli.GLOBAL_KEYS)

    def test_key_value_pass_through_keeps_flat_back_compat(self):
        self.assertEqual(cli.parse(["gates", "EVENT_BUS=spool"]).tail, ["EVENT_BUS=spool"])
        with self.assertRaises(cli.UsageError):
            cli.parse(["gates", "1BAD=x"])

    def test_positional_path_only_for_init_and_try(self):
        self.assertEqual(cli.parse(["repo", "try", "../r"]).positional, ["../r"])
        self.assertEqual(cli.parse(["init", "../r"]).positional, ["../r"])
        with self.assertRaises(cli.UsageError):
            cli.parse(["repo", "try", "a", "b"])
        with self.assertRaises(cli.UsageError):
            cli.parse(["ticket", "assign", "0001"])


class SecretRuleTests(unittest.TestCase):
    """ADR 0112 §2: secrets are env names, never arguments."""

    def test_secret_named_keys_are_refused_without_echoing_the_value(self):
        for suffix in ("KEY", "TOKEN", "SECRET", "PASSWORD", "HEADER"):
            name = f"DEMO_SERVICE_{suffix}"
            for argv in (["ticket", "assign", f"{name}={SECRET}"],
                         ["ticket", "assign", f"--{name.lower().replace('_', '-')}", SECRET],
                         ["ticket", "assign", f"--{name.lower().replace('_', '-')}={SECRET}"]):
                with self.assertRaises(cli.UsageError) as cm:
                    cli.parse(argv)
                self.assertNotIn(SECRET, str(cm.exception), argv)

    def test_main_refuses_secret_argument_with_empty_stdout(self):
        rc, out, err = run_main(["ticket", "assign", f"DEMO_SERVICE_TOKEN={SECRET}"])
        self.assertEqual((rc, out), (2, ""))
        self.assertNotIn(SECRET, err)


class JsonModeTests(unittest.TestCase):
    """ADR 0112 §4–§5: who may take --json and --jsonl."""

    def test_gate_shaped_commands_refuse_json_and_jsonl(self):
        for command in cli.COMMANDS:
            if command.flat not in GATE_SHAPED:
                continue
            for flag in ("--json", "--jsonl"):
                with self.assertRaises(cli.UsageError, msg=f"{command.flat} {flag}"):
                    cli.parse([command.domain, command.verb, flag])
                with self.assertRaises(cli.UsageError, msg=f"flat {command.flat} {flag}"):
                    cli.parse([flag, command.flat])

    def test_non_gate_commands_accept_either_json_flag_anywhere(self):
        for command in cli.COMMANDS:
            if command.flat in GATE_SHAPED or command.flat in ("start", "next", "top"):
                continue
            self.assertEqual(cli.parse([command.domain, command.verb, "--json"]).json_mode, "json", command.flat)
            self.assertEqual(cli.parse(["--jsonl", command.domain, command.verb]).json_mode, "jsonl", command.flat)

    def test_json_flags_are_not_forwarded_to_the_command(self):
        plan = cli.parse(["ticket", "assign", "--json", "--ticket", "1"])
        self.assertEqual(plan.tail, ["TICKET=1"])

    def test_both_json_flags_together_are_usage(self):
        with self.assertRaises(cli.UsageError):
            cli.parse(["ticket", "assign", "--json", "--jsonl"])

    def test_start_and_next_keep_their_native_json(self):
        for domain, flat in (("config", "start"), ("config", "next"), ("ops", "top")):
            plan = cli.parse([domain, flat, "--json"])
            self.assertEqual(plan.json_mode, "")
            self.assertEqual(plan.tail, ["--json"])
            with self.assertRaises(cli.UsageError):
                cli.parse([domain, flat, "--jsonl"])


class ExitCodeTests(unittest.TestCase):
    """ADR 0112 §3: the taxonomy 0 GO, 1 NO-GO, 2 usage, 3 waiting, 4 budget."""

    def test_constants(self):
        self.assertEqual((cli.EXIT_GO, cli.EXIT_NOGO, cli.EXIT_USAGE, cli.EXIT_WAITING, cli.EXIT_BUDGET),
                         (0, 1, 2, 3, 4))

    def test_go_and_nogo(self):
        self.assertEqual(cli.classify(0, "GO: assign", False), ("GO", 0))
        self.assertEqual(cli.classify(1, "NO-GO: assign — x", False), ("NO-GO", 1))
        self.assertEqual(cli.classify(0, "some report text", False), ("GO", 0))

    def test_contradictions_are_nogo(self):
        self.assertEqual(cli.classify(0, "NO-GO: assign — x", False), ("NO-GO", 1))
        self.assertEqual(cli.classify(1, "GO: assign", False), ("NO-GO", 1))
        self.assertEqual(cli.classify(1, "", False), ("NO-GO", 1))

    def test_make_exit_two_is_a_failed_recipe_not_a_usage_error(self):
        self.assertEqual(cli.classify(2, "NO-GO: x — failed", False), ("NO-GO", 1))
        self.assertEqual(cli.classify(2, "", False), ("NO-GO", 1))

    def test_waiting_markers(self):
        for reason in ("ticket 0001 is needs-human", "awaiting the answer", "waiting for the launcher",
                       "waiting on review", "canary held at 25%", "another runner holds lockfile .crew/r.lock",
                       "the plane is locked"):
            self.assertEqual(cli.classify(1, f"NO-GO: watch — {reason}", False), ("WAITING", 3), reason)

    def test_budget_markers_win_over_waiting(self):
        for reason in ("SPEND_BUDGET_TICKET_USD exceeded", "over budget", "stopped: SPEND_STOP set"):
            self.assertEqual(cli.classify(1, f"NO-GO: assign — {reason}", False), ("BUDGET", 4), reason)
        self.assertEqual(cli.classify(1, "NO-GO: assign — needs-human after budget stop", False), ("BUDGET", 4))

    def test_only_nogo_lines_are_classified_wait_or_budget(self):
        self.assertEqual(cli.classify(0, "GO: spend — within budget, nothing held at", False), ("GO", 0))
        self.assertEqual(cli.classify(1, "plain failure about a budget", False), ("NO-GO", 1))

    def test_gate_shaped_commands_stay_two_valued(self):
        self.assertEqual(cli.classify(1, "NO-GO: gates — over budget", True), ("NO-GO", 1))
        self.assertEqual(cli.classify(1, "NO-GO: gates — waiting for a lock", True), ("NO-GO", 1))
        self.assertEqual(cli.classify(0, "GO: gates", True), ("GO", 0))


class EnvelopeTests(unittest.TestCase):
    """ADR 0112 §4: the envelope, and never a secret value."""

    def setUp(self):
        self.plan = cli.parse(["ticket", "assign", "--ticket", "0001"])

    def test_envelope_fields(self):
        env = cli.envelope(self.plan, 0, ["noise", "GO: assign"], {})
        self.assertEqual(env["schema"], "lights-out.cli/1")
        self.assertEqual(env["command"], "ticket assign")
        self.assertEqual(env["flat"], "assign")
        self.assertEqual(env["verdict"], "GO")
        self.assertEqual(env["exit_code"], 0)
        self.assertEqual(env["line"], "GO: assign")
        self.assertEqual(env["args"], {"TICKET": "0001"})
        self.assertEqual(env["secrets"], {})

    def test_no_stdout_gives_an_empty_line(self):
        self.assertEqual(cli.envelope(self.plan, 1, [], {})["line"], "")

    def test_secret_presence_is_boolean_by_name_only(self):
        env = cli.envelope(self.plan, 0, ["GO: assign"], {
            "DEMO_SERVICE_TOKEN": SECRET, "OTHER_SERVICE_KEY": "", "UNRELATED": "plain",
        })
        self.assertEqual(env["secrets"], {"DEMO_SERVICE_TOKEN": True, "OTHER_SERVICE_KEY": False})
        self.assertNotIn(SECRET, json.dumps(env))

    def test_listed_secret_names_report_false_when_unset(self):
        env = cli.envelope(self.plan, 0, ["GO: assign"], {"LIGHTS_OUT_SECRET_NAMES": "A_ONE B_TWO",
                                                          "B_TWO": SECRET})
        self.assertEqual(env["secrets"], {"A_ONE": False, "B_TWO": True})
        self.assertNotIn(SECRET, json.dumps(env))

    def test_secret_values_in_output_lines_are_redacted(self):
        env = cli.envelope(self.plan, 0, [f"GO: assign — used {SECRET}"], {"DEMO_SERVICE_TOKEN": SECRET})
        self.assertEqual(env["line"], "GO: assign — used <redacted>")

    def test_secret_values_in_args_are_redacted(self):
        plan = cli.parse(["ticket", "answer", "--ticket", "1", "--answer", SECRET])
        env = cli.envelope(plan, 0, ["GO: answer"], {"DEMO_SERVICE_TOKEN": SECRET})
        self.assertEqual(env["args"]["ANSWER"], "<redacted>")
        self.assertNotIn(SECRET, json.dumps(env))

    def test_short_secret_values_are_not_redacted(self):
        env = cli.envelope(self.plan, 0, ["GO: assign — on"], {"DEMO_SERVICE_TOKEN": "on"})
        self.assertEqual(env["line"], "GO: assign — on")
        self.assertEqual(env["secrets"], {"DEMO_SERVICE_TOKEN": True})

    def test_verdict_names_follow_the_taxonomy(self):
        for rc, line, verdict, code in (
            (1, "NO-GO: assign — x", "NO-GO", 1),
            (1, "NO-GO: assign — needs-human", "WAITING", 3),
            (1, "NO-GO: assign — over budget", "BUDGET", 4),
        ):
            env = cli.envelope(self.plan, rc, [line], {})
            self.assertEqual((env["verdict"], env["exit_code"]), (verdict, code))


class MainTests(unittest.TestCase):
    """The dispatcher: argv to the runner, output modes, exit codes."""

    def test_dispatches_to_go_with_the_flat_command_and_translated_args(self):
        runner = FakeRunner(["GO: assign"])
        rc, out, _ = run_main(["ticket", "assign", "--ticket", "0001"], runner=runner)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "GO: assign\n")
        argv, _ = runner.calls[0]
        self.assertEqual(argv[1:], ["assign", "TICKET=0001"])
        self.assertTrue(argv[0].endswith("go"))

    def test_go_location_can_be_overridden_and_defaults_to_the_checkout(self):
        runner = FakeRunner(["GO: spend"])
        run_main(["ops", "spend"], env={"LIGHTS_OUT_GO": "/opt/x/go"}, runner=runner)
        self.assertEqual(runner.calls[0][0][0], "/opt/x/go")
        self.assertEqual(cli.go_argv(cli.parse(["ops", "spend"]), go="g"), ["g", "spend"])
        self.assertEqual(os.path.realpath(cli.default_go()), str(ROOT / "go"))

    def test_positional_path_goes_first_and_engine_is_dropped(self):
        self.assertEqual(cli.go_argv(cli.parse(["repo", "try", "../r"]), go="g"), ["g", "try", "../r"])
        self.assertEqual(cli.go_argv(cli.parse(["repo", "gates", "--engine", "BASE=a"]), go="g"),
                         ["g", "gates", "BASE=a"])

    def test_native_flags_reach_go_in_order(self):
        argv = cli.go_argv(cli.parse(["config", "start", "--non-interactive", "PRESET=x", "--json"]), go="g")
        self.assertEqual(argv, ["g", "start", "--non-interactive", "PRESET=x", "--json"])

    def test_clone_only_and_every_flat_name_dispatch(self):
        for command in cli.COMMANDS:
            plan = cli.parse([command.domain, command.verb])
            self.assertEqual(cli.go_argv(plan, go="g"), ["g", command.flat])

    def test_json_prints_exactly_one_envelope_line_and_hides_the_flag_from_the_runner(self):
        runner = FakeRunner(["[tower] chatter", "GO: assign"])
        rc, out, _ = run_main(["--json", "ticket", "assign", "--ticket", "0001"],
                              env={"DEMO_SERVICE_TOKEN": SECRET}, runner=runner)
        self.assertEqual(rc, 0)
        self.assertEqual(len(out.splitlines()), 1)
        env = json.loads(out)
        self.assertEqual((env["verdict"], env["line"], env["args"]), ("GO", "GO: assign", {"TICKET": "0001"}))
        self.assertEqual(env["secrets"], {"DEMO_SERVICE_TOKEN": True})
        self.assertNotIn(SECRET, out)
        self.assertNotIn("--json", runner.calls[0][0])

    def test_jsonl_streams_stdout_events_then_a_verdict(self):
        runner = FakeRunner(["one", "two", "NO-GO: watch — another runner holds lockfile x"], rc=1)
        rc, out, _ = run_main(["ops", "watch", "--jsonl", "--once"], runner=runner)
        self.assertEqual(rc, 3)
        events = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([e["event"] for e in events], ["stdout", "stdout", "stdout", "verdict"])
        self.assertEqual([e["line"] for e in events[:2]], ["one", "two"])
        self.assertEqual(events[-1]["verdict"], "WAITING")
        self.assertEqual(events[-1]["exit_code"], 3)

    def test_raw_mode_echoes_the_command_and_maps_the_exit_code(self):
        rc, out, _ = run_main(["ticket", "assign", "--ticket", "1"],
                              runner=FakeRunner(["a", "NO-GO: assign — over budget"], rc=1))
        self.assertEqual((rc, out), (4, "a\nNO-GO: assign — over budget\n"))
        rc, out, _ = run_main(["ticket", "resume", "--ticket", "1"], runner=FakeRunner(["GO: resume"]))
        self.assertEqual((rc, out), (0, "GO: resume\n"))

    def test_gate_shaped_raw_output_is_the_childs_one_line_and_two_valued(self):
        rc, out, _ = run_main(["repo", "gates"], runner=FakeRunner(["GO: gates"]))
        self.assertEqual((rc, out), (0, "GO: gates\n"))
        rc, out, _ = run_main(["gates"], runner=FakeRunner(["NO-GO: gates — over budget"], rc=1))
        self.assertEqual((rc, out), (1, "NO-GO: gates — over budget\n"))

    def test_usage_error_exits_two_with_empty_stdout(self):
        for argv in (["ticket"], ["frobnicate"], ["ticket", "assign", "--bogus", "x"],
                     ["repo", "gates", "--json"], ["repo", "gates", "--jsonl"]):
            runner = FakeRunner()
            rc, out, err = run_main(argv, runner=runner)
            self.assertEqual((rc, out), (2, ""), argv)
            self.assertTrue(err.strip(), argv)
            self.assertEqual(runner.calls, [], argv)

    def test_usage_error_under_json_prints_a_usage_envelope(self):
        rc, out, err = run_main(["--json", "ticket", "assign", "--bogus", "x"])
        self.assertEqual(rc, 2)
        self.assertEqual(len(out.splitlines()), 1)
        env = json.loads(out)
        self.assertEqual((env["verdict"], env["exit_code"]), ("USAGE", 2))
        self.assertIn("--bogus", env["error"])
        self.assertTrue(err.strip())

    def test_dry_run_prints_the_plan_and_runs_nothing(self):
        runner = FakeRunner()
        rc, out, _ = run_main(["--dry-run", "ticket", "assign", "--ticket", "0001"],
                              env={"LIGHTS_OUT_GO": "g"}, runner=runner)
        self.assertEqual(rc, 0)
        self.assertEqual(runner.calls, [])
        plan = json.loads(out)
        self.assertEqual(plan["argv"], ["g", "assign", "TICKET=0001"])
        self.assertEqual((plan["command"], plan["flat"], plan["json"]), ("ticket assign", "assign", ""))

    def test_runner_failure_to_start_is_a_nogo(self):
        def broken(argv, env, on_line):
            raise OSError("no such file")
        rc, out, err = run_main(["ticket", "assign", "--ticket", "1"], runner=broken)
        self.assertEqual(rc, 1)
        self.assertIn("no such file", err)

    def test_real_runner_streams_a_child_process(self):
        lines = []
        rc = cli.subprocess_runner([sys.executable, "-c", "print('GO: x'); print('b')"],
                                   dict(os.environ), lines.append)
        self.assertEqual((rc, lines), (0, ["GO: x", "b"]))


class EnvelopeVerbTests(unittest.TestCase):
    """`envelope`: wraps a command's stdout (stdin) for the entrypoint."""

    def test_wraps_stdin_in_one_envelope_and_exits_by_taxonomy(self):
        rc, out, _ = run_main(["envelope", "--command", "assign", "--rc", "1", "TICKET=0001"],
                              stdin="[x] noise\nNO-GO: assign — over budget\n")
        self.assertEqual(rc, 4)
        env = json.loads(out)
        self.assertEqual((env["command"], env["flat"], env["verdict"]), ("ticket assign", "assign", "BUDGET"))
        self.assertEqual(env["args"], {"TICKET": "0001"})

    def test_jsonl_variant(self):
        rc, out, _ = run_main(["envelope", "--jsonl", "--command", "dora", "--rc", "0"], stdin="GO: dora\n")
        self.assertEqual(rc, 0)
        events = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([e["event"] for e in events], ["stdout", "verdict"])

    def test_unknown_command_and_missing_rc_are_usage(self):
        self.assertEqual(run_main(["envelope", "--command", "nope", "--rc", "0"])[0], 2)
        self.assertEqual(run_main(["envelope", "--command", "assign"])[0], 2)
        self.assertEqual(run_main(["envelope", "--command", "gates", "--rc", "0"])[0], 2)

    def test_double_dash_stops_envelope_option_parsing(self):
        rc, out, _ = run_main(["envelope", "--command", "answer", "--rc", "0", "--", "--answer", "--command"],
                              stdin="GO: answer\n")
        self.assertEqual(rc, 0)
        env = json.loads(out)
        self.assertEqual((env["command"], env["args"]), ("ticket answer", {"ANSWER": "--command"}))


class CompletionTests(unittest.TestCase):
    """ADR 0112 §7."""

    def test_each_shell_completes_domains_verbs_flags_and_itself(self):
        for shell in SHELLS:
            rc, out, _ = run_main(["completion", shell])
            self.assertEqual(rc, 0, shell)
            self.assertTrue(out.endswith("\n"))
            for domain, verbs in SPEC.items():
                self.assertIn(domain, out, shell)
                for verb in verbs:
                    self.assertIn(verb, out, f"{shell} {verb}")
            for word in ("--json", "--jsonl", "--ticket", "--base", "--non-interactive"):
                self.assertIn(word.lstrip("-") if shell == "fish" else word, out, f"{shell} {word}")
                if shell == "fish":
                    self.assertIn(f"-l {word[2:]}", out)
            for word in ("bash", "zsh", "fish"):
                self.assertIn(word, out, f"{shell} {word}")
        self.assertIn("complete -F _lights_out lights-out", cli.completion("bash"))
        self.assertTrue(cli.completion("zsh").startswith("#compdef lights-out"))
        self.assertIn("complete -c lights-out", cli.completion("fish"))

    def test_scripts_are_deterministic_and_carry_no_environment(self):
        for shell in SHELLS:
            self.assertEqual(cli.completion(shell),
                             run_main(["completion", shell], env={"DEMO_SERVICE_TOKEN": SECRET})[1])
            self.assertNotIn(SECRET, cli.completion(shell))

    def test_unknown_shell_or_missing_shell_is_usage(self):
        for argv in (["completion"], ["completion", "tcsh"]):
            rc, out, err = run_main(argv)
            self.assertEqual((rc, out), (2, ""), argv)
            for shell in SHELLS:
                self.assertIn(shell, err)

    def test_top_words_cover_domains_and_flat_commands(self):
        words = cli.top_words()
        for domain in cli.DOMAINS:
            self.assertIn(domain, words)
        for command in cli.COMMANDS:
            self.assertIn(command.flat, words)
        self.assertEqual(len(words), len(set(words)))


class ScriptEntryTests(unittest.TestCase):
    """`python3 core/cli.py` and `sh core/cli.sh` work as scripts from a clone."""

    def _run(self, *cmd):
        return subprocess.run(list(cmd), cwd=ROOT, capture_output=True, text=True, check=False,
                              env={**os.environ, "LIGHTS_OUT_GO": "g"})

    def test_python_script_dry_run(self):
        res = self._run(sys.executable, "core/cli.py", "--dry-run", "ticket", "assign", "--ticket", "1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["argv"], ["g", "assign", "TICKET=1"])

    def test_module_usage_error_exit_two(self):
        res = self._run(sys.executable, "-m", "core.cli", "ticket")
        self.assertEqual((res.returncode, res.stdout), (2, ""))

    def test_shell_launcher_is_thin_and_works(self):
        launcher = ROOT / "core" / "cli.sh"
        code = [ln for ln in launcher.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
        self.assertLessEqual(len(code), 30)
        res = self._run("sh", "core/cli.sh", "--dry-run", "ops", "spend")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["argv"], ["g", "spend"])
        res = self._run("sh", "core/cli.sh", "completion", "bash")
        self.assertEqual(res.stdout, cli.completion("bash"))


class SourcePerimeterTests(unittest.TestCase):
    """The meta-gates scan core/: the new module must not trip them.

    The banned words are read from the gate scripts themselves, so this file
    names none of them.
    """

    @staticmethod
    def _pattern(script, var):
        text = (ROOT / "ci" / script).read_text(encoding="utf-8")
        match = re.search(rf"^{var}='(.*)'$", text, re.M)
        assert match, f"{var} not found in ci/{script}"
        return match.group(1)

    def test_cli_source_is_vendor_app_and_model_free(self):
        for script, var in (("vendor-free.sh", "words"), ("app-free.sh", "words"),
                            ("model-free.sh", "pattern")):
            pattern = self._pattern(script, var)
            for rel in ("core/cli.py", "core/cli.sh", "core/tests/test_cli.py"):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertIsNone(re.search(pattern, text, re.I | re.M), f"{rel} vs ci/{script}")


class LaterCommandsCLITests(unittest.TestCase):
    def test_workspace_genesis_and_epics_commands_parse(self):
        p_init = cli.parse(["repo", "init", "--workspace", "--apply"])
        self.assertEqual(p_init.command.flat, "init")
        self.assertEqual(p_init.tail, ["--workspace", "--apply"])

        p_gen = cli.parse(["repo", "genesis", "--brief", "todo api", "--stack", "fastapi-postgres", "--no-commit", "."])
        self.assertEqual(p_gen.command.flat, "genesis")
        self.assertEqual(p_gen.positional, ["."])
        self.assertIn("--no-commit", p_gen.tail)

        p_ep = cli.parse(["ticket", "epics", "--id", "E1", "--title", "Core", "--baseline-mode", "incremental", "0001"])
        self.assertEqual(p_ep.command.flat, "epics")
        self.assertEqual(p_ep.positional, ["0001"])
        self.assertIn("--id", p_ep.tail)

        for sh_name in SHELLS:
            comp = cli.completion(sh_name)
            self.assertIn("genesis", comp)
            self.assertIn("epics", comp)
            flag_prefix = "-l " if sh_name == "fish" else "--"
            self.assertIn(f"{flag_prefix}workspace", comp)
            self.assertIn(f"{flag_prefix}no-publish", comp)

    def test_spec_split_m24_and_watch_commands_parse(self):
        p_ss = cli.parse(["ticket", "spec-split", "split", "tickets/0012.md", "--in-place"])
        self.assertEqual(p_ss.command.flat, "spec-split")
        self.assertEqual(p_ss.positional, ["split", "tickets/0012.md"])
        self.assertEqual(p_ss.tail, ["--in-place"])

        p_m24 = cli.parse([
            "ops", "m24-check", "evaluate", "--data", "d.json", "--logbook", "l.json",
            "--logbooks", "a.json", "b.json", "--min-providers", "3", "--allow-incomplete",
            "--out", "out.json", "--replace",
        ])
        self.assertEqual(p_m24.command.flat, "m24-check")
        self.assertEqual(p_m24.positional, ["evaluate", "b.json"])
        self.assertIn("--allow-incomplete", p_m24.tail)
        self.assertIn("--logbook", p_m24.tail)

        # --logbook stays KEY=VALUE on top while remaining native on m24-check
        self.assertEqual(cli.parse(["ops", "top", "--logbook", "l.json"]).tail, ["LOGBOOK=l.json"])

        p_watch = cli.parse(["ops", "watch", "--once", "--workers", "4", "--runner-workers", "4"])
        self.assertEqual(p_watch.tail, ["ONCE=1", "WORKERS=4", "RUNNER_WORKERS=4"])
        self.assertEqual(
            cli.parse(["ticket", "intake", "--intake-network-split", "on"]).tail,
            ["INTAKE_NETWORK_SPLIT=on"],
        )
        self.assertEqual(
            cli.parse(["ticket", "assign", "--network-auto-split", "on"]).tail,
            ["NETWORK_AUTO_SPLIT=on"],
        )

        for sh_name in SHELLS:
            comp = cli.completion(sh_name)
            self.assertIn("spec-split", comp)
            self.assertIn("m24-check", comp)
            flag_prefix = "-l " if sh_name == "fish" else "--"
            for flag in ("in-place", "allow-incomplete", "workers", "runner-workers",
                         "intake-network-split", "network-auto-split"):
                self.assertIn(f"{flag_prefix}{flag}", comp)

    def test_v090_commands_and_flags_parse(self):
        p_dl = cli.parse(["ops", "demo-loop", "run", "--out", ".crew/dl", "--budget", "120", "--inject-break", "gates"])
        self.assertEqual(p_dl.command.flat, "demo-loop")
        self.assertEqual(p_dl.positional, ["run"])
        self.assertEqual(p_dl.tail, ["--out", ".crew/dl", "--budget", "120", "--inject-break", "gates"])

        p_jury = cli.parse([
            "config", "jury", "select-env", "--ticket", "0007",
            "--author-provider", "alpha", "--author-harness", "alpha-cli",
            "--risk", "high", "--role", "architect",
        ])
        self.assertEqual(p_jury.command.flat, "jury")
        self.assertEqual(p_jury.positional, ["select-env"])
        self.assertEqual(
            p_jury.tail,
            ["TICKET=0007", "--author-provider", "alpha", "--author-harness", "alpha-cli",
             "--risk", "high", "--role", "architect"],
        )

        p_mbe = cli.parse([
            "repo", "map-brief-eval", "--repo", ".", "--rev", "HEAD",
            "--write-map", "--no-determinism-check",
        ])
        self.assertEqual(p_mbe.command.flat, "map-brief-eval")
        self.assertEqual(p_mbe.tail, ["--repo", ".", "--rev", "HEAD", "--write-map", "--no-determinism-check"])

        p_lp = cli.parse(["config", "live-preflight", "--repo", ".", "--strict"])
        self.assertEqual(p_lp.command.flat, "live-preflight")
        self.assertEqual(p_lp.tail, ["--repo", ".", "--strict"])

        for sh_name in SHELLS:
            comp = cli.completion(sh_name)
            for cmd in ("demo-loop", "jury", "map-brief-eval", "live-preflight"):
                self.assertIn(cmd, comp)
            flag_prefix = "-l " if sh_name == "fish" else "--"
            for flag in ("budget", "inject-break", "author-provider", "write-map", "strict"):
                self.assertIn(f"{flag_prefix}{flag}", comp)

    def test_thresholds_provenance_attach_and_conformance_parse(self):
        p_thr = cli.parse(["config", "thresholds", "--no-git", "--root", ".", "--contract-dir", "c"])
        self.assertEqual(p_thr.command.flat, "thresholds")
        self.assertEqual(p_thr.tail, ["--no-git", "--root", ".", "--contract-dir", "c"])
        self.assertEqual(cli.parse(["thresholds", "JSON=1"]).tail, ["JSON=1"])

        rc, out, _ = run_main(
            ["--json", "config", "thresholds", "--no-git"],
            runner=FakeRunner(["GO: thresholds — 43 keys (0 overridden)"]),
        )
        self.assertEqual(rc, 0)
        env = json.loads(out)
        self.assertEqual((env["command"], env["flat"], env["verdict"]), ("config thresholds", "thresholds", "GO"))
        self.assertIn("43 keys", env["line"])

        p_prov_b = cli.parse(["release", "provenance", "--version", "0.9.0", "--attach", "--attach-required"])
        self.assertEqual(p_prov_b.tail, ["VERSION=0.9.0", "--attach", "--attach-required"])

        p_prov_a = cli.parse([
            "release", "provenance", "attach", "--statement", "s.intoto.json",
            "--bundle", "s.bundle", "--sha", "abc123", "--out-receipt", "r.json", "--required",
        ])
        self.assertEqual(p_prov_a.positional, ["attach"])
        self.assertEqual(
            p_prov_a.tail,
            ["--statement", "s.intoto.json", "--bundle", "s.bundle", "--sha", "abc123",
             "--out-receipt", "r.json", "--required"],
        )

        for sh_name in SHELLS:
            comp = cli.completion(sh_name)
            for cmd in ("thresholds", "metrics-conformance", "orchestrator-conformance"):
                self.assertIn(cmd, comp)
            flag_prefix = "-l " if sh_name == "fish" else "--"
            for flag in ("no-git", "contract-dir", "attach", "attach-required", "statement"):
                self.assertIn(f"{flag_prefix}{flag}", comp)

    def test_reviewer_eval_and_portfolio_parse(self):
        p_re = cli.parse(["ops", "reviewer-eval", "--mode", "replay", "--role", "architect", "--thinking", "low"])
        self.assertEqual(p_re.command.flat, "reviewer-eval")
        self.assertEqual(p_re.tail, ["--mode", "replay", "--role", "architect", "--thinking", "low"])
        self.assertEqual(
            cli.parse(["reviewer-eval", "REVIEWER_ROLE=architect", "THINKING=low", "JSON=1"]).tail,
            ["REVIEWER_ROLE=architect", "THINKING=low", "JSON=1"],
        )

        rc_re, out_re, _ = run_main(
            ["--json", "ops", "reviewer-eval", "--mode", "replay"],
            runner=FakeRunner(["GO: reviewer-eval — 6/6 defects caught, 0/6 false positives (replay)"]),
        )
        self.assertEqual(rc_re, 0)
        env_re = json.loads(out_re)
        self.assertEqual((env_re["command"], env_re["flat"], env_re["verdict"]), ("ops reviewer-eval", "reviewer-eval", "GO"))
        self.assertIn("6/6 defects caught", env_re["line"])

        p_po = cli.parse(["ops", "portfolio", "--repos", "/tmp/a,/tmp/b", "--repo", ".", "sub"])
        self.assertEqual(p_po.command.flat, "portfolio")
        self.assertEqual(p_po.positional, ["sub"])
        self.assertEqual(p_po.tail, ["--repos", "/tmp/a,/tmp/b", "--repo", "."])

        rc_po, out_po, _ = run_main(
            ["--json", "ops", "portfolio", "--repo", "."],
            runner=FakeRunner(["GO: portfolio — 1 repos, 0 held, 0 your-turn"]),
        )
        self.assertEqual(rc_po, 0)
        env_po = json.loads(out_po)
        self.assertEqual((env_po["command"], env_po["flat"], env_po["verdict"]), ("ops portfolio", "portfolio", "GO"))
        self.assertIn("1 repos", env_po["line"])

        for sh_name in SHELLS:
            comp = cli.completion(sh_name)
            for cmd in ("reviewer-eval", "portfolio"):
                self.assertIn(cmd, comp)
            flag_prefix = "-l " if sh_name == "fish" else "--"
            for flag in ("fixtures", "record-replay", "strict-replay-sha", "compare-manifest", "repos"):
                self.assertIn(f"{flag_prefix}{flag}", comp)


if __name__ == "__main__":
    unittest.main()
