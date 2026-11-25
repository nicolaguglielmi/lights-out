# SPDX-License-Identifier: Apache-2.0
"""The unified power-user CLI (docs/adr/0112): one grammar over the flat commands.

    python3 core/cli.py [--json|--jsonl] [--dry-run] <domain> <verb> [--flag value | KEY=VALUE ...]
    python3 core/cli.py <flat command> [KEY=VALUE ...]        (every flat command keeps working)
    python3 core/cli.py completion bash|zsh|fish
    python3 core/cli.py envelope --command <flat> --rc <n> [--jsonl] [KEY=VALUE ...]   (stdin: the stdout to wrap)

Domains: repo, ticket, release, ops, config, completion. A verb is one flat
command; the plan is dispatched to `./go <flat> ...` (LIGHTS_OUT_GO overrides
the path). Exit codes: 0 GO, 1 NO-GO, 2 usage or contract error, 3 waiting on
a human or a lock, 4 budget stop. `--json` prints one envelope line, `--jsonl`
one event per stdout line then a verdict; neither exists on gate-shaped
commands, which keep the one-line gate contract. Secret values are never
printed: only whether a secret-named environment variable is set.

Stdlib only. The same table drives the wrapper's and the entrypoint's front
half (ci/tests/test_cli_wrapper_parity.py keeps the three in step).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Callable, NamedTuple

SCHEMA = "lights-out.cli/1"
EXIT_GO, EXIT_NOGO, EXIT_USAGE, EXIT_WAITING, EXIT_BUDGET = 0, 1, 2, 3, 4

DOMAINS = ("repo", "ticket", "release", "ops", "config", "completion")
SHELLS = ("bash", "zsh", "fish")

# The 29 keys of ADR 0105, in the wrapper's order: allowed on every command.
GLOBAL_KEYS = (
    "BASE", "HEAD", "JUDGED", "STAGED", "ALERT", "INCIDENT", "PROFILE", "MODEL_PROVIDER", "MODEL",
    "TICKET", "TEXT", "ANSWER", "DECISIONS", "VERSION", "CANARY_VERSION", "CANARY_TAG", "GO_FOR_LAUNCH",
    "REASON", "MODE", "PRESET", "CLOUD", "DELIVERY", "CODE_HOST", "AGENT_HARNESS", "PROVIDER",
    "ADAPTERS_PATH", "WINDOW_DAYS", "SPEND_BUDGET_TICKET_USD", "SPEND_BUDGET_DAY_USD",
)
# The per-command extension keys of ADR 0112 §2, in completion order.
EXTRA_KEYS = (
    "REQUEST_BY", "REQUEST_VIA", "ATTEMPT", "ONCE", "POLL_S", "RUNNER_WORKERS", "WORKERS",
    "INTAKE_NETWORK_SPLIT", "NETWORK_AUTO_SPLIT", "ALERT_FILE", "RELEASE_RECORD",
    "NAME", "FROM", "TO", "LOGS", "MAP_FILE", "SLO_MODE", "CORPUS_FULL",
    "LOGBOOK_FILE", "LOGBOOK", "URL", "JSON", "VIEW", "WIDTH", "HEIGHT", "TICKET_FILE",
    "PROVENANCE_DIR", "PROVENANCE_ATTACH", "PROVENANCE_ATTACH_REQUIRED",
    "TRACKER", "CHARTS", "METRICS", "ORCHESTRATOR",
    "REVIEWER_ROLE", "THINKING", "PORTFOLIO_REPOS",
)

START_NATIVE = (
    ("--non-interactive", False), ("--preset", True), ("--provider", True), ("--harness", True),
    ("--delivery", True), ("--profile", True), ("--ticket-budget-usd", True), ("--day-budget-usd", True),
    ("--mode", True),
)
INIT_NATIVE = (
    ("--apply", False), ("--branch", True), ("--commit", False),
    ("--force", False), ("--workspace", False),
    ("--baseline-mode", True), ("--baseline-max-files", True),
)
GENESIS_NATIVE = (
    ("--repo", True), ("--brief", True), ("--brief-file", True),
    ("--stack", True), ("--name", True), ("--port", True),
    ("--tickets-count", True), ("--no-commit", False),
    ("--today", True), ("--stacks", True),
)
EPICS_NATIVE = (
    ("--id", True), ("--title", True), ("--tickets", True),
    ("--tickets-dir", True), ("--state-dir", True), ("--no-publish", False),
    ("--repo", True), ("--out", True), ("--manifest", True),
    ("--stacks", True), ("--today", True), ("--baseline-mode", True),
    ("--changed", True),
)
SPEC_SPLIT_NATIVE = (("--in-place", False),)
M24_CHECK_NATIVE = (
    ("--data", True), ("--logbook", True), ("--logbooks", True),
    ("--min-providers", True), ("--allow-incomplete", False),
    ("--out", True), ("--handoff", True), ("--scope", True),
    ("--repository", True), ("--kind", True), ("--phase", True),
    ("--replace", False),
)
DEMO_LOOP_NATIVE = (
    ("--out", True), ("--budget", True), ("--inject-break", True),
)
JURY_NATIVE = (
    ("--author-provider", True), ("--author-harness", True),
    ("--risk", True), ("--role", True),
    ("--run-json", True), ("--review-run-json", True),
    ("--doc", True), ("--providers", True),
)
MAP_BRIEF_EVAL_NATIVE = (
    ("--repo", True), ("--rev", True), ("--map", True),
    ("--tickets", True), ("--exclude", True), ("--worker-brief", True),
    ("--write-map", False), ("--no-determinism-check", False),
)
LIVE_PREFLIGHT_NATIVE = (
    ("--repo", True), ("--strict", False),
)
THRESHOLDS_NATIVE = (
    ("--root", True), ("--contract-dir", True), ("--no-git", False),
)
PROVENANCE_NATIVE = (
    ("--attach", False), ("--attach-required", False), ("--required", False),
    ("--statement", True), ("--bundle", True), ("--sha", True),
    ("--out-receipt", True), ("--key", True), ("--out-dir", True), ("--repo", True),
)
REVIEWER_EVAL_NATIVE = (
    ("--fixtures", True), ("--repo", True), ("--mode", True), ("--role", True),
    ("--scope", True), ("--provider", True), ("--model", True), ("--thinking", True),
    ("--record-replay", False), ("--strict-replay-sha", False), ("--no-gitleaks", False),
    ("--only-cases", True), ("--compare-manifest", True), ("--compare-tickets", True),
    ("--tickets-dir", True), ("--patches-dir", True), ("--map", True),
    ("--save-compare-replay", True), ("--only-compare", False),
)
PORTFOLIO_NATIVE = (
    ("--repos", True), ("--repo", True),
)
NATIVE_FLAGS = (
    "--json", "--jsonl", "--help", "--engine", "--non-interactive", "--mode", "--preset", "--provider",
    "--harness", "--delivery", "--profile", "--ticket-budget-usd", "--day-budget-usd", "--level",
    "--staged", "--once", "--apply", "--branch", "--commit", "--force", "--workspace", "--baseline-mode",
    "--baseline-max-files", "--check", "--repo", "--brief", "--brief-file", "--stack", "--name", "--port",
    "--tickets-count", "--no-commit", "--today", "--stacks", "--id", "--title", "--tickets", "--tickets-dir",
    "--state-dir", "--no-publish", "--out", "--manifest", "--changed", "--in-place", "--data", "--logbook",
    "--logbooks", "--min-providers", "--allow-incomplete", "--handoff", "--scope", "--repository",
    "--kind", "--phase", "--replace", "--budget", "--inject-break", "--author-provider", "--author-harness",
    "--risk", "--role", "--run-json", "--review-run-json", "--doc", "--providers", "--rev", "--map",
    "--exclude", "--worker-brief", "--write-map", "--no-determinism-check", "--strict",
    "--root", "--contract-dir", "--no-git",
    "--attach", "--attach-required", "--required", "--statement", "--bundle", "--sha", "--out-receipt",
    "--key", "--out-dir",
    "--fixtures", "--model", "--thinking", "--record-replay", "--strict-replay-sha", "--no-gitleaks",
    "--only-cases", "--compare-manifest", "--compare-tickets", "--patches-dir", "--save-compare-replay",
    "--only-compare", "--repos",
)
BOOL_FLAGS = {"--staged": "STAGED", "--once": "ONCE"}
ENGINE_FLAT = ("init", "gates", "test-proof", "test-integrity", "adr-check", "migration-safety")
GLOBAL_OPTS = ("--json", "--jsonl", "--dry-run")
GATE_SHAPED = ("init", "try", "gates", "test-proof", "test-integrity", "adr-check", "migration-safety",
               "doctor", "map", "corpus", "production-readiness", "tracker-conformance", "charts-conformance",
               "metrics-conformance", "orchestrator-conformance")
NATIVE_JSON = ("start", "next", "top")
TAIL_FLAT = ("console", "version")

SECRET_KEY_RE = re.compile(r"_(KEY|TOKEN|SECRET|PASSWORD|HEADER)$")
SECRET_NAME_RE = re.compile(r"_(KEY|TOKEN|SECRET|PASSWORD)$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BUDGET_RE = re.compile(r"\bbudget\b|SPEND_BUDGET|SPEND_STOP", re.I)
WAITING_RE = re.compile(r"needs-human|awaiting|waiting (for|on)|held at|lockfile|\blocked\b", re.I)
MIN_REDACT = 4  # ADR 0112 §4: shorter values would mangle ordinary text


class Command(NamedTuple):
    domain: str
    verb: str
    flat: str
    keys: tuple = ()
    native: tuple = ()
    positional: int = 0
    surface: str = "image"


def _c(domain, verb, flat=None, **kw):
    return Command(domain, verb, flat or verb, **kw)


COMMANDS = (
    _c("repo", "init", positional=1, native=INIT_NATIVE),
    _c("repo", "try", positional=1),
    _c("repo", "gates"),
    _c("repo", "test-proof"),
    _c("repo", "test-integrity"),
    _c("repo", "adr-check"),
    _c("repo", "migration-safety", keys=("TICKET_FILE",)),
    _c("repo", "doctor", native=(("--level", True),)),
    _c("repo", "map", keys=("MAP_FILE",)),
    _c("repo", "corpus", keys=("CORPUS_FULL",), surface="clone"),
    _c("repo", "genesis", positional=1, native=GENESIS_NATIVE),
    _c("repo", "map-brief-eval", native=MAP_BRIEF_EVAL_NATIVE),
    _c("ticket", "intake", keys=("INTAKE_NETWORK_SPLIT",)),
    _c("ticket", "request", keys=("REQUEST_BY", "REQUEST_VIA")),
    _c("ticket", "answer", keys=("INTAKE_NETWORK_SPLIT",)),
    _c("ticket", "intake-confirm"),
    _c("ticket", "assign", keys=("NETWORK_AUTO_SPLIT",)),
    _c("ticket", "resume", keys=("INTAKE_NETWORK_SPLIT", "NETWORK_AUTO_SPLIT")),
    _c("ticket", "network", keys=("ATTEMPT",)),
    _c("ticket", "audit"),
    _c("ticket", "epics", positional=8, native=EPICS_NATIVE),
    _c("ticket", "spec-split", positional=2, native=SPEC_SPLIT_NATIVE),
    _c("release", "up"),
    _c("release", "down"),
    _c("release", "smoke"),
    _c("release", "deploy", "release"),
    _c("release", "promote"),
    _c("release", "evidence"),
    _c("release", "provenance", keys=("PROVENANCE_DIR", "PROVENANCE_ATTACH", "PROVENANCE_ATTACH_REQUIRED"),
       positional=2, native=PROVENANCE_NATIVE),
    _c("release", "production-readiness"),
    _c("ops", "watch", keys=("ONCE", "POLL_S", "RUNNER_WORKERS", "WORKERS")),
    _c("ops", "alert-webhook", keys=("ALERT_FILE",)),
    _c("ops", "triage"),
    _c("ops", "auto-incident", keys=("RELEASE_RECORD",)),
    _c("ops", "incident", keys=("NAME", "FROM", "TO", "LOGS")),
    _c("ops", "postmortem"),
    _c("ops", "dora"),
    _c("ops", "spend"),
    _c("ops", "top", keys=("LOGBOOK_FILE", "LOGBOOK", "URL", "ONCE", "JSON", "POLL_S", "VIEW", "WIDTH", "HEIGHT")),
    _c("ops", "charts-conformance", keys=("CHARTS",), surface="clone"),
    _c("ops", "metrics-conformance", keys=("METRICS",), surface="clone"),
    _c("ops", "orchestrator-conformance", keys=("ORCHESTRATOR",), surface="clone"),
    _c("ops", "m24-check", positional=8, native=M24_CHECK_NATIVE),
    _c("ops", "demo-loop", positional=1, native=DEMO_LOOP_NATIVE),
    _c("ops", "reviewer-eval", keys=("JSON", "REVIEWER_ROLE", "THINKING"), native=REVIEWER_EVAL_NATIVE),
    _c("ops", "portfolio", keys=("PORTFOLIO_REPOS", "JSON"), positional=8, native=PORTFOLIO_NATIVE),
    _c("config", "start", native=START_NATIVE),
    _c("config", "next", native=(("--mode", True),)),
    _c("config", "cloud-setup", keys=("SLO_MODE",)),
    _c("config", "scoreboard"),
    _c("config", "agents", surface="clone"),
    _c("config", "tracker-conformance", keys=("TRACKER",), surface="clone"),
    _c("config", "console-build", native=(("--check", False),), surface="clone"),
    _c("config", "jury", positional=1, native=JURY_NATIVE),
    _c("config", "live-preflight", native=LIVE_PREFLIGHT_NATIVE),
    _c("config", "thresholds", keys=("JSON",), native=THRESHOLDS_NATIVE),
)
FLAT_ONLY = (Command("", "console", "console"),)
FLAT = {c.flat: c for c in (*COMMANDS, *FLAT_ONLY)}
COMPLETION_VERBS = {"completion": SHELLS}


class UsageError(Exception):
    """The command line is refused before anything runs (exit 2)."""

    def __init__(self, message, json_mode=""):
        super().__init__(message)
        self.json_mode = json_mode


class Plan(NamedTuple):
    command: Command
    tail: list
    positional: list
    pairs: dict
    json_mode: str
    dry_run: bool


def verbs(domain):
    if domain in COMPLETION_VERBS:
        return list(COMPLETION_VERBS[domain])
    return [c.verb for c in COMMANDS if c.domain == domain]


def display(command):
    return f"{command.domain} {command.verb}" if command.domain else command.verb


def gate_shaped(command):
    return command.flat in GATE_SHAPED


def flag_key(name):
    return name[2:].upper().replace("-", "_")


def key_flag(key):
    return "--" + key.lower().replace("_", "-")


def allowed_keys(command):
    return list(GLOBAL_KEYS) + [k for k in command.keys if k not in GLOBAL_KEYS]


def _is_secret_key(key):
    return bool(SECRET_KEY_RE.search(key))


def _native(command, name):
    for flag, takes in command.native:
        if flag == name:
            return takes
    return None


def _resolve(args, state):
    """The command and the remaining tokens, or a UsageError."""
    head, rest = args[0], args[1:]
    if head == "release" and rest and rest[0] in verbs("release"):
        domain = "release"
    elif head in FLAT:
        return FLAT[head], rest
    elif head in DOMAINS:
        domain = head
    else:
        raise UsageError(f"unknown command '{head}'; a domain ({', '.join(DOMAINS)}) and a verb, "
                         f"or one of: {' '.join(FLAT)}", state["json"])
    if domain == "completion":
        raise UsageError("completion takes a shell: " + " ".join(SHELLS), state["json"])
    if not rest or rest[0].startswith("-"):
        raise UsageError(f"{domain} needs a verb: {' '.join(verbs(domain))}", state["json"])
    for command in COMMANDS:
        if command.domain == domain and command.verb == rest[0]:
            return command, rest[1:]
    raise UsageError(f"unknown {domain} verb '{rest[0]}'; one of: {' '.join(verbs(domain))}", state["json"])


def parse(argv):
    """argv (without the program name) to a Plan; UsageError otherwise."""
    args = list(argv)
    state = {"json": ""}
    dry_run = False
    while args and args[0] in GLOBAL_OPTS:
        opt = args.pop(0)
        if opt == "--dry-run":
            dry_run = True
        elif state["json"] and state["json"] != opt[2:]:
            raise UsageError("--json and --jsonl are mutually exclusive")
        else:
            state["json"] = opt[2:]
    if not args:
        raise UsageError("a command is needed", state["json"])
    command, rest = _resolve(args, state)
    native_json = command.flat in NATIVE_JSON

    tail, positional, pairs = [], [], {}
    if state["json"] and native_json and state["json"] == "json":
        tail.append("--json")
        state["json"] = ""
    elif state["json"] and native_json:
        raise UsageError(f"--jsonl is not offered for {command.flat}; --json is its native document")
    elif state["json"] and gate_shaped(command):
        state["json"] = ""
        raise UsageError(f"{command.flat} is a gate: it keeps the one-line GO/NO-GO contract and takes "
                         "no --json or --jsonl; read its line and its exit code")
    allowed = allowed_keys(command)

    def need(i, what):
        if i >= len(rest):
            raise UsageError(f"{what} requires a value", state["json"])
        return rest[i]

    i = 0
    while i < len(rest):
        a = rest[i]
        i += 1
        if a == "--dry-run":
            dry_run = True
        elif a in ("--json", "--jsonl"):
            if native_json and a == "--json":
                tail.append(a)
            elif native_json:
                raise UsageError(f"{a} is not offered for {command.flat}; --json is its native document",
                                 state["json"])
            elif gate_shaped(command):
                state["json"] = ""
                raise UsageError(f"{command.flat} is a gate: it keeps the one-line GO/NO-GO contract and takes "
                                 f"no {a}; read its line and its exit code")
            elif state["json"] and state["json"] != a[2:]:
                raise UsageError("--json and --jsonl are mutually exclusive", state["json"])
            else:
                state["json"] = a[2:]
        elif a.startswith("--"):
            name, eq, value = a.partition("=")
            takes = _native(command, name)
            if takes is not None and (takes or not eq):
                tail.append(a)
                if takes and not eq:
                    tail.append(need(i, name))
                    i += 1
            elif name == "--engine" and command.flat in ENGINE_FLAT and not eq:
                tail.append(a)
            else:
                key = flag_key(name)
                if name in BOOL_FLAGS and not eq:
                    key, value = BOOL_FLAGS[name], "1"
                elif not eq:
                    value = need(i, name)
                    i += 1
                if _is_secret_key(key):
                    raise UsageError(f"{name}: secrets are passed by environment variable name, never as arguments",
                                     state["json"])
                if key not in allowed:
                    raise UsageError(f"{name} is not an option of {command.domain} {command.verb}; "
                                     f"one of: {' '.join(key_flag(k) for k in allowed)}", state["json"])
                tail.append(f"{key}={value}")
                pairs[key] = value
        elif "=" in a:
            key, _, value = a.partition("=")
            if not IDENT_RE.match(key):
                raise UsageError(f"'{key}=...' is not KEY=VALUE", state["json"])
            if _is_secret_key(key):
                raise UsageError(f"{key}: secrets are passed by environment variable name, never as arguments",
                                 state["json"])
            tail.append(a)
            pairs[key] = value
        elif a.startswith("-"):
            raise UsageError(f"unknown option '{a}'", state["json"])
        elif len(positional) >= command.positional:
            raise UsageError(f"{command.domain} {command.verb} takes no argument '{a}'; "
                             "use --flag value or KEY=VALUE", state["json"])
        else:
            positional.append(a)
    return Plan(command, tail, positional, pairs, state["json"], dry_run)


def default_go():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "go")


def go_argv(plan, go=None):
    """The `./go` command line: the flat name, a path if given, then the arguments in order."""
    tail = [t for t in plan.tail if t != "--engine"]
    return [go or default_go(), plan.command.flat, *plan.positional, *tail]


def classify(rc, line, shaped):
    """(verdict, exit code) of a finished command by ADR 0112 §3."""
    line = line or ""
    nogo = line.startswith("NO-GO:")
    if rc == 0 and not nogo:
        return "GO", EXIT_GO
    if nogo and not shaped:
        if BUDGET_RE.search(line):
            return "BUDGET", EXIT_BUDGET
        if WAITING_RE.search(line):
            return "WAITING", EXIT_WAITING
    return "NO-GO", EXIT_NOGO


def secret_presence(environ):
    """Name to boolean for every secret-named variable and every LIGHTS_OUT_SECRET_NAMES entry."""
    names = {n for n in environ if SECRET_NAME_RE.search(n)}
    names.update((environ.get("LIGHTS_OUT_SECRET_NAMES") or "").split())
    return {n: bool(environ.get(n)) for n in sorted(names)}


def _secret_values(environ):
    names = {n for n in environ if SECRET_KEY_RE.search(n)}
    names.update((environ.get("LIGHTS_OUT_SECRET_NAMES") or "").split())
    values = {environ[n] for n in names if len(environ.get(n) or "") >= MIN_REDACT}
    return sorted(values, key=len, reverse=True)


def _redact(text, values):
    for value in values:
        text = text.replace(value, "<redacted>")
    return text


def _last_line(lines):
    for line in reversed(lines):
        if line.strip():
            return line
    return ""


def envelope(plan, rc, lines, environ):
    """The one-line JSON document of a finished command; never a secret value."""
    values = _secret_values(environ)
    raw = _last_line(lines)
    verdict, code = classify(rc, raw, gate_shaped(plan.command))
    return {
        "schema": SCHEMA,
        "command": display(plan.command),
        "flat": plan.command.flat,
        "verdict": verdict,
        "exit_code": code,
        "line": _redact(raw, values),
        "args": {k: _redact(v, values) for k, v in plan.pairs.items()},
        "secrets": secret_presence(environ),
    }


def usage_envelope(message, environ):
    return {
        "schema": SCHEMA, "command": "", "flat": "", "verdict": "USAGE", "exit_code": EXIT_USAGE,
        "line": "", "args": {}, "secrets": secret_presence(environ),
        "error": _redact(message, _secret_values(environ)),
    }


def subprocess_runner(argv, env, on_line):
    """Run argv, hand every stdout line to on_line as it arrives, return the exit code."""
    with subprocess.Popen(argv, stdout=subprocess.PIPE, env=env, text=True, bufsize=1) as proc:
        for line in proc.stdout:
            on_line(line.rstrip("\n"))
        return proc.wait()


# ---------------------------------------------------------------- completions

def top_words():
    words = list(DOMAINS) + [c.flat for c in COMMANDS] + list(TAIL_FLAT)
    return list(dict.fromkeys(words))


def flag_words():
    words = list(NATIVE_FLAGS)
    for key in (*GLOBAL_KEYS, *EXTRA_KEYS):
        flag = key_flag(key)
        if flag not in words:
            words.append(flag)
    return words


def completion(shell):
    top, flags = " ".join(top_words()), " ".join(flag_words())
    if shell == "bash":
        lines = [
            '# lights-out bash completion: eval "$(lights-out completion bash)"',
            "_lights_out() {",
            "  local cur=${COMP_WORDS[COMP_CWORD]}",
            '  if [ "$COMP_CWORD" -eq 1 ]; then',
            f'    COMPREPLY=($(compgen -W "{top}" -- "$cur"))',
            "    return 0",
            "  fi",
            '  case "${COMP_WORDS[1]}:$COMP_CWORD" in',
        ]
        lines += [f'    {d}:2) COMPREPLY=($(compgen -W "{" ".join(verbs(d))}" -- "$cur")) ;;' for d in DOMAINS]
        lines += [f'    *) COMPREPLY=($(compgen -W "{flags}" -- "$cur")) ;;', "  esac", "}",
                  "complete -F _lights_out lights-out"]
    elif shell == "zsh":
        lines = [
            "#compdef lights-out",
            '# lights-out zsh completion: eval "$(lights-out completion zsh)"',
            "_lights_out() {",
            "  if (( CURRENT == 2 )); then",
            f"    compadd -- {top}",
            "    return 0",
            "  fi",
            '  case "${words[2]}:$CURRENT" in',
        ]
        lines += [f'    {d}:3) compadd -- {" ".join(verbs(d))} ;;' for d in DOMAINS]
        lines += [f"    *) compadd -- {flags} ;;", "  esac", "}", "compdef _lights_out lights-out"]
    elif shell == "fish":
        lines = [
            "# lights-out fish completion: lights-out completion fish | source",
            "complete -c lights-out -f",
            f"complete -c lights-out -n '__fish_use_subcommand' -a '{top}'",
        ]
        lines += [f"complete -c lights-out -n '__fish_seen_subcommand_from {d}' -a '{' '.join(verbs(d))}'"
                  for d in DOMAINS]
        lines += [f"complete -c lights-out -l {flag[2:]}" for flag in flag_words()]
    else:
        raise UsageError(f"completion supports: {' '.join(SHELLS)}")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------- main

def help_text():
    rows = [f"  {d:<11} {' '.join(verbs(d))}" for d in DOMAINS]
    return (
        "usage: lights-out [--json|--jsonl] <domain> <verb> [--flag value | KEY=VALUE ...]\n"
        "       lights-out <command> [KEY=VALUE ...]          (flat commands keep working)\n\n"
        + "\n".join(rows)
        + "\n\nexit codes: 0 GO, 1 NO-GO, 2 usage error, 3 waiting on a human or a lock, 4 budget stop\n"
    )


def domain_help(domain):
    return f"usage: lights-out {domain} <verb> [--flag value | KEY=VALUE ...]\nverbs: {' '.join(verbs(domain))}\n"


def _emit(out, doc):
    out.write(json.dumps(doc, sort_keys=False) + "\n")
    out.flush()


def _envelope_verb(args, environ, stdin, out, err):
    """`envelope [--json|--jsonl] --command <flat> --rc <n> [command arguments]`: wrap stdin."""
    opts = {"command": "", "rc": None}
    mode, rest = "json", []
    try:
        i = 0
        while i < len(args):
            a = args[i]
            i += 1
            if a == "--":
                rest.extend(args[i:])
                break
            if a in ("--json", "--jsonl"):
                mode = a[2:]
            elif a in ("--command", "--rc"):
                if i >= len(args):
                    raise UsageError(f"{a} requires a value")
                opts[a[2:]] = args[i]
                i += 1
            else:
                rest.append(a)
        command = FLAT.get(opts["command"])
        if command is None or gate_shaped(command):
            raise UsageError(f"envelope: '{opts['command']}' is not a command with a structured output")
        try:
            rc = int(opts["rc"])
        except (TypeError, ValueError):
            raise UsageError("envelope: --rc <exit code> is required") from None
        plan = parse([command.flat, *rest])._replace(json_mode=mode)
    except UsageError as exc:
        err.write(f"lights-out: {exc}\n")
        return EXIT_USAGE
    lines = stdin.read().splitlines()
    doc = envelope(plan, rc, lines, environ)
    if mode == "jsonl":
        values = _secret_values(environ)
        for line in lines:
            _emit(out, {"event": "stdout", "line": _redact(line, values)})
        _emit(out, {"event": "verdict", **doc})
    else:
        _emit(out, doc)
    return doc["exit_code"]


def main(argv=None, env=None, runner: Callable | None = None, stdin=None, stdout=None, stderr=None):
    args = list(sys.argv[1:] if argv is None else argv)
    environ = dict(os.environ if env is None else env)
    out, err = stdout or sys.stdout, stderr or sys.stderr
    stdin = stdin or sys.stdin
    runner = runner or subprocess_runner

    if not args or args[0] in ("help", "-h", "--help"):
        out.write(help_text())
        return EXIT_GO
    if args[0] == "envelope":
        return _envelope_verb(args[1:], environ, stdin, out, err)
    if args[0] == "completion":
        try:
            if len(args) != 2:
                raise UsageError("completion takes exactly one shell: " + " ".join(SHELLS))
            out.write(completion(args[1]))
        except UsageError as exc:
            err.write(f"lights-out: {exc}\n")
            return EXIT_USAGE
        return EXIT_GO
    if args[0] in DOMAINS and len(args) > 1 and args[1] in ("-h", "--help", "help"):
        out.write(domain_help(args[0]))
        return EXIT_GO

    try:
        plan = parse(args)
    except UsageError as exc:
        err.write(f"lights-out: {exc}\n")
        if exc.json_mode:
            doc = usage_envelope(str(exc), environ)
            _emit(out, {"event": "verdict", **doc} if exc.json_mode == "jsonl" else doc)
        return EXIT_USAGE

    go = environ.get("LIGHTS_OUT_GO") or default_go()
    argv_out = go_argv(plan, go)
    if plan.dry_run:
        _emit(out, {"command": display(plan.command), "flat": plan.command.flat,
                    "json": plan.json_mode, "argv": argv_out})
        return EXIT_GO

    lines = []
    values = _secret_values(environ)

    def on_line(line):
        lines.append(line)
        if plan.json_mode == "jsonl":
            _emit(out, {"event": "stdout", "line": _redact(line, values)})
        elif not plan.json_mode:
            out.write(line + "\n")
            out.flush()

    try:
        rc = runner(argv_out, environ, on_line)
    except OSError as exc:
        err.write(f"lights-out: cannot run {argv_out[0]}: {exc}\n")
        rc = EXIT_NOGO
        lines.append(f"NO-GO: {plan.command.flat} — cannot run {argv_out[0]}")
        if not plan.json_mode:
            return rc
    doc = envelope(plan, rc, lines, environ)
    if plan.json_mode == "json":
        _emit(out, doc)
    elif plan.json_mode == "jsonl":
        _emit(out, {"event": "verdict", **doc})
    return doc["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
