"""Safe contract apply, scoped artifact commits, and incremental mutation scope (ADR 0113): stdlib only.

Provides:
- apply_draft(draft_dir, target_repo, contract_rel, *, branch=None, commit=False, force=False)
- select_incremental_scope(repo_dir, scope, max_files=10)
- commit_ticket_artifact(repo_dir, artifact_path, message)

Strictly vendor-free, app-free, and model-free: callers supply contract_rel dynamically.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import stat
import subprocess  # nosec B404 - invokes git with explicit argv lists only
import tempfile
from typing import NamedTuple


class ApplyError(Exception):
    """Raised when applying a draft, scanning secrets, or committing fails safely."""


class IncrementalScope(NamedTuple):
    """Selected MUTATION_LINES specification and its human-readable provenance."""

    mutation_lines: str
    provenance: str


_SOURCE_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".jsx",
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".go",
        ".rs",
        ".java",
        ".kt",
    }
)

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "target",
        "build",
        "dist",
        "out",
        ".gradle",
        "vendor",
        "testdata",
        "test",
        "tests",
        "__tests__",
        "benches",
        "examples",
        "fixtures",
        ".crew",
        ".gates-out",
    }
)

_EXCLUDED_NAMES = frozenset(
    {
        "conftest.py",
        "uv.lock",
        "poetry.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lock",
        "bun.lockb",
        "Cargo.lock",
        "gradle.lockfile",
        "buildscript-gradle.lockfile",
        "go.sum",
        "go.mod",
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "requirements.txt",
        "tsconfig.json",
    }
)

_TEST_STEM_RE = re.compile(
    r"(?:^test_.*|.*_test|.*-test|.*\.test|.*\.spec)$",
    re.IGNORECASE,
)
_JVM_TEST_RE = re.compile(r".*(?:Test|Tests|Spec)\.(?:java|kt)$")

_PRIVATE_KEY_NAMES = frozenset({"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "cosign.key"})
_PRIVATE_KEY_EXTS = (".pem", ".key", ".p12", ".pfx", ".jks")
_PEM_HEADER_RE = re.compile(r"-{5}BEGIN\s+(?:[A-Z0-9]+\s+)*PRIVATE\s+KEY-{5}")

# Minimum prefix/payload widths match documented token formats (ADR 0113 Provenance).
_HIGH_SIGNAL_SECRET_RES = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bBearer\s+(?!<)[A-Za-z0-9._-]{16,}\b"),
)

_SECRET_ASSIGN_RE = re.compile(
    r"""^(?:export\s+)?["']?([A-Za-z0-9_]*(?:password|passwd|secret|token|credential|auth_header|(?:api|access|auth|client|signing|encryption|private)[_-]?key))[A-Za-z0-9_]*["']?\s*[:=]\s*(.+?)\s*,?\s*$""",
    re.IGNORECASE,
)


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"):
        env.pop(key, None)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def _git(
    repo_dir: str,
    *args: str,
    check: bool = True,
    with_identity_fallback: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-C", repo_dir, "-c", "core.quotePath=false", "-c", "color.ui=never"]
    if with_identity_fallback:
        name = _git_config_get(repo_dir, "user.name")
        email = _git_config_get(repo_dir, "user.email")
        if not name:
            cmd.extend(["-c", "user.name=lights-out"])
        if not email:
            cmd.extend(["-c", "user.email=lights-out@localhost.invalid"])
        cmd.extend(["-c", "commit.gpgsign=false"])
    cmd.extend(args)
    proc = subprocess.run(  # nosec B603 - explicit git argv list, shell=False
        cmd,
        env=_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise ApplyError(f"git {' '.join(args[:2])} failed: {detail}")
    return proc


def _git_config_get(repo_dir: str, key: str) -> str:
    proc = subprocess.run(  # nosec B603 - explicit git argv list, shell=False
        ["git", "-C", repo_dir, "config", "--get", key],
        env=_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _is_git_repo(repo_dir: str) -> bool:
    proc = _git(repo_dir, "rev-parse", "--is-inside-work-tree", check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _has_head(repo_dir: str) -> bool:
    return _git(repo_dir, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode == 0


def _current_branch(repo_dir: str) -> str | None:
    proc = _git(repo_dir, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if proc.returncode == 0:
        name = proc.stdout.strip()
        return name or None
    return None


def _normalize_rel_path(raw: str | os.PathLike[str], label: str, *, allow_dot: bool = False) -> str:
    text = os.fspath(raw)
    if not isinstance(text, str):
        raise ValueError(f"{label} must be a path string")
    text = text.strip()
    if not text:
        raise ValueError(f"{label} must not be empty")
    if "\0" in text or "$" in text or "#" in text:
        raise ValueError(f"{label}={text!r} contains forbidden characters")
    if os.path.isabs(text) or text.startswith("/") or text.startswith("\\"):
        raise ValueError(f"{label}={text!r} must be a relative path")
    posix_raw = text.replace("\\", "/")
    parts = [p for p in posix_raw.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"{label}={text!r} must not traverse parent directories")
    norm = "/".join(parts)
    if not norm:
        if allow_dot:
            return "."
        raise ValueError(f"{label}={text!r} must name a subdirectory or file inside the repository")
    return norm


def _resolve_inside_repo(repo_dir: str, rel_posix: str, label: str, *, allow_repo_root: bool = False) -> str:
    repo_real = os.path.realpath(repo_dir)
    if rel_posix == ".":
        if not allow_repo_root:
            raise ValueError(f"{label} must not point to the repository root")
        return repo_real
    candidate = os.path.join(repo_real, *rel_posix.split("/"))
    resolved = os.path.realpath(candidate)
    if os.path.commonpath([repo_real, resolved]) != repo_real:
        raise ValueError(f"{label}={rel_posix!r} escapes the repository root")
    if not allow_repo_root and resolved == repo_real:
        raise ValueError(f"{label}={rel_posix!r} must resolve strictly inside the repository")
    # Also ensure no existing parent component is a symlink pointing outside the repo.
    cur = repo_real
    for part in rel_posix.split("/"):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            link_real = os.path.realpath(cur)
            if os.path.commonpath([repo_real, link_real]) != repo_real:
                raise ValueError(f"{label}={rel_posix!r} traverses a symlink outside the repository")
    return resolved


def _is_placeholder_or_code_rhs(raw_rhs: str) -> bool:
    val = raw_rhs.strip()
    if not val:
        return True
    quoted = (len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'))
    unquoted = val[1:-1].strip() if quoted else val
    if not unquoted:
        return True
    if unquoted.startswith("$") or (unquoted.startswith("<") and unquoted.endswith(">")):
        return True
    if unquoted.lower() in ("true", "false", "null", "none", "off", "on"):
        return True
    if not quoted and any(tok in unquoted for tok in ("(", "[", "{", "os.environ", "process.env")):
        return True
    return False


def _check_secret_content(rel_path: str, data: bytes) -> None:
    base = posixpath.basename(rel_path)
    if base == ".env" or base.startswith(".env."):
        raise ApplyError(f"refusing secret environment file in artifact/draft: {rel_path}")
    if base in _PRIVATE_KEY_NAMES or base.endswith(_PRIVATE_KEY_EXTS):
        raise ApplyError(f"refusing private key file in artifact/draft: {rel_path}")

    text = data.decode("utf-8", errors="replace")
    if _PEM_HEADER_RE.search(text):
        raise ApplyError(f"refusing private key material in {rel_path}")
    for pat in _HIGH_SIGNAL_SECRET_RES:
        if pat.search(text):
            raise ApplyError(f"refusing secret token pattern in {rel_path}")
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _SECRET_ASSIGN_RE.match(stripped)
        if match and not _is_placeholder_or_code_rhs(match.group(2)):
            raise ApplyError(f"refusing credential assignment ({match.group(1)}) at {rel_path}:{lineno}")


def _collect_draft_files(draft_dir: str) -> list[tuple[str, bytes, bool]]:
    if not os.path.isdir(draft_dir):
        raise ApplyError(f"draft directory does not exist: {draft_dir}")
    draft_real = os.path.realpath(draft_dir)
    entries: list[tuple[str, bytes, bool]] = []
    for root, dirs, files in os.walk(draft_real):
        dirs.sort()
        files.sort()
        for d in dirs:
            d_path = os.path.join(root, d)
            if os.path.islink(d_path):
                raise ApplyError(f"refusing symlink directory in draft: {d_path}")
        for fname in files:
            full = os.path.join(root, fname)
            if os.path.islink(full):
                raise ApplyError(f"refusing symlink in draft: {full}")
            rel = os.path.relpath(full, draft_real).replace(os.sep, "/")
            with open(full, "rb") as fh:
                data = fh.read()
            _check_secret_content(rel, data)
            is_exec = bool(os.stat(full).st_mode & stat.S_IXUSR)
            entries.append((rel, data, is_exec))
    if not entries:
        raise ApplyError(f"draft directory is empty: {draft_dir}")
    entries.sort(key=lambda item: item[0])
    return entries


def _git_nul_paths(repo_dir: str, *args: str) -> set[str]:
    proc = _git(repo_dir, *args, check=False)
    if proc.returncode != 0 or not proc.stdout:
        return set()
    return {p for p in proc.stdout.split("\0") if p}


def _git_status_paths(repo_dir: str, rel_scope: str) -> set[str]:
    proc = _git(repo_dir, "status", "--porcelain=v1", "-z", "--", rel_scope, check=False)
    if proc.returncode != 0 or not proc.stdout:
        return set()
    paths: set[str] = set()
    tokens = proc.stdout.split("\0")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if len(tok) < 4:
            continue
        code = tok[:2]
        path = tok[3:]
        if path:
            paths.add(path)
        if ("R" in code or "C" in code) and i < len(tokens):
            old_path = tokens[i]
            i += 1
            if old_path:
                paths.add(old_path)
    return paths


def _validate_branch_name(repo_dir: str, branch: str) -> str:
    if not isinstance(branch, str) or not branch.strip():
        raise ValueError("branch name must not be empty")
    clean = branch.strip()
    if clean.startswith("-") or any(ch.isspace() for ch in clean):
        raise ValueError(f"invalid branch name: {branch!r}")
    proc = _git(repo_dir, "check-ref-format", "--branch", clean, check=False)
    if proc.returncode != 0:
        raise ValueError(f"invalid git branch name: {branch!r}")
    return clean


def _place_files_atomically(
    target_repo: str,
    dest_dir: str,
    planned: list[tuple[str, bytes, bool]],
    overwritten_set: set[str],
) -> None:
    repo_real = os.path.realpath(target_repo)
    stage_dir = tempfile.mkdtemp(prefix=".lo-apply-stage-", dir=repo_real)
    backup_dir = tempfile.mkdtemp(prefix=".lo-apply-backup-", dir=repo_real)
    backed_up: list[tuple[str, str]] = []
    placed: list[str] = []
    created_dirs: list[str] = []
    try:
        for repo_rel, data, is_exec in planned:
            staged_path = os.path.join(stage_dir, *repo_rel.split("/"))
            os.makedirs(os.path.dirname(staged_path), exist_ok=True)
            with open(staged_path, "wb") as fh:
                fh.write(data)
            os.chmod(staged_path, 0o755 if is_exec else 0o644)  # nosec B103 - owner-writable script/config modes

        for repo_rel, _, _ in planned:
            dst_path = os.path.join(repo_real, *repo_rel.split("/"))
            dst_parent = os.path.dirname(dst_path)
            if not os.path.exists(dst_parent):
                # Track directories we newly create for clean rollback on error.
                missing: list[str] = []
                cur = dst_parent
                while cur and cur != repo_real and not os.path.exists(cur):
                    missing.append(cur)
                    cur = os.path.dirname(cur)
                os.makedirs(dst_parent, exist_ok=True)
                created_dirs.extend(missing)

            if os.path.islink(dst_path):
                raise ApplyError(f"refusing to overwrite symlink at target path: {repo_rel}")
            if os.path.exists(dst_path):
                if repo_rel not in overwritten_set:
                    raise ApplyError(f"refusing to overwrite existing file without force: {repo_rel}")
                bak_path = os.path.join(backup_dir, *repo_rel.split("/"))
                os.makedirs(os.path.dirname(bak_path), exist_ok=True)
                os.replace(dst_path, bak_path)
                backed_up.append((dst_path, bak_path))

            staged_path = os.path.join(stage_dir, *repo_rel.split("/"))
            os.replace(staged_path, dst_path)
            placed.append(dst_path)
    except Exception:
        for dst_path in reversed(placed):
            try:
                if os.path.exists(dst_path):
                    os.remove(dst_path)
            except OSError:
                pass
        for dst_path, bak_path in reversed(backed_up):
            try:
                if os.path.exists(bak_path):
                    os.replace(bak_path, dst_path)
            except OSError:
                pass
        for d in created_dirs:
            try:
                if os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
        shutil.rmtree(backup_dir, ignore_errors=True)


def _commit_scoped_paths(repo_path: str, paths: list[str] | tuple[str, ...], message: str) -> str | None:
    _git(repo_path, "add", "--", *paths)
    should_commit = not _has_head(repo_path)
    if not should_commit:
        diff_proc = _git(repo_path, "diff", "--cached", "--quiet", "--", *paths, check=False)
        should_commit = diff_proc.returncode != 0
    if not should_commit:
        return None
    _git(
        repo_path,
        "commit",
        "--only",
        "-q",
        "-m",
        message,
        "--",
        *paths,
        with_identity_fallback=True,
    )
    return _git(repo_path, "rev-parse", "HEAD").stdout.strip()


def apply_draft(
    draft_dir: str | os.PathLike[str],
    target_repo: str | os.PathLike[str],
    contract_rel: str,
    *,
    branch: str | None = None,
    commit: bool = False,
    force: bool = False,
) -> dict[str, object]:
    """Atomically copy drafted contract files into <target_repo>/<contract_rel>.

    Refuses to overwrite existing/tracked/modified files unless force=True,
    refuses secret patterns before touching target_repo, optionally creates or
    switches to `branch`, and when commit=True stages and commits exclusively the
    applied files without touching unrelated worktree/index state.
    """
    draft_path = os.path.abspath(os.fspath(draft_dir))
    repo_path = os.path.abspath(os.fspath(target_repo))
    if not os.path.isdir(repo_path):
        raise ApplyError(f"target repository does not exist: {repo_path}")

    norm_rel = _normalize_rel_path(contract_rel, "contract_rel", allow_dot=False)
    dest_dir = _resolve_inside_repo(repo_path, norm_rel, "contract_rel", allow_repo_root=False)
    if os.path.exists(dest_dir) and not os.path.isdir(dest_dir):
        raise ApplyError(f"target contract path exists and is not a directory: {norm_rel}")

    draft_entries = _collect_draft_files(draft_path)
    planned = [(f"{norm_rel}/{rel}", data, is_exec) for rel, data, is_exec in draft_entries]
    planned_paths = [item[0] for item in planned]

    is_git = _is_git_repo(repo_path)
    if (branch is not None or commit) and not is_git:
        raise ApplyError(f"target path is not a git repository: {repo_path}")

    clean_branch: str | None = None
    branch_exists = False
    if branch is not None:
        clean_branch = _validate_branch_name(repo_path, branch)
        branch_exists = (
            _git(repo_path, "show-ref", "--verify", "--quiet", f"refs/heads/{clean_branch}", check=False).returncode
            == 0
        )

    tracked: set[str] = set()
    status_paths: set[str] = set()
    target_branch_paths: set[str] = set()
    if is_git:
        tracked = _git_nul_paths(repo_path, "ls-files", "-z", "--", norm_rel)
        status_paths = _git_status_paths(repo_path, norm_rel)
        if clean_branch is not None and branch_exists and clean_branch != _current_branch(repo_path):
            target_branch_paths = _git_nul_paths(
                repo_path, "ls-tree", "-r", "-z", "--name-only", clean_branch, "--", norm_rel
            )

    conflicts: list[str] = []
    for repo_rel in planned_paths:
        dst_file = os.path.join(repo_path, *repo_rel.split("/"))
        if (
            os.path.lexists(dst_file)
            or repo_rel in tracked
            or repo_rel in status_paths
            or repo_rel in target_branch_paths
        ):
            conflicts.append(repo_rel)

    if conflicts and not force:
        raise ApplyError(
            f"refusing to overwrite existing/tracked contract files without force=True: {', '.join(conflicts)}"
        )

    if clean_branch is not None:
        cur = _current_branch(repo_path)
        if cur != clean_branch:
            if branch_exists:
                _git(repo_path, "checkout", "-q", clean_branch)
            else:
                _git(repo_path, "checkout", "-q", "-b", clean_branch)
            # Re-check disk presence after checkout if we switched branches.
            for repo_rel in planned_paths:
                dst_file = os.path.join(repo_path, *repo_rel.split("/"))
                if os.path.lexists(dst_file) and repo_rel not in conflicts:
                    conflicts.append(repo_rel)
            if conflicts and not force:
                raise ApplyError(
                    f"refusing to overwrite existing/tracked contract files without force=True: {', '.join(conflicts)}"
                )

    overwritten = sorted(set(conflicts))
    _place_files_atomically(repo_path, dest_dir, planned, set(overwritten))

    commit_sha = _commit_scoped_paths(repo_path, planned_paths, f"chore: apply contract files to {norm_rel}") if commit else None

    active_branch = _current_branch(repo_path) if is_git else None
    return {
        "applied_files": planned_paths,
        "branch": active_branch,
        "commit_sha": commit_sha,
        "overwritten": overwritten,
    }


def _is_eligible_source(rel_posix: str) -> bool:
    parts = rel_posix.split("/")
    if any(part.startswith(".") or part in _SKIP_DIRS for part in parts[:-1]):
        return False
    base = parts[-1]
    if not base or base.startswith(".") or base in _EXCLUDED_NAMES:
        return False
    if base.endswith(".d.ts") or base.endswith((".lock", ".lockfile")):
        return False
    _, ext = posixpath.splitext(base)
    if ext.lower() not in _SOURCE_EXTENSIONS:
        return False
    stem = base[: -len(ext)] if ext else base
    if _TEST_STEM_RE.match(stem) or _JVM_TEST_RE.match(base):
        return False
    return True


def _within_scope(rel_posix: str, norm_scope: str) -> bool:
    if norm_scope == ".":
        return True
    return rel_posix == norm_scope or rel_posix.startswith(norm_scope + "/")


def _count_lines(path: str) -> int:
    with open(path, "rb") as fh:
        data = fh.read()
    if not data:
        return 1
    return max(1, len(data.splitlines()))


def _recent_git_files(repo_path: str, norm_scope: str, max_files: int) -> list[str]:
    if not _is_git_repo(repo_path) or not _has_head(repo_path):
        return []
    scope_arg = "." if norm_scope == "." else norm_scope
    proc = subprocess.Popen(  # nosec B603 - explicit git argv list, shell=False
        ["git", "-C", repo_path, "-c", "core.quotePath=false", "log", "--name-only", "--pretty=format:", "--", scope_arg],
        env=_git_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    selected: list[str] = []
    seen: set[str] = set()
    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            rel = raw_line.strip()
            if not rel or rel in seen:
                continue
            seen.add(rel)
            if not _within_scope(rel, norm_scope) or not _is_eligible_source(rel):
                continue
            full = os.path.join(repo_path, *rel.split("/"))
            if os.path.isfile(full) and not os.path.islink(full):
                selected.append(rel)
                if len(selected) >= max_files:
                    break
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    return selected


def _lexical_source_files(repo_path: str, norm_scope: str) -> list[str]:
    root_dir = repo_path if norm_scope == "." else os.path.join(repo_path, *norm_scope.split("/"))
    if not os.path.isdir(root_dir):
        return []
    found: list[str] = []
    for cur_root, dirs, files in os.walk(root_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS)
        for fname in sorted(files):
            full = os.path.join(cur_root, fname)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, repo_path).replace(os.sep, "/")
            if _within_scope(rel, norm_scope) and _is_eligible_source(rel):
                found.append(rel)
    found.sort()
    return found


def select_incremental_scope(
    repo_dir: str | os.PathLike[str],
    scope: str,
    max_files: int = 10,
) -> IncrementalScope:
    """Deterministically select up to max_files recently touched source files inside scope.

    Emits MUTATION_LINES in `<file>=1-<line_count>` space-separated wire format
    (matching core/gates/mutation.py and stack runners) plus structured provenance.
    """
    if isinstance(max_files, bool) or not isinstance(max_files, int) or max_files < 1:
        raise ValueError(f"max_files must be an integer >= 1, got {max_files!r}")

    repo_path = os.path.abspath(os.fspath(repo_dir))
    if not os.path.isdir(repo_path):
        raise ValueError(f"repository directory does not exist: {repo_path}")

    norm_scope = _normalize_rel_path(scope, "scope", allow_dot=True)
    _resolve_inside_repo(repo_path, norm_scope, "scope", allow_repo_root=(norm_scope == "."))
    display_scope = "." if norm_scope == "." else f"{norm_scope}/"

    selected = _recent_git_files(repo_path, norm_scope, max_files)
    if len(selected) < max_files:
        seen = set(selected)
        for rel in _lexical_source_files(repo_path, norm_scope):
            if rel not in seen:
                selected.append(rel)
                seen.add(rel)
                if len(selected) >= max_files:
                    break

    if not selected:
        return IncrementalScope("", f"incremental: 0 eligible source files in {display_scope}")

    specs = []
    for rel in selected:
        full = os.path.join(repo_path, *rel.split("/"))
        specs.append(f"{rel}=1-{_count_lines(full)}")
    mutation_lines = " ".join(specs)
    provenance = f"incremental: top {len(selected)} recently changed files in {display_scope} ({', '.join(selected)})"
    return IncrementalScope(mutation_lines, provenance)


def commit_ticket_artifact(
    repo_dir: str | os.PathLike[str],
    artifact_path: str | os.PathLike[str],
    message: str,
) -> str | None:
    """Safely stage and commit only artifact_path inside repo_dir.

    Refuses absolute paths, traversal or symlink escapes, secret patterns, and
    empty commit messages; leaves unrelated dirty or pre-staged files untouched.
    """
    if not isinstance(message, str) or not message.strip():
        raise ValueError("commit message must not be empty")

    repo_path = os.path.abspath(os.fspath(repo_dir))
    if not os.path.isdir(repo_path) or not _is_git_repo(repo_path):
        raise ApplyError(f"target path is not a git repository: {repo_path}")

    rel_posix = _normalize_rel_path(artifact_path, "artifact_path", allow_dot=False)
    abs_artifact = _resolve_inside_repo(repo_path, rel_posix, "artifact_path", allow_repo_root=False)
    if os.path.islink(os.path.join(repo_path, *rel_posix.split("/"))):
        raise ValueError(f"artifact_path={rel_posix!r} must not be a symlink")
    if not os.path.isfile(abs_artifact):
        raise ApplyError(f"artifact file does not exist: {rel_posix}")

    with open(abs_artifact, "rb") as fh:
        _check_secret_content(rel_posix, fh.read())

    return _commit_scoped_paths(repo_path, [rel_posix], message.strip())
