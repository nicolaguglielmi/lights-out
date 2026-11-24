# SPDX-License-Identifier: Apache-2.0
"""gates/tracker-conformance.sh's suite: the Tracker port (ADR 0044, ADR 0116).

    python3 -m core.gates.tracker

Drives whichever TRACKER adapter is set via put.sh, get.sh, and optional
ready.sh / sync.sh. Six cases in order, the first disagreement a NO-GO naming
the case:
  1 put-create: put.sh <title> <origin> with Markdown body on stdin creates a
    ticket and prints a single non-empty ID token on stdout
  2 get-roundtrip: get.sh <id> prints Markdown holding the title,
    `- Origin: <origin>`, and the body
  3 put-idempotent: calling put.sh again with the same <origin> returns the
    exact same ID
  4 sync-metadata: if sync.sh exists, sync.sh <id> <state> [pr_url] [adr_ref]
    updates status, PR, and ADR metadata idempotently and get.sh reflects it
  5 invalid-put-refused: put.sh with empty title or empty origin exits nonzero
    with empty stdout
  6 missing-get-refused: get.sh on an unknown or empty ID exits nonzero with
    empty stdout
"""

import os
import re

from . import NoGo, adapter, main, run_id, say

ADAPTER_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def _single_token(text):
    lines = text.splitlines()
    if len(lines) != 1:
        return None
    token = lines[0].strip()
    if not token or token != lines[0] or any(ch.isspace() for ch in token):
        return None
    return token


def _snip(text):
    return text.encode("utf-8")[:200].decode("utf-8", "ignore").replace("\n", " ")


def resolve_adapter_dir(tracker_name, env):
    """Resolve TRACKER to an in-repo directory or outside/<name> path."""
    if not tracker_name:
        raise NoGo("TRACKER is not set; set TRACKER=<adapter> (CONFORMANCE=1)")
    if tracker_name.startswith("outside/"):
        name = tracker_name[len("outside/"):]
        if not ADAPTER_NAME.fullmatch(name):
            raise NoGo(f"TRACKER={tracker_name} is invalid: outside adapter name must match [a-z0-9][a-z0-9-]*")
        linked = f"adapters/outside/{name}"
        if os.path.isdir(linked):
            return linked
        adapters_path = env.get("ADAPTERS_PATH", "")
        if not adapters_path or not os.path.isabs(adapters_path) or not os.path.isdir(adapters_path):
            raise NoGo(
                f"TRACKER={tracker_name} has no {linked} link and ADAPTERS_PATH [{adapters_path}] "
                "is not an absolute directory"
            )
        candidate = os.path.join(adapters_path, name)
        if not os.path.isdir(candidate):
            raise NoGo(f"TRACKER={tracker_name} adapter directory not found at {candidate}")
        return candidate
    if not ADAPTER_NAME.fullmatch(tracker_name):
        raise NoGo(f"TRACKER={tracker_name} is invalid: adapter name must match [a-z0-9][a-z0-9-]*")
    candidate = f"adapters/{tracker_name}"
    if not os.path.isdir(candidate):
        raise NoGo(f"{candidate} does not exist")
    return candidate


class Suite:
    def __init__(self, tracker_name, verb, *, has_sync=True, has_ready=False):
        self.tracker_name = tracker_name
        self.verb = verb
        self.has_sync = has_sync
        self.has_ready = has_ready
        self.rid = run_id()
        self.title = f"Conformance ticket {self.rid}"
        self.origin = f"conformance-{self.rid}"
        self.body = (
            "## Problem\n\n"
            "Conformance probe body.\n\n"
            "## Acceptance criteria\n\n"
            "Given a ticket\n"
            "When fetched\n"
            "Then origin and body match.\n"
        )
        say(f"[tracker-conformance] run {self.rid} on TRACKER={tracker_name}")

    def call(self, name, *args, data=None):
        rc, out, err = self.verb(name, *args, data=data)
        for line in err.splitlines():
            say(f"  [{name}] {line}")
        return rc, out

    def _assert_roundtrip(self, ticket_id, case_label, *, expected_lines=()):
        rc, out = self.call("get", ticket_id)
        if rc != 0 or not out.strip():
            raise NoGo(f"{case_label}: get.sh {ticket_id} exited {rc} with stdout [{_snip(out)}]")
        lines = [ln.rstrip("\r") for ln in out.splitlines()]
        if self.title not in out:
            raise NoGo(f"{case_label}: get.sh {ticket_id} omitted title {self.title!r}")
        origin_line = f"- Origin: {self.origin}"
        if origin_line not in lines:
            raise NoGo(f"{case_label}: get.sh {ticket_id} omitted exact line {origin_line!r}")
        for snippet in ("Conformance probe body.", "Then origin and body match."):
            if snippet not in out:
                raise NoGo(f"{case_label}: get.sh {ticket_id} omitted body text {snippet!r}")
        for line in expected_lines:
            if line not in lines:
                raise NoGo(f"{case_label}: get.sh {ticket_id} omitted synced line {line!r}")
        return lines

    def run(self):
        if self.has_ready:
            rc, out = self.call("ready")
            if rc != 0 or out != "":
                raise NoGo(f"preflight ready: ready.sh exited {rc} with stdout {out!r}, want exit 0 and empty stdout")
            say("[tracker-conformance] preflight ready: GO")

        body_bytes = self.body.encode("utf-8")
        rc, out = self.call("put", self.title, self.origin, data=body_bytes)
        ticket_id = _single_token(out) if rc == 0 else None
        if rc != 0 or not ticket_id:
            raise NoGo(f"case1 put-create: put.sh exited {rc} with stdout {out!r}, want exit 0 and one non-empty ID")
        say(f"[tracker-conformance] case 1 put-create ({ticket_id}): GO")

        self._assert_roundtrip(ticket_id, "case2 get-roundtrip")
        say("[tracker-conformance] case 2 get-roundtrip: GO")

        rc, out = self.call("put", self.title, self.origin, data=body_bytes)
        second_id = _single_token(out) if rc == 0 else None
        if rc != 0 or second_id != ticket_id:
            raise NoGo(
                f"case3 put-idempotent: repeated put.sh exited {rc} with {out!r}, want exact {ticket_id!r}"
            )
        say("[tracker-conformance] case 3 put-idempotent: GO")

        if self.has_sync:
            state = "in-progress"
            pr_url = "https://code.example.invalid/org/repo/pull/42"
            adr_ref = "ADR-0116"
            expected = (f"- Status: {state}", f"- PR: {pr_url}", f"- ADR: {adr_ref}")
            for attempt in ("first", "second"):
                rc, out = self.call("sync", ticket_id, state, pr_url, adr_ref)
                if rc != 0 or out != "":
                    raise NoGo(
                        f"case4 sync-metadata: {attempt} sync.sh exited {rc} with stdout {out!r}, "
                        "want exit 0 and empty stdout"
                    )
                lines = self._assert_roundtrip(ticket_id, "case4 sync-metadata", expected_lines=expected)
                for prefix in ("- Status:", "- PR:", "- ADR:", "- Origin:"):
                    count = sum(1 for ln in lines if ln.startswith(prefix))
                    if count != 1:
                        raise NoGo(
                            f"case4 sync-metadata: after {attempt} sync.sh, get.sh printed {count} "
                            f"lines starting with {prefix!r}, want 1"
                        )
            say("[tracker-conformance] case 4 sync-metadata: GO")
        else:
            say("[tracker-conformance] case 4 sync-metadata: skipped (no sync.sh)")

        for bad_args, desc in ((("", self.origin), "empty title"), ((self.title, ""), "empty origin")):
            rc, out = self.call("put", *bad_args, data=body_bytes)
            if rc == 0 or out != "":
                raise NoGo(
                    f"case5 invalid-put-refused: put.sh with {desc} exited {rc} with stdout {out!r}, "
                    "want nonzero and empty stdout"
                )
        say("[tracker-conformance] case 5 invalid-put-refused: GO")

        for missing_id, desc in ((f"missing-{self.rid}-999999", "unknown ID"), ("", "empty ID")):
            rc, out = self.call("get", missing_id)
            if rc == 0 or out != "":
                raise NoGo(
                    f"case6 missing-get-refused: get.sh on {desc} exited {rc} with stdout {out!r}, "
                    "want nonzero and empty stdout"
                )
        say("[tracker-conformance] case 6 missing-get-refused: GO")

        if self.has_sync:
            return f"{self.tracker_name}, 6 cases"
        return f"{self.tracker_name}, 5 cases (no sync.sh)"


def judge(argv, env=None, verb=None, *, has_sync=None, has_ready=None):
    env = os.environ if env is None else env
    tracker_name = env.get("TRACKER", "")
    if not tracker_name:
        raise NoGo("TRACKER is not set; set TRACKER=<adapter> (CONFORMANCE=1)")
    if verb is None:
        adapter_dir = resolve_adapter_dir(tracker_name, env)
        for required in ("put", "get"):
            if not os.path.isfile(os.path.join(adapter_dir, f"{required}.sh")):
                raise NoGo(f"{adapter_dir}/{required}.sh is missing: a Tracker adapter ships put and get")
        if has_ready is None:
            has_ready = os.path.isfile(os.path.join(adapter_dir, "ready.sh"))
        if has_sync is None:
            has_sync = os.path.isfile(os.path.join(adapter_dir, "sync.sh"))
        verb = adapter(adapter_dir)
    else:
        if has_ready is None:
            has_ready = False
        if has_sync is None:
            has_sync = True
    suite = Suite(tracker_name, verb, has_sync=has_sync, has_ready=has_ready)
    return suite.run()


if __name__ == "__main__":
    raise SystemExit(main(judge))
