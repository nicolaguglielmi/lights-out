"""Draft a repository's .lights-out/ gates half (docs/adr/0042 §6): stdlib only.

    python3 core/init.py --repo <clone> --out <contract dir> --stacks <stacks dir>
    python3 core/init.py --set-baseline <workload.env> <score> "<provenance>"

The first form reads the clone, infers its stack (python from
pyproject.toml and uv.lock, javascript from package.json and
package-lock.json; python wins when both are there) and writes into
<contract dir> only: workload.env (the twelve keys, every value under a
"# Provenance, ..." comment), worker.md, policy.env and runners/ copied
from <stacks dir>/<stack>/runners/. MUTATION_BASELINE is left empty, for
core/init.sh to measure; the second form writes it in with its provenance.
Prints "STACK=<stack>" on stdout and what it inferred on stderr; on a
repository it cannot draft, exit 1 with one line on stdout saying what it
looked for. Never writes outside <contract dir> (ADR 0037 §1).

The workbench's own runtimes, the facts a draft is matched against: the
versions workbench/Containerfile installs, read on 2026-11-03.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tomllib
from datetime import date

try:
    from . import init_plane
except ImportError:  # run as a script: core/ is sys.path[0]
    import init_plane

WORKBENCH_PYTHON = (3, 13)
WORKBENCH_NODE = 24
# The images a runtime the workbench does not admit gets (docs/adr/0042
# resolved 8): astral's uv images carry a Python and uv; node's carry
# node, npm and procps (Stryker's command runner needs `ps`; the slim
# variant has none, found 2026-11-03). Tags, not digests: init cannot
# resolve one without the engine, and the comment says to pin it.
PYTHON_CANDIDATES = [(3, 14), (3, 13), (3, 12), (3, 11), (3, 10), (3, 9)]
NODE_CANDIDATES = [24, 22, 20, 18]

PLANE_KEYS = ("CANARY_VERSION", "WORKLOAD_KILL_SWITCH", "WORKLOAD_SAMPLE_PATH")

# Which files are tests (docs/adr/0053 §1), a stack's default discovery as
# fnmatch globs, `*` crossing `/`: a root-level and a nested form each, so
# that `test_*` never matches `latest_x.py`.
PYTHON_TEST_PATHS = "test_*.py:*/test_*.py:*_test.py:conftest.py:*/conftest.py"
JAVASCRIPT_TEST_PATHS = "test/*:*/test/*:*.test.*:*-test.*:*_test.*"


class Undraftable(Exception):
    """One line on stdout: what init looked for and did not find."""


# --- version specifiers -------------------------------------------------


def _parse_version(text):
    parts = text.strip().split(".")
    out = []
    for p in parts:
        if p in ("*", "x", "X"):
            break
        if not p.isdigit():
            raise ValueError(text)
        out.append(int(p))
    if not out:
        raise ValueError(text)
    return tuple(out)


def _cmp(a, b):
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def python_admits(spec, version):
    """True when PEP 440 specifier `spec` admits major.minor `version`.

    Handles the operators a requires-python uses: ==, !=, >=, <=, >, <, ~=
    and the trailing .* wildcard; anything else is "not admitted", so a
    specifier init does not understand never yields the workbench.
    """
    spec = spec.strip()
    if not spec:
        return True
    for clause in spec.split(","):
        clause = clause.strip()
        m = re.match(r"^(===|==|!=|~=|>=|<=|>|<)\s*([0-9][0-9.]*(?:\.\*)?)$", clause)
        if not m:
            return False
        op, val = m.groups()
        wildcard = val.endswith(".*")
        try:
            v = _parse_version(val[:-2] if wildcard else val)
        except ValueError:
            return False
        if op in ("==", "===") and wildcard:
            ok = version[: len(v)] == v
        elif op == "!=" and wildcard:
            ok = version[: len(v)] != v
        elif op == "==" or op == "===":
            ok = _cmp(version, v) == 0 if len(v) >= 2 else version[: len(v)] == v
        elif op == "!=":
            ok = _cmp(version, v) != 0
        elif op == ">=":
            ok = _cmp(version, v) >= 0
        elif op == "<=":
            ok = _cmp(version, v) <= 0
        elif op == ">":
            ok = _cmp(version, v) > 0
        elif op == "<":
            ok = _cmp(version, v) < 0
        elif op == "~=":
            ok = _cmp(version, v) >= 0 and version[: len(v) - 1] == v[:-1]
        else:
            ok = False
        if not ok:
            return False
    return True


def node_admits(range_text, major):
    """True when the npm semver range admits node `major`.x.

    Comparators (>=, >, <=, <, =, bare), caret, tilde, x-ranges, hyphen
    ranges, space-joined ANDs and || ORs; a range init does not understand
    is "not admitted".
    """
    range_text = range_text.strip()
    if not range_text or range_text == "*":
        return True
    version = (major, 0, 0)

    def one(comparator):
        comparator = comparator.strip()
        m = re.match(r"^(>=|<=|>|<|=|\^|~)?\s*v?([0-9]+(?:\.[0-9xX*]+){0,2})$", comparator)
        if not m:
            raise ValueError(comparator)
        op, val = m.groups()
        parts = val.split(".")
        wild = len(parts) < 3 or any(p in ("x", "X", "*") for p in parts)
        v = _parse_version(val)
        if op in (None, "", "=") and wild:
            return version[: len(v)] == v
        if op in (None, "", "="):
            return _cmp(version, v) == 0
        if op == "^":
            return _cmp(version, v) >= 0 and version[0] == v[0]
        if op == "~":
            return _cmp(version, v) >= 0 and version[: max(1, min(2, len(v)))] == v[: max(1, min(2, len(v)))]
        if op == ">=":
            return _cmp(version, v) >= 0
        if op == ">":
            return _cmp(version, v + (0,) * (3 - len(v))) > 0 if not wild else version[: len(v)] > v
        if op == "<=":
            return _cmp(version, v) <= 0
        if op == "<":
            return _cmp(version, v) < 0
        raise ValueError(comparator)

    try:
        for alternative in range_text.split("||"):
            alternative = alternative.strip()
            m = re.match(r"^v?([0-9][0-9.xX*]*)\s+-\s+v?([0-9][0-9.xX*]*)$", alternative)
            if m:
                lo, hi = _parse_version(m.group(1)), _parse_version(m.group(2))
                if _cmp(version, lo) >= 0 and version[: len(hi)] <= hi:
                    return True
                continue
            if all(one(c) for c in alternative.split()):
                return True
    except ValueError:
        return False
    return False


# --- the draft ------------------------------------------------------------


def _read_committed(repo):
    """KEY=VALUE of the clone's committed .lights-out/workload.env, or {}."""
    path = os.path.join(repo, ".lights-out", "workload.env")
    values = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, sep, value = line.partition("=")
                if sep:
                    values[key] = value
    except FileNotFoundError:
        pass
    return values


def infer_python(repo):
    with open(os.path.join(repo, "pyproject.toml"), "rb") as fh:
        pyproject = tomllib.load(fh)
    notes = []
    if not os.path.isfile(os.path.join(repo, "uv.lock")):
        raise Undraftable(
            "pyproject.toml found but no uv.lock beside it: the deps gate scans a lockfile "
            "(docs/adr/0015 §5); run `uv lock` and commit it"
        )
    project = pyproject.get("project", {})
    name = project.get("name", "")

    package = None
    evidence = None
    hatch = (
        pyproject.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {})
    )
    for candidate in hatch.get("packages", []) or []:
        if os.path.isfile(os.path.join(repo, candidate, "__init__.py")):
            package, evidence = candidate, f"[tool.hatch.build.targets.wheel].packages names {candidate!r}"
            break
    if package is None and name:
        normalised = re.sub(r"[-.]", "_", name).lower()
        for candidate in (normalised, os.path.join("src", normalised)):
            if os.path.isfile(os.path.join(repo, candidate, "__init__.py")):
                package, evidence = candidate, f"[project].name = {name!r} and {candidate}/__init__.py exists"
                break
    if package is None:
        for entry in sorted(os.listdir(repo)):
            if entry in ("tests", "test") or entry.startswith("."):
                continue
            if os.path.isfile(os.path.join(repo, entry, "__init__.py")):
                package, evidence = entry, f"{entry}/__init__.py is the first package at the root"
                break
    if package is None:
        raise Undraftable(
            "no python package found: looked for [tool.hatch.build.targets.wheel].packages, "
            f"{name or '<project.name>'}/__init__.py, src/{name or '<project.name>'}/__init__.py "
            "and any <dir>/__init__.py at the root"
        )

    tests = None
    for candidate in ("tests", "test"):
        if os.path.isdir(os.path.join(repo, candidate)):
            tests = candidate
            break
    test_target = tests or ""
    test_cmd = f"uv run --locked pytest {test_target} -q -p no:cacheprovider".replace("  ", " ")
    mutant_test_cmd = f"uv run --locked pytest {test_target} -q -x -p no:cacheprovider".replace("  ", " ")
    test_evidence = (
        f"{tests}/ exists; pytest through uv, the lock honoured"
        if tests
        else "no tests/ or test/ directory: pytest's own discovery, the lock honoured"
    )

    requires = project.get("requires-python", "")
    if python_admits(requires, WORKBENCH_PYTHON):
        toolchain = "workbench"
        toolchain_evidence = (
            f"requires-python = {requires!r} admits the workbench's python3 "
            f"{WORKBENCH_PYTHON[0]}.{WORKBENCH_PYTHON[1]}; set an image to run elsewhere"
            if requires
            else f"pyproject.toml declares no requires-python; the workbench's python3 "
            f"{WORKBENCH_PYTHON[0]}.{WORKBENCH_PYTHON[1]} is assumed"
        )
    else:
        chosen = next((v for v in PYTHON_CANDIDATES if python_admits(requires, v)), None)
        if chosen is None:
            raise Undraftable(
                f"requires-python = {requires!r} admits none of the Pythons init knows "
                f"({', '.join('.'.join(map(str, v)) for v in PYTHON_CANDIDATES)})"
            )
        toolchain = f"ghcr.io/astral-sh/uv:python{chosen[0]}.{chosen[1]}-trixie-slim"
        toolchain_evidence = (
            f"requires-python = {requires!r} does not admit the workbench's python3 "
            f"{WORKBENCH_PYTHON[0]}.{WORKBENCH_PYTHON[1]}; astral's image with Python "
            f"{chosen[0]}.{chosen[1]} and uv, a tag: pin it by digest before committing"
        )

    notes.append(f"stack python: package {package}/, tests {tests or '(discovery)'}, toolchain {toolchain}")
    return {
        "stack": "python",
        "notes": notes,
        "scope": package + "/",
        "scope_evidence": evidence,
        "lockfiles": "uv.lock",
        "lockfiles_evidence": "uv.lock at the root",
        "test_root": "",
        "test_root_evidence": "the package sits at the repository root, nothing to strip",
        "test_paths": PYTHON_TEST_PATHS,
        "test_paths_evidence": "pytest's default python_files (test_*.py, *_test.py) anywhere, plus its conftest.py (docs/adr/0053 §1)",
        "toolchain": toolchain,
        "toolchain_evidence": toolchain_evidence,
        "test_cmd": test_cmd,
        "test_cmd_evidence": test_evidence,
        "sast_cmd": f"sh .lights-out/runners/sast.sh {package}",
        "sast_cmd_evidence": "bandit over the package, the stack's runner (stacks/python/runners/sast.sh)",
        "mutation_cmd": f'sh .lights-out/runners/mutation.sh {package} "{mutant_test_cmd}"',
        "mutation_cmd_evidence": "cosmic-ray over the package, the stack's runner (stacks/python/runners/mutation.sh), the suite stopped at its first failure per mutant",
        "scope_paths": [package + "/"] + ([tests + "/"] if tests else []),
        "tests_sentence": f"From the repository root, `{test_cmd}`. Use exactly this command.",
    }


def infer_javascript(repo):
    with open(os.path.join(repo, "package.json")) as fh:
        package_json = json.load(fh)
    if not os.path.isfile(os.path.join(repo, "package-lock.json")):
        raise Undraftable(
            "package.json found but no package-lock.json beside it: the deps gate scans a lockfile "
            "(docs/adr/0015 §5); run `npm install` and commit it"
        )
    src = None
    for candidate in ("src", "lib"):
        if os.path.isdir(os.path.join(repo, candidate)):
            src = candidate
            break
    if src is None:
        raise Undraftable("no javascript source directory found: looked for src/ and lib/ at the root")

    scripts = package_json.get("scripts", {}) or {}
    if "test" in scripts:
        test_cmd = "npm ci && npm test"
        suite = "npm test"
        test_evidence = f"package.json scripts.test = {scripts['test']!r}, after a locked install"
    else:
        test_cmd = "npm ci && node --test"
        suite = "node --test"
        test_evidence = "package.json has no scripts.test: node's own test runner, after a locked install"

    engines = (package_json.get("engines", {}) or {}).get("node", "")
    if node_admits(engines, WORKBENCH_NODE):
        toolchain = "workbench"
        toolchain_evidence = (
            f"engines.node = {engines!r} admits the workbench's node {WORKBENCH_NODE}; set an image to run elsewhere"
            if engines
            else f"package.json declares no engines.node; the workbench's node {WORKBENCH_NODE} is assumed"
        )
    else:
        chosen = next((v for v in NODE_CANDIDATES if node_admits(engines, v)), None)
        if chosen is None:
            raise Undraftable(
                f"engines.node = {engines!r} admits none of the node majors init knows "
                f"({', '.join(map(str, NODE_CANDIDATES))})"
            )
        toolchain = f"docker.io/library/node:{chosen}"
        toolchain_evidence = (
            f"engines.node = {engines!r} does not admit the workbench's node {WORKBENCH_NODE}; "
            f"the official node {chosen} image, a tag: pin it by digest before committing"
        )

    tests_dir = next((c for c in ("test", "tests", "__tests__") if os.path.isdir(os.path.join(repo, c))), None)
    return {
        "stack": "javascript",
        "notes": [f"stack javascript: source {src}/, tests {tests_dir or '(discovery)'}, toolchain {toolchain}"],
        "scope": src + "/",
        "scope_evidence": f"{src}/ exists at the root",
        "lockfiles": "package-lock.json",
        "lockfiles_evidence": "package-lock.json at the root",
        "test_root": "",
        "test_root_evidence": "the sources sit at the repository root, nothing to strip",
        "test_paths": JAVASCRIPT_TEST_PATHS,
        "test_paths_evidence": "node --test's default patterns (Node 24 docs: test/ directories, *.test.*, *-test.*, *_test.*), any extension (docs/adr/0053 §1)",
        "toolchain": toolchain,
        "toolchain_evidence": toolchain_evidence,
        "test_cmd": test_cmd,
        "test_cmd_evidence": test_evidence,
        "sast_cmd": f"sh .lights-out/runners/sast.sh {src}",
        "sast_cmd_evidence": "eslint with eslint-plugin-security over the sources, the stack's runner (stacks/javascript/runners/sast.sh)",
        "mutation_cmd": f'npm ci && sh .lights-out/runners/mutation.sh {src} "{suite}"',
        "mutation_cmd_evidence": "Stryker over the sources, the stack's runner (stacks/javascript/runners/mutation.sh), the suite run whole per mutant",
        "scope_paths": [src + "/"] + ([tests_dir + "/"] if tests_dir else []),
        "tests_sentence": f"From the repository root, `{test_cmd}`. Use exactly this command.",
    }


def infer(repo):
    if os.path.isfile(os.path.join(repo, "pyproject.toml")):
        return infer_python(repo)
    if os.path.isfile(os.path.join(repo, "package.json")):
        return infer_javascript(repo)
    raise Undraftable("no stack found: looked for pyproject.toml (python) and package.json (javascript) at the root")


def render_workload_env(d, committed, today, commit, plane=None):
    kept = {k: committed.get(k, "") for k in PLANE_KEYS}
    has_committed = (
        any(kept.values())
        or ("WORKLOAD_HEALTH_PROBE" in committed)
        or ("WORKLOAD_PORT" in committed)
    )
    lines = [
        f"# .lights-out/workload.env, drafted by ./go init on {today} at {commit} (docs/adr/0042 §6):",
        "# the twelve keys the platform reads (docs/adr/0030 §2, 0042 §1, 0053 §1), each under",
        "# a comment saying where its value comes from. KEY=VALUE, one per line, no",
        "# inline comments, no `$` or `#` in a value. Paths are relative to the",
        "# repository's root. Edit, then copy .lights-out/ into your repository and",
        "# commit it: lights-out never writes there.",
        "",
    ]

    def key(name, value, provenance):
        lines.append(f"# Provenance, {provenance}")
        lines.append(f"{name}={value}")
        lines.append("")

    if plane is not None:
        for name, (val, prov) in plane.items():
            if has_committed and name in committed:
                key(name, committed[name], "kept: from the committed .lights-out/workload.env")
            else:
                key(name, val, prov)
        if has_committed:
            for extra in ("WORKLOAD_HEALTH_PROBE", "WORKLOAD_PORT"):
                if extra in committed and extra not in plane:
                    key(extra, committed[extra], "kept: from the committed .lights-out/workload.env")
    else:
        for name in PLANE_KEYS:
            if has_committed:
                key(name, kept[name], "kept: from the committed .lights-out/workload.env; the plane half is not drafted")
            else:
                key(name, "", "default: empty, the plane half is not drafted (docs/adr/0042 §7); fill in to run the plane")
        if "WORKLOAD_HEALTH_PROBE" in committed:
            key(
                "WORKLOAD_HEALTH_PROBE",
                committed["WORKLOAD_HEALTH_PROBE"],
                "kept: from the committed .lights-out/workload.env; optional health probe (ADR 0098 §3)",
            )
        if "WORKLOAD_PORT" in committed:
            key(
                "WORKLOAD_PORT",
                committed["WORKLOAD_PORT"],
                "kept: from the committed .lights-out/workload.env; optional workload port (ADR 0100)",
            )
    key("MUTATION_BASELINE", "", "measurement: not yet measured; ./go init measures it (docs/adr/0042 §6)")
    key("WORKLOAD_LOCKFILES", d["lockfiles"], f"inferred: {d['lockfiles_evidence']}")
    key("WORKLOAD_MUTATION_SCOPE", d["scope"], f"inferred: {d['scope_evidence']}")
    key("WORKLOAD_TEST_ROOT", d["test_root"], f"inferred: {d['test_root_evidence']}")
    key("WORKLOAD_TEST_PATHS", d["test_paths"], f"default: {d['test_paths_evidence']}")
    key("TOOLCHAIN_IMAGE", d["toolchain"], f"inferred: {d['toolchain_evidence']}")
    key("TEST_CMD", d["test_cmd"], f"inferred: {d['test_cmd_evidence']}")
    key("SAST_CMD", d["sast_cmd"], f"default: {d['sast_cmd_evidence']}")
    key("MUTATION_CMD", d["mutation_cmd"], f"default: {d['mutation_cmd_evidence']}")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_worker_md(d, repo_name, today):
    paths = " and ".join(f"`{p}`" for p in d["scope_paths"])
    return (
        f"# Brief for the dev-worker: {repo_name}\n"
        "\n"
        f"Drafted by ./go init on {today} (docs/adr/0042 §6) from what the repository\n"
        "declares; edit it before committing. What this repository tells the\n"
        "lights-out dev-worker (docs/adr/0037 §6): where its code is and how its\n"
        "tests run.\n"
        "\n"
        "## Scope\n"
        "\n"
        f"Your entire task is inside {paths}. Read only those paths, plus the\n"
        "ticket. `.lights-out/` is the platform's contract: leave it alone.\n"
        "\n"
        "## Tests\n"
        "\n"
        f"{d['tests_sentence']}\n"
    )


def render_policy_env(today):
    return (
        "# This repository's overrides of lights-out's policy/merge.env (docs/adr/\n"
        f"# 0037 §5), drafted by ./go init on {today}: none. Every threshold is the\n"
        "# platform's default, and MUTATION_BASELINE is measured in workload.env.\n"
        "# To override one: KEY=VALUE under a \"# Provenance,\n"
        "# measurement|external|decision:\" line, nine keys allowed (docs/\n"
        "# use-on-your-repo.md). A PR is judged by its base's copy, never its own.\n"
    )


def write_draft(repo, out, stacks, today, commit):
    repo = os.path.abspath(repo)
    out = os.path.abspath(out)
    if not os.path.isdir(repo):
        raise Undraftable(f"{repo} is not a directory")
    d = infer(repo)
    committed = _read_committed(repo)
    runners_src = os.path.join(stacks, d["stack"], "runners")
    if not os.path.isdir(runners_src):
        raise Undraftable(f"{runners_src} is missing: the {d['stack']} stack has no runners to copy")

    os.makedirs(out, exist_ok=True)
    runners_dst = os.path.join(out, "runners")
    if os.path.isdir(runners_dst):
        shutil.rmtree(runners_dst)
    shutil.copytree(runners_src, runners_dst)
    container = init_plane.detect_container(repo)
    plane = init_plane.draft_plane(repo, out, container, committed) if container is not None else None
    with open(os.path.join(out, "workload.env"), "w") as fh:
        fh.write(render_workload_env(d, committed, today, commit, plane=plane))
    with open(os.path.join(out, "worker.md"), "w") as fh:
        fh.write(render_worker_md(d, os.path.basename(repo.rstrip("/")), today))
    with open(os.path.join(out, "policy.env"), "w") as fh:
        fh.write(render_policy_env(today))
    for note in d["notes"]:
        print(f"init: {note}", file=sys.stderr)
    if container is not None and plane is not None:
        print(
            f"init: plane: {container['dockerfile']} port {container['port']}, probe {plane['WORKLOAD_HEALTH_PROBE'][0]}",
            file=sys.stderr,
        )
    else:
        print(
            "init: plane: no Containerfile or Dockerfile at root; drafted gates half only",
            file=sys.stderr,
        )
    if committed:
        print(
            "init: the repository has a committed .lights-out/; its workload.env, worker.md, "
            "policy.env and runners/ are replaced by this draft in the clone only",
            file=sys.stderr,
        )
    # Nothing about a repository's review preferences or connectors can be
    # inferred, so neither roles/ nor mcp.json is drafted (docs/adr/0073 §11).
    print("init: agents: no role instructions, no MCP servers; see docs/use-on-your-repo.md#agents",
          file=sys.stderr)
    return d["stack"]


def set_baseline(path, score, provenance):
    with open(path) as fh:
        lines = fh.read().split("\n")
    out = []
    i = 0
    found = False
    while i < len(lines):
        line = lines[i]
        if line.startswith("MUTATION_BASELINE="):
            # Replace the provenance comment block directly above it too.
            while out and out[-1].startswith("# Provenance,"):
                out.pop()
            out.append(f"# Provenance, measurement: {provenance}")
            out.append(f"MUTATION_BASELINE={score}")
            found = True
        else:
            out.append(line)
        i += 1
    if not found:
        raise Undraftable(f"{path} has no MUTATION_BASELINE line to set")
    with open(path, "w") as fh:
        fh.write("\n".join(out))


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo")
    parser.add_argument("--out")
    parser.add_argument("--stacks", default="stacks")
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--today", default=date.today().isoformat())
    parser.add_argument("--set-baseline", nargs=3, metavar=("WORKLOAD_ENV", "SCORE", "PROVENANCE"))
    args = parser.parse_args(argv)
    try:
        if args.set_baseline:
            set_baseline(*args.set_baseline)
            return 0
        if not args.repo or not args.out:
            parser.error("--repo and --out are required")
        stack = write_draft(args.repo, args.out, args.stacks, args.today, args.commit)
    except Undraftable as exc:
        print(str(exc))
        return 1
    print(f"STACK={stack}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
