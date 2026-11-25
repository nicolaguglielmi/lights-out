# SPDX-License-Identifier: Apache-2.0
"""harness-conformance-proof: gates/harness-conformance.sh can fail (CLAUDE.md:
a gate that cannot fail is not a gate), and it can pass, without an engine, a
key or a real CLI. Run by gates/harness-conformance/proof.sh through
`verdict`, after its guards (docs/adr/0084, amendment A2).

The suite is run, whole, on a copy of the tree that holds one made-up CLI
harness, `fake-cli`, and a `docker-compose` that is a stand-in; both are
files under gates/harness-conformance/fake/, the proof's fixtures:
  - fake-cli is a pure adapter: a run.sh that honours the contract of
    docs/adr/0041 (the refusal of another provider, the artefacts, the stop
    lines, MODEL_BASE_URL) around a client that speaks Anthropic Messages to
    the scripted model and calls the guard before every tool, as Claude
    Code's PreToolUse hook does. No container.
  - the stand-in `docker-compose` answers the calls the suite and
    core/egress.sh make: config, build, up -d (the scripted model; the
    proxy), ps, rm -sf, logs, exec; `run` is refused, since fake-cli never
    uses it, so a run.sh that calls it is a run.sh that started a container.
The good fake must reach GO. Each row of DEFECTS is one way a real harness can
be wrong; the suite must refuse it, with a NO-GO naming the case it belongs
to. So the table is also a record of what each case catches.

Confinement (docs/adr/0046) is proved the same way, with stand-ins for what an
engine would give: the stand-in compose starts the real core/egress.py
(through egress_wrap.py, which gives it names and a source address of its
own, 127.0.0.2, the one the stub then sees) and answers `exec` in the stub's
container; and fake-cli, with the probe it runs, executes under a
sitecustomize that plays the harness container's network: the proxy is
reachable, every other address is not, and `egress` is the only name that
resolves. A defect is a hole in that stand-in network or in the proxy's list.
The probe is also proved channel by channel on its own, before the suite
runs: each of its eight ways is opened alone and must be the one that reads
`open`.

Case 10, agents (docs/adr/0073), is proved on the real core/agents.py: the
tree holds a contract, `c/` (WORKLOAD_CONTRACT=c), with an mcp.json and a
stand-in for the fixture server, notes.py, that answers search_notes and
count_notes as the fixture does. fake-cli honours mcp.json the way docs/adr/
0073 §8-§9 asks. A second good run takes fake-cli's mcp.sh away, with an
adapter that is no CLI harness keeping one (replay's shape), so 10r, §8's
refusal, is proved too; its defects are planted in agents.py's mcp verb, the
one thing 10r judges. The contract sits at the tree's top so that a server
started in fake-cli's own directory, not the workspace, still starts, and
answers from the wrong place.

The stand-ins listen on local ports the proof picks, and one picker serves
them all (fake/freeport.py): it is proved first, under the load the proxy's
own sockets put on the kernel's port allocator, to hand out only ports its
listeners can bind (a picker that asked on 127.0.0.1 failed one podman run in
twenty, the stub's 60 s wait, and two docker ones, the proxy's). How much load
and how many picks (decisions, against a measurement in the workbench on
2026-11-11): a quarter of the ephemeral range, which bind(0) fills on one
parity first, so about half of the ports a pick is offered are held; a picker
that asks on 127.0.0.1 then landed on a held port 95 times in 200 picks, so 20
picks miss that defect with a chance near one in a million.

Run by `make harness-conformance` before the suite judges any real harness.
Not a ci/ check: it starts the stub and fake-cli about sixty times, about five
minutes that ./go ci and ci/contract.sh would spend.

It is not a conformance run of any real harness and says nothing of one:
fake-cli proves the suite, never an adapter. What it cannot prove without an
engine: the internal network itself, DNS, the container's real peer address
(127.0.0.2 stands in for egress's), a real CLI honouring HTTP_PROXY and
NO_PROXY, and the container side of run.sh. Those are case 9 on docker in
the hosted CI and, once, on rootless podman.
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import time
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.gates import DEVNULL, NoGo, main, proc, say

SUITE = "gates/harness-conformance"
FAKE = SUITE + "/fake"
GO_PREFIX = "GO: harness-conformance — fake-cli (anthropic), fake-cli (zai), against a scripted model, "
# A good run says, on stderr, that it ran both providers (docs/adr/0092 §2).
PROGRESS = ("harness-conformance: fake-cli (provider anthropic, model fake-model-1)",
            "harness-conformance: fake-cli (provider zai, model fake-model-2)")
NOGO_PREFIX = "NO-GO: harness-conformance — "
WAYS = "abcdefgh"

COMPOSE_GOOD = {"services": {"conformance-model": {"networks": {"default": {"aliases": ["conformance-sink"]}}},
                             "egress": {}, "fake-cli": {}}}

# A guard that only knows git push: pushes are refused, secrets are readable.
PUSH_ONLY_GUARD = """#!/bin/sh
input=$(cat)
if printf '%s' "$input" | grep -q 'git push'; then
  printf 'DENY x git push\\n' >> "${CREW_OUT:-.}/hooks.log"
  echo "denied: matched git push" >&2
  exit 2
fi
printf 'ALLOW x -\\n' >> "${CREW_OUT:-.}/hooks.log"
exit 0
"""


# --- the tree the suite runs in, and what a row may change in it ---------------------


def build_tree(t, root="."):
    """A copy of what the suite reads, plus fake-cli, under T."""
    for d in ("gates", "ci", "core/gates", "roles", "adapters/fake-cli/hooks", ".crew", "c/mcp"):
        os.makedirs(os.path.join(t, d), exist_ok=True)
    src = lambda rel: os.path.join(root, rel)  # noqa: E731 - a path in the checkout
    shutil.copy(src("gates/harness-conformance.sh"), t + "/gates/")
    shutil.copytree(src(SUITE), t + "/" + SUITE)
    shutil.copy(src("ci/lib.sh"), t + "/ci/")
    for f in ("__init__.py", "cmd_util.py", "harness-check.sh", "egress.sh", "egress_cmd.py", "egress.py",
              "agents.py", "map.py", "tower.py", "gates/__init__.py", "gates/harness.py"):
        shutil.copy(src("core/" + f), t + "/core/" + f)
    shutil.copy(src(FAKE + "/egress_wrap.py"), t + "/proof-egress.py")
    shutil.copytree(src("roles/dev-worker"), t + "/roles/dev-worker")
    shutil.copytree(src("roles/test-reviewer"), t + "/roles/test-reviewer")
    fake = t + "/adapters/fake-cli"
    for f in ("run.sh", "run.py", "fake_cli.py", "mcp.sh"):
        shutil.copy(src(FAKE + "/" + f), fake + "/")
    shutil.copytree(src(FAKE + "/netshim"), fake + "/netshim")
    shutil.copy(src(FAKE + "/guard.sh"), fake + "/hooks/guard.sh")
    # Case 10's contract (WORKLOAD_CONTRACT=c): the server, and mcp.json naming search_notes only.
    shutil.copy(src(FAKE + "/notes.py"), t + "/c/mcp/notes.py")
    put(t, "c/mcp.json", '{"servers": {"notes": {"command": ["python3", "c/mcp/notes.py"], "tools": ["search_notes"]}}}\n')
    put(t, "adapters/fake-cli/package-lock.json", "{}\n")
    put(t, "adapters/fake-cli/Containerfile", "FROM scratch\n")
    # Two providers (docs/adr/0092 §2), their maps different where the suite's role asks (high):
    # a run that reads the first provider's map for the second sends the wrong value.
    put(t, "adapters/fake-cli/thinking.env", "ANTHROPIC_LOW=low\nANTHROPIC_MEDIUM=medium\nANTHROPIC_HIGH=high\n"
        "ZAI_LOW=low\nZAI_MEDIUM=high\nZAI_HIGH=max\n")
    put(t, "adapters/fake-cli/conformance.env", "PROVIDER=anthropic\nMODEL=fake-model-1\nKEY_VAR=FAKE_KEY_VALUE\n"
        "THINKING_PATH=.output_config.effort\nEGRESS=api.fake-provider.example:443\n")
    # The second provider: its own key variable, model and host, and a BASE_URL, which the suite never reads.
    put(t, "adapters/fake-cli/conformance.zai.env", "PROVIDER=zai\nMODEL=fake-model-2\nKEY_VAR=FAKE_KEY_VALUE_ZAI\n"
        "THINKING_PATH=.output_config.effort\nEGRESS=api.fake-zai.example:443\n"
        "BASE_URL=https://api.fake-zai.example/api/anthropic\n")


def put(t, rel, text):
    path = os.path.join(t, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def push_only_guard(t, root="."):
    put(t, "adapters/fake-cli/hooks/guard.sh", PUSH_ONLY_GUARD)


def _no_egress(t, name):
    with open(t + "/adapters/fake-cli/" + name, encoding="utf-8") as fh:
        kept = [line for line in fh.read().splitlines(True) if not line.startswith("EGRESS=")]
    put(t, "adapters/fake-cli/" + name, "".join(kept))


def no_egress_key(t, root="."):
    _no_egress(t, "conformance.env")


def no_zai_egress(t, root="."):
    """The second provider's file: the suite reads every conformance file, not the first alone."""
    _no_egress(t, "conformance.zai.env")


def no_mcp_sh(t, root="."):
    """fake-cli without an mcp.sh, beside an adapter that is no CLI harness and
    has one (replay's shape)."""
    os.remove(t + "/adapters/fake-cli/mcp.sh")
    os.makedirs(t + "/adapters/fake-replay", exist_ok=True)
    shutil.copy(os.path.join(root, FAKE, "mcp.sh"), t + "/adapters/fake-replay/mcp.sh")


def agents_shim(t, root="."):
    """As no_mcp_sh, with core/agents.py's mcp verb wrong as FAKE_DEFECT says."""
    no_mcp_sh(t, root)
    os.replace(t + "/core/agents.py", t + "/core/agents_real.py")
    shutil.copy(os.path.join(root, FAKE, "agents_shim.py"), t + "/core/agents.py")


# --- the table ------------------------------------------------------------------------

# A row: what is wrong, said for a reader; FAKE_DEFECT, what fake-cli does wrong
# ("" for none); the verdict the suite must give, GO or the text its NO-GO line
# must hold after the gate's name; the change to the tree (a function above);
# and the compose project the stand-in renders (None: the good one).
Defect = namedtuple("Defect", "name defect want tweak compose")
_ = None
DEFECTS = (
    Defect("good fake-cli", "", "GO", _, _),
    # One defect each: a real harness's way of being wrong, and the case that must say so.
    Defect("answers another provider after starting a container", "late-refusal", "fake-cli (anthropic): case 0:", _, _),
    Defect("writes a second line before refusing", "more-lines", "fake-cli (anthropic): case 0:", _, _),
    Defect("accepts a provider none of its files declares", "no-refusal", "fake-cli (anthropic): case 0:", _, _),
    Defect("forgets the thinking value", "no-thinking", "fake-cli (anthropic): case 1:", _, _),
    Defect("forgets the role prompt", "no-prompt", "fake-cli (anthropic): case 1:", _, _),
    Defect("records the requested model, not the served one", "raw-model", "fake-cli (anthropic): case 1:", _, _),
    Defect("reports other tokens than the model spent", "tokens-doubled", "fake-cli (anthropic): case 1:", _, _),
    Defect("drops the endpoint from run.json", "no-endpoint", "fake-cli (anthropic): case 1:", _, _),
    Defect("writes a tokens.cached the model did not report", "cached-wrong",
           "fake-cli (anthropic): case 1: run.json .tokens.cached is", _, _),
    Defect("a harness that reports no tokens.cached", "omit-cached", "GO", _, _),
    # A second provider (docs/adr/0092 §2): the good fake passed both; each defect fails the second alone.
    Defect("ignores the second file's provider and refuses it", "one-provider",
           "fake-cli (zai): case 1: run.sh exited 1, want 0", _, _),
    Defect("sends the first provider's thinking value for the second", "map-of-first",
           "fake-cli (zai): case 1: the first request carries 'high' at .output_config.effort, want 'max' "
           "(thinking.env's ZAI_HIGH)", _, _),
    Defect("hooks that log a denial and let the call through", "log-only", "fake-cli (anthropic): case 2:", _, _),
    Defect("no hook at all", "no-hook", "fake-cli (anthropic): case 1: OUT/hooks.log", _, _),
    Defect("ignores MAX_TURNS", "no-turn-limit", "fake-cli (anthropic): case 5:", _, _),
    Defect("stops on TOKEN_BUDGET with its own words", "wrong-stop-phrase", "fake-cli (anthropic): case 5:", _, _),
    Defect("never stops on TOKEN_BUDGET", "no-token-limit", "fake-cli (anthropic): case 6:", _, _),
    Defect("leaves no run.json when it stops on TOKEN_BUDGET", "no-run-on-stop",
           "fake-cli (anthropic): case 6: the over-budget run left no run.json", _, _),
    Defect("the key reaches a request body", "key-in-body", "fake-cli (anthropic): case 3:", _, _),
    # Confinement (docs/adr/0046). Case 1 meets what any case would: a request that did
    # not come from the proxy, one that reached the sink's name, one that went to /exfil.
    Defect("reaches the model around the proxy", "direct-model", "fake-cli (anthropic): case 1: the stub saw requests from", _, _),
    Defect("talks to the sink through a list that lets it through", "sink-touch",
           "fake-cli (anthropic): case 1: 1 request(s) reached conformance-sink or /exfil", _, _),
    Defect("posts to /exfil on the model's own host", "exfil-path",
           "fake-cli (anthropic): case 1: 1 request(s) reached conformance-sink or /exfil", _, _),
    # Case 9: each way the probe tries, left open in turn, and a proxy that took one host too many.
    Defect("a list that lets the sink through (probe ways a, b)", "allow-sink", "fake-cli (anthropic): case 9: channel a is open", _, _),
    Defect("a list that lets the model's name through on another port (c)", "allow-model-port",
           "fake-cli (anthropic): case 9: channel c is open", _, _),
    Defect("a list that lets example.com through (d)", "allow-example", "fake-cli (anthropic): case 9: channel d is open", _, _),
    Defect("a container that reaches the sink's address directly (e)", "open-sink-direct",
           "fake-cli (anthropic): case 9: channel e is open", _, _),
    Defect("a container that reaches 1.1.1.1 directly (g)", "open-public", "fake-cli (anthropic): case 9: channel g is open", _, _),
    Defect("a container whose DNS resolves example.com (h)", "open-dns", "fake-cli (anthropic): case 9: channel h is open", _, _),
    Defect("a probe that cannot reach the proxy, so tried nothing", "probe-cut-off",
           "fake-cli (anthropic): case 9: the proxy's log holds no DENY", _, _),
    Defect("a proxy that takes a host of the harness's own", "side-host",
           "fake-cli (anthropic): case 9: the proxy took a host other than the scripted model", _, _),
    Defect("a tool result cut short, so the probe's last ways are not seen", "cut-output",
           "fake-cli (anthropic): case 9: the tool result has no 'egress-probe d' line", _, _),
    Defect("the key in an artefact of case 9 alone", "key-in-probe-case", "fake-cli (anthropic): case 8:", _, _),
    Defect("a guard that knows git push and nothing else", "", "fake-cli (anthropic): case 3:", push_only_guard, _),
    # A key that leaks into an artefact only when a run is stopped: only case 8 reads every case.
    Defect("the key in an artefact of a stopped run", "key-on-stop", "fake-cli (anthropic): case 8:", _, _),
    Defect("a worker whose service starts Postgres with it", "", "would also start postgres", _,
           {"services": {"conformance-model": {"networks": {"default": {"aliases": ["conformance-sink"]}}},
                         "egress": {}, "fake-cli": {"depends_on": {"postgres": {}}}}}),
    # ci/harness-shape.sh judges EGRESS for everyone, but the suite does not let a harness it runs skip it.
    Defect("a conformance.env without EGRESS", "", "has no EGRESS=", no_egress_key, _),
    Defect("a conformance.zai.env without EGRESS", "", "conformance.zai.env has no EGRESS=", no_zai_egress, _),
    # A project without the proxy, or without the sink's name: case 9 would prove nothing.
    Defect("a project with no egress service", "", "has no service egress", _,
           {"services": {"conformance-model": {"networks": {"default": {"aliases": ["conformance-sink"]}}},
                         "fake-cli": {}}}),
    Defect("a scripted model with no sink alias", "", "no alias conformance-sink", _,
           {"services": {"conformance-model": {}, "egress": {}, "fake-cli": {}}}),
    # Case 10 (docs/adr/0073). 10a, the reviewer's role file: each defect leaves case 1
    # standing and breaks 10a alone.
    Defect("commits even when the reviewer changed nothing", "commit-always", "fake-cli (anthropic): case 10a: run.sh exited 1, want 0",
           _, _),
    Defect("puts the role prompt after the ticket", "prompt-after-ticket",
           "fake-cli (anthropic): case 10a: the first request holds the role prompt, the role file's marker and the restated output "
           "contract out of order", _, _),
    Defect("sends the input's first twenty lines only", "input-cut",
           "fake-cli (anthropic): case 10a: the first request lacks the restated output contract", _, _),
    Defect("writes final.md under a heading of its own", "final-md-header",
           "fake-cli (anthropic): case 10a: final.md's first line is 'Final answer:', want APPROVED", _, _),
    # 10b, the dev-worker's calls.
    Defect("offers the model no MCP tool", "mcp-undeclared",
           "fake-cli (anthropic): case 10b: the first request declares no tool ending with search_notes", _, _),
    Defect("says the unnamed tools are hidden while offering them", "mcp-says-hidden",
           "fake-cli (anthropic): case 10b: run.json says the unnamed tools are hidden, and the first request declares count_notes",
           _, _),
    Defect("writes no mcp into run.json", "mcp-null", "fake-cli (anthropic): case 10b: run.json .mcp.unlisted is 'absent'", _, _),
    Defect("a guard that lets an unnamed MCP tool through", "mcp-guard-open",
           "fake-cli (anthropic): case 10b: hooks.log has no DENY for count_notes", _, _),
    Defect("logs the denial of an unnamed MCP tool and calls it anyway", "mcp-log-only",
           "fake-cli (anthropic): case 10b: count_notes's answer reached the model", _, _),
    Defect("hands the model no server answer", "mcp-no-result",
           "fake-cli (anthropic): case 10b: no request's tool result carries notes-hit:", _, _),
    Defect("starts the server in its own directory, not the workspace", "mcp-own-cwd",
           "fake-cli (anthropic): case 10b: the tool result does not name the workspace", _, _),
    Defect("counts the calls without reading hooks.log", "mcp-calls-unread",
           "fake-cli (anthropic): case 10b: run.json .mcp.calls is [], want", _, _),
    # 10c, a server that exits at start.
    Defect("goes on without a server that did not start", "mcp-down-ignored",
           "fake-cli (anthropic): case 10c: run.sh exited 0 with a server that exits at start, want 1", _, _),
    Defect("fails without naming the server or ADR 0073 §8", "mcp-down-vague",
           "fake-cli (anthropic): case 10c: run.sh's stderr has no line naming the server notes and ADR 0073 §8", _, _),
    Defect("fails after writing pr.json", "mcp-down-late",
           "fake-cli (anthropic): case 10c: a run whose declared server did not start left OUT/pr.json", _, _),
    # Case 8 reads case 10's artefacts too.
    Defect("the key in an artefact of the reviewer's run alone", "key-in-review",
           "fake-cli (anthropic): case 8: the key canary is in the artefacts", _, _),
    # 10r: a harness with no mcp.sh. Good, then agents.py's refusal wrong each way 10r reads it.
    Defect("fake-cli without an mcp.sh", "", "GO", no_mcp_sh, _),
    Defect("agents.py accepts a harness with no mcp.sh", "agents-mcp-accepts",
           "fake-cli (anthropic): case 10r: core/agents.py mcp --harness fake-cli exited 0 for a harness with no mcp.sh, want 1",
           agents_shim, _),
    Defect("agents.py's refusal has a second line", "agents-mcp-chatty",
           "fake-cli (anthropic): case 10r: core/agents.py mcp's refusal is 2 lines on stdout, want one", agents_shim, _),
    Defect("agents.py refuses in other words", "agents-mcp-other-words",
           "fake-cli (anthropic): case 10r: core/agents.py mcp's refusal is not ADR 0073 §8's line", agents_shim, _),
    Defect("agents.py names a harness with no mcp.sh", "agents-mcp-wrong-list",
           "fake-cli (anthropic): case 10r: the refusal lists 'fake-cli fake-replay', want the harnesses that have "
           "adapters/<name>/mcp.sh: 'fake-replay'", agents_shim, _),
    Defect("agents.py refuses and writes decl.json", "agents-mcp-writes-decl",
           "fake-cli (anthropic): case 10r: core/agents.py mcp refused and still wrote decl.json", agents_shim, _),
)

# The probe on its own (docs/adr/0046): each row a name, the ways that must
# read `open`, the holes opened in the stand-in network, the hosts added to
# the proxy's list ({port} the stub's, {next} the one after it), whether the
# proxy is cut off, and whether the stub has no case (it answers 503: an error
# page is an answer, not a refusal).
Probe = namedtuple("Probe", "name open holes allow cut nocase")
PROBES = (
    Probe("every way shut", "", "", "", False, False),
    Probe("the proxy's list holds the sink", "a b", "", "conformance-sink:{port}", False, False),
    Probe("a server's error page is not a refusal", "a b", "", "conformance-sink:{port}", False, True),
    Probe("the list holds the model's name on another port", "c", "", "conformance-model:{next}", False, False),
    Probe("the list holds example.com", "d", "", "example.com:443", False, False),
    Probe("the stub's address is reachable", "f", "tcp-stub", "", False, False),
    Probe("the sink's name resolves and its address is reachable", "e f", "dns-sink tcp-stub", "", False, False),
    Probe("1.1.1.1:443 is reachable", "g", "tcp-public", "", False, False),
    Probe("example.com resolves", "h", "dns-public", "", False, False),
    Probe("the proxy cannot be reached: nothing is open, and the suite must notice nothing was tried", "", "", "",
          True, False),
)

def expected_lines(open_ways):
    """What the probe must print: eight lines, and nothing else."""
    opened = open_ways.split()
    return "\n".join("egress-probe %s %s" % (c, "open" if c in opened else "blocked") for c in WAYS)


def verdict_flaw(name, want, rc, verdict, removed, stderr=""):
    """Why the suite's verdict on a row is wrong, or None. REMOVED: how many
    times the run removed the proxy (the good run's must be 2: at its start, so
    a proxy from an earlier run does not serve this one, and on exit); STDERR:
    the run's, where a good run says it ran each provider."""
    if want == "GO":
        if not (verdict.startswith(GO_PREFIX) and rc == 0):
            return "the good fake-cli was refused (%s)" % name
        if not all(line in stderr for line in PROGRESS):
            return "the good fake-cli's run has no progress line for each of its two providers (%s)" % name
        if removed != 2:
            return "the good fake-cli's run did not remove egress exactly at its start and on exit (%s)" % name
        return None
    if rc != 1:
        return "the suite accepted a harness that is wrong (%s): exit %d" % (name, rc)
    if not (verdict.startswith(NOGO_PREFIX) and want in verdict[len(NOGO_PREFIX):]):
        return "the suite refused '%s' for another reason than expected" % name
    return None


def probe_flaw(name, want, got, secret):
    if secret in got:
        return "the probe printed what it read (%s)" % name
    if got.rstrip("\n") != want:
        return "the probe's verdict is wrong (%s)" % name
    return None


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def records(path):
    return [json.loads(line) for line in read(path).splitlines() if line.strip()]


# --- the run --------------------------------------------------------------------------


class Proof:
    def __init__(self, work):
        self.work = work
        self.bin = work + "/bin"
        os.makedirs(self.bin)
        for f in ("docker-compose", "freeport.py"):
            shutil.copy(os.path.join(FAKE, f), self.bin + "/" + f)
        os.chmod(self.bin + "/docker-compose", 0o755)  # nosec B103 - the stand-in must run from PATH
        # The workbench has sha256sum; a host this may also run on may not.
        if not shutil.which("sha256sum"):
            put(self.bin, "sha256sum", '#!/bin/sh\nexec shasum -a 256 "$@"\n')
            os.chmod(self.bin + "/sha256sum", 0o755)  # nosec B103 - as above
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("CONFORMANCE_HARNESS", "CONFORMANCE_PORT", "CONFORMANCE_TIMEOUT_S",
                                 "PROOF_COMPOSE_JSON", "FAKE_DEFECT")}
        self.env["PATH"] = self.bin + ":" + self.env.get("PATH", "")

    def freeport(self):
        return int(proc(["python3", self.bin + "/freeport.py"], stdin=DEVNULL, capture_output=True, text=True).stdout)

    def compose(self, cwd, *args, **env):
        return proc(["docker-compose", *args], cwd=cwd, stdin=DEVNULL, stdout=2, env=dict(self.env, **env)).returncode

    # -- the port picker, under the proxy's load
    def picker(self):
        """Every port the picker hands out binds the way its listener binds it
        (stub.py: ThreadingHTTPServer on 0.0.0.0; core/egress.py: SO_REUSEADDR on
        0.0.0.0) while the proxy's outgoing sockets hold ports on 127.0.0.2. The
        load is planted as the proxy leaves it: connections from ("127.0.0.2", 0)
        that close first, so each stays in TIME_WAIT for the kernel's 60 s."""
        lo, hi = map(int, read("/proc/sys/net/ipv4/ip_local_port_range").split())
        sink = socket.socket()
        sink.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sink.bind(("127.0.0.1", 0))
        sink.listen(128)
        held = set()
        for _ in range((hi - lo + 1) // 4):
            c = socket.create_connection(sink.getsockname(), 5, ("127.0.0.2", 0))
            held.add(c.getsockname()[1])
            a, _ = sink.accept()
            c.close()
            a.close()
        sink.close()
        for i in range(20):
            port = self.freeport()
            try:
                ThreadingHTTPServer(("0.0.0.0", port), BaseHTTPRequestHandler).server_close()  # nosec B104 - stub.py's bind
                s = socket.socket()
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))  # nosec B104 - core/egress.py's bind
                s.close()
            except OSError as e:
                raise NoGo("the port picker handed out a port its listener cannot bind (the proof's freeport)",
                           "pick %d of 20: port %d, %s the %d the proxy's sockets hold: %s"
                           % (i + 1, port, "one of" if port in held else "not one of", len(held), e))

    # -- the probe, one way out at a time
    def probe_proof(self):
        """Without the suite: the real stub and the real proxy on local ports (the
        stand-in compose starts both, in a tree of their own), the probe under the
        stand-in network, one hole opened at a time. The expected output is exact:
        eight lines, and nothing else, never what the probe read."""
        pd = self.work + "/probe"
        for d in ("gates", ".crew/conformance/model", ".crew/egress", "core"):
            os.makedirs(os.path.join(pd, d), exist_ok=True)
        shutil.copytree(SUITE, pd + "/" + SUITE)
        shutil.copy("core/egress.py", pd + "/core/egress.py")
        shutil.copy(FAKE + "/egress_wrap.py", pd + "/proof-egress.py")
        model = pd + "/.crew/conformance/model"
        put(model, "case.json", '{"case":"probe/1","cwd":"/w","steps":[{"op":"final","text":"x"}]}\n')
        secret = "probe-secret-%d-%s" % (os.getpid(), os.urandom(8).hex())
        stub_port = self.freeport()
        try:
            self.compose(pd, "up", "-d", "conformance-model", CONFORMANCE_PORT=str(stub_port),
                         CONFORMANCE_CANARY="probe-stub-key")
            self.wait(model + "/ready", "the stub did not start for the probe's proof", pd + "/.crew/conformance/stub.err")
            self.compose(pd, "up", "-d", "egress")
            self.wait(pd + "/.crew/egress/ready", "the proxy did not start for the probe's proof", pd + "/.proof/egress.err")
            proxy_port = read(pd + "/.proof/egress.port").strip()
            reqlog = model + "/probe/1/requests.jsonl"
            for row in PROBES:
                if row.nocase:
                    os.replace(model + "/case.json", model + "/case.json.off")
                self.probe_must(pd, row, stub_port, proxy_port, secret)
                if row.nocase:
                    os.replace(model + "/case.json.off", model + "/case.json")
                if row.name == "the proxy's list holds the sink":
                    # The leak is recorded where the suite will look: on the sink's name, from the proxy's address.
                    if len([r for r in records(reqlog) if r.get("sink") is True and r.get("path") == "/exfil"
                            and r.get("peer") == "127.0.0.2"]) < 2:
                        raise NoGo("the stub did not record the probe's leak through the proxy (sink, /exfil, the "
                                   "proxy's address)")
                if row.name == "the sink's name resolves and its address is reachable":
                    if len([r for r in records(reqlog) if r.get("path") == "/exfil" and r.get("peer") == "127.0.0.1"]) < 2:
                        raise NoGo("the stub did not record the probe's direct leak (/exfil from an address that is "
                                   "not the proxy's)")
        finally:
            self.compose(pd, "rm", "-sf", "conformance-model")
            self.compose(pd, "rm", "-sf", "egress")

    def wait(self, path, why, err):
        for _ in range(21):
            if os.path.isfile(path):
                return
            time.sleep(1)
        raise NoGo(why, read(err))

    def probe_must(self, pd, row, stub_port, proxy_port, secret):
        extra = row.allow.format(port=stub_port, next=stub_port + 1)
        put(pd, ".crew/egress/allow", "conformance-model:%d\n%s\n" % (stub_port, extra))
        env = dict(self.env, NETSHIM_OPEN=row.holes, NETSHIM_EGRESS_PORT=proxy_port, NETSHIM_STUB_PORT=str(stub_port),
                   HTTP_PROXY="http://egress:3128", PYTHONPATH=os.path.abspath(FAKE + "/netshim"),
                   FAKE_KEY_VALUE=secret)
        if row.cut:
            env["NETSHIM_CUT"] = "1"
        got = proc(["python3", SUITE + "/probe.py", "10.9.0.3", str(stub_port)], stdin=DEVNULL, capture_output=True,
                   text=True, env=env)
        if got.returncode != 0:
            raise NoGo("the probe crashed (%s)" % row.name, got.stderr)
        want = expected_lines(row.open)
        flaw = probe_flaw(row.name, want, got.stdout, secret)
        if flaw:
            raise NoGo(flaw, "wanted:", want, "got:", got.stdout, got.stderr)

    # -- the suite, on a fresh tree per row
    def prove(self, i, row):
        t = self.work + "/tree"
        shutil.rmtree(t, ignore_errors=True)
        build_tree(t)
        if row.tweak:
            row.tweak(t)
        invoked = self.work + "/invoked"
        if os.path.exists(invoked):
            os.remove(invoked)
        errfile = "%s/run-%d.err" % (self.work, i)
        env = dict(self.env, PROOF_INVOKED=invoked, CONFORMANCE="1", CONFORMANCE_PORT=str(self.freeport()),
                   CONFORMANCE_TIMEOUT_S="2", PROOF_COMPOSE_JSON=json.dumps(row.compose) if row.compose else "",
                   WORKBENCH_IMAGE="proof", COMPOSE_FILE="compose.yaml", WORKLOAD="w", WORKLOAD_CONTRACT="c",
                   FAKE_DEFECT=row.defect)
        with open(errfile, "w") as err:
            got = proc(["sh", "gates/harness-conformance.sh"], cwd=t, stdin=DEVNULL, stdout=-1, stderr=err, text=True,
                       env=env)
        verdict = (got.stdout.splitlines() or [""])[0]
        say("harness-conformance-proof: [%s] %s => %s" % (row.name, row.defect or "-", verdict))
        # Whatever the verdict, the suite leaves no proxy behind (docs/adr/0046: it
        # removes `egress` on exit, like the scripted model).
        if os.path.exists(t + "/.proof/egress.pid"):
            raise NoGo("the suite left the egress proxy running (%s)" % row.name)
        removed = len(read(t + "/.proof/egress.removed").splitlines())
        flaw = verdict_flaw(row.name, row.want, got.returncode, verdict, removed, read(errfile))
        if flaw:
            raise NoGo(flaw, "wanted: " + row.want, "got: " + verdict, "--- the suite's stderr, last 30 lines",
                       "\n".join(read(errfile).splitlines()[-30:]))

    def kill_leftovers(self):
        """For a run that was cut short: the stand-in compose starts the proxy and
        the stub in the background, and the suite removes them on exit."""
        for d in (self.work + "/tree/.proof", self.work + "/tree/.crew/conformance", self.work + "/probe/.proof",
                  self.work + "/probe/.crew/conformance"):
            for f in ("egress.pid", "stub.pid"):
                try:
                    os.kill(int(read(os.path.join(d, f)).strip()), 15)
                except (OSError, ValueError):
                    pass


def judge(argv):
    work = tempfile.mkdtemp()
    proof = Proof(work)
    try:
        proof.picker()
        proof.probe_proof()
        for i, row in enumerate(DEFECTS, 1):
            proof.prove(i, row)
    finally:
        proof.kill_leftovers()
        shutil.rmtree(work, ignore_errors=True)
    return None


if __name__ == "__main__":
    sys.exit(main(judge))
