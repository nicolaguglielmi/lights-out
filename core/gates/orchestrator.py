# SPDX-License-Identifier: Apache-2.0
"""gates/orchestrator-conformance.sh's suite: the Orchestrator port (ADR 0019, ADR 0133).

    python3 -m core.gates.orchestrator

Drives whichever ORCHESTRATOR adapter is set via run.sh (and optional ready.sh).
Five cases in order:
  1 ready: ready.sh exits 0 with empty stdout (skipped when adapter has no ready.sh)
  2 idle-queue: run.sh on an empty EVENT_BUS exits 0 with empty stdout
  3 step-dispatch: queued {ticket, step} event is drained/acked and transitions
    the ticket when executed locally via core/tower.sh
  4 failed-step-acked: queued event for a failing step exits nonzero with empty
    stdout and acks the failed event so the bus stays clear
  5 missing-bus-and-store-refused: missing/unknown EVENT_BUS or unreadable
    STATE_STORE exits nonzero with empty stdout
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile

from . import NoGo, adapter, main, proc, run_id, say

ADAPTER_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def resolve_adapter_dir(port_var: str, adapter_name: str, env) -> str:
    """Resolve a port adapter to an in-repo directory or outside/<name> path."""
    if not adapter_name:
        raise NoGo(f"{port_var} is not set; set {port_var}=<adapter> (CONFORMANCE=1)")
    if adapter_name.startswith("outside/"):
        name = adapter_name[len("outside/"):]
        if not ADAPTER_NAME.fullmatch(name):
            raise NoGo(f"{port_var}={adapter_name} is invalid: outside adapter name must match [a-z0-9][a-z0-9-]*")
        linked = os.path.join("adapters", "outside", name)
        if os.path.isdir(linked):
            return linked
        adapters_path = env.get("ADAPTERS_PATH", "")
        if not adapters_path or not os.path.isabs(adapters_path) or not os.path.isdir(adapters_path):
            raise NoGo(
                f"{port_var}={adapter_name} has no {linked} link and ADAPTERS_PATH [{adapters_path}] "
                "is not an absolute directory"
            )
        candidate = os.path.join(adapters_path, name)
        if not os.path.isdir(candidate):
            raise NoGo(f"{port_var}={adapter_name} adapter directory not found at {candidate}")
        return candidate
    if not ADAPTER_NAME.fullmatch(adapter_name):
        raise NoGo(f"{port_var}={adapter_name} is invalid: adapter name must match [a-z0-9][a-z0-9-]*")
    candidate = os.path.join("adapters", adapter_name)
    if not os.path.isdir(candidate):
        raise NoGo(f"{candidate} does not exist")
    return candidate


def _write_exe(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IRUSR)


class Suite:
    def __init__(self, orch_name: str, verb, env, *, has_ready: bool = False,
                 bus_verb=None, store_verb=None) -> None:
        self.orch_name = orch_name
        self.verb = verb
        self.base_env = dict(env)
        self.has_ready = has_ready
        self.rid = run_id()
        self._tmp = tempfile.mkdtemp(prefix="orch-conf-")
        self.steps_dir = os.path.join(self._tmp, "steps")
        os.makedirs(self.steps_dir, exist_ok=True)
        self.marker_path = os.path.join(self._tmp, "step-ran")
        _write_exe(
            os.path.join(self.steps_dir, "assign.sh"),
            "#!/bin/sh\n"
            "set -eu\n"
            '[ -n "${CONFORMANCE_STEP_MARKER:-}" ] && : > "$CONFORMANCE_STEP_MARKER"\n'
            'printf \'{"state":"held"}\\n\'\n',
        )

        self.bus_name = self.base_env.get("EVENT_BUS") or "spool"
        self.store_name = self.base_env.get("STATE_STORE") or "sqlite"
        self.run_env = dict(self.base_env)
        self.run_env["EVENT_BUS"] = self.bus_name
        self.run_env["STATE_STORE"] = self.store_name
        self.run_env["TOWER_STEPS"] = self.steps_dir
        self.run_env["CONFORMANCE_STEP_MARKER"] = self.marker_path
        if self.bus_name == "spool" and not self.base_env.get("SPOOL_FILE"):
            self.run_env["SPOOL_FILE"] = os.path.join(self._tmp, "spool.sqlite")
        if self.store_name == "sqlite" and not self.base_env.get("STATE_FILE"):
            self.run_env["STATE_FILE"] = os.path.join(self._tmp, "state.sqlite")

        if bus_verb is None:
            bus_dir = resolve_adapter_dir("EVENT_BUS", self.bus_name, self.run_env)
            bus_verb = adapter(bus_dir)
        if store_verb is None:
            store_dir = resolve_adapter_dir("STATE_STORE", self.store_name, self.run_env)
            store_verb = adapter(store_dir)
        self.bus = bus_verb
        self.store = store_verb
        self.tid = f"conf-{self.rid}"
        self.seeded = False
        say(f"[orchestrator-conformance] run {self.rid} on ORCHESTRATOR={orch_name}")

    def cleanup(self) -> None:
        try:
            if self.seeded:
                self.store("delete", "tickets", self.tid, env=self.run_env)
        finally:
            shutil.rmtree(self._tmp, ignore_errors=True)

    def call(self, name: str, *args: str, env=None):
        use_env = self.run_env if env is None else env
        rc, out, err = self.verb(name, *args, env=use_env)
        for line in err.splitlines():
            say(f"  [{name}] {line}")
        return rc, out

    def _publish(self, payload: dict) -> None:
        msg = json.dumps(payload, separators=(",", ":"))
        rc, out, err = self.bus("publish", msg, env=self.run_env)
        if rc != 0:
            raise NoGo(f"eventbus publish failed (rc={rc}): {err.strip() or out.strip()}")

    def _drain_check(self, case_label: str) -> None:
        rc, out, err = self.bus("pull", env=self.run_env)
        if rc != 0:
            raise NoGo(f"{case_label}: eventbus pull exited {rc}: {err.strip()}")
        if out.strip():
            ack_id = out.splitlines()[0].split(" ", 1)[0]
            if ack_id:
                self.bus("ack", ack_id, env=self.run_env)
            raise NoGo(f"{case_label}: EVENT_BUS was not drained; leftover message {out!r}")

    def run(self) -> str:
        if self.has_ready:
            rc, out = self.call("ready")
            if rc != 0 or out != "":
                raise NoGo(f"case1 ready: ready.sh exited {rc} with stdout {out!r}, want exit 0 and empty stdout")
            say("[orchestrator-conformance] case 1 ready: GO")
        else:
            say("[orchestrator-conformance] case 1 ready: skipped (no ready.sh)")

        self._drain_check("case2 idle-queue precheck")
        rc, out = self.call("run")
        if rc != 0 or out != "":
            raise NoGo(f"case2 idle-queue: run.sh exited {rc} with stdout {out!r}, want exit 0 and empty stdout")
        say("[orchestrator-conformance] case 2 idle-queue: GO")

        initial_doc = json.dumps({"id": self.tid, "state": "opened", "history": []}).encode("utf-8") + b"\n"
        rc_put, _, err_put = self.store("put", "tickets", self.tid, data=initial_doc, env=self.run_env)
        if rc_put != 0:
            raise NoGo(f"case3 step-dispatch: could not seed ticket {self.tid}: {err_put.strip()}")
        self.seeded = True
        self._publish({"ticket": self.tid, "step": "assign"})
        rc, out = self.call("run")
        if rc != 0 or out != "":
            raise NoGo(f"case3 step-dispatch: run.sh exited {rc} with stdout {out!r}, want exit 0 and empty stdout")
        self._drain_check("case3 step-dispatch")
        if os.path.isfile(self.marker_path):
            rc_get, out_get, _ = self.store("get", "tickets", self.tid, env=self.run_env)
            if rc_get != 0:
                raise NoGo(f"case3 step-dispatch: state store get failed after step (rc={rc_get})")
            try:
                updated = json.loads(out_get)
            except ValueError:
                raise NoGo(f"case3 step-dispatch: stored document is not JSON: {out_get!r}") from None
            if updated.get("state") != "held":
                raise NoGo(f"case3 step-dispatch: ticket state is {updated.get('state')!r}, want 'held'")
        say("[orchestrator-conformance] case 3 step-dispatch: GO")

        missing_tid = f"missing-{self.rid}"
        self._publish({"ticket": missing_tid, "step": "assign"})
        rc, out = self.call("run")
        if rc == 0 or out != "":
            raise NoGo(
                f"case4 failed-step-acked: run.sh on failing step exited {rc} with stdout {out!r}, "
                "want nonzero and empty stdout"
            )
        self._drain_check("case4 failed-step-acked")
        say("[orchestrator-conformance] case 4 failed-step-acked: GO")

        no_bus_env = dict(self.run_env)
        no_bus_env.pop("EVENT_BUS", None)
        rc, out = self.call("run", env=no_bus_env)
        if rc == 0 or out != "":
            raise NoGo(
                f"case5 missing-bus-and-store-refused: run.sh without EVENT_BUS exited {rc} "
                f"with stdout {out!r}, want nonzero and empty stdout"
            )

        bad_bus_env = dict(self.run_env, EVENT_BUS=f"missing-bus-{self.rid}")
        rc, out = self.call("run", env=bad_bus_env)
        if rc == 0 or out != "":
            raise NoGo(
                f"case5 missing-bus-and-store-refused: run.sh with unknown EVENT_BUS exited {rc} "
                f"with stdout {out!r}, want nonzero and empty stdout"
            )

        if self.base_env.get("CONFORMANCE_SKIP_STORE_REFUSAL") != "1":
            # Ensure a queued event hitting an unreadable/absent STATE_STORE fails cleanly.
            self._publish({"ticket": self.tid, "step": "assign"})
            bad_store_env = dict(self.run_env, STATE_STORE=f"missing-store-{self.rid}")
            rc, out = self.call("run", env=bad_store_env)
            if rc == 0 or out != "":
                raise NoGo(
                    f"case5 missing-bus-and-store-refused: run.sh with unreadable STATE_STORE exited {rc} "
                    f"with stdout {out!r}, want nonzero and empty stdout"
                )
            self._drain_check("case5 missing-bus-and-store-refused")
        say("[orchestrator-conformance] case 5 missing-bus-and-store-refused: GO")

        suffix = "" if self.has_ready else " (no ready.sh)"
        return f"{self.orch_name}, 5 cases{suffix}"


def judge(argv, env=None, verb=None, *, has_ready=None, bus_verb=None, store_verb=None):
    _ = argv
    env = os.environ if env is None else env
    orch_name = env.get("ORCHESTRATOR", "")
    if not orch_name:
        raise NoGo("ORCHESTRATOR is not set; set ORCHESTRATOR=<adapter> (CONFORMANCE=1)")
    if verb is None:
        adapter_dir = resolve_adapter_dir("ORCHESTRATOR", orch_name, env)
        script = os.path.join(adapter_dir, "run.sh")
        if not os.path.isfile(script):
            raise NoGo(f"{script} is missing: an Orchestrator adapter ships run.sh")
        if has_ready is None:
            has_ready = os.path.isfile(os.path.join(adapter_dir, "ready.sh"))
        verb = adapter(adapter_dir)
    elif has_ready is None:
        has_ready = False
    suite = Suite(orch_name, verb, env, has_ready=has_ready, bus_verb=bus_verb, store_verb=store_verb)
    try:
        return suite.run()
    finally:
        suite.cleanup()


if __name__ == "__main__":
    raise SystemExit(main(judge))
