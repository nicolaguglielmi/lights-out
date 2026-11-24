"""Multi-ticket epics, depends_on DAG scheduling, and multi-service monorepos (docs/adr/0119): stdlib only.

    python3 core/epics.py create --id <epic> --title <title> --tickets <id,...> [--tickets-dir <dir>] [--state-dir <dir>] [--json]
    python3 core/epics.py status <epic> [--tickets-dir <dir>] [--state-dir <dir>] [--json]
    python3 core/epics.py unblock [<ticket>] [--tickets-dir <dir>] [--state-dir <dir>] [--json]
    python3 core/epics.py workspace [--repo <dir>] [--out <dir>] [--stacks <dir>] [--today <date>] [--baseline-mode full|incremental] [--changed <path> ...] [--json]

Strictly vendor-free, app-free, and model-free (ci/vendor-free.sh,
ci/app-free.sh, ci/model-free.sh). Epic documents are stored in STATE_STORE
collection "epics", separate from "tickets".
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import posixpath
import re
import subprocess  # nosec B404 - git invoked with explicit argv list only (docs/adr/0052 §3)
import sys
import tempfile
from datetime import datetime, timezone

try:
    from . import assign, init, stacks
    from . import spec as spec_mod
    from .tower import compact, dig, sh, text
except ImportError:  # run as a script: core/ is sys.path[0]
    import assign
    import init
    import spec as spec_mod
    import stacks
    from tower import compact, dig, sh, text

EPICS_COLLECTION = "epics"
TICKETS_COLLECTION = "tickets"
WORKSPACE_SCHEMA = "lights-out.workspace/1"

_TICKET_ID_RE = re.compile(r"^\d{4}$")
_EPIC_ID_RE = re.compile(r"^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$")

_WORKSPACE_SKIP_DIRS = frozenset({
    "node_modules", "target", "build", "dist", "vendor", "__pycache__",
    "docs", "tickets", "fixtures", "stacks", "ci", "roles", "profiles",
})
_ROOT_MANIFESTS = frozenset({
    "package.json", "pnpm-workspace.yaml", "pnpm-lock.yaml", "package-lock.json",
    "yarn.lock", "bun.lock", "Cargo.toml", "Cargo.lock", "pom.xml",
    "settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts",
    "gradle.lockfile", "buildscript-gradle.lockfile", "pyproject.toml", "uv.lock",
    "poetry.lock", "requirements.txt", "go.work", "go.mod", "go.sum",
})
_SOURCE_EXTS = frozenset({
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts",
    ".go", ".rs", ".java", ".kt", ".sql",
})


class Refused(ValueError):
    """Raised when an epic, DAG, or workspace operation is invalid or fails."""


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_epic_id(epic_id):
    if not isinstance(epic_id, str) or not _EPIC_ID_RE.match(epic_id.strip()):
        raise Refused(f"invalid epic id {epic_id!r}: must be alphanumeric with optional '-' or '_'")
    return epic_id.strip()


def _validate_ticket_id(tid):
    if not isinstance(tid, str) or not _TICKET_ID_RE.match(tid.strip()):
        raise Refused(f"invalid ticket id {tid!r}: must be a 4-digit ID")
    return tid.strip()


def _atomic_write(path, body):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".epics.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _store_paths(state_store_dir, collection, doc_id):
    return (
        os.path.join(state_store_dir, f"{collection}.{doc_id}.json"),
        os.path.join(state_store_dir, collection, f"{doc_id}.json"),
    )


def _sub_env(cfg):
    merged = dict(os.environ)
    if cfg:
        merged.update(cfg)
    return merged


def _store_get(collection, doc_id, state_store_dir=None, env=None):
    if state_store_dir is not None:
        for path in _store_paths(state_store_dir, collection, doc_id):
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as fh:
                        doc = json.load(fh)
                except (OSError, ValueError) as exc:
                    raise Refused(f"cannot read {path}: {exc}") from None
                if not isinstance(doc, dict):
                    raise Refused(f"{path} is not a JSON object")
                return doc
        return None

    cfg = os.environ if env is None else env
    store = (cfg.get("STATE_STORE") or "").strip()
    if not store:
        raise Refused("STATE_STORE not set; pass state_store_dir or run via ./go")
    got = sh(
        f"adapters/{store}/get.sh",
        collection,
        doc_id,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_sub_env(cfg),
    )
    if got.returncode == 1:
        return None
    if got.returncode != 0:
        detail = (got.stderr or "").strip() or f"exit {got.returncode}"
        raise Refused(f"STATE_STORE get {collection}/{doc_id} failed: {detail}")
    try:
        doc = json.loads(got.stdout)
    except ValueError:
        raise Refused(f"{collection}/{doc_id} is not JSON") from None
    if not isinstance(doc, dict):
        raise Refused(f"{collection}/{doc_id} is not a JSON object")
    return doc


def _store_put(collection, doc_id, doc, state_store_dir=None, env=None):
    if not isinstance(doc, dict):
        raise Refused(f"{collection}/{doc_id} document must be a JSON object")
    payload = compact(doc) + "\n"
    if state_store_dir is not None:
        flat_path, nested_path = _store_paths(state_store_dir, collection, doc_id)
        nested_dir = os.path.join(state_store_dir, collection)
        target = nested_path if os.path.isdir(nested_dir) and not os.path.exists(flat_path) else flat_path
        _atomic_write(target, payload)
        return

    cfg = os.environ if env is None else env
    store = (cfg.get("STATE_STORE") or "").strip()
    if not store:
        raise Refused("STATE_STORE not set; pass state_store_dir or run via ./go")
    put = sh(
        f"adapters/{store}/put.sh",
        collection,
        doc_id,
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_sub_env(cfg),
    )
    if put.returncode != 0:
        detail = (put.stderr or "").strip() or f"exit {put.returncode}"
        raise Refused(f"STATE_STORE put {collection}/{doc_id} failed: {detail}")


def _list_collection_ids(collection, tickets_dir=None, state_store_dir=None):
    ids = set()
    if state_store_dir is not None and os.path.isdir(state_store_dir):
        prefix = f"{collection}."
        for entry in sorted(os.listdir(state_store_dir)):
            if entry.startswith(prefix) and entry.endswith(".json"):
                ident = entry[len(prefix):-len(".json")]
                if ident:
                    ids.add(ident)
        nested = os.path.join(state_store_dir, collection)
        if os.path.isdir(nested):
            for entry in sorted(os.listdir(nested)):
                if entry.endswith(".json") and not entry.startswith("."):
                    ids.add(entry[:-len(".json")])
    if collection == TICKETS_COLLECTION and tickets_dir and os.path.isdir(tickets_dir):
        for entry in sorted(os.listdir(tickets_dir)):
            if entry.endswith(".md") and _TICKET_ID_RE.match(entry[:-3]):
                ids.add(entry[:-3])
    return sorted(ids)


def _is_red_main(state_store_dir=None, env=None):
    doc = _store_get("mainline", "main", state_store_dir=state_store_dir, env=env)
    return isinstance(doc, dict) and doc.get("state") == "red"


def _publish_assign(ticket_id, env):
    bus = (env.get("EVENT_BUS") or "").strip()
    if not bus:
        return False
    msg = compact({"ticket": ticket_id, "step": "assign"})
    ran = sh(
        f"adapters/{bus}/publish.sh",
        msg,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_sub_env(env),
    )
    return ran.returncode == 0


def _landed_on_green_main(doc):
    """True when the ticket is at `main-green` or passed `mainline` green before `release`."""
    if not isinstance(doc, dict):
        return False
    st = text(doc.get("state"))
    if st == "main-green":
        return True
    if st in ("held", "rolled-back"):
        if dig(doc, "mainline", "verdict") == "GO":
            return True
        return any(
            isinstance(h, dict) and h.get("step") == "mainline" and h.get("state") == "main-green"
            for h in (doc.get("history") or [])
        )
    return False


# --- DAG scheduling & perimeter-disjoint waves ------------------------------


def compute_waves(dag):
    """Validate DAG (refusing unknown dependencies and cycles with path) and return topological waves."""
    if not isinstance(dag, dict):
        raise Refused("dag must be a mapping of ticket_id -> dependency IDs")
    norm = {}
    for raw_tid, raw_deps in dag.items():
        tid = str(raw_tid)
        if not isinstance(raw_deps, (list, tuple, set)):
            raise Refused(f"dependencies for {tid} must be a list")
        deps = []
        seen = set()
        for raw_dep in raw_deps:
            dep = str(raw_dep)
            if dep == tid:
                raise Refused(f"dependency cycle detected: {tid} -> {tid}")
            if dep not in seen:
                seen.add(dep)
                deps.append(dep)
        norm[tid] = deps

    for tid in sorted(norm):
        for dep in norm[tid]:
            if dep not in norm:
                raise Refused(f"unknown dependency {dep} referenced by ticket {tid}")

    # Deterministic 3-color DFS to name the exact cycle path `A -> B -> A`.
    white, gray, black = 0, 1, 2
    color = {tid: white for tid in norm}
    stack = []

    def dfs(node):
        color[node] = gray
        stack.append(node)
        for dep in sorted(norm[node]):
            if color[dep] == gray:
                idx = stack.index(dep)
                cycle = " -> ".join(stack[idx:] + [dep])
                raise Refused(f"dependency cycle detected: {cycle}")
            if color[dep] == white:
                dfs(dep)
        stack.pop()
        color[node] = black

    for tid in sorted(norm):
        if color[tid] == white:
            dfs(tid)

    if not norm:
        return []

    depth = {}

    def wave_of(node):
        if node in depth:
            return depth[node]
        deps = norm[node]
        d = 0 if not deps else 1 + max(wave_of(dep) for dep in deps)
        depth[node] = d
        return d

    for tid in norm:
        wave_of(tid)

    max_depth = max(depth.values(), default=-1)
    return [sorted(tid for tid, d in depth.items() if d == level) for level in range(max_depth + 1)]


def _perimeters_overlap(globs_a, globs_b):
    for a in globs_a or ():
        for b in globs_b or ():
            if assign.overlaps(a, b):
                return True
    return False


def partition_disjoint_waves(waves, perimeters):
    """Split each topological wave into perimeter-disjoint execution sub-waves."""
    sub_waves = []
    for wave in waves:
        batches = []
        for tid in wave:
            globs = list(perimeters.get(tid) or [])
            placed = False
            for batch in batches:
                if not any(_perimeters_overlap(globs, perimeters.get(other) or []) for other in batch):
                    batch.append(tid)
                    placed = True
                    break
            if not placed:
                batches.append([tid])
        sub_waves.extend(batches)
    return sub_waves


# --- Epic persistence & progress -------------------------------------------


def _compute_progress(epic_doc, ticket_docs):
    tickets = list(epic_doc.get("tickets") or [])
    dag = epic_doc.get("dag") or {}
    states = {}
    blocked = []
    ready = []
    completed = []
    in_progress = 0
    failed = 0

    for tid in tickets:
        doc = ticket_docs.get(tid)
        st = text(doc.get("state")) if isinstance(doc, dict) else None
        states[tid] = st or None
        if _landed_on_green_main(doc):
            completed.append(tid)
            continue
        deps = [d for d in (dag.get(tid) or (doc.get("depends_on") if isinstance(doc, dict) else None) or []) if d]
        blocking = [d for d in deps if not _landed_on_green_main(ticket_docs.get(d))]
        if st in ("ci-failed", "needs-human", "rolled-back"):
            failed += 1
        elif blocking or dig(doc, "wait", "kind") == "depends_on":
            blocked.append({"ticket": tid, "blocking": blocking})
        elif st in ("requested", "questions", "spec-ready", "pr-opened", "review", "ci-passed", "merged",
                    "intake", "intake-questions", "drafted", "proposed", "confirmed"):
            in_progress += 1
        elif st == "opened":
            ready.append(tid)

    total = len(tickets)
    if total > 0 and len(completed) == total:
        epic_state = "completed"
    elif completed or in_progress > 0 or failed > 0:
        epic_state = "in-progress"
    else:
        epic_state = "open"

    progress = {
        "total": total,
        "completed": len(completed),
        "in_progress": in_progress,
        "blocked": len(blocked),
        "ready": len(ready),
        "failed": failed,
        "states": states,
    }
    return epic_state, progress, blocked, ready, completed


def create_epic(epic_id, title, tickets_dir, ticket_ids, state_store_dir=None, env=None):
    """Build and validate an epic DAG from ticket specs and store it in STATE_STORE collection `epics`."""
    eid = _validate_epic_id(epic_id)
    if not isinstance(title, str) or not title.strip():
        raise Refused("an epic needs a non-empty title")
    if not isinstance(ticket_ids, (list, tuple)) or not ticket_ids:
        raise Refused("an epic needs at least one ticket id")

    ordered_ids = []
    seen = set()
    for raw_id in ticket_ids:
        tid = _validate_ticket_id(str(raw_id))
        if tid not in seen:
            seen.add(tid)
            ordered_ids.append(tid)

    dag = {}
    perimeters = {}
    features = {}
    for tid in ordered_ids:
        path = os.path.join(tickets_dir, f"{tid}.md")
        if not os.path.isfile(path):
            raise Refused(f"ticket {tid} not found in {tickets_dir}")
        with open(path, encoding="utf-8") as fh:
            md_text = fh.read()
        try:
            deps, declared_epic = spec_mod.dependencies(md_text, own_id=tid)
        except ValueError as exc:
            raise Refused(f"ticket {tid}: {exc}") from None
        if declared_epic is not None and declared_epic != eid:
            raise Refused(f"ticket {tid} declares - Epic: {declared_epic}, expected {eid}")
        hdr = spec_mod.fields(md_text)
        dag[tid] = deps
        perimeters[tid] = assign.perimeter(md_text)
        features[tid] = hdr.get("feature")

    waves = compute_waves(dag)
    sub_waves = partition_disjoint_waves(waves, perimeters)

    cfg = os.environ if env is None else env
    ticket_docs = {
        tid: _store_get(TICKETS_COLLECTION, tid, state_store_dir=state_store_dir, env=cfg)
        for tid in ordered_ids
    }
    ts = _now()
    epic_doc = {
        "id": eid,
        "kind": "epic",
        "title": title.strip(),
        "state": "open",
        "tickets": ordered_ids,
        "dag": dag,
        "waves": waves,
        "sub_waves": sub_waves,
        "perimeters": perimeters,
        "features": features,
        "created_at": ts,
        "updated_at": ts,
    }
    epic_state, progress, _, _, _ = _compute_progress(epic_doc, ticket_docs)
    epic_doc["state"] = epic_state
    epic_doc["progress"] = progress
    _store_put(EPICS_COLLECTION, eid, epic_doc, state_store_dir=state_store_dir, env=cfg)
    return epic_doc


def epic_status(epic_id, state_store_dir=None, env=None, tickets_dir=None):
    """Read an epic from STATE_STORE and return live DAG progress and blocking details."""
    eid = _validate_epic_id(epic_id)
    cfg = os.environ if env is None else env
    epic_doc = _store_get(EPICS_COLLECTION, eid, state_store_dir=state_store_dir, env=cfg)
    if epic_doc is None:
        raise Refused(f"epic {eid} not found in STATE_STORE")

    ticket_docs = {}
    for tid in epic_doc.get("tickets") or []:
        ticket_docs[tid] = _store_get(TICKETS_COLLECTION, tid, state_store_dir=state_store_dir, env=cfg)

    epic_state, progress, blocked, ready, completed = _compute_progress(epic_doc, ticket_docs)
    out = dict(epic_doc)
    out["state"] = epic_state
    out["progress"] = progress
    out["blocked"] = blocked
    out["ready"] = ready
    out["completed"] = completed
    return out


def _discover_ticket_and_epic_ids(completed_ticket_id, tdir, state_store_dir, cfg):
    ticket_ids = set(_list_collection_ids(TICKETS_COLLECTION, tickets_dir=tdir, state_store_dir=state_store_dir))
    if completed_ticket_id:
        ticket_ids.add(completed_ticket_id)
    epic_ids = set(_list_collection_ids(EPICS_COLLECTION, state_store_dir=state_store_dir))

    if tdir and os.path.isdir(tdir):
        for entry in sorted(os.listdir(tdir)):
            if not (entry.endswith(".md") and _TICKET_ID_RE.match(entry[:-3])):
                continue
            try:
                with open(os.path.join(tdir, entry), encoding="utf-8") as fh:
                    _, epic_val = spec_mod.dependencies(fh.read(), own_id=entry[:-3])
                if epic_val:
                    epic_ids.add(epic_val)
            except (OSError, ValueError):
                pass

    docs_cache = {}
    for tid in sorted(ticket_ids):
        doc = _store_get(TICKETS_COLLECTION, tid, state_store_dir=state_store_dir, env=cfg)
        docs_cache[tid] = doc
        if isinstance(doc, dict) and isinstance(doc.get("epic"), str) and doc["epic"].strip():
            epic_ids.add(doc["epic"].strip())

    epics_cache = {}
    for eid in sorted(epic_ids):
        edoc = _store_get(EPICS_COLLECTION, eid, state_store_dir=state_store_dir, env=cfg)
        if isinstance(edoc, dict):
            epics_cache[eid] = edoc
            for tid in edoc.get("tickets") or []:
                if tid not in docs_cache:
                    docs_cache[tid] = _store_get(TICKETS_COLLECTION, tid, state_store_dir=state_store_dir, env=cfg)

    return docs_cache, epics_cache


def _refresh_epics(epics_cache, docs_cache, state_store_dir, cfg):
    updated_epics = []
    for eid in sorted(epics_cache):
        edoc = dict(epics_cache[eid])
        for tid in edoc.get("tickets") or []:
            if tid not in docs_cache:
                docs_cache[tid] = _store_get(TICKETS_COLLECTION, tid, state_store_dir=state_store_dir, env=cfg)
        new_state, progress, _, _, _ = _compute_progress(edoc, docs_cache)
        if edoc.get("state") != new_state or edoc.get("progress") != progress:
            edoc["state"] = new_state
            edoc["progress"] = progress
            edoc["updated_at"] = _now()
            _store_put(EPICS_COLLECTION, eid, edoc, state_store_dir=state_store_dir, env=cfg)
            updated_epics.append({"id": eid, "state": new_state, "progress": progress})
    return updated_epics


def _unblock_pass(completed_ticket_id, state_store_dir=None, env=None, tickets_dir=None, publish=True):
    cfg = dict(os.environ if env is None else env)
    tdir = tickets_dir or cfg.get("TICKETS_DIR") or "tickets"
    docs_cache, epics_cache = _discover_ticket_and_epic_ids(completed_ticket_id, tdir, state_store_dir, cfg)
    red_main = _is_red_main(state_store_dir=state_store_dir, env=cfg)

    unblocked = []
    published = []
    still_blocked = {}
    skipped_red_main = []

    for tid in sorted(docs_cache):
        doc = docs_cache[tid]
        if not isinstance(doc, dict) or text(doc.get("state")) != "opened":
            continue
        deps = [d for d in (doc.get("depends_on") or []) if isinstance(d, str) and d]
        if not deps:
            continue
        waiting_on_deps = dig(doc, "wait", "kind") == "depends_on"
        blocking = []
        for dep_id in deps:
            if dep_id not in docs_cache:
                docs_cache[dep_id] = _store_get(TICKETS_COLLECTION, dep_id, state_store_dir=state_store_dir, env=cfg)
            if not _landed_on_green_main(docs_cache[dep_id]):
                blocking.append(dep_id)

        if blocking:
            still_blocked[tid] = blocking
            if waiting_on_deps and dig(doc, "wait", "blocking") != blocking:
                updated = dict(doc, wait={"kind": "depends_on", "blocking": blocking})
                _store_put(TICKETS_COLLECTION, tid, updated, state_store_dir=state_store_dir, env=cfg)
                docs_cache[tid] = updated
            continue

        if not waiting_on_deps:
            continue

        if red_main:
            skipped_red_main.append(tid)
            if dig(doc, "wait", "blocking") != []:
                updated = dict(doc, wait={"kind": "depends_on", "blocking": []})
                _store_put(TICKETS_COLLECTION, tid, updated, state_store_dir=state_store_dir, env=cfg)
                docs_cache[tid] = updated
            print(f"[epics] {tid}: dependencies satisfied, but main is red (BLOCKED_BY_RED_MAIN); not publishing assign",
                  file=sys.stderr, flush=True)
            continue

        cleared = {k: v for k, v in doc.items() if k != "wait"}
        if publish and (cfg.get("EVENT_BUS") or "").strip():
            if not _publish_assign(tid, cfg):
                print(f"[epics] {tid}: could not publish assign on EVENT_BUS", file=sys.stderr, flush=True)
                continue
            published.append(tid)
        _store_put(TICKETS_COLLECTION, tid, cleared, state_store_dir=state_store_dir, env=cfg)
        docs_cache[tid] = cleared
        unblocked.append(tid)
        print(f"[epics] {tid}: all depends_on landed on main-green; assign unblocked", file=sys.stderr, flush=True)

    epics_updated = _refresh_epics(epics_cache, docs_cache, state_store_dir, cfg)
    return {
        "completed_ticket": completed_ticket_id,
        "unblocked": unblocked,
        "published": published,
        "still_blocked": still_blocked,
        "skipped_red_main": skipped_red_main,
        "epics_updated": epics_updated,
    }


def on_main_green(completed_ticket_id, state_store_dir=None, env=None, tickets_dir=None, publish=True):
    """Handle a ticket landing at `main-green`: update blockers, publish `assign`, and update epic progress."""
    tid = _validate_ticket_id(str(completed_ticket_id))
    return _unblock_pass(tid, state_store_dir=state_store_dir, env=env, tickets_dir=tickets_dir, publish=publish)


def unblock(completed_ticket_id=None, state_store_dir=None, env=None, tickets_dir=None, publish=True):
    """Unblock waiting downstream tickets whose dependencies are now at `main-green`."""
    tid = _validate_ticket_id(str(completed_ticket_id)) if completed_ticket_id else None
    return _unblock_pass(tid, state_store_dir=state_store_dir, env=env, tickets_dir=tickets_dir, publish=publish)


# --- Multi-service monorepo coordinator ------------------------------------


def _git_short_commit(repo_dir):
    proc = subprocess.run(  # nosec B603 B607 - explicit git argv list, shell=False
        ["git", "-C", repo_dir, "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "HEAD"


def _read_workload_env_keys(path, keys):
    out = {k: "" for k in keys}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k in out and not out[k]:
                out[k] = v
    return out


def _discover_shared_paths(repo_dir, service_dirs):
    svc_set = {s.strip("/") for s in service_dirs if s and s != "."}
    if not svc_set:
        return []
    ancestors = set()
    for svc in svc_set:
        parts = svc.split("/")
        for idx in range(1, len(parts)):
            ancestors.add("/".join(parts[:idx]))

    shared = set()
    for entry in sorted(os.listdir(repo_dir)):
        if entry.startswith(".") or entry in _WORKSPACE_SKIP_DIRS:
            continue
        full = os.path.join(repo_dir, entry)
        if os.path.isfile(full):
            _, ext = posixpath.splitext(entry)
            if entry in _ROOT_MANIFESTS or ext.lower() in _SOURCE_EXTS:
                shared.add(entry)

    for cur_root, dirs, files in os.walk(repo_dir):
        rel = os.path.relpath(cur_root, repo_dir).replace(os.sep, "/")
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _WORKSPACE_SKIP_DIRS)
        if rel == ".":
            continue
        if rel in svc_set or any(rel.startswith(s + "/") for s in svc_set):
            dirs[:] = []
            continue
        if rel in ancestors:
            for fname in sorted(files):
                if fname.startswith("."):
                    continue
                _, ext = posixpath.splitext(fname)
                if fname in _ROOT_MANIFESTS or ext.lower() in _SOURCE_EXTS:
                    shared.add(f"{rel}/{fname}")
            continue
        # Disjoint directory outside all service roots: mark as shared if it holds non-doc files.
        has_content = False
        for _, subdirs, subfiles in os.walk(cur_root):
            subdirs[:] = [d for d in subdirs if not d.startswith(".") and d not in _WORKSPACE_SKIP_DIRS]
            if any(not f.startswith(".") and not f.endswith((".md", ".txt", ".rst")) for f in subfiles):
                has_content = True
                break
        if has_content:
            shared.add(f"{rel}/")
        dirs[:] = []
    return sorted(shared)


def _resolve_stacks_dir(stacks_dir):
    if os.path.isdir(stacks_dir):
        return stacks_dir
    if stacks_dir == "stacks":
        repo_stacks = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stacks")
        if os.path.isdir(repo_stacks):
            return repo_stacks
    raise Refused(f"stacks directory not found: {stacks_dir}")


def build_workspace_manifest(repo_dir, out_dir=None, stacks_dir="stacks", today=None, baseline_mode="incremental"):
    """Discover monorepo services, draft each service contract, and write `<out_dir>/workspace.json`."""
    repo_abs = os.path.abspath(os.fspath(repo_dir))
    if not os.path.isdir(repo_abs):
        raise Refused(f"repository directory does not exist: {repo_abs}")

    target_out = os.path.abspath(os.fspath(out_dir)) if out_dir is not None else os.path.join(repo_abs, ".crew", "workspace")
    resolved_stacks = _resolve_stacks_dir(stacks_dir)
    today_str = today or datetime.now(timezone.utc).date().isoformat()
    commit = _git_short_commit(repo_abs)

    kind, services, is_virtual = stacks.discover_workspace_services(repo_abs)
    targets = list(services) if services else ["."]

    service_entries = []
    for svc in targets:
        if svc == ".":
            svc_out = os.path.join(target_out, "services", "root")
            draft_service = None
        else:
            svc_out = os.path.join(target_out, "services", *svc.split("/"))
            draft_service = svc
        try:
            stack = init.write_draft(
                repo_abs,
                svc_out,
                resolved_stacks,
                today_str,
                commit,
                service=draft_service,
                baseline_mode=baseline_mode,
            )
        except init.Undraftable as exc:
            raise Refused(f"cannot draft workspace service {svc!r}: {exc}") from None

        cfg = _read_workload_env_keys(
            os.path.join(svc_out, "workload.env"),
            ("WORKLOAD_LOCKFILES", "WORKLOAD_MUTATION_SCOPE", "WORKLOAD_TEST_ROOT"),
        )
        lockfiles = [x for x in cfg["WORKLOAD_LOCKFILES"].split(":") if x]
        service_entries.append({
            "id": svc,
            "path": svc,
            "stack": stack,
            "contract_dir": os.path.relpath(svc_out, target_out).replace(os.sep, "/"),
            "test_root": cfg["WORKLOAD_TEST_ROOT"],
            "mutation_scope": cfg["WORKLOAD_MUTATION_SCOPE"],
            "lockfiles": lockfiles,
            "depends_on": [],
        })

    shared_paths = _discover_shared_paths(repo_abs, services)
    manifest = {
        "schema": WORKSPACE_SCHEMA,
        "kind": kind or "single-service",
        "virtual_root": bool(is_virtual),
        "repo": repo_abs,
        "commit": commit,
        "today": today_str,
        "baseline_mode": baseline_mode,
        "services": service_entries,
        "shared_paths": shared_paths,
    }
    _atomic_write(os.path.join(target_out, "workspace.json"), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def _path_matches(path, pattern):
    pat = str(pattern).strip().replace("\\", "/").lstrip("./")
    if not pat:
        return False
    if pat.endswith("/"):
        return path == pat.rstrip("/") or path.startswith(pat)
    if any(ch in pat for ch in "*?["):
        return fnmatch.fnmatchcase(path, pat)
    return path == pat or path.startswith(pat + "/")


def affected_services(manifest, changed_paths):
    """Map changed repo-relative paths (including shared code) to affected service IDs."""
    if isinstance(manifest, (str, os.PathLike)):
        with open(os.fspath(manifest), encoding="utf-8") as fh:
            manifest = json.load(fh)
    if not isinstance(manifest, dict):
        raise Refused("workspace manifest must be a JSON object")

    entries = []
    for raw in manifest.get("services") or []:
        if isinstance(raw, str):
            entries.append({"id": raw, "path": raw, "lockfiles": [], "depends_on": []})
        elif isinstance(raw, dict):
            sid = str(raw.get("id") or raw.get("path") or "")
            spath = str(raw.get("path") or sid)
            entries.append({
                "id": sid,
                "path": spath,
                "lockfiles": list(raw.get("lockfiles") or []),
                "depends_on": list(raw.get("depends_on") or []),
            })
    all_ids = [e["id"] for e in entries if e["id"]]
    affected = set()

    for raw_path in changed_paths or ():
        norm = str(raw_path).strip().replace("\\", "/").lstrip("./")
        if not norm or norm == ".." or norm.startswith("../"):
            continue
        for entry in entries:
            sid = entry["id"]
            spath = entry["path"].rstrip("/")
            if spath == "." or norm == spath or norm.startswith(spath + "/"):
                affected.add(sid)
            elif norm in entry["lockfiles"]:
                affected.add(sid)
        for sp in manifest.get("shared_paths") or []:
            if isinstance(sp, dict):
                pat = sp.get("path") or ""
                targets = sp.get("services") or all_ids
                if _path_matches(norm, pat):
                    affected.update(str(t) for t in targets)
            elif isinstance(sp, str) and _path_matches(norm, sp):
                affected.update(all_ids)

    changed = True
    while changed:
        changed = False
        for entry in entries:
            sid = entry["id"]
            if sid not in affected and any(dep in affected for dep in entry["depends_on"]):
                affected.add(sid)
                changed = True

    return [sid for sid in all_ids if sid in affected]


# --- CLI --------------------------------------------------------------------


def _split_ticket_args(raw_values):
    out = []
    for val in raw_values or []:
        for part in str(val).split(","):
            item = part.strip()
            if item:
                out.append(item)
    return out


def _build_parser():
    parser = argparse.ArgumentParser(prog="core/epics.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Create and validate an epic DAG in STATE_STORE")
    p_create.add_argument("--id", required=True, dest="epic_id", help="Epic ID or slug")
    p_create.add_argument("--title", required=True, help="Epic title")
    p_create.add_argument("--tickets", required=True, nargs="+", help="Ticket IDs (comma or space separated)")
    p_create.add_argument("--tickets-dir", default=None, help="Directory holding ticket Markdown files")
    p_create.add_argument("--state-dir", default=None, help="Optional file-backed StateStore directory")
    p_create.add_argument("--json", action="store_true", help="Emit JSON on stdout")

    p_status = sub.add_parser("status", help="Inspect epic DAG status and progress")
    p_status.add_argument("epic_pos", nargs="?", default=None, help="Epic ID or slug")
    p_status.add_argument("--id", dest="epic_id", default=None, help="Epic ID or slug")
    p_status.add_argument("--tickets-dir", default=None, help="Directory holding ticket Markdown files")
    p_status.add_argument("--state-dir", default=None, help="Optional file-backed StateStore directory")
    p_status.add_argument("--json", action="store_true", help="Emit JSON on stdout")

    p_unblock = sub.add_parser("unblock", help="Unblock waiting downstream tickets whose dependencies are main-green")
    p_unblock.add_argument("ticket_pos", nargs="?", default=None, help="Optional completed ticket ID")
    p_unblock.add_argument("--ticket", dest="ticket_id", default=None, help="Optional completed ticket ID")
    p_unblock.add_argument("--tickets-dir", default=None, help="Directory holding ticket Markdown files")
    p_unblock.add_argument("--state-dir", default=None, help="Optional file-backed StateStore directory")
    p_unblock.add_argument("--no-publish", action="store_true", help="Update wait state without publishing assign")
    p_unblock.add_argument("--json", action="store_true", help="Emit JSON on stdout")

    p_ws = sub.add_parser("workspace", help="Coordinate multi-service monorepo contracts and affected services")
    p_ws.add_argument("--repo", default=".", help="Monorepo root directory")
    p_ws.add_argument("--out", default=None, help="Output directory for workspace.json and services/")
    p_ws.add_argument("--manifest", default=None, help="Existing workspace.json path (skip drafting)")
    p_ws.add_argument("--stacks", default="stacks", help="Stacks directory")
    p_ws.add_argument("--today", default=None, help="Date stamp (YYYY-MM-DD)")
    p_ws.add_argument("--baseline-mode", choices=("full", "incremental"), default="incremental")
    p_ws.add_argument("--changed", nargs="*", default=None, help="Optional changed paths to compute affected services")
    p_ws.add_argument("--json", action="store_true", help="Emit JSON on stdout")

    return parser


def main(argv=None, env=None):
    cfg = dict(os.environ if env is None else env)
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        if args.cmd == "create":
            tdir = args.tickets_dir or cfg.get("TICKETS_DIR") or "tickets"
            result = create_epic(
                args.epic_id,
                args.title,
                tdir,
                _split_ticket_args(args.tickets),
                state_store_dir=args.state_dir,
                env=cfg,
            )
        elif args.cmd == "status":
            eid = args.epic_id or args.epic_pos
            if not eid:
                print("epics: status requires an epic id", file=sys.stderr)
                return 2
            tdir = args.tickets_dir or cfg.get("TICKETS_DIR") or "tickets"
            result = epic_status(eid, state_store_dir=args.state_dir, env=cfg, tickets_dir=tdir)
        elif args.cmd == "unblock":
            tid = args.ticket_id or args.ticket_pos
            tdir = args.tickets_dir or cfg.get("TICKETS_DIR") or "tickets"
            result = unblock(
                tid,
                state_store_dir=args.state_dir,
                env=cfg,
                tickets_dir=tdir,
                publish=not args.no_publish,
            )
        else:  # workspace
            if args.manifest:
                with open(args.manifest, encoding="utf-8") as fh:
                    manifest = json.load(fh)
            else:
                manifest = build_workspace_manifest(
                    args.repo,
                    out_dir=args.out,
                    stacks_dir=args.stacks,
                    today=args.today,
                    baseline_mode=args.baseline_mode,
                )
            result = dict(manifest)
            if args.changed is not None:
                result["affected_services"] = affected_services(manifest, args.changed)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (Refused, OSError, ValueError) as exc:
        print(f"epics: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
