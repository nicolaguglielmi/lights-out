"""Adapters outside the repository (docs/adr/0058 §1-§4): stdlib only.

A profile or .env sets ADAPTERS_PATH, one absolute directory; a port
variable names an adapter in it as `outside/<name>`. Only a port with a
conformance suite can take one, and only the bytes its suite passed on:

    python3 core/adapters.py hash <dir>              the adapter's sha256 on stdout
    python3 core/adapters.py admit                   exit 0 and silent when every outside adapter is
                                                     proven (each linked at adapters/outside/<name>);
                                                     otherwise the first refusal, one line on stdout, exit 1
    python3 core/adapters.py trial <PORT>            link PORT's adapter for its suite, unproven, and
                                                     record the hash on trial
    python3 core/adapters.py register <PORT> <suite> after the suite's GO: record the proof, only if the
                                                     bytes are still those on trial
    python3 core/adapters.py untrial <PORT>          the suite has ended, GO or NO-GO: the trial is over

The hash covers every regular file of the adapter's tree, sorted by path:
the path, the executable bit and the content. A symlink or any other file
that is not regular refuses the adapter, since the hash would not cover what
runs. The register, ADAPTERS_REGISTER (default .crew/adapters/register), is
one tab-separated line per proof: port, name, sha256, <suite>@<the first 12
hex of gates/<suite>.sh's sha256>, UTC time; a proof counts only against
the suite file as it is now (§3). It is local to the checkout and written
by `register` only. ADAPTERS_TRIAL=<PORT>,
which a suite's target exports to the makes it starts, admits that port's
adapter on its trial hash. The Makefile turns admit's line into
$(error ...), so the tower refuses before anything runs (§4). A port's
adapter of ours (no `outside/` prefix) is never this module's business.
"""

import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# The ports with a conformance suite (§1), in the order admit reports them.
SUITES = {
    "AGENT_HARNESS": "harness-conformance",
    "CODE_HOST": "codehost-conformance",
    "DELIVERY": "delivery-conformance",
    "STATE_STORE": "statestore-conformance",
    "EVENT_BUS": "eventbus-conformance",
    "REGISTRY": "registry-conformance",
    "FLAGS": "flags-conformance",
}
# The profile's other port variables: an outside adapter there has no suite.
NO_SUITE = ("TRACKER", "ORCHESTRATOR", "METRICS")
PREFIX = "outside/"
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
LINKS = Path("adapters/outside")
USAGE = "usage: adapters.py hash <dir> | admit | trial <PORT> | register <PORT> <suite> | untrial <PORT>"


class Refused(Exception):
    """One line saying why an outside adapter cannot run."""


def tree_hash(root):
    """sha256 over the regular files under root (§2); Refused on anything else."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise Refused(f"{root} is not a directory")
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                raise Refused(f"{rel} in {root} is a symlink; the hash would not cover what it points at")
            if p.is_dir():
                continue
            if not p.is_file():
                raise Refused(f"{rel} in {root} is not a regular file")
            entries.append(rel)
    h = hashlib.sha256()
    for rel in sorted(entries):
        p = root / rel
        exe = "x" if p.stat().st_mode & 0o111 else "-"
        h.update(rel.encode() + b"\0" + exe.encode() + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode() + b"\n")
    return h.hexdigest()


def suite_id(suite):
    """<suite>@<12 hex of gates/<suite>.sh>: the proof names the suite's bytes (§3)."""
    try:
        data = Path(f"gates/{suite}.sh").read_bytes()
    except FileNotFoundError:
        raise Refused(f"gates/{suite}.sh not found") from None
    return f"{suite}@{hashlib.sha256(data).hexdigest()[:12]}"


def register_file(env):
    return Path(env.get("ADAPTERS_REGISTER") or ".crew/adapters/register")


def trial_file(env, port):
    return register_file(env).parent / f"trial-{port}"


def outside_name(port, value):
    """The adapter name in `outside/<name>`, or None for one of ours."""
    if not value.startswith(PREFIX):
        return None
    name = value[len(PREFIX):]
    if not NAME.fullmatch(name):
        raise Refused(f"{port}={value} is refused: [{name}] is not an adapter name ([a-z0-9][a-z0-9-]*)")
    return name


def adapter_dir(env, port, value, name):
    base = env.get("ADAPTERS_PATH", "")
    if not base or not os.path.isabs(base) or not os.path.isdir(base):
        raise Refused(f"{port}={value} is refused: ADAPTERS_PATH [{base}] is not an absolute directory; "
                      "set it in .env or the profile (docs/adr/0058 §1)")
    d = Path(base) / name
    if not d.is_dir() or d.is_symlink():
        raise Refused(f"{port}={value} is refused: {d} is not a directory")
    return d


def link(name, target):
    place = LINKS / name
    if place.is_symlink():
        place.unlink()
    elif place.exists():
        raise Refused(f"{place} is in the way and is not a link; remove it")
    LINKS.mkdir(parents=True, exist_ok=True)
    place.symlink_to(target)


def proven(env, port, name, digest):
    try:
        lines = register_file(env).read_text().splitlines()
    except FileNotFoundError:
        return False
    for line in lines:
        f = line.split("\t")
        if len(f) == 5 and f[0] == port and f[1] == name and f[2] == digest and f[3] == suite_id(SUITES[port]):
            return True
    return False


def on_trial(env, port, digest):
    if env.get("ADAPTERS_TRIAL") != port:
        return False
    try:
        return trial_file(env, port).read_text().strip() == digest
    except FileNotFoundError:
        return False


def admit(env):
    for port in tuple(SUITES) + NO_SUITE:
        value = env.get(port, "")
        name = outside_name(port, value)
        if name is None:
            continue
        if port not in SUITES:
            raise Refused(f"{port}={value} is refused: the port has no conformance suite, "
                          "so no outside adapter can prove itself on it (docs/adr/0058 §1)")
        try:
            d = adapter_dir(env, port, value, name)
            try:
                digest = tree_hash(d)
            except Refused as why:
                raise Refused(f"{port}={value} is refused: {why}") from None
            if not (proven(env, port, name, digest) or on_trial(env, port, digest)):
                suite = SUITES[port]
                raise Refused(f"{port}={value} is refused: sha256 {digest[:12]} has no {suite} GO on record "
                              f"for its suite as it is; run ./go {suite} {port}={value}")
        except Refused:
            if (LINKS / name).is_symlink():
                (LINKS / name).unlink()
            raise
        link(name, d)


def trial(env, port):
    if port not in SUITES and port not in NO_SUITE:
        raise Refused(f"{port} is not a port")
    value = env.get(port, "")
    name = outside_name(port, value)
    if name is None:
        return
    if port not in SUITES:
        raise Refused(f"{port}={value} is refused: the port has no conformance suite (docs/adr/0058 §1)")
    d = adapter_dir(env, port, value, name)
    digest = tree_hash(d)
    link(name, d)
    tf = trial_file(env, port)
    tf.parent.mkdir(parents=True, exist_ok=True)
    tf.write_text(digest + "\n")
    print(f"[adapters] {port}={value} on trial for {SUITES[port]}, sha256 {digest}", file=sys.stderr)


def register(env, port, suite):
    if port not in SUITES and port not in NO_SUITE:
        raise Refused(f"{port} is not a port")
    value = env.get(port, "")
    name = outside_name(port, value)
    if name is None:
        return
    if SUITES.get(port) != suite:
        raise Refused(f"{suite} is not the suite of {port}; nothing recorded")
    tf = trial_file(env, port)
    try:
        tried = tf.read_text().strip()
    except FileNotFoundError:
        raise Refused(f"{port}={value} was never on trial; nothing recorded") from None
    digest = tree_hash(adapter_dir(env, port, value, name))
    tf.unlink()
    if digest != tried:
        raise Refused(f"{port}={value} changed during {suite} (sha256 {tried[:12]} on trial, {digest[:12]} now); "
                      "nothing recorded")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg = register_file(env)
    reg.parent.mkdir(parents=True, exist_ok=True)
    with reg.open("a") as f:
        f.write("\t".join((port, name, digest, suite_id(suite), stamp)) + "\n")
    print(f"[adapters] {port}={value} registered: {suite} GO on sha256 {digest}", file=sys.stderr)


def untrial(env, port):
    trial_file(env, port).unlink(missing_ok=True)


def main(argv, env):
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if cmd == "hash" and len(args) == 1:
            print(tree_hash(args[0]))
        elif cmd == "admit" and not args:
            try:
                admit(env)
            except Refused as why:
                print(why)
                return 1
        elif cmd == "trial" and len(args) == 1:
            trial(env, args[0])
        elif cmd == "untrial" and len(args) == 1:
            untrial(env, args[0])
        elif cmd == "register" and len(args) == 2:
            register(env, args[0], args[1])
        else:
            print(USAGE, file=sys.stderr)
            return 2
    except Refused as why:
        print(f"adapters: {why}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], dict(os.environ)))
