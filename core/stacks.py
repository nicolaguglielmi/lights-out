"""Additional stack inference & monorepo discovery (ADR 0102, ADR 0110): stdlib only.

Implements Go, Rust, Java/Kotlin (Maven & Gradle), TypeScript/JavaScript
(npm, pnpm, yarn, bun), Python fallbacks (poetry, pinned requirements.txt), and
monorepo sub-service discovery (INIT_SERVICE) without touching vendor, model,
or contract-directory literals (ci/vendor-free.sh, ci/app-free.sh,
ci/model-free.sh).
"""

import glob
import json
import os
import posixpath
import re
import tomllib

try:
    from . import init
except ImportError:  # run as a script: core/ is sys.path[0]
    import init

GO_TEST_PATHS = "*_test.go:*/*_test.go"
RUST_TEST_PATHS = "tests/*.rs:tests/*/*.rs:*_test.rs:*/*_test.rs"
JAVA_TEST_PATHS = (
    "src/test/*:*/src/test/*:*Test.java:*/*Test.java:*Tests.java:*/*Tests.java:"
    "*Test.kt:*/*Test.kt:*Tests.kt:*/*Tests.kt"
)
JAVASCRIPT_TEST_PATHS = "test/*:*/test/*:*.test.*:*-test.*:*_test.*"
TYPESCRIPT_TEST_PATHS = "test/*:*/test/*:*.test.*:*.spec.*:*-test.*:*_test.*"

_GO_SKIP_DIRS = {"vendor", "testdata"}
_RUST_SKIP_DIRS = {"target", "tests", "benches", "examples"}
_JAVA_SKIP_DIRS = {"target", "build", "out", ".gradle", "test"}
_WORKSPACE_SKIP_DIRS = {"node_modules", "target", "build", "dist", "vendor", ".git"}
_MANIFEST_NAMES = (
    "pyproject.toml",
    "requirements.txt",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "package.json",
)
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


def _dir_has_files(dirpath, predicate, skip_dirs=()):
    for root, dirs, files in os.walk(dirpath):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in skip_dirs)
        if any(predicate(f) for f in files):
            return True
    return False


def _find_go_scope(repo):
    is_go = lambda f: f.endswith(".go") and not f.endswith("_test.go")  # noqa: E731
    for candidate in ("pkg", "internal", "cmd"):
        path = os.path.join(repo, candidate)
        if os.path.isdir(path) and _dir_has_files(path, is_go, _GO_SKIP_DIRS):
            return candidate + "/", f"{candidate}/ holds Go source files"
    for entry in sorted(os.listdir(repo)):
        if entry.startswith(".") or entry in _GO_SKIP_DIRS:
            continue
        path = os.path.join(repo, entry)
        if os.path.isdir(path) and _dir_has_files(path, is_go, _GO_SKIP_DIRS):
            return entry + "/", f"{entry}/ is the first Go source directory at the root"
    with os.scandir(repo) as entries:
        if any(entry.is_file() and is_go(entry.name) for entry in entries):
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


def _find_rust_scope(repo):
    is_rs = lambda f: f.endswith(".rs") and not f.endswith("_test.rs")  # noqa: E731
    src_path = os.path.join(repo, "src")
    if os.path.isdir(src_path) and _dir_has_files(src_path, is_rs, _RUST_SKIP_DIRS):
        return "src/", "src/ holds Rust source files"
    for entry in sorted(os.listdir(repo)):
        if entry.startswith(".") or entry in _RUST_SKIP_DIRS:
            continue
        path = os.path.join(repo, entry)
        if os.path.isdir(path) and _dir_has_files(path, is_rs, _RUST_SKIP_DIRS):
            return entry + "/", f"{entry}/ is the first Rust source directory at the root"
    with os.scandir(repo) as entries:
        if any(entry.is_file() and is_rs(entry.name) for entry in entries):
            return ".", "Rust source files sit at the repository root"
    raise init.Undraftable(
        "no Rust source files found (*.rs excluding *_test.rs) in src/, subdirectories, or at the root"
    )


def infer_rust(repo, runners_dir="runners", workspace_root=None):
    """Infer contract metadata for a Rust crate with Cargo.toml and Cargo.lock."""
    repo = os.fspath(repo)
    cargo_toml = os.path.join(repo, "Cargo.toml")
    if not os.path.isfile(cargo_toml):
        raise init.Undraftable("Cargo.toml not found at the crate root")

    lockfile_at_workspace_root = False
    if os.path.isfile(os.path.join(repo, "Cargo.lock")):
        lock_evidence = "Cargo.lock beside Cargo.toml (osv-scanner 2.6.0 scans Cargo.lock v3/v4 directly)"
    elif workspace_root and os.path.isfile(os.path.join(workspace_root, "Cargo.lock")):
        lockfile_at_workspace_root = True
        lock_evidence = "Cargo.lock at workspace root (osv-scanner 2.6.0 scans Cargo.lock v3/v4 directly)"
    else:
        raise init.Undraftable(
            "Cargo.toml found but no Cargo.lock beside it: the deps gate scans a lockfile; "
            "run `cargo generate-lockfile` and commit Cargo.lock"
        )

    scope, scope_evidence = _find_rust_scope(repo)
    scope_arg = scope.rstrip("/") or "."
    toolchain = "docker.io/library/rust:1.85"
    toolchain_evidence = (
        "Cargo.toml declares a Rust crate; the official rust 1.85 image, a tag: "
        "pin it by digest before committing (workbench has no Rust toolchain)"
    )
    test_cmd = "cargo test"
    return {
        "stack": "rust",
        "notes": [f"stack rust: scope {scope}, toolchain {toolchain}"],
        "scope": scope,
        "scope_evidence": scope_evidence,
        "lockfiles": "Cargo.lock",
        "lockfiles_evidence": lock_evidence,
        "lockfile_at_workspace_root": lockfile_at_workspace_root,
        "test_root": "",
        "test_root_evidence": "the crate sits at the repository root, nothing to strip",
        "test_paths": RUST_TEST_PATHS,
        "test_paths_evidence": "Rust integration tests under tests/ and *_test.rs modules",
        "toolchain": toolchain,
        "toolchain_evidence": toolchain_evidence,
        "test_cmd": test_cmd,
        "test_cmd_evidence": "standard Cargo test suite",
        "sast_cmd": f"sh {runners_dir}/sast.sh {scope_arg}",
        "sast_cmd_evidence": "Rust static security analyzer over the scope, the stack's runner (stacks/rust/runners/sast.sh)",
        "mutation_cmd": f'sh {runners_dir}/mutation.sh {scope_arg} "{test_cmd}"',
        "mutation_cmd_evidence": "line-scoped Rust mutator over the scope, the stack's runner (stacks/rust/runners/mutation.sh)",
        "scope_paths": [scope],
        "tests_sentence": f"From the repository root, `{test_cmd}`. Use exactly this command.",
    }


def _find_java_scope(repo):
    is_jvm = lambda f: f.endswith((".java", ".kt")) and not f.endswith(  # noqa: E731
        ("Test.java", "Tests.java", "Test.kt", "Tests.kt", "Spec.java", "Spec.kt")
    )
    src_main = os.path.join(repo, "src", "main")
    if os.path.isdir(src_main) and _dir_has_files(src_main, is_jvm, _JAVA_SKIP_DIRS):
        return "src/main/", "src/main/ holds Java/Kotlin production source files"
    src_dir = os.path.join(repo, "src")
    if os.path.isdir(src_dir) and _dir_has_files(src_dir, is_jvm, _JAVA_SKIP_DIRS):
        return "src/", "src/ holds Java/Kotlin production source files"
    raise init.Undraftable(
        "no Java/Kotlin source files found (*.java/*.kt) under src/main/ or src/"
    )


def _wrapper_cmd(repo, wrapper_name, fallback_cmd, workspace_root=None):
    if os.path.isfile(os.path.join(repo, wrapper_name)):
        return f"./{wrapper_name} test", f"{wrapper_name} wrapper exists"
    if workspace_root and os.path.isfile(os.path.join(workspace_root, wrapper_name)):
        rel = posixpath.relpath(os.path.join(workspace_root, wrapper_name), repo)
        return f"{rel} test", f"{wrapper_name} wrapper at workspace root"
    return f"{fallback_cmd} test", f"standard {fallback_cmd} test suite"


def infer_java(repo, runners_dir="runners", workspace_root=None):
    """Infer contract metadata for a Java/Kotlin Maven or Gradle project."""
    repo = os.fspath(repo)
    has_pom = os.path.isfile(os.path.join(repo, "pom.xml"))
    has_gradle = os.path.isfile(os.path.join(repo, "build.gradle")) or os.path.isfile(
        os.path.join(repo, "build.gradle.kts")
    )
    if not has_pom and not has_gradle:
        raise init.Undraftable("no pom.xml, build.gradle, or build.gradle.kts found")

    lockfile_at_workspace_root = False
    if has_pom:
        lockfiles = "pom.xml"
        lockfiles_evidence = "pom.xml at the root (verified directly supported by osv-scanner 2.6.0)"
        toolchain = "docker.io/library/maven:3.9-eclipse-temurin-21"
        toolchain_evidence = (
            "pom.xml declares a Maven build; the official Maven/Temurin 21 image, a tag: "
            "pin it by digest before committing (workbench has no JVM toolchain)"
        )
        test_cmd, test_evidence = _wrapper_cmd(repo, "mvnw", "mvn", workspace_root=workspace_root)
    else:
        gradle_locks = ("gradle.lockfile", "gradle/verification-metadata.xml", "buildscript-gradle.lockfile")
        chosen_lock = next(
            (cand for cand in gradle_locks if os.path.isfile(os.path.join(repo, cand))),
            None,
        )
        if chosen_lock is None and workspace_root:
            chosen_lock = next(
                (cand for cand in gradle_locks if os.path.isfile(os.path.join(workspace_root, cand))),
                None,
            )
            if chosen_lock is not None:
                lockfile_at_workspace_root = True
        if chosen_lock is None:
            raise init.Undraftable(
                "Gradle build file found without gradle.lockfile or gradle/verification-metadata.xml: "
                "osv-scanner 2.6.0 exits 127 on build.gradle; run `./gradlew dependencies --write-locks` "
                "and commit gradle.lockfile"
            )
        lockfiles = chosen_lock
        lockfiles_evidence = f"{chosen_lock} (verified supported by osv-scanner 2.6.0)"
        toolchain = "docker.io/library/gradle:8-jdk21"
        toolchain_evidence = (
            "Gradle build declares a JVM project; the official Gradle 8/JDK 21 image, a tag: "
            "pin it by digest before committing (workbench has no JVM toolchain)"
        )
        test_cmd, test_evidence = _wrapper_cmd(repo, "gradlew", "gradle", workspace_root=workspace_root)

    scope, scope_evidence = _find_java_scope(repo)
    scope_arg = scope.rstrip("/")
    return {
        "stack": "java",
        "notes": [f"stack java: scope {scope}, lockfile {lockfiles}, toolchain {toolchain}"],
        "scope": scope,
        "scope_evidence": scope_evidence,
        "lockfiles": lockfiles,
        "lockfiles_evidence": lockfiles_evidence,
        "lockfile_at_workspace_root": lockfile_at_workspace_root,
        "test_root": "",
        "test_root_evidence": "the project sits at the repository root, nothing to strip",
        "test_paths": JAVA_TEST_PATHS,
        "test_paths_evidence": "standard Java/Kotlin test directory and *Test/*Tests suffixes",
        "toolchain": toolchain,
        "toolchain_evidence": toolchain_evidence,
        "test_cmd": test_cmd,
        "test_cmd_evidence": test_evidence,
        "sast_cmd": f"sh {runners_dir}/sast.sh {scope_arg}",
        "sast_cmd_evidence": "Java/Kotlin static security analyzer over src/main, the stack's runner (stacks/java/runners/sast.sh)",
        "mutation_cmd": f'sh {runners_dir}/mutation.sh {scope_arg} "{test_cmd}"',
        "mutation_cmd_evidence": "line-scoped Java/Kotlin mutator over src/main, the stack's runner (stacks/java/runners/mutation.sh)",
        "scope_paths": [scope] + (["src/test/"] if os.path.isdir(os.path.join(repo, "src", "test")) else []),
        "tests_sentence": f"From the repository root, `{test_cmd}`. Use exactly this command.",
    }


def _detect_js_lockfile(repo, package_json, workspace_root=None):
    """Return (lockfile_name, lockfile_path, at_workspace_root) or raise init.Undraftable."""
    candidates = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock")
    pm = str(package_json.get("packageManager", "") or "").lower()
    if pm.startswith("pnpm@"):
        order = ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "bun.lock")
    elif pm.startswith("yarn@"):
        order = ("yarn.lock", "package-lock.json", "pnpm-lock.yaml", "bun.lock")
    elif pm.startswith("bun@"):
        order = ("bun.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")
    else:
        order = candidates

    for name in order:
        p = os.path.join(repo, name)
        if os.path.isfile(p):
            return name, p, False
    if workspace_root:
        for name in order:
            p = os.path.join(workspace_root, name)
            if os.path.isfile(p):
                return name, p, True

    if os.path.isfile(os.path.join(repo, "bun.lockb")) or (
        workspace_root and os.path.isfile(os.path.join(workspace_root, "bun.lockb"))
    ):
        raise init.Undraftable(
            "package.json and bun.lockb found, but osv-scanner 2.6.0 does not support binary bun.lockb: "
            "run `bun install --save-text-lockfile` (Bun >= 1.2) and commit text bun.lock"
        )
    raise init.Undraftable(
        "package.json found but no lockfile beside it: looked for package-lock.json, pnpm-lock.yaml, "
        "yarn.lock, or bun.lock (the deps gate scans a lockfile; run `npm install`, `pnpm install`, "
        "`yarn install`, or `bun install --save-text-lockfile` and commit it)"
    )


def _has_npm_workspaces(workspace_root):
    pkg_path = os.path.join(workspace_root, "package.json")
    if not os.path.isfile(pkg_path):
        return False
    try:
        with open(pkg_path, encoding="utf-8") as fh:
            return bool(json.load(fh).get("workspaces"))
    except (OSError, ValueError):
        return False


def _node_toolchain(package_json):
    engines = (package_json.get("engines", {}) or {}).get("node", "")
    if init.node_admits(engines, init.WORKBENCH_NODE):
        toolchain = "workbench"
        evidence = (
            f"engines.node = {engines!r} admits the workbench's node {init.WORKBENCH_NODE}; set an image to run elsewhere"
            if engines
            else f"package.json declares no engines.node; the workbench's node {init.WORKBENCH_NODE} is assumed"
        )
        return toolchain, evidence
    chosen = next((v for v in init.NODE_CANDIDATES if init.node_admits(engines, v)), None)
    if chosen is None:
        raise init.Undraftable(
            f"engines.node = {engines!r} admits none of the node majors init knows "
            f"({', '.join(map(str, init.NODE_CANDIDATES))})"
        )
    toolchain = f"docker.io/library/node:{chosen}"
    evidence = (
        f"engines.node = {engines!r} does not admit the workbench's node {init.WORKBENCH_NODE}; "
        f"the official node {chosen} image, a tag: pin it by digest before committing"
    )
    return toolchain, evidence


def _yarn_install_cmd(lockfile_path):
    try:
        with open(lockfile_path, encoding="utf-8", errors="replace") as fh:
            head = "".join(fh.readline() for _ in range(25))
    except OSError:
        head = ""
    if "__metadata:" in head and "yarn lockfile v1" not in head:
        return "corepack yarn install --immutable"
    return "corepack yarn install --frozen-lockfile"


def _infer_node_project(repo, stack_name, runners_dir="runners", workspace_root=None):
    repo = os.fspath(repo)
    pkg_path = os.path.join(repo, "package.json")
    if not os.path.isfile(pkg_path):
        raise init.Undraftable(f"package.json is required for {stack_name}")
    if stack_name == "typescript" and not os.path.isfile(os.path.join(repo, "tsconfig.json")):
        raise init.Undraftable("package.json and tsconfig.json are both required for typescript")

    with open(pkg_path, encoding="utf-8") as fh:
        package_json = json.load(fh)

    lockfile, lock_path, lock_at_root = _detect_js_lockfile(repo, package_json, workspace_root=workspace_root)
    src = next((c for c in ("src", "lib") if os.path.isdir(os.path.join(repo, c))), None)
    if src is None:
        raise init.Undraftable(f"no {stack_name} source directory found: looked for src/ and lib/ at the root")

    scripts = package_json.get("scripts", {}) or {}
    has_test_script = "test" in scripts

    if lockfile == "package-lock.json":
        if lock_at_root and workspace_root:
            install_cmd = f"npm ci --prefix {posixpath.relpath(workspace_root, repo)}"
        elif workspace_root and _has_npm_workspaces(workspace_root):
            install_cmd = "npm ci --workspaces=false"
        else:
            install_cmd = "npm ci"
        suite = "npm test" if has_test_script else "node --test"
        toolchain, toolchain_evidence = _node_toolchain(package_json)
    elif lockfile == "pnpm-lock.yaml":
        install_cmd = "corepack pnpm install --frozen-lockfile"
        suite = "corepack pnpm test" if has_test_script else "node --test"
        toolchain, toolchain_evidence = _node_toolchain(package_json)
    elif lockfile == "yarn.lock":
        install_cmd = _yarn_install_cmd(lock_path)
        suite = "corepack yarn test" if has_test_script else "node --test"
        toolchain, toolchain_evidence = _node_toolchain(package_json)
    else:  # bun.lock
        install_cmd = "bun install --frozen-lockfile"
        suite = "bun run test" if has_test_script else "bun test"
        toolchain = "docker.io/oven/bun:1"
        toolchain_evidence = (
            "bun.lock uses the Bun runtime; the official oven/bun:1 image, a tag: "
            "pin it by digest before committing (workbench has no bun binary)"
        )

    test_cmd = f"{install_cmd} && {suite}"
    if has_test_script:
        test_evidence = f"package.json scripts.test = {scripts['test']!r}, after locked install ({lockfile})"
    elif lockfile == "bun.lock":
        test_evidence = "package.json has no scripts.test: bun test after locked install (bun.lock)"
    else:
        test_evidence = f"package.json has no scripts.test: Node {init.WORKBENCH_NODE} runs tests via node --test after locked install ({lockfile})"

    tests_dir = next((c for c in ("test", "tests", "__tests__") if os.path.isdir(os.path.join(repo, c))), None)
    if stack_name == "typescript":
        test_paths = TYPESCRIPT_TEST_PATHS
        test_paths_evidence = "Node 24 test runner and TypeScript test/spec patterns across extensions"
        sast_evidence = f"eslint with eslint-plugin-security and typescript parser over the sources, the stack's runner (stacks/typescript/runners/sast.sh)"
        mut_evidence = f"Stryker over the TypeScript sources, the stack's runner (stacks/typescript/runners/mutation.sh), the suite run whole per mutant"
    else:
        test_paths = JAVASCRIPT_TEST_PATHS
        test_paths_evidence = "node --test's default patterns (Node 24 docs: test/ directories, *.test.*, *-test.*, *_test.*), any extension (docs/adr/0053 §1)"
        sast_evidence = f"eslint with eslint-plugin-security over the sources, the stack's runner (stacks/javascript/runners/sast.sh)"
        mut_evidence = f"Stryker over the sources, the stack's runner (stacks/javascript/runners/mutation.sh), the suite run whole per mutant"

    lock_where = "workspace root" if lock_at_root else "root"
    return {
        "stack": stack_name,
        "notes": [f"stack {stack_name}: source {src}/, lockfile {lockfile}, tests {tests_dir or '(discovery)'}, toolchain {toolchain}"],
        "scope": src + "/",
        "scope_evidence": f"{src}/ exists at the root",
        "lockfiles": lockfile,
        "lockfiles_evidence": f"{lockfile} at the {lock_where}",
        "lockfile_at_workspace_root": lock_at_root,
        "test_root": "",
        "test_root_evidence": "the sources sit at the repository root, nothing to strip",
        "test_paths": test_paths,
        "test_paths_evidence": test_paths_evidence,
        "toolchain": toolchain,
        "toolchain_evidence": toolchain_evidence,
        "test_cmd": test_cmd,
        "test_cmd_evidence": test_evidence,
        "sast_cmd": f"sh {runners_dir}/sast.sh {src}",
        "sast_cmd_evidence": sast_evidence,
        "mutation_cmd": f'{install_cmd} && sh {runners_dir}/mutation.sh {src} "{suite}"',
        "mutation_cmd_evidence": mut_evidence,
        "scope_paths": [src + "/"] + ([tests_dir + "/"] if tests_dir else []),
        "tests_sentence": f"From the repository root, `{test_cmd}`. Use exactly this command.",
    }


def infer_javascript(repo, runners_dir="runners", workspace_root=None):
    """Infer contract metadata for a JavaScript repository."""
    return _infer_node_project(repo, "javascript", runners_dir=runners_dir, workspace_root=workspace_root)


def infer_typescript(repo, runners_dir="runners", workspace_root=None):
    """Infer contract metadata for a TypeScript repository."""
    return _infer_node_project(repo, "typescript", runners_dir=runners_dir, workspace_root=workspace_root)


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
        line = re.sub(r"\s+#.*$", "", line).strip()
        if not line:
            continue
        first_tok = line.split()[0]
        if first_tok in _PIP_SKIP_FLAGS or any(first_tok.startswith(f + "=") for f in _PIP_SKIP_FLAGS):
            continue
        if line.startswith(("-r", "--requirement", "-c", "--constraint", "-e", "--editable")):
            unpinned.append(line)
            continue
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


# --- monorepo & sub-service discovery (ADR 0110) --------------------------


def normalize_service(repo, service=None):
    """Resolve and validate INIT_SERVICE / --service relative path inside repo."""
    raw = os.environ.get("INIT_SERVICE", "") if service is None else service
    raw = raw.strip()
    if not raw or raw == ".":
        return ""
    if "\0" in raw or "$" in raw or "#" in raw:
        raise init.Undraftable(f"INIT_SERVICE={raw!r} contains forbidden characters")
    if os.path.isabs(raw) or raw.startswith("/"):
        raise init.Undraftable(f"INIT_SERVICE={raw!r} must be a relative path inside the repository")
    norm = posixpath.normpath(raw.replace(os.sep, "/")).strip("/")
    if norm in ("", "."):
        return ""
    if norm == ".." or norm.startswith("../"):
        raise init.Undraftable(f"INIT_SERVICE={raw!r} escapes the repository root")
    full = os.path.join(repo, norm)
    if not os.path.isdir(full):
        raise init.Undraftable(f"INIT_SERVICE={norm!r} directory does not exist in {repo}")
    return norm


def _expand_workspace_globs(repo, patterns, require_manifest=True):
    """Expand workspace glob patterns into sorted repo-relative sub-service dirs."""
    included = set()
    excluded = set()
    for pat in patterns:
        if not isinstance(pat, str):
            continue
        p = pat.strip().rstrip("/")
        if not p:
            continue
        is_neg = p.startswith("!")
        raw = p[1:].lstrip("/") if is_neg else p.lstrip("/")
        if not raw or ".." in raw.split("/"):
            continue
        matches = glob.glob(os.path.join(repo, raw))
        for m in matches:
            if not os.path.isdir(m):
                continue
            rel = os.path.relpath(m, repo).replace(os.sep, "/")
            if rel in ("", ".") or any(part.startswith(".") or part in _WORKSPACE_SKIP_DIRS for part in rel.split("/")):
                continue
            if is_neg:
                excluded.add(rel)
            elif not require_manifest or any(os.path.isfile(os.path.join(m, mf)) for mf in _MANIFEST_NAMES):
                included.add(rel)
    return sorted(included - excluded)


def _parse_pnpm_workspace_patterns(path):
    patterns = []
    in_packages = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = re.sub(r"\s+#.*$", "", raw).strip()
            if not line or line.startswith("#"):
                continue
            if re.match(r"^packages\s*:", line):
                in_packages = True
                continue
            if in_packages:
                if line.startswith("-"):
                    item = line[1:].strip().strip("'\"")
                    if item:
                        patterns.append(item)
                elif ":" in line:
                    break
    return patterns


def _parse_gradle_settings(path, repo):
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    found = set()
    for m in re.finditer(r"\binclude\b\s*(?:\(([^)]*)\)|([^\n;]+))", text):
        arg_str = m.group(1) if m.group(1) is not None else m.group(2)
        for token in re.findall(r"""['"]([^'"]+)['"]""", arg_str):
            rel = token.strip().lstrip(":").replace(":", "/")
            if rel and os.path.isdir(os.path.join(repo, rel)):
                found.add(rel)
    return sorted(found)


def _parse_maven_modules(path, repo):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    modules = []
    for block in re.finditer(
        r"<(?:[A-Za-z0-9_-]+:)?modules\b[^>]*>(.*?)</(?:[A-Za-z0-9_-]+:)?modules\s*>",
        text,
        flags=re.DOTALL,
    ):
        for m in re.finditer(
            r"<(?:[A-Za-z0-9_-]+:)?module\b[^>]*>([^<]+)</(?:[A-Za-z0-9_-]+:)?module\s*>",
            block.group(1),
        ):
            rel = m.group(1).strip().strip("/")
            if rel and os.path.isdir(os.path.join(repo, rel)):
                modules.append(rel)
    return sorted(set(modules))


def _scan_subdir_services(repo):
    found = set()
    for root, dirs, files in os.walk(repo):
        rel_dir = os.path.relpath(root, repo).replace(os.sep, "/")
        depth = 0 if rel_dir == "." else len(rel_dir.split("/"))
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _WORKSPACE_SKIP_DIRS)
        if depth >= 2:
            dirs[:] = []
        if rel_dir != "." and any(f in files for f in _MANIFEST_NAMES):
            found.add(rel_dir)
            dirs[:] = []
    return sorted(found)


def discover_workspace_services(repo):
    """Return (kind_label, sorted_service_dirs, is_virtual_root) for monorepo roots."""
    pkg_path = os.path.join(repo, "package.json")
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path, encoding="utf-8") as fh:
                pkg = json.load(fh)
        except (OSError, ValueError):
            pkg = {}
        ws = pkg.get("workspaces")
        patterns = ws if isinstance(ws, list) else (ws.get("packages", []) if isinstance(ws, dict) else [])
        if patterns:
            services = _expand_workspace_globs(repo, patterns)
            if services:
                return "package.json workspaces", services, not (
                    os.path.isdir(os.path.join(repo, "src")) or os.path.isdir(os.path.join(repo, "lib"))
                )

    pnpm_ws = os.path.join(repo, "pnpm-workspace.yaml")
    if os.path.isfile(pnpm_ws):
        services = _expand_workspace_globs(repo, _parse_pnpm_workspace_patterns(pnpm_ws))
        if services:
            return "pnpm-workspace.yaml", services, not os.path.isfile(pkg_path)

    cargo_path = os.path.join(repo, "Cargo.toml")
    if os.path.isfile(cargo_path):
        try:
            with open(cargo_path, "rb") as fh:
                cargo = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            cargo = {}
        ws = cargo.get("workspace")
        if isinstance(ws, dict):
            members = list(ws.get("members") or [])
            excludes = [f"!{e}" for e in (ws.get("exclude") or [])]
            services = _expand_workspace_globs(repo, members + excludes)
            is_virtual = "package" not in cargo
            if services or is_virtual:
                return "Cargo.toml [workspace]", services, is_virtual

    for settings_name in ("settings.gradle", "settings.gradle.kts"):
        sp = os.path.join(repo, settings_name)
        if os.path.isfile(sp):
            services = _parse_gradle_settings(sp, repo)
            if services:
                has_root_build = os.path.isfile(os.path.join(repo, "build.gradle")) or os.path.isfile(
                    os.path.join(repo, "build.gradle.kts")
                )
                return settings_name, services, not has_root_build

    pom_path = os.path.join(repo, "pom.xml")
    if os.path.isfile(pom_path):
        modules = _parse_maven_modules(pom_path, repo)
        if modules:
            return "pom.xml <modules>", modules, not os.path.isdir(os.path.join(repo, "src"))

    has_root_manifest = any(os.path.isfile(os.path.join(repo, mf)) for mf in _MANIFEST_NAMES)
    if not has_root_manifest:
        services = _scan_subdir_services(repo)
        if len(services) >= 2:
            return "multi-service repository", services, True

    return None, [], False


def apply_service_prefix(result, service):
    """Scope an inferred contract dictionary to monorepo sub-service `service`."""
    scoped = dict(result)
    at_root = bool(scoped.pop("lockfile_at_workspace_root", False))
    scoped["test_root"] = f"{service}/"
    scoped["test_root_evidence"] = f"{service}/ is the selected sub-service root (INIT_SERVICE={service})"
    if scoped["scope"] == ".":
        scoped["scope"] = f"{service}/"
    else:
        scoped["scope"] = f"{service}/{scoped['scope']}"
    scoped["scope_evidence"] = f"{service}/: {scoped['scope_evidence']}"
    if not at_root and scoped["lockfiles"]:
        scoped["lockfiles"] = ":".join(f"{service}/{x}" for x in scoped["lockfiles"].split(":") if x)
    if scoped["test_paths"]:
        scoped["test_paths"] = ":".join(f"{service}/{g}" for g in scoped["test_paths"].split(":") if g)
    scoped["scope_paths"] = [
        f"{service}/" if p == "." else f"{service}/{p}" for p in scoped["scope_paths"]
    ]
    scoped["tests_sentence"] = f"From `{service}/`, `{scoped['test_cmd']}`. Use exactly this command."
    scoped["notes"] = [f"{n}, service {service}/" for n in scoped["notes"]]
    return scoped
