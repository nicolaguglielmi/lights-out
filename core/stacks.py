"""Additional stack inference (Go, TypeScript, Python fallbacks): stdlib only.

Implements ADR 0102 without touching vendor or contract-directory literals
(ci/vendor-free.sh, ci/app-free.sh). Called from init.infer(repo) which
passes the contract's runners directory.
"""

import json
import os
import re
import tomllib

try:
    from . import init
except ImportError:  # run as a script: core/ is sys.path[0]
    import init

GO_TEST_PATHS = "*_test.go:*/*_test.go"
TYPESCRIPT_TEST_PATHS = "test/*:*/test/*:*.test.*:*.spec.*:*-test.*:*_test.*"

_GO_SKIP_DIRS = {"vendor", "testdata"}
_PIP_SKIP_FLAGS = {
    "-i",
    "--index-url",
    "--extra-index-url",
    "--trusted-host",
    "-f",
    "--find-links",
    "--no-index",
    "--no-binary",
    "--only-binary",
    "--prefer-binary",
    "--pre",
    "--require-hashes",
    "--use-pep517",
    "--no-build-isolation",
}
_PIN_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,\s-]+\])?\s*==\s*[^\s=*<>!~,;]+$"
)


def _dir_has_go_source(dirpath):
    for root, dirs, files in os.walk(dirpath):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _GO_SKIP_DIRS)
        if any(f.endswith(".go") and not f.endswith("_test.go") for f in files):
            return True
    return False


def _find_go_scope(repo):
    for candidate in ("pkg", "internal", "cmd"):
        path = os.path.join(repo, candidate)
        if os.path.isdir(path) and _dir_has_go_source(path):
            return candidate + "/", f"{candidate}/ holds Go source files"
    for entry in sorted(os.listdir(repo)):
        if entry.startswith(".") or entry in _GO_SKIP_DIRS:
            continue
        path = os.path.join(repo, entry)
        if os.path.isdir(path) and _dir_has_go_source(path):
            return entry + "/", f"{entry}/ is the first Go source directory at the root"
    with os.scandir(repo) as entries:
        if any(
            entry.is_file() and entry.name.endswith(".go") and not entry.name.endswith("_test.go")
            for entry in entries
        ):
            return ".", "Go source files sit at the repository root"
    raise init.Undraftable(
        "no Go source files found (*.go excluding *_test.go) in pkg/, internal/, cmd/, subdirectories, or at the root"
    )


def infer_go(repo, runners_dir="runners"):
    """Infer contract metadata for a Go repository with go.mod and go.sum."""
    repo = os.fspath(repo)
    go_mod_path = os.path.join(repo, "go.mod")
    if not os.path.isfile(go_mod_path):
        raise init.Undraftable("go.mod not found at the repository root")
    if not os.path.isfile(os.path.join(repo, "go.sum")):
        raise init.Undraftable(
            "go.mod found but no go.sum beside it: run `go mod tidy` and commit go.sum alongside go.mod"
        )
    with open(go_mod_path, encoding="utf-8") as fh:
        go_mod = fh.read()
    m = re.search(r"(?m)^\s*go\s+(\d+\.\d+)(?:\.\d+)?\b", go_mod)
    if not m:
        raise init.Undraftable("go.mod declares no `go <major>.<minor>` directive")
    go_ver = m.group(1)

    scope, scope_evidence = _find_go_scope(repo)
    scope_arg = scope.rstrip("/") or "."
    toolchain = f"docker.io/library/golang:{go_ver}"
    toolchain_evidence = (
        f"go.mod declares go {go_ver}; the official golang {go_ver} image, a tag: "
        "pin it by digest before committing (workbench has no Go toolchain)"
    )
    test_cmd = "go test ./..."
    return {
        "stack": "go",
        "notes": [f"stack go: scope {scope}, toolchain {toolchain}"],
        "scope": scope,
        "scope_evidence": scope_evidence,
        "lockfiles": "go.mod",
        "lockfiles_evidence": "go.mod at the root (osv-scanner scans go.mod; go.sum verified present beside it)",
        "test_root": "",
        "test_root_evidence": "the module sits at the repository root, nothing to strip",
        "test_paths": GO_TEST_PATHS,
        "test_paths_evidence": "Go test files (*_test.go) at the root or under any package directory",
        "toolchain": toolchain,
        "toolchain_evidence": toolchain_evidence,
        "test_cmd": test_cmd,
        "test_cmd_evidence": "standard Go package test suite across the module",
        "sast_cmd": f"sh {runners_dir}/sast.sh {scope_arg}",
        "sast_cmd_evidence": "go/ast and go vet scanner over the scope, the stack's runner (stacks/go/runners/sast.sh)",
        "mutation_cmd": f'sh {runners_dir}/mutation.sh {scope_arg} "{test_cmd}"',
        "mutation_cmd_evidence": "go/ast mutator over the scope, the stack's runner (stacks/go/runners/mutation.sh)",
        "scope_paths": [scope],
        "tests_sentence": f"From the repository root, `{test_cmd}`. Use exactly this command.",
    }


def infer_typescript(repo, runners_dir="runners"):
    """Infer contract metadata for a TypeScript repository with package.json, tsconfig.json, and package-lock.json."""
    repo = os.fspath(repo)
    pkg_path = os.path.join(repo, "package.json")
    if not os.path.isfile(pkg_path) or not os.path.isfile(os.path.join(repo, "tsconfig.json")):
        raise init.Undraftable("package.json and tsconfig.json are both required for typescript")
    if not os.path.isfile(os.path.join(repo, "package-lock.json")):
        raise init.Undraftable(
            "package.json and tsconfig.json found but no package-lock.json beside them: "
            "the deps gate scans a lockfile; run `npm install` and commit it"
        )
    with open(pkg_path, encoding="utf-8") as fh:
        package_json = json.load(fh)

    src = next((c for c in ("src", "lib") if os.path.isdir(os.path.join(repo, c))), None)
    if src is None:
        raise init.Undraftable("no typescript source directory found: looked for src/ and lib/ at the root")

    scripts = package_json.get("scripts", {}) or {}
    if "test" in scripts:
        test_cmd = "npm ci && npm test"
        suite = "npm test"
        test_evidence = f"package.json scripts.test = {scripts['test']!r}, after a locked install"
    else:
        test_cmd = "npm ci && node --test"
        suite = "node --test"
        test_evidence = (
            "package.json has no scripts.test: Node 24 runs .ts tests natively with node --test after a locked install"
        )

    engines = (package_json.get("engines", {}) or {}).get("node", "")
    if init.node_admits(engines, init.WORKBENCH_NODE):
        toolchain = "workbench"
        toolchain_evidence = (
            f"engines.node = {engines!r} admits the workbench's node {init.WORKBENCH_NODE}; set an image to run elsewhere"
            if engines
            else f"package.json declares no engines.node; the workbench's node {init.WORKBENCH_NODE} is assumed"
        )
    else:
        chosen = next((v for v in init.NODE_CANDIDATES if init.node_admits(engines, v)), None)
        if chosen is None:
            raise init.Undraftable(
                f"engines.node = {engines!r} admits none of the node majors init knows "
                f"({', '.join(map(str, init.NODE_CANDIDATES))})"
            )
        toolchain = f"docker.io/library/node:{chosen}"
        toolchain_evidence = (
            f"engines.node = {engines!r} does not admit the workbench's node {init.WORKBENCH_NODE}; "
            f"the official node {chosen} image, a tag: pin it by digest before committing"
        )

    tests_dir = next((c for c in ("test", "tests", "__tests__") if os.path.isdir(os.path.join(repo, c))), None)
    return {
        "stack": "typescript",
        "notes": [f"stack typescript: source {src}/, tests {tests_dir or '(discovery)'}, toolchain {toolchain}"],
        "scope": src + "/",
        "scope_evidence": f"{src}/ exists at the root",
        "lockfiles": "package-lock.json",
        "lockfiles_evidence": "package-lock.json at the root",
        "test_root": "",
        "test_root_evidence": "the sources sit at the repository root, nothing to strip",
        "test_paths": TYPESCRIPT_TEST_PATHS,
        "test_paths_evidence": "Node 24 test runner and TypeScript test/spec patterns across extensions",
        "toolchain": toolchain,
        "toolchain_evidence": toolchain_evidence,
        "test_cmd": test_cmd,
        "test_cmd_evidence": test_evidence,
        "sast_cmd": f"sh {runners_dir}/sast.sh {src}",
        "sast_cmd_evidence": (
            "eslint with eslint-plugin-security and typescript parser over the sources, "
            "the stack's runner (stacks/typescript/runners/sast.sh)"
        ),
        "mutation_cmd": f'npm ci && sh {runners_dir}/mutation.sh {src} "{suite}"',
        "mutation_cmd_evidence": (
            "Stryker over the TypeScript sources, the stack's runner (stacks/typescript/runners/mutation.sh), "
            "the suite run whole per mutant"
        ),
        "scope_paths": [src + "/"] + ([tests_dir + "/"] if tests_dir else []),
        "tests_sentence": f"From the repository root, `{test_cmd}`. Use exactly this command.",
    }


def _poetry_to_pep440(spec):
    """Translate simple Poetry ^ and ~ version constraints to PEP 440 clauses."""
    clauses = []
    for raw in spec.split(","):
        clause = raw.strip()
        if not clause:
            continue
        m = re.match(r"^\^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?$", clause)
        if m:
            maj, min_s, pat_s = m.groups()
            ver = ".".join(p for p in (maj, min_s, pat_s) if p is not None)
            upper = f"{int(maj) + 1}.0" if maj != "0" or min_s is None else f"0.{int(min_s) + 1}"
            clauses.append(f">={ver},<{upper}")
            continue
        m = re.match(r"^~\s*(\d+)\.(\d+)(?:\.(\d+))?$", clause)
        if m:
            maj, min_s, pat_s = m.groups()
            ver = ".".join(p for p in (maj, min_s, pat_s) if p is not None)
            clauses.append(f">={ver},<{maj}.{int(min_s) + 1}")
            continue
        clauses.append(clause)
    return ",".join(clauses)


def _python_toolchain(requires, source_label):
    pep440 = _poetry_to_pep440(requires) if requires else ""
    if init.python_admits(pep440, init.WORKBENCH_PYTHON):
        toolchain = "workbench"
        evidence = (
            f"{source_label} = {requires!r} admits the workbench's python3 "
            f"{init.WORKBENCH_PYTHON[0]}.{init.WORKBENCH_PYTHON[1]}; set an image to run elsewhere"
            if requires
            else f"no Python constraint declared; the workbench's python3 "
            f"{init.WORKBENCH_PYTHON[0]}.{init.WORKBENCH_PYTHON[1]} is assumed"
        )
        return toolchain, evidence
    chosen = next((v for v in init.PYTHON_CANDIDATES if init.python_admits(pep440, v)), None)
    if chosen is None:
        raise init.Undraftable(
            f"{source_label} = {requires!r} admits none of the Pythons init knows "
            f"({', '.join('.'.join(map(str, v)) for v in init.PYTHON_CANDIDATES)})"
        )
    toolchain = f"ghcr.io/astral-sh/uv:python{chosen[0]}.{chosen[1]}-trixie-slim"
    evidence = (
        f"{source_label} = {requires!r} does not admit the workbench's python3 "
        f"{init.WORKBENCH_PYTHON[0]}.{init.WORKBENCH_PYTHON[1]}; astral's image with Python "
        f"{chosen[0]}.{chosen[1]} and uv, a tag: pin it by digest before committing"
    )
    return toolchain, evidence


def _find_python_package(repo, pyproject=None):
    pyproject = pyproject or {}
    poetry = pyproject.get("tool", {}).get("poetry", {}) or {}
    for entry in poetry.get("packages", []) or []:
        if isinstance(entry, dict) and entry.get("include"):
            inc = entry["include"]
            frm = entry.get("from", "")
            cand = os.path.join(frm, inc) if frm else inc
            if os.path.isfile(os.path.join(repo, cand, "__init__.py")):
                return cand, f"[tool.poetry].packages names {cand!r}"
    hatch = pyproject.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {})
    for cand in hatch.get("packages", []) or []:
        if os.path.isfile(os.path.join(repo, cand, "__init__.py")):
            return cand, f"[tool.hatch.build.targets.wheel].packages names {cand!r}"
    name = (pyproject.get("project", {}) or {}).get("name") or poetry.get("name") or ""
    if name:
        normalised = re.sub(r"[-.]", "_", name).lower()
        for cand in (normalised, os.path.join("src", normalised)):
            if os.path.isfile(os.path.join(repo, cand, "__init__.py")):
                return cand, f"project name = {name!r} and {cand}/__init__.py exists"
    for entry in sorted(os.listdir(repo)):
        if entry in ("tests", "test") or entry.startswith("."):
            continue
        if os.path.isfile(os.path.join(repo, entry, "__init__.py")):
            return entry, f"{entry}/__init__.py is the first package at the root"
    src_dir = os.path.join(repo, "src")
    if os.path.isdir(src_dir):
        for entry in sorted(os.listdir(src_dir)):
            if entry in ("tests", "test") or entry.startswith("."):
                continue
            cand = os.path.join("src", entry)
            if os.path.isfile(os.path.join(repo, cand, "__init__.py")):
                return cand, f"{cand}/__init__.py is the first package under src/"
    raise init.Undraftable(
        "no python package found: looked for poetry/hatch packages, project name, <dir>/__init__.py, and src/<dir>/__init__.py"
    )


def _logical_lines(text):
    buf = ""
    for raw in text.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1].strip() + " "
        else:
            yield (buf + stripped).strip()
            buf = ""
    if buf.strip():
        yield buf.strip()


def _parse_pinned_requirements(path):
    """Return unpinned package/entry names found in requirements.txt."""
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    unpinned = []
    for line in _logical_lines(raw):
        if not line or line.startswith("#"):
            continue
        # Strip inline comment preceded by whitespace or after requirement.
        line = re.sub(r"\s+#.*$", "", line).strip()
        if not line:
            continue
        first_tok = line.split()[0]
        if first_tok in _PIP_SKIP_FLAGS or any(first_tok.startswith(f + "=") for f in _PIP_SKIP_FLAGS):
            continue
        if line.startswith(("-r", "--requirement", "-c", "--constraint", "-e", "--editable")):
            unpinned.append(line)
            continue
        # Remove --hash=... tokens that follow a pinned version.
        no_hashes = re.sub(r"\s+--hash=\S+", "", line).strip()
        req_part = no_hashes.split(";", 1)[0].strip()
        if not _PIN_RE.match(req_part):
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", req_part)
            unpinned.append(m.group(1) if m else req_part)
    return unpinned


def _build_python_result(package, evidence, tests, lockfiles, lockfiles_evidence, toolchain, toolchain_evidence, test_cmd, mutant_test_cmd, test_evidence, runners_dir, mutation_prefix=""):
    mutation_cmd = f'{mutation_prefix}sh {runners_dir}/mutation.sh {package} "{mutant_test_cmd}"'
    return {
        "stack": "python",
        "notes": [f"stack python ({lockfiles}): package {package}/, tests {tests or '(discovery)'}, toolchain {toolchain}"],
        "scope": package + "/",
        "scope_evidence": evidence,
        "lockfiles": lockfiles,
        "lockfiles_evidence": lockfiles_evidence,
        "test_root": "",
        "test_root_evidence": "the package sits at the repository root, nothing to strip",
        "test_paths": init.PYTHON_TEST_PATHS,
        "test_paths_evidence": "pytest's default python_files (test_*.py, *_test.py) anywhere, plus its conftest.py (docs/adr/0053 §1)",
        "toolchain": toolchain,
        "toolchain_evidence": toolchain_evidence,
        "test_cmd": test_cmd,
        "test_cmd_evidence": test_evidence,
        "sast_cmd": f"sh {runners_dir}/sast.sh {package}",
        "sast_cmd_evidence": "bandit over the package, the stack's runner (stacks/python/runners/sast.sh)",
        "mutation_cmd": mutation_cmd,
        "mutation_cmd_evidence": "cosmic-ray over the package, the stack's runner (stacks/python/runners/mutation.sh), the suite stopped at its first failure per mutant",
        "scope_paths": [package + "/"] + ([tests + "/"] if tests else []),
        "tests_sentence": f"From the repository root, `{test_cmd}`. Use exactly this command.",
    }


def infer_python_fallback(repo, runners_dir="runners"):
    """Infer contract metadata for Poetry (poetry.lock) or pinned requirements.txt Python repositories."""
    repo = os.fspath(repo)
    pyproject_path = os.path.join(repo, "pyproject.toml")
    pyproject = {}
    if os.path.isfile(pyproject_path):
        with open(pyproject_path, "rb") as fh:
            pyproject = tomllib.load(fh)

    tests = next((c for c in ("tests", "test") if os.path.isdir(os.path.join(repo, c))), None)
    test_target = tests or ""

    if os.path.isfile(pyproject_path) and os.path.isfile(os.path.join(repo, "poetry.lock")):
        package, evidence = _find_python_package(repo, pyproject)
        project = pyproject.get("project", {}) or {}
        poetry = pyproject.get("tool", {}).get("poetry", {}) or {}
        requires = project.get("requires-python") or (poetry.get("dependencies", {}) or {}).get("python", "")
        source_label = "requires-python" if project.get("requires-python") else "tool.poetry.dependencies.python"
        toolchain, toolchain_evidence = _python_toolchain(requires, source_label)
        install_cmd = (
            "uv run --no-project --with poetry env -u VIRTUAL_ENV "
            "POETRY_VIRTUALENVS_IN_PROJECT=true python -m poetry install --no-interaction"
        )
        pytest_prefix = "env PYTHONPATH=src " if package.startswith("src" + os.sep) or package.startswith("src/") else ""
        run_pytest = f"{pytest_prefix}.venv/bin/python -m pytest {test_target} -q -p no:cacheprovider".replace("  ", " ")
        mutant_test_cmd = f"{pytest_prefix}.venv/bin/python -m pytest {test_target} -q -x -p no:cacheprovider".replace("  ", " ")
        test_cmd = f"{install_cmd} && {run_pytest}"
        test_evidence = (
            f"{tests}/ exists; pytest via poetry lock"
            if tests
            else "no tests/ or test/ directory: pytest's own discovery via poetry lock"
        )
        return _build_python_result(
            package,
            evidence,
            tests,
            "poetry.lock",
            "poetry.lock at the root (no uv.lock)",
            toolchain,
            toolchain_evidence,
            test_cmd,
            mutant_test_cmd,
            test_evidence,
            runners_dir,
            mutation_prefix=f"{install_cmd} && ",
        )

    req_path = os.path.join(repo, "requirements.txt")
    if os.path.isfile(req_path):
        unpinned = _parse_pinned_requirements(req_path)
        if unpinned:
            raise init.Undraftable(
                f"requirements.txt has unpinned dependencies ({', '.join(unpinned)}): "
                "pin every dependency with == so the deps gate is reproducible"
            )
        package, evidence = _find_python_package(repo, pyproject)
        requires = (pyproject.get("project", {}) or {}).get("requires-python", "")
        toolchain, toolchain_evidence = _python_toolchain(requires, "requires-python")
        test_cmd = (
            f"uv run --no-project --with-requirements requirements.txt --with pytest "
            f"python -m pytest {test_target} -q -p no:cacheprovider"
        ).replace("  ", " ")
        mutant_test_cmd = (
            f"uv run --no-project --with-requirements requirements.txt --with pytest "
            f"python -m pytest {test_target} -q -x -p no:cacheprovider"
        ).replace("  ", " ")
        test_evidence = (
            f"{tests}/ exists; pytest against pinned requirements.txt"
            if tests
            else "no tests/ or test/ directory: pytest's own discovery against pinned requirements.txt"
        )
        return _build_python_result(
            package,
            evidence,
            tests,
            "requirements.txt",
            "requirements.txt at the root (all dependencies == pinned)",
            toolchain,
            toolchain_evidence,
            test_cmd,
            mutant_test_cmd,
            test_evidence,
            runners_dir,
        )

    raise init.Undraftable("no poetry.lock or requirements.txt found at the repository root")
