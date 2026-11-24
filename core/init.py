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
import posixpath
import re
import shutil
import subprocess  # nosec B404 - invokes git and platform helpers with explicit argv lists only
import sys
import tempfile
import time
import tomllib
from datetime import date, datetime, timezone

try:
    from . import init_apply, init_plane
except ImportError:  # run as a script: core/ is sys.path[0]
    import init_apply
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


def _uv_has_pytest(repo):
    """True when uv.lock declares pytest or is a minimal package-less stub."""
    try:
        with open(os.path.join(repo, "uv.lock"), "rb") as fh:
            lock = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return True
    pkgs = lock.get("package")
    if not isinstance(pkgs, list) or not pkgs:
        return True
    return any(isinstance(p, dict) and str(p.get("name", "")).lower() == "pytest" for p in pkgs)


def infer_python(repo, runners_dir=".lights-out/runners"):
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
    if _uv_has_pytest(repo):
        test_cmd = f"uv run --locked pytest {test_target} -q -p no:cacheprovider".replace("  ", " ")
        mutant_test_cmd = f"uv run --locked pytest {test_target} -q -x -p no:cacheprovider".replace("  ", " ")
        test_evidence = (
            f"{tests}/ exists; pytest through uv, the lock honoured"
            if tests
            else "no tests/ or test/ directory: pytest's own discovery, the lock honoured"
        )
    else:
        discover_dir = f" -s {tests}" if tests else ""
        test_cmd = f"uv run --locked python3 -m unittest discover{discover_dir} -q"
        mutant_test_cmd = f"uv run --locked python3 -m unittest discover{discover_dir} -q -f"
        test_evidence = (
            f"{tests or '.'}/ has no pytest in uv.lock; stdlib unittest discovery through uv, the lock honoured"
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
        "sast_cmd": f"sh {runners_dir}/sast.sh {package}",
        "sast_cmd_evidence": "bandit over the package, the stack's runner (stacks/python/runners/sast.sh)",
        "mutation_cmd": f'sh {runners_dir}/mutation.sh {package} "{mutant_test_cmd}"',
        "mutation_cmd_evidence": "cosmic-ray over the package, the stack's runner (stacks/python/runners/mutation.sh), the suite stopped at its first failure per mutant",
        "scope_paths": [package + "/"] + ([tests + "/"] if tests else []),
        "tests_sentence": f"From the repository root, `{test_cmd}`. Use exactly this command.",
    }


def infer_javascript(repo, runners_dir=".lights-out/runners"):
    try:
        from . import stacks
    except ImportError:  # run as a script: core/ is sys.path[0]
        import stacks

    return stacks.infer_javascript(repo, runners_dir=runners_dir)


def _infer_single_dir(repo, runners_dir, workspace_root=None):
    try:
        from . import stacks
    except ImportError:  # run as a script: core/ is sys.path[0]
        import stacks

    has_pyproject = os.path.isfile(os.path.join(repo, "pyproject.toml"))
    has_uv = os.path.isfile(os.path.join(repo, "uv.lock"))
    has_poetry = os.path.isfile(os.path.join(repo, "poetry.lock"))
    has_reqs = os.path.isfile(os.path.join(repo, "requirements.txt"))
    if has_pyproject:
        if has_uv:
            return infer_python(repo, runners_dir=runners_dir)
        if has_poetry or has_reqs:
            return stacks.infer_python_fallback(repo, runners_dir=runners_dir)
        return infer_python(repo, runners_dir=runners_dir)
    if has_reqs:
        return stacks.infer_python_fallback(repo, runners_dir=runners_dir)
    if os.path.isfile(os.path.join(repo, "go.mod")):
        return stacks.infer_go(repo, runners_dir=runners_dir)
    if os.path.isfile(os.path.join(repo, "Cargo.toml")):
        return stacks.infer_rust(repo, runners_dir=runners_dir, workspace_root=workspace_root)
    if (
        os.path.isfile(os.path.join(repo, "pom.xml"))
        or os.path.isfile(os.path.join(repo, "build.gradle"))
        or os.path.isfile(os.path.join(repo, "build.gradle.kts"))
    ):
        if stacks.is_kotlin_project(repo):
            return stacks.infer_kotlin(repo, runners_dir=runners_dir, workspace_root=workspace_root)
        return stacks.infer_java(repo, runners_dir=runners_dir, workspace_root=workspace_root)
    if stacks.has_dotnet_manifest(repo):
        return stacks.infer_dotnet(repo, runners_dir=runners_dir, workspace_root=workspace_root)
    if os.path.isfile(os.path.join(repo, "Package.swift")):
        return stacks.infer_swift(repo, runners_dir=runners_dir, workspace_root=workspace_root)
    if os.path.isfile(os.path.join(repo, "composer.json")):
        return stacks.infer_php(repo, runners_dir=runners_dir, workspace_root=workspace_root)
    if os.path.isfile(os.path.join(repo, "Gemfile")):
        return stacks.infer_ruby(repo, runners_dir=runners_dir, workspace_root=workspace_root)
    if os.path.isfile(os.path.join(repo, "package.json")):
        if os.path.isfile(os.path.join(repo, "tsconfig.json")):
            return stacks.infer_typescript(repo, runners_dir=runners_dir, workspace_root=workspace_root)
        return stacks.infer_javascript(repo, runners_dir=runners_dir, workspace_root=workspace_root)
    raise Undraftable(
        "no stack found: looked for pyproject.toml / requirements.txt (python), "
        "go.mod (go), Cargo.toml (rust), pom.xml / build.gradle (java/kotlin), "
        "*.csproj / *.sln (dotnet), Package.swift (swift), composer.json (php), "
        "Gemfile (ruby), and package.json (typescript/javascript) at the root"
    )


def infer(repo, service=None):
    try:
        from . import stacks
    except ImportError:  # run as a script: core/ is sys.path[0]
        import stacks

    resolved_service = stacks.normalize_service(repo, service)
    if not resolved_service:
        kind, services, is_virtual = stacks.discover_workspace_services(repo)
        if len(services) > 1:
            raise Undraftable(
                f"{kind} detected with multiple services ({', '.join(services)}): "
                "set INIT_SERVICE=<service> to target one"
            )
        if is_virtual and kind:
            if len(services) == 1:
                resolved_service = services[0]
            else:
                raise Undraftable(
                    f"{kind} has no discoverable sub-services: set INIT_SERVICE=<service> to target one"
                )

    if resolved_service:
        runners_dir = posixpath.relpath(".lights-out/runners", resolved_service)
        result = _infer_single_dir(os.path.join(repo, resolved_service), runners_dir, workspace_root=repo)
        return stacks.apply_service_prefix(result, resolved_service)
    return _infer_single_dir(repo, ".lights-out/runners")


def render_workload_env(d, committed, today, commit, plane=None, incremental_baseline=None):
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
    if incremental_baseline is not None:
        ml_spec, inc_prov = incremental_baseline
        baseline_prov = (
            f"measurement: incremental baseline scope (not yet measured); {inc_prov}; MUTATION_LINES={ml_spec}"
            if ml_spec
            else f"measurement: incremental baseline scope (not yet measured); {inc_prov}"
        )
    else:
        baseline_prov = "measurement: not yet measured; ./go init measures it (docs/adr/0042 §6)"
    key("MUTATION_BASELINE", "", baseline_prov)
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


def _write_draft_with_info(repo, out, stacks, today, commit, service=None, baseline_mode="full", baseline_max_files=10):
    repo = os.path.abspath(repo)
    out = os.path.abspath(out)
    if baseline_mode not in ("full", "incremental"):
        raise Undraftable(f"unknown baseline mode {baseline_mode!r}; expected 'full' or 'incremental'")
    if isinstance(baseline_max_files, bool) or not isinstance(baseline_max_files, int) or baseline_max_files < 1:
        raise Undraftable("--baseline-max-files must be an integer >= 1")
    if not os.path.isdir(repo):
        raise Undraftable(f"{repo} is not a directory")
    d = infer(repo, service=service)
    incremental_baseline = (
        init_apply.select_incremental_scope(repo, d["scope"], max_files=baseline_max_files)
        if baseline_mode == "incremental"
        else None
    )
    committed = _read_committed(repo)
    runners_src = os.path.join(stacks, d["stack"], "runners")
    if not os.path.isdir(runners_src):
        raise Undraftable(f"{runners_src} is missing: the {d['stack']} stack has no runners to copy")

    os.makedirs(out, exist_ok=True)
    runners_dst = os.path.join(out, "runners")
    if os.path.isdir(runners_dst):
        shutil.rmtree(runners_dst)
    shutil.copytree(runners_src, runners_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    effective_service = d["test_root"].rstrip("/") if d.get("test_root") else ""
    container = init_plane.detect_container(repo, service=effective_service)
    plane = (
        init_plane.draft_plane(repo, out, container, committed, service=effective_service)
        if container is not None
        else None
    )
    with open(os.path.join(out, "workload.env"), "w") as fh:
        fh.write(
            render_workload_env(
                d, committed, today, commit, plane=plane, incremental_baseline=incremental_baseline
            )
        )
    with open(os.path.join(out, "worker.md"), "w") as fh:
        fh.write(render_worker_md(d, os.path.basename(repo.rstrip("/")), today))
    with open(os.path.join(out, "policy.env"), "w") as fh:
        fh.write(render_policy_env(today))
    for note in d["notes"]:
        print(f"init: {note}", file=sys.stderr)
    if incremental_baseline is not None:
        print(f"init: {incremental_baseline.provenance}", file=sys.stderr)
        if incremental_baseline.mutation_lines:
            print(f"init: MUTATION_LINES={incremental_baseline.mutation_lines}", file=sys.stderr)
    if container is not None and plane is not None:
        print(
            f"init: plane: {container['dockerfile']} port {container['port']}, probe {plane['WORKLOAD_HEALTH_PROBE'][0]}",
            file=sys.stderr,
        )
        if container.get("stateful_deps"):
            print(
                f"init: plane: stateful deps ({', '.join(container['stateful_deps'])}) -> annotated hints in compose.yaml",
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
    return d["stack"], incremental_baseline


def write_draft(repo, out, stacks, today, commit, service=None, baseline_mode="full", baseline_max_files=10):
    stack, _ = _write_draft_with_info(
        repo,
        out,
        stacks,
        today,
        commit,
        service=service,
        baseline_mode=baseline_mode,
        baseline_max_files=baseline_max_files,
    )
    return stack


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


class _DriverParser(argparse.ArgumentParser):
    def error(self, message):
        raise Undraftable(message)


def _read_env_keys(draft_path, keys):
    values = {k: "" for k in keys}
    with open(draft_path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            k, sep, v = line.partition("=")
            if sep and k in values and not values[k]:
                values[k] = v
    return values


def _run_workspace(
    repo_path,
    *,
    out_dir=None,
    stacks_dir="stacks",
    today=None,
    baseline_mode="incremental",
    apply_mode=False,
    branch=None,
    commit=False,
    force=False,
):
    """Build a multi-service workspace manifest and optionally apply it to `.lights-out/`."""
    try:
        from . import epics
    except ImportError:  # run as a script: core/ is sys.path[0]
        import epics
    if not apply_mode:
        manifest = epics.build_workspace_manifest(
            repo_path, out_dir=out_dir, stacks_dir=stacks_dir, today=today, baseline_mode=baseline_mode
        )
        return manifest, None
    dest_contract = os.path.realpath(os.path.join(repo_path, ".lights-out"))
    use_out = bool(out_dir and os.path.realpath(out_dir) != dest_contract)
    if use_out:
        manifest = epics.build_workspace_manifest(
            repo_path, out_dir=out_dir, stacks_dir=stacks_dir, today=today, baseline_mode=baseline_mode
        )
        summary = init_apply.apply_draft(
            out_dir, repo_path, ".lights-out", branch=branch, commit=commit, force=force
        )
    else:
        with tempfile.TemporaryDirectory() as tmp_draft:
            manifest = epics.build_workspace_manifest(
                repo_path, out_dir=tmp_draft, stacks_dir=stacks_dir, today=today, baseline_mode=baseline_mode
            )
            summary = init_apply.apply_draft(
                tmp_draft, repo_path, ".lights-out", branch=branch, commit=commit, force=force
            )
    return manifest, summary


def run_driver(argv, env):
    """Execute the `make init` workflow (draft-in-clone or zero-copy --apply) with baseline measurement."""
    workload = (env.get("WORKLOAD") or "").strip()
    workload_contract = (env.get("WORKLOAD_CONTRACT") or "").strip()
    workload_attached = (env.get("WORKLOAD_ATTACHED") or "").strip()
    if not workload:
        raise Undraftable("WORKLOAD not set; run this via ./go, not directly")
    if not workload_contract:
        raise Undraftable("WORKLOAD_CONTRACT not set; run this via ./go, not directly")
    if not workload_attached:
        raise Undraftable(
            f"WORKLOAD={workload} is not an attached repository; ./go init <path>, "
            "or ./go attach <path> first (the bundled app has its contract)"
        )
    if not os.path.isdir("stacks"):
        raise Undraftable("stacks/ not found; run this from the lights-out checkout")

    parser = _DriverParser(prog="init", add_help=False)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--apply-to", default=None)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workspace", action="store_true")
    parser.add_argument("--baseline-mode", choices=("full", "incremental"), default=None)
    parser.add_argument("--baseline-max-files", type=int, default=10)
    parser.add_argument("--service", default=None)
    ns = parser.parse_args(argv)

    apply_mode = bool(ns.apply or ns.apply_to)
    if (ns.branch is not None or ns.commit or ns.force) and not apply_mode:
        raise Undraftable("--branch, --commit, and --force require --apply")
    baseline_mode = ns.baseline_mode or ("incremental" if ns.workspace else "full")

    target_workload = os.path.abspath(ns.apply_to) if ns.apply_to else workload
    target_contract = os.path.join(target_workload, ".lights-out") if ns.apply_to else workload_contract

    rev = subprocess.run(  # nosec B603 B607 - explicit git argv list, shell=False
        ["git", "-C", target_workload, "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if rev.returncode != 0:
        raise Undraftable(f"{target_workload} is not a git repository")
    commit = rev.stdout.strip()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    service = ns.service if ns.service is not None else (env.get("INIT_SERVICE") or None)

    if ns.workspace:
        manifest, summary = _run_workspace(
            target_workload,
            out_dir=None if apply_mode else target_contract,
            stacks_dir="stacks",
            today=today,
            baseline_mode=baseline_mode,
            apply_mode=apply_mode,
            branch=ns.branch,
            commit=bool(ns.commit),
            force=ns.force,
        )
        if apply_mode:
            target_contract = os.path.join(target_workload, ".lights-out")
            meta = [f"{len(summary['applied_files'])} file(s)"]
            if summary["branch"]:
                meta.append(f"branch={summary['branch']}")
            if summary["commit_sha"]:
                meta.append(f"commit={summary['commit_sha'][:7]}")
            return (
                f"workspace ({manifest['kind']}, {len(manifest['services'])} service(s)), "
                f"applied to {target_contract} ({', '.join(meta)})"
            )
        return (
            f"workspace ({manifest['kind']}, {len(manifest['services'])} service(s)), "
            f"draft in {target_contract}/workspace.json; copy .lights-out/ into your repository and commit it"
        )

    applied_files = []
    active_branch = None
    if apply_mode:
        with tempfile.TemporaryDirectory() as tmp_draft:
            stack, inc_scope = _write_draft_with_info(
                target_workload,
                tmp_draft,
                "stacks",
                today,
                commit,
                service=service,
                baseline_mode=baseline_mode,
                baseline_max_files=ns.baseline_max_files,
            )
            summary = init_apply.apply_draft(
                tmp_draft,
                target_workload,
                ".lights-out",
                branch=ns.branch,
                commit=False,
                force=ns.force,
            )
            applied_files = list(summary["applied_files"])
            active_branch = summary["branch"]
        target_contract = os.path.join(target_workload, ".lights-out")
    else:
        stack, inc_scope = _write_draft_with_info(
            target_workload,
            target_contract,
            "stacks",
            today,
            commit,
            service=service,
            baseline_mode=baseline_mode,
            baseline_max_files=ns.baseline_max_files,
        )

    sub_env = dict(env)
    sub_env["WORKLOAD"] = target_workload
    sub_env["WORKLOAD_CONTRACT"] = target_contract
    sub_env["WORKLOAD_ATTACHED"] = "1"
    root = os.getcwd()
    sub_env["PYTHONPATH"] = f"{root}:{sub_env['PYTHONPATH']}" if sub_env.get("PYTHONPATH") else root

    hist = subprocess.run(  # nosec B603 B607 - explicit sh argv list, shell=False
        ["sh", "core/history-findings.sh"],
        env=sub_env,
        stdout=subprocess.DEVNULL,
        check=False,
    )
    if hist.returncode != 0:
        print(
            "init: the history scan could not run (see above); the secrets gate still scans it on every judgement",
            file=sys.stderr,
        )

    draft = os.path.join(target_contract, "workload.env")
    cfg = _read_env_keys(
        draft, ("TOOLCHAIN_IMAGE", "MUTATION_CMD", "WORKLOAD_TEST_ROOT", "WORKLOAD_MUTATION_SCOPE")
    )
    if not cfg["MUTATION_CMD"]:
        raise Undraftable("the draft has no MUTATION_CMD")
    sub_env.update(cfg)

    pre = subprocess.run(  # nosec B603 - explicit python toolchain argv list, shell=False
        [sys.executable, "-P", "-m", "core.gates.toolchain", "preflight"],
        env=sub_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if pre.returncode != 0:
        if pre.stderr:
            sys.stderr.write(pre.stderr)
        raise Undraftable(pre.stdout.strip() or "the toolchain preflight failed; see stderr")
    toolchain_where = pre.stdout.strip()

    if baseline_mode == "incremental" and inc_scope is not None and inc_scope.mutation_lines:
        sub_env["MUTATION_LINES"] = inc_scope.mutation_lines
        ml_label = f"{inc_scope.provenance}; MUTATION_LINES={inc_scope.mutation_lines}"
    else:
        sub_env.pop("MUTATION_LINES", None)
        ml_label = "MUTATION_LINES=all"

    t0 = time.monotonic()
    mb = subprocess.run(  # nosec B603 B607 - explicit sh argv list, shell=False
        ["sh", "core/mutation-baseline.sh"],
        env=sub_env,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    elapsed = int(time.monotonic() - t0)
    if mb.returncode != 0:
        raise Undraftable(
            f"in {toolchain_where}: MUTATION_CMD could not be measured; the draft is in {target_contract} "
            "with MUTATION_BASELINE empty, see stderr"
        )
    last_line = next((ln.strip() for ln in reversed(mb.stdout.splitlines()) if ln.strip()), "")
    try:
        parsed = json.loads(last_line)
        total = parsed["total"]
        killed = parsed["killed"]
        score = parsed["score"]
    except (ValueError, KeyError, TypeError) as exc:
        raise Undraftable(f"could not parse mutation baseline JSON ({exc}): {last_line!r}") from None

    try:
        set_baseline(
            draft,
            str(score),
            f"measured by ./go init on {today} at {commit}, in {toolchain_where}: {cfg['MUTATION_CMD']} "
            f"with {ml_label}, {total} mutants, {killed} killed, {elapsed}s wall time. "
            "Re-measure with ./go mutation-baseline when the suite or the tool changes.",
        )
    except Undraftable:
        raise
    except OSError as exc:
        raise Undraftable(f"could not write MUTATION_BASELINE into {draft}: {exc}") from None

    wk = subprocess.run(  # nosec B603 B607 - explicit sh argv list, shell=False
        ["sh", "ci/workloads.sh", target_workload],
        env=sub_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if wk.returncode != 0:
        if wk.stderr:
            sys.stderr.write(wk.stderr)
        msg = (wk.stdout or "").strip()
        if msg.startswith("NO-GO: workloads — "):
            msg = msg[len("NO-GO: workloads — ") :]
        raise Undraftable(f"the draft fails the contract check: {msg}")

    commit_sha = None
    if apply_mode and ns.commit:
        commit_sha = init_apply.commit_applied_paths(
            target_workload, applied_files, "chore: apply contract files to .lights-out"
        )

    if apply_mode:
        meta = [f"{len(applied_files)} file(s)"]
        if active_branch:
            meta.append(f"branch={active_branch}")
        if commit_sha:
            meta.append(f"commit={commit_sha[:7]}")
        return (
            f"{stack}, applied to {target_contract} ({', '.join(meta)}), "
            f"baseline {score} ({killed}/{total} mutants killed, {elapsed}s, in {toolchain_where})"
        )
    return (
        f"{stack}, draft in {target_contract}, baseline {score} ({killed}/{total} mutants killed, "
        f"{elapsed}s, in {toolchain_where}); copy .lights-out/ into your repository and commit it"
    )


def main(argv):
    if argv and argv[0] == "--run":
        try:
            print(run_driver(argv[1:], dict(os.environ)))
            return 0
        except (Undraftable, init_apply.ApplyError, ValueError) as exc:
            print(" ".join(str(exc).splitlines()))
            return 1
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo")
    parser.add_argument("--out")
    parser.add_argument("--stacks", default="stacks")
    parser.add_argument("--commit", nargs="?", const=True, default=None)
    parser.add_argument("--today", default=date.today().isoformat())
    parser.add_argument("--service", default=None)
    parser.add_argument("--set-baseline", nargs=3, metavar=("WORKLOAD_ENV", "SCORE", "PROVENANCE"))
    parser.add_argument("--apply-to", default=None)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workspace", action="store_true")
    parser.add_argument("--baseline-mode", choices=("full", "incremental"), default=None)
    parser.add_argument("--baseline-max-files", type=int, default=10)
    args = parser.parse_args(argv)
    apply_commit = args.commit is True
    header_commit = args.commit if isinstance(args.commit, str) else "HEAD"
    baseline_mode = args.baseline_mode or ("incremental" if args.workspace else "full")
    try:
        if args.set_baseline:
            set_baseline(*args.set_baseline)
            return 0
        if (args.branch is not None or apply_commit or args.force) and not args.apply_to:
            parser.error("--branch, --commit (without SHA), and --force require --apply-to")
        repo = args.repo or args.apply_to
        if not repo:
            parser.error("--repo (or --apply-to) is required")
        if not args.out and not args.apply_to and not args.workspace:
            parser.error("--out (or --apply-to) is required")

        if args.workspace:
            target_repo = os.path.abspath(args.apply_to) if args.apply_to else repo
            manifest, summary = _run_workspace(
                target_repo,
                out_dir=args.out,
                stacks_dir=args.stacks,
                today=args.today,
                baseline_mode=baseline_mode,
                apply_mode=bool(args.apply_to),
                branch=args.branch,
                commit=apply_commit,
                force=args.force,
            )
            if summary is not None:
                detail = [f"{len(summary['applied_files'])} file(s)"]
                if summary["branch"]:
                    detail.append(f"branch={summary['branch']}")
                if summary["commit_sha"]:
                    detail.append(f"commit={summary['commit_sha'][:7]}")
                print(f"init: applied workspace to {target_repo}/.lights-out ({', '.join(detail)})", file=sys.stderr)
            print(f"WORKSPACE={manifest['kind']} SERVICES={len(manifest['services'])}")
            return 0

        def run_draft(out_dir):
            return write_draft(
                repo,
                out_dir,
                args.stacks,
                args.today,
                header_commit,
                service=args.service,
                baseline_mode=baseline_mode,
                baseline_max_files=args.baseline_max_files,
            )

        if args.apply_to:
            target_repo = os.path.abspath(args.apply_to)
            dest_contract = os.path.realpath(os.path.join(target_repo, ".lights-out"))
            use_out = bool(args.out and os.path.realpath(args.out) != dest_contract)
            if use_out:
                stack = run_draft(args.out)
                summary = init_apply.apply_draft(
                    args.out,
                    target_repo,
                    ".lights-out",
                    branch=args.branch,
                    commit=apply_commit,
                    force=args.force,
                )
            else:
                with tempfile.TemporaryDirectory() as tmp_draft:
                    stack = run_draft(tmp_draft)
                    summary = init_apply.apply_draft(
                        tmp_draft,
                        target_repo,
                        ".lights-out",
                        branch=args.branch,
                        commit=apply_commit,
                        force=args.force,
                    )
            detail = [f"{len(summary['applied_files'])} file(s)"]
            if summary["branch"]:
                detail.append(f"branch={summary['branch']}")
            if summary["commit_sha"]:
                detail.append(f"commit={summary['commit_sha'][:7]}")
            print(f"init: applied to {target_repo}/.lights-out ({', '.join(detail)})", file=sys.stderr)
        else:
            stack = run_draft(args.out)
    except (Undraftable, init_apply.ApplyError, ValueError) as exc:
        print(str(exc))
        return 1
    print(f"STACK={stack}")
    return 0


if __name__ == "__main__":
    sys.modules.setdefault(f"{__package__}.init" if __package__ else "init", sys.modules[__name__])
    sys.exit(main(sys.argv[1:]))
