"""Signed SLSA v1.0 / in-toto agent provenance attestations (docs/adr/0117): stdlib only.

Builds and verifies in-toto Statement/v1 JSON attestations carrying the
https://lights-out.dev/provenance/v1 predicate. Reads the optional tower ticket
document (via STATE_STORE and core/audit.py's adapter_json), the optional
release log (RELEASES_LOG + VERSION), and the optional git repository
(WORKLOAD or .) to bind SHA-256 digests of changed and cited decision records
under docs/adr/ or adr/. Never fabricates missing evidence and never emits
secrets.

    python3 core/provenance.py build [--attach] [--attach-required]
        write PROVENANCE_DIR/<subject-id>.intoto.json (default .crew/provenance),
        print `<path>\\t<missing_count>` on stdout and each `missing: <path>`
        on stderr, exit 0; or print one-line refusal reason on stdout, exit 1.

    python3 core/provenance.py verify --statement <path> [--bundle <bundle>] [--key <pub>] [--repo <dir>]
        verify Statement/v1 envelope, normalized subject digests, predicate
        schema, optional bundle / cosign signature, and ADR SHA-256 digests
        against --repo; print `OK: <path> verified` and exit 0, or reason and exit 1.

    python3 core/provenance.py attach --statement <path> [--bundle <bundle>] [--sha <commit>] [--out-receipt <path>]
        invoke adapters/<CODE_HOST>/attest.sh when present, parse its one-line
        JSON receipt, record it additively into the provenance receipt file,
        and print verification guidance on stderr.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess  # nosec B404 - git and cosign invoked with explicit argv lists only
import sys
from datetime import datetime, timezone

try:
    from .audit import Refused as AuditRefused, adapter_json
    from .evidence import Refused as EvidenceRefused, pick as pick_release, read_log, released_versions
except ImportError:  # run as a script: core/ is sys.path[0]
    from audit import Refused as AuditRefused, adapter_json
    from evidence import Refused as EvidenceRefused, pick as pick_release, read_log, released_versions

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://lights-out.dev/provenance/v1"
BUILDER_ID = "https://lights-out.dev/builder/tower/v1"

PR_GATES = ("risk", "secrets", "test-weakening", "deps", "sast", "test", "mutation", "test-proof")
REQUIRED_PREDICATE_KEYS = ("ticket", "intake", "adrs", "agents", "gates", "merge", "human_gate", "missing")
AGENT_KEYS = ("role", "harness", "provider", "model", "thinking", "turns", "input_sha256", "tokens", "usd")

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/~^-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_ADR_REL_RE = re.compile(r"^(?:docs/)?adr/[^/]+\.md$")
_ADR_CITE_NUM_RE = re.compile(r"\bADR[- ](\d{4})\b", re.IGNORECASE)
_ADR_CITE_PATH_RE = re.compile(r"\b((?:docs/)?adr/\d{4}-[A-Za-z0-9._-]+\.md)\b")
_GATE_LINE_RE = re.compile(r"^(GO|NO-GO):\s+([A-Za-z0-9._-]+)(?:\s+—\s+.*)?$")

_SECRET_KV_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET|PASSWORD))\s*=\s*"
    r'(?!"\[REDACTED\]"|\'\[REDACTED\]\'|\[REDACTED\])("[^"]*"|\'[^\']*\'|[^\s,;]+)',
    re.IGNORECASE,
)
_SECRET_AUTH_RE = re.compile(
    r"(Authorization:\s*(?:Bearer|Basic)\s+)(?!\[REDACTED\])\S+",
    re.IGNORECASE,
)
_SECRET_URL_RE = re.compile(
    r"([a-z][a-z0-9+.-]*://)(?!\[REDACTED\]@)([^/@\s]+:[^/@\s]+)@",
    re.IGNORECASE,
)
_SECRET_DICT_KEY_RE = re.compile(r"(?:_key|_token|_secret|password)$", re.IGNORECASE)


class Refused(Exception):
    pass


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_id(value, label):
    if not _SAFE_ID_RE.fullmatch(value) or value.startswith("."):
        raise Refused(f"{label} {value!r} is not a safe identifier ([A-Za-z0-9._-], no leading .)")


def _is_git_repo(repo):
    return bool(repo) and os.path.exists(os.path.join(repo, ".git"))


def _git_out(repo, *args):
    if not _is_git_repo(repo):
        return None
    proc = subprocess.run(
        ["git", "-C", repo, *args],  # nosec B603 B607
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _git_bytes(repo, *args):
    if not _is_git_repo(repo):
        return None
    proc = subprocess.run(
        ["git", "-C", repo, *args],  # nosec B603 B607
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def normalize_sha256(raw):
    if not isinstance(raw, str):
        return None
    val = raw.strip().lower()
    if val.startswith("sha256:"):
        val = val[len("sha256:"):]
    return val if _SHA256_RE.fullmatch(val) else None


def normalize_git_commit(raw, repo=None):
    if isinstance(raw, str) and raw.strip():
        val = raw.strip()
        lower = val.lower()
        for prefix in ("gitcommit:", "sha1:"):
            if lower.startswith(prefix):
                lower = lower[len(prefix):]
        if _GIT_COMMIT_RE.fullmatch(lower):
            return lower
        if repo and _SAFE_REF_RE.fullmatch(val) and not val.startswith("-"):
            resolved = _git_out(repo, "rev-parse", "--verify", "-q", f"{val}^{{commit}}")
            if resolved:
                cand = resolved.strip().lower()
                if _GIT_COMMIT_RE.fullmatch(cand):
                    return cand
    return None


def scrub_text(text):
    out = _SECRET_KV_RE.sub(r"\1=[REDACTED]", text)
    out = _SECRET_AUTH_RE.sub(r"\1[REDACTED]", out)
    out = _SECRET_URL_RE.sub(r"\1[REDACTED]@", out)
    return out


def scrub_secrets(node):
    if isinstance(node, str):
        return scrub_text(node)
    if isinstance(node, list):
        return [scrub_secrets(item) for item in node]
    if isinstance(node, dict):
        cleaned = {}
        for k, v in node.items():
            if isinstance(k, str) and _SECRET_DICT_KEY_RE.search(k):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = scrub_secrets(v)
        return cleaned
    return node


def _has_unredacted_secret(node):
    if isinstance(node, str):
        return bool(
            _SECRET_KV_RE.search(node)
            or _SECRET_AUTH_RE.search(node)
            or _SECRET_URL_RE.search(node)
        )
    if isinstance(node, list):
        return any(_has_unredacted_secret(x) for x in node)
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and _SECRET_DICT_KEY_RE.search(k) and v not in (None, "[REDACTED]"):
                return True
            if _has_unredacted_secret(v):
                return True
    return False


def _resolve_subject(env, doc, release_rec, repo):
    explicit_sha = env.get("SUBJECT_SHA256")
    if explicit_sha:
        norm = normalize_sha256(explicit_sha)
        if not norm:
            raise Refused(f"SUBJECT_SHA256 {explicit_sha!r} is not a 64-character hex SHA-256 digest")
        name = (env.get("SUBJECT_NAME") or env.get("IMAGE") or norm).strip()
        return [{"name": name, "digest": {"sha256": norm}}]

    image = (
        env.get("IMAGE")
        or ((release_rec or {}).get("image") if isinstance(release_rec, dict) else None)
        or (((doc or {}).get("release") or {}).get("image") if isinstance(doc, dict) else None)
    )
    if isinstance(image, str) and "@sha256:" in image:
        _, digest_part = image.rsplit("@sha256:", 1)
        norm = normalize_sha256(digest_part)
        if norm:
            return [{"name": image.strip(), "digest": {"sha256": norm}}]

    commit_candidates = []
    if isinstance(doc, dict):
        merge_obj = doc.get("merge") if isinstance(doc.get("merge"), dict) else {}
        ci_obj = doc.get("ci") if isinstance(doc.get("ci"), dict) else {}
        pr_obj = doc.get("pr") if isinstance(doc.get("pr"), dict) else {}
        for c in (merge_obj.get("sha"), ci_obj.get("head_sha"), pr_obj.get("head_sha")):
            if isinstance(c, str) and c.strip():
                commit_candidates.append(c.strip())

    for cand in commit_candidates:
        norm = normalize_git_commit(cand, repo=repo)
        if norm:
            return [{"name": norm, "digest": {"gitCommit": norm}}]

    if not commit_candidates:
        norm = normalize_git_commit("HEAD", repo=repo)
        if norm:
            return [{"name": norm, "digest": {"gitCommit": norm}}]

    raise Refused("cannot bind subject: no valid 64-hex image sha256 or 40/64-hex gitCommit digest")


def _safe_repo_file(repo_real, rel_path):
    if not isinstance(rel_path, str) or not _ADR_REL_RE.fullmatch(rel_path) or ".." in rel_path.split("/"):
        return None
    joined = os.path.realpath(os.path.join(repo_real, rel_path))
    if not joined.startswith(repo_real + os.sep):
        return None
    return joined


def _discover_adrs(repo, doc, ticket_id, tickets_dir, missing):
    if not repo or not os.path.isdir(repo):
        return []
    repo_real = os.path.realpath(repo)
    rel_paths = set()
    cited_nums = set()

    # 1. Changed ADR files listed in task patches on the ticket document
    if isinstance(doc, dict):
        runs = []
        if isinstance(doc.get("tasks"), list):
            runs.extend(doc["tasks"])
        elif isinstance(doc.get("task"), dict):
            runs.append(doc["task"])
        for run in runs:
            if not isinstance(run, dict):
                continue
            for fobj in run.get("files") or []:
                path = fobj.get("path") if isinstance(fobj, dict) else None
                if isinstance(path, str) and _ADR_REL_RE.fullmatch(path):
                    rel_paths.add(path)

        spec_obj = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
        title = spec_obj.get("title") or ""
        if isinstance(title, str):
            cited_nums.update(_ADR_CITE_NUM_RE.findall(title))
            rel_paths.update(_ADR_CITE_PATH_RE.findall(title))

    # 2. Ticket markdown text if available locally
    if ticket_id and tickets_dir:
        tfile = os.path.join(tickets_dir, f"{ticket_id}.md")
        if os.path.isfile(tfile):
            try:
                with open(tfile, encoding="utf-8") as fh:
                    ttext = fh.read()
                cited_nums.update(_ADR_CITE_NUM_RE.findall(ttext))
                rel_paths.update(_ADR_CITE_PATH_RE.findall(ttext))
            except OSError:
                pass

    # 3. Git diff & commit messages when repo is a git repository
    target_commit = None
    if _is_git_repo(repo_real):
        base_sha = None
        if isinstance(doc, dict):
            pr_obj = doc.get("pr") if isinstance(doc.get("pr"), dict) else {}
            merge_obj = doc.get("merge") if isinstance(doc.get("merge"), dict) else {}
            ci_obj = doc.get("ci") if isinstance(doc.get("ci"), dict) else {}
            base_sha = normalize_git_commit(pr_obj.get("base_sha"), repo=repo_real)
            target_commit = (
                normalize_git_commit(merge_obj.get("sha"), repo=repo_real)
                or normalize_git_commit(ci_obj.get("head_sha"), repo=repo_real)
                or normalize_git_commit(pr_obj.get("head_sha"), repo=repo_real)
            )
        if not target_commit:
            target_commit = normalize_git_commit("HEAD", repo=repo_real)

        if base_sha and target_commit:
            diff_out = _git_out(repo_real, "diff", "--no-renames", "--name-only", base_sha, target_commit, "--", "docs/adr", "adr")
            if diff_out:
                for line in diff_out.splitlines():
                    if _ADR_REL_RE.fullmatch(line.strip()):
                        rel_paths.add(line.strip())
            msg_out = _git_out(repo_real, "log", "--format=%B", f"{base_sha}..{target_commit}")
            if msg_out:
                cited_nums.update(_ADR_CITE_NUM_RE.findall(msg_out))
                rel_paths.update(_ADR_CITE_PATH_RE.findall(msg_out))
        elif target_commit:
            has_second_parent = _git_out(repo_real, "rev-parse", "--verify", "-q", f"{target_commit}^2")
            if has_second_parent:
                diff_out = _git_out(
                    repo_real, "diff", "--no-renames", "--name-only",
                    f"{target_commit}^1", target_commit, "--", "docs/adr", "adr",
                )
            else:
                diff_out = _git_out(
                    repo_real, "diff-tree", "--no-commit-id", "--name-only",
                    "-r", "--root", "--no-renames", target_commit, "--", "docs/adr", "adr",
                )
            if diff_out:
                for line in diff_out.splitlines():
                    if _ADR_REL_RE.fullmatch(line.strip()):
                        rel_paths.add(line.strip())
            msg_out = _git_out(repo_real, "log", "-n", "1", "--format=%B", target_commit)
            if msg_out:
                cited_nums.update(_ADR_CITE_NUM_RE.findall(msg_out))
                rel_paths.update(_ADR_CITE_PATH_RE.findall(msg_out))

    # Resolve cited ADR numbers against docs/adr/ and adr/
    for num in sorted(cited_nums):
        matched = False
        for subdir in ("docs/adr", "adr"):
            abs_sub = os.path.join(repo_real, subdir)
            if not os.path.isdir(abs_sub):
                continue
            try:
                names = sorted(os.listdir(abs_sub))
            except OSError:
                continue
            for name in names:
                if name.startswith(f"{num}-") and name.endswith(".md"):
                    rel_paths.add(f"{subdir}/{name}")
                    matched = True
        if not matched:
            missing.append(f"adrs:ADR-{num}")

    adrs = []
    for rel in sorted(rel_paths):
        full = _safe_repo_file(repo_real, rel)
        data = None
        if full and os.path.isfile(full):
            try:
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError:
                data = None
        elif target_commit and _ADR_REL_RE.fullmatch(rel):
            data = _git_bytes(repo_real, "show", f"{target_commit}:{rel}")

        if data is None:
            missing.append(f"adrs:{rel}")
        else:
            adrs.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest()})
    return adrs


def _compute_fallback_usd(run_obj, day):
    try:
        from . import spend
    except ImportError:
        try:
            import spend
        except ImportError:
            return None
    prices, _ = spend.load_prices()
    if prices is None:
        return None
    usd, _ = spend.cost(run_obj, prices, day)
    return round(float(usd), 6) if isinstance(usd, (int, float)) and not isinstance(usd, bool) else None


def _normalize_jury(raw):
    """Normalize optional M37 jury metadata without fabricating absent fields."""
    if not isinstance(raw, dict):
        return None
    if not any(k in raw for k in ("reviewer_provider", "cross_vendor", "mode")):
        return None
    cross = raw.get("cross_vendor")
    return {
        "mode": raw.get("mode") if isinstance(raw.get("mode"), str) else None,
        "cross_vendor": cross if isinstance(cross, bool) else None,
        "author_provider": raw.get("author_provider") if isinstance(raw.get("author_provider"), str) else None,
        "reviewer_provider": raw.get("reviewer_provider") if isinstance(raw.get("reviewer_provider"), str) else None,
        "reviewer_model": raw.get("reviewer_model") if isinstance(raw.get("reviewer_model"), str) else None,
        "reviewer_harness": raw.get("reviewer_harness") if isinstance(raw.get("reviewer_harness"), str) else None,
        "reason": raw.get("reason") if isinstance(raw.get("reason"), str) else None,
    }


def _discover_crew_jury(ticket, env):
    """Read optional jury metadata from .crew/<ticket>/(review/)out/run.json if present."""
    if not ticket:
        return None
    crew_dir = (env.get("CREW_DIR") or ".crew").strip() or ".crew"
    for parts in ((crew_dir, ticket, "review", "out", "run.json"), (crew_dir, ticket, "out", "run.json")):
        path = os.path.join(*parts)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(obj, dict):
            norm = _normalize_jury(obj.get("jury"))
            if norm is not None:
                return norm
    return None


def _build_agents(doc, missing, extra_jury=None):
    if not isinstance(doc, dict):
        return []
    candidates = []
    prod = doc.get("product")
    if isinstance(prod, dict) and prod.get("consulted") is not False and any(prod.get(k) for k in ("harness", "provider", "model")):
        candidates.append(("product", None, prod))
    arch = doc.get("architect")
    if isinstance(arch, dict) and arch.get("consulted") is not False and any(arch.get(k) for k in ("harness", "provider", "model")):
        candidates.append(("architect", None, arch))

    tasks = doc.get("tasks")
    if isinstance(tasks, list) and tasks:
        for item in tasks:
            if isinstance(item, dict):
                candidates.append(("task", item.get("attempt"), item))
    elif isinstance(doc.get("task"), dict):
        t = doc["task"]
        candidates.append(("task", t.get("attempt") or doc.get("attempt"), t))

    rev = doc.get("review")
    if isinstance(rev, dict) and any(rev.get(k) for k in ("harness", "provider", "model", "verdict")):
        candidates.append(("review", rev.get("attempt") or doc.get("attempt"), rev))

    ledger = doc.get("spend") if isinstance(doc.get("spend"), list) else []
    used_ledger = set()
    today = _now_iso()[:10]
    agents = []

    for idx, (kind, attempt_num, run_obj) in enumerate(candidates):
        default_role = "dev-worker" if kind == "task" else ("test-reviewer" if kind == "review" else kind)
        role = run_obj.get("role") or default_role
        usd = None
        for direct_key in ("usd", "cost_usd"):
            val = run_obj.get(direct_key)
            if isinstance(val, (int, float)) and not isinstance(val, bool) and val >= 0:
                usd = round(float(val), 6)
                break
        if usd is None:
            for s_idx, entry in enumerate(ledger):
                if s_idx in used_ledger or not isinstance(entry, dict):
                    continue
                if entry.get("role") != role:
                    continue
                if attempt_num is not None and entry.get("attempt") is not None and entry.get("attempt") != attempt_num:
                    continue
                used_ledger.add(s_idx)
                cval = entry.get("cost_usd")
                if isinstance(cval, (int, float)) and not isinstance(cval, bool) and cval >= 0:
                    usd = round(float(cval), 6)
                break
        if usd is None:
            usd = _compute_fallback_usd(run_obj, today)
        if usd is None:
            missing.append(f"agents[{idx}].usd")

        tokens = run_obj.get("tokens") if isinstance(run_obj.get("tokens"), dict) else None
        turns = run_obj.get("turns")
        if isinstance(turns, bool) or not isinstance(turns, int):
            turns = None
        in_sha = run_obj.get("input_sha256")
        in_sha = in_sha.strip().lower() if isinstance(in_sha, str) and in_sha.strip() else None

        entry = {
            "kind": kind,
            "role": role,
            "attempt": attempt_num if isinstance(attempt_num, int) and not isinstance(attempt_num, bool) else None,
            "harness": run_obj.get("harness") if isinstance(run_obj.get("harness"), str) else None,
            "provider": run_obj.get("provider") if isinstance(run_obj.get("provider"), str) else None,
            "model": run_obj.get("model") if isinstance(run_obj.get("model"), str) else None,
            "thinking": run_obj.get("thinking") if isinstance(run_obj.get("thinking"), str) else None,
            "turns": turns,
            "input_sha256": in_sha,
            "tokens": tokens,
            "usd": usd,
        }
        raw_jury = run_obj.get("jury")
        if raw_jury is None and kind == "review":
            raw_jury = doc.get("jury") if isinstance(doc.get("jury"), dict) else extra_jury
        norm_jury = _normalize_jury(raw_jury)
        if norm_jury is not None:
            entry["jury"] = norm_jury
        agents.append(entry)

    if not agents:
        missing.append("agents")
    return agents


def _build_gates(doc, missing):
    if not isinstance(doc, dict) or not isinstance(doc.get("ci"), dict):
        if isinstance(doc, dict):
            missing.append("gates")
        return None
    ci = doc["ci"]
    lines = ci.get("lines") if isinstance(ci.get("lines"), list) else None
    if lines is None:
        missing.append("gates.lines")
    raw_seconds = ci.get("seconds") if isinstance(ci.get("seconds"), dict) else None
    durations = None
    if raw_seconds is not None:
        durations = {}
        for k, v in raw_seconds.items():
            if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                durations[k] = int(v) if isinstance(v, int) else round(float(v), 3)
    else:
        missing.append("gates.durations_s")

    by_gate = {}
    extra_order = []
    for raw_line in lines or []:
        if not isinstance(raw_line, str):
            continue
        m = _GATE_LINE_RE.match(raw_line.strip())
        if not m:
            continue
        verdict_word, gate_name = m.group(1), m.group(2)
        by_gate[gate_name] = (verdict_word, raw_line)
        if gate_name not in PR_GATES and gate_name not in extra_order:
            extra_order.append(gate_name)

    checks = []
    for gate_name in list(PR_GATES) + extra_order:
        verdict_word, full_line = by_gate.get(gate_name, (None, None))
        dur = durations.get(gate_name) if isinstance(durations, dict) else None
        checks.append({
            "gate": gate_name,
            "verdict": verdict_word,
            "line": full_line,
            "duration_s": dur,
        })

    return {
        "verdict": ci.get("verdict"),
        "line": ci.get("line"),
        "head_sha": ci.get("head_sha"),
        "decision": ci.get("decision"),
        "failed": ci.get("failed"),
        "lines": lines,
        "durations_s": durations,
        "checks": checks,
    }


def _build_merge(doc, missing):
    if not isinstance(doc, dict):
        return None
    merge_obj = doc.get("merge") if isinstance(doc.get("merge"), dict) else {}
    pr_obj = doc.get("pr") if isinstance(doc.get("pr"), dict) else None
    ci_obj = doc.get("ci") if isinstance(doc.get("ci"), dict) else {}

    sha = merge_obj.get("sha")
    if not sha:
        missing.append("merge.sha")
    pr_out = None
    if isinstance(pr_obj, dict):
        pr_out = {
            "number": pr_obj.get("number"),
            "url": pr_obj.get("url"),
            "head_sha": pr_obj.get("head_sha"),
            "base_sha": pr_obj.get("base_sha"),
        }
    else:
        missing.append("merge.pr")

    risk_level = ci_obj.get("risk_level")
    if not risk_level:
        missing.append("merge.risk_level")

    return {
        "sha": sha,
        "head_sha": merge_obj.get("head_sha"),
        "pr": pr_out,
        "risk_level": risk_level,
    }


def _build_intake(doc, store, missing):
    if not isinstance(doc, dict) or doc.get("intake") is None:
        return None
    raw_intake = doc.get("intake")
    if isinstance(raw_intake, str) and raw_intake.strip():
        iid = raw_intake.strip()
        if store:
            try:
                idoc = adapter_json(
                    [f"adapters/{store}/get.sh", "tickets", iid],
                    f"no intake document {iid} in {store}",
                )
                meta = idoc.get("intake") if isinstance(idoc.get("intake"), dict) else {}
                conf = idoc.get("confirmation") if isinstance(idoc.get("confirmation"), dict) else {}
                return {
                    "id": iid,
                    "by": meta.get("by"),
                    "via": meta.get("via"),
                    "at": meta.get("at"),
                    "confirmed_by": conf.get("by"),
                    "confirmed_via": conf.get("via"),
                    "confirmed_at": conf.get("at"),
                }
            except AuditRefused:
                missing.append("intake.document")
        else:
            missing.append("intake.document")
        return {
            "id": iid,
            "by": None,
            "via": None,
            "at": None,
            "confirmed_by": None,
            "confirmed_via": None,
            "confirmed_at": None,
        }
    if isinstance(raw_intake, dict):
        conf = doc.get("confirmation") if isinstance(doc.get("confirmation"), dict) else {}
        return {
            "id": raw_intake.get("id") or (doc.get("id") if doc.get("kind") == "intake" else None),
            "by": raw_intake.get("by"),
            "via": raw_intake.get("via"),
            "at": raw_intake.get("at"),
            "confirmed_by": raw_intake.get("confirmed_by") or conf.get("by"),
            "confirmed_via": raw_intake.get("confirmed_via") or conf.get("via"),
            "confirmed_at": raw_intake.get("confirmed_at") or conf.get("at"),
        }
    return None


def _build_human_gate(answer_tuple, missing):
    if answer_tuple is None:
        missing.append("human_gate")
        return None
    _, ans = answer_tuple
    brief_sha = ans.get("brief_sha256")
    if not brief_sha:
        missing.append("human_gate.brief_sha256")
    return {
        "gate": "go-for-launch",
        "answer": ans.get("answer"),
        "by": ans.get("by"),
        "via": ans.get("via"),
        "at": ans.get("at"),
        "brief_sha256": brief_sha,
        "reason": ans.get("reason"),
    }


def statement(env):
    """Build (subject_id, statement_dict) from ENV or raise Refused."""
    version = (env.get("VERSION") or "").strip()
    ticket = (env.get("TICKET") or "").strip()
    store = (env.get("STATE_STORE") or "").strip()
    log_path = env.get("RELEASES_LOG") or ".crew/releases.jsonl"
    repo = env.get("WORKLOAD") or "."
    tickets_dir = env.get("TICKETS_DIR") or "tickets"

    if version:
        _validate_id(version, "VERSION")
    if ticket:
        _validate_id(ticket, "TICKET")

    release_line = None
    release_rec = None
    answer_tuple = None

    if version:
        try:
            lines = read_log(log_path)
        except EvidenceRefused as exc:
            raise Refused(str(exc)) from None
        picked = pick_release(lines, version)
        if picked is None:
            known = " ".join(released_versions(lines)) or "none"
            raise Refused(f"no release of {version} in {log_path}; released here: {known}")
        _, release_line, _, answer_tuple = picked
        release_rec = release_line.get("record") if isinstance(release_line.get("record"), dict) else {}
        rel_ticket = release_line.get("ticket")
        if ticket and rel_ticket and ticket != rel_ticket:
            raise Refused(f"TICKET={ticket} disagrees with release {version} ticket {rel_ticket}")
        if not ticket and isinstance(rel_ticket, str) and rel_ticket.strip():
            ticket = rel_ticket.strip()
            _validate_id(ticket, "release ticket")

    missing = []
    doc = None
    if ticket:
        if store:
            try:
                doc = adapter_json(
                    [f"adapters/{store}/get.sh", "tickets", ticket],
                    f"no document for ticket {ticket} in {store}",
                )
            except AuditRefused as exc:
                if not version:
                    raise Refused(str(exc)) from None
                missing.append("ticket.document")
        else:
            missing.append("ticket.document")
    elif version:
        missing.append("ticket")

    # When built from TICKET without explicit VERSION, inspect RELEASES_LOG if it exists
    if not version and isinstance(doc, dict) and os.path.isfile(log_path):
        doc_ver = (doc.get("release") or {}).get("version") if isinstance(doc.get("release"), dict) else None
        if isinstance(doc_ver, str) and doc_ver.strip():
            try:
                lines = read_log(log_path)
                picked = pick_release(lines, doc_ver.strip())
                if picked is not None:
                    _, release_line, _, answer_tuple = picked
                    release_rec = release_line.get("record") if isinstance(release_line.get("record"), dict) else {}
            except EvidenceRefused:
                pass

    subj = _resolve_subject(env, doc, release_rec, repo)
    subject_id = version or ticket or next(iter(subj[0]["digest"].values()))
    _validate_id(subject_id, "subject id")

    ticket_section = None
    if isinstance(doc, dict):
        spec_obj = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
        spec_sha = spec_obj.get("sha256")
        spec_title = spec_obj.get("title")
        if not spec_sha:
            missing.append("ticket.spec_sha256")
        if not spec_title:
            missing.append("ticket.spec_title")
        ticket_section = {
            "id": str(doc.get("id") or ticket),
            "state": doc.get("state"),
            "spec_sha256": spec_sha,
            "spec_title": spec_title,
        }
    elif ticket:
        ticket_section = {
            "id": ticket,
            "state": None,
            "spec_sha256": None,
            "spec_title": None,
        }

    crew_jury = _discover_crew_jury(ticket, env)
    intake_section = _build_intake(doc, store, missing)
    adrs_section = _discover_adrs(repo, doc, ticket, tickets_dir, missing)
    agents_section = _build_agents(doc, missing, extra_jury=crew_jury)
    gates_section = _build_gates(doc, missing)
    merge_section = _build_merge(doc, missing)
    human_section = _build_human_gate(answer_tuple, missing)

    rev_obj = doc.get("review") if isinstance(doc, dict) and isinstance(doc.get("review"), dict) else {}
    arch_obj = doc.get("architect") if isinstance(doc, dict) and isinstance(doc.get("architect"), dict) else {}
    review_jury = _normalize_jury(
        rev_obj.get("jury")
        or (doc.get("jury") if isinstance(doc, dict) else None)
        or arch_obj.get("jury")
        or crew_jury
    )

    dedup_missing = []
    for item in missing:
        if item not in dedup_missing:
            dedup_missing.append(item)

    predicate = {
        "builder": {"id": BUILDER_ID},
        "built_at": _now_iso(),
        "ticket": ticket_section,
        "intake": intake_section,
        "adrs": adrs_section,
        "agents": agents_section,
        "gates": gates_section,
        "merge": merge_section,
        "human_gate": human_section,
        "missing": dedup_missing,
    }
    if review_jury is not None:
        base_rev = rev_obj if rev_obj else arch_obj
        predicate["review"] = {
            "role": base_rev.get("role") or ("architect" if (arch_obj and not rev_obj) else "test-reviewer"),
            "verdict": base_rev.get("verdict"),
            "provider": base_rev.get("provider") or review_jury.get("reviewer_provider"),
            "model": base_rev.get("model") or review_jury.get("reviewer_model"),
            "harness": base_rev.get("harness") or review_jury.get("reviewer_harness"),
            "jury": review_jury,
        }

    stmt = {
        "_type": STATEMENT_TYPE,
        "subject": subj,
        "predicateType": PREDICATE_TYPE,
        "predicate": predicate,
    }
    return subject_id, scrub_secrets(stmt)


def build(env):
    subject_id, stmt = statement(env)
    directory = env.get("PROVENANCE_DIR") or ".crew/provenance"
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{subject_id}.intoto.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(stmt, ensure_ascii=False, indent=2) + "\n")
    return path, stmt


def verify_statement(statement_path, bundle_path=None, key_path=None, repo_dir=None):
    if not statement_path or not os.path.isfile(statement_path):
        raise Refused(f"statement not found: {statement_path}")
    try:
        with open(statement_path, encoding="utf-8") as fh:
            stmt = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refused(f"{statement_path} is not valid JSON: {exc}") from None

    if not isinstance(stmt, dict):
        raise Refused(f"{statement_path} root must be a JSON object")
    if stmt.get("_type") != STATEMENT_TYPE:
        raise Refused(f"unexpected _type {stmt.get('_type')!r}, want {STATEMENT_TYPE!r}")
    if stmt.get("predicateType") != PREDICATE_TYPE:
        raise Refused(f"unexpected predicateType {stmt.get('predicateType')!r}, want {PREDICATE_TYPE!r}")

    subj = stmt.get("subject")
    if not isinstance(subj, list) or not subj:
        raise Refused("subject must be a non-empty list")
    for idx, item in enumerate(subj):
        if not isinstance(item, dict):
            raise Refused(f"subject[{idx}] is not an object")
        name = item.get("name")
        digest = item.get("digest")
        if not isinstance(name, str) or not name.strip():
            raise Refused(f"subject[{idx}].name must be a non-empty string")
        if not isinstance(digest, dict) or not digest:
            raise Refused(f"subject[{idx}].digest must be a non-empty object")
        for alg, val in digest.items():
            if alg == "sha256":
                if not isinstance(val, str) or not _SHA256_RE.fullmatch(val):
                    raise Refused(f"subject[{idx}].digest.sha256 is not normalized 64-char lowercase hex")
            elif alg == "gitCommit":
                if not isinstance(val, str) or not _GIT_COMMIT_RE.fullmatch(val):
                    raise Refused(f"subject[{idx}].digest.gitCommit is not normalized 40/64-char lowercase hex")
            else:
                raise Refused(f"subject[{idx}].digest uses unsupported algorithm {alg!r}")

    pred = stmt.get("predicate")
    if not isinstance(pred, dict):
        raise Refused("predicate must be a JSON object")
    for req in REQUIRED_PREDICATE_KEYS:
        if req not in pred:
            raise Refused(f"predicate missing required key {req!r}")

    if not isinstance(pred["missing"], list) or not all(isinstance(x, str) for x in pred["missing"]):
        raise Refused("predicate.missing must be a list of strings")

    adrs = pred["adrs"]
    if not isinstance(adrs, list):
        raise Refused("predicate.adrs must be a list")
    for idx, entry in enumerate(adrs):
        if not isinstance(entry, dict):
            raise Refused(f"predicate.adrs[{idx}] is not an object")
        apath = entry.get("path")
        asha = entry.get("sha256")
        if not isinstance(apath, str) or not _ADR_REL_RE.fullmatch(apath) or ".." in apath.split("/"):
            raise Refused(f"predicate.adrs[{idx}].path {apath!r} is not a safe relative ADR path")
        if not isinstance(asha, str) or not _SHA256_RE.fullmatch(asha):
            raise Refused(f"predicate.adrs[{idx}].sha256 is not 64-char lowercase hex")

    agents = pred["agents"]
    if not isinstance(agents, list):
        raise Refused("predicate.agents must be a list")
    for idx, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise Refused(f"predicate.agents[{idx}] is not an object")
        for akey in AGENT_KEYS:
            if akey not in agent:
                raise Refused(f"predicate.agents[{idx}] missing key {akey!r}")
        usd = agent.get("usd")
        if usd is not None and (isinstance(usd, bool) or not isinstance(usd, (int, float)) or usd < 0):
            raise Refused(f"predicate.agents[{idx}].usd must be a non-negative number or null")
        if "jury" in agent and agent["jury"] is not None and not isinstance(agent["jury"], dict):
            raise Refused(f"predicate.agents[{idx}].jury must be an object or null")

    if "review" in pred and pred["review"] is not None:
        if not isinstance(pred["review"], dict):
            raise Refused("predicate.review must be an object or null")
        if "jury" in pred["review"] and pred["review"]["jury"] is not None and not isinstance(pred["review"]["jury"], dict):
            raise Refused("predicate.review.jury must be an object or null")

    if _has_unredacted_secret(stmt):
        raise Refused("statement contains unredacted secret material")

    if bundle_path:
        if not os.path.isfile(bundle_path) or os.path.getsize(bundle_path) == 0:
            raise Refused(f"bundle not found or empty: {bundle_path}")
        try:
            with open(bundle_path, encoding="utf-8") as bfh:
                bobj = json.load(bfh)
        except (OSError, ValueError) as exc:
            raise Refused(f"bundle {bundle_path} is not valid JSON: {exc}") from None
        if not isinstance(bobj, dict) or not any(k in bobj for k in ("messageSignature", "dsseEnvelope", "mediaType")):
            raise Refused(f"bundle {bundle_path} is not a Sigstore bundle object")
        if key_path:
            if not os.path.isfile(key_path):
                raise Refused(f"public key not found: {key_path}")
            proc = subprocess.run(
                [
                    "cosign", "verify-blob",
                    "--key", key_path,
                    "--insecure-ignore-tlog",
                    "--bundle", bundle_path,
                    statement_path,
                ],  # nosec B603 B607
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise Refused(f"cosign verify-blob failed against {key_path}")

    if repo_dir:
        if not os.path.isdir(repo_dir):
            raise Refused(f"repository directory not found: {repo_dir}")
        repo_real = os.path.realpath(repo_dir)
        for entry in adrs:
            full = _safe_repo_file(repo_real, entry["path"])
            if not full or not os.path.isfile(full):
                raise Refused(f"ADR file missing in {repo_dir}: {entry['path']}")
            try:
                with open(full, "rb") as afh:
                    actual = hashlib.sha256(afh.read()).hexdigest()
            except OSError as exc:
                raise Refused(f"cannot read ADR file {entry['path']}: {exc}") from None
            if actual != entry["sha256"]:
                raise Refused(
                    f"ADR digest mismatch for {entry['path']}: statement has {entry['sha256']}, repo has {actual}"
                )

    return stmt


_SAFE_SHA_ARG_RE = re.compile(r"^[A-Za-z0-9:._-]+$")


def _safe_code_host(raw):
    val = (raw or "").strip()
    if not val:
        return None
    parts = val.split("/")
    if len(parts) > 2 or any(not p or p.startswith(".") or not _SAFE_ID_RE.fullmatch(p) for p in parts):
        raise Refused(f"CODE_HOST {val!r} is not a safe adapter name")
    return val


def _resolve_attach_sha(stmt, explicit_sha=None, repo=None):
    if explicit_sha is not None:
        raw = explicit_sha.strip()
        if not raw:
            raise Refused("empty --sha value")
        if not _SAFE_SHA_ARG_RE.fullmatch(raw) or raw.startswith("-") or raw.startswith("."):
            raise Refused(f"unsafe --sha value {explicit_sha!r}")
        norm_git = normalize_git_commit(raw, repo=repo)
        if norm_git:
            return norm_git
        norm_s256 = normalize_sha256(raw)
        if norm_s256:
            return f"sha256:{norm_s256}" if raw.lower().startswith("sha256:") else norm_s256
        return raw

    pred = stmt.get("predicate") if isinstance(stmt.get("predicate"), dict) else {}
    merge_obj = pred.get("merge") if isinstance(pred.get("merge"), dict) else {}
    gates_obj = pred.get("gates") if isinstance(pred.get("gates"), dict) else {}
    for cand in (merge_obj.get("sha"), merge_obj.get("head_sha"), gates_obj.get("head_sha")):
        norm = normalize_git_commit(cand, repo=repo)
        if norm:
            return norm

    subj = stmt.get("subject") if isinstance(stmt.get("subject"), list) else []
    for item in subj:
        if isinstance(item, dict) and isinstance(item.get("digest"), dict):
            norm = normalize_git_commit(item["digest"].get("gitCommit"), repo=repo)
            if norm:
                return norm
    for item in subj:
        if isinstance(item, dict) and isinstance(item.get("digest"), dict):
            norm = normalize_sha256(item["digest"].get("sha256"))
            if norm:
                return norm

    norm = normalize_git_commit("HEAD", repo=repo)
    if norm:
        return norm
    raise Refused("cannot resolve subject sha for codehost attestation; pass --sha <commit>")


def _write_receipt_additive(receipt_path, entry, explicit_out=False):
    existing = {}
    if os.path.isfile(receipt_path):
        try:
            with open(receipt_path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, ValueError) as exc:
            if explicit_out:
                raise Refused(f"cannot read existing receipt {receipt_path}: {exc}") from None

    out = dict(existing)
    attachments = list(existing.get("attachments")) if isinstance(existing.get("attachments"), list) else []
    attachments.append(entry)
    out.update({
        "statement": entry["statement"],
        "bundle": entry["bundle"],
        "sha": entry["sha"],
        "code_host": entry["code_host"],
        "status": entry["status"],
        "attached_at": entry["attached_at"],
        "receipt": entry["receipt"],
        "attachments": attachments,
    })
    parent = os.path.dirname(os.path.abspath(receipt_path))
    try:
        os.makedirs(parent, exist_ok=True)
        with open(receipt_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(scrub_secrets(out), ensure_ascii=False, indent=2) + "\n")
        return receipt_path
    except OSError as exc:
        if explicit_out:
            raise Refused(f"cannot write receipt {receipt_path}: {exc}") from None
        return None


def attach_statement(
    statement_path,
    bundle_path=None,
    sha=None,
    out_receipt=None,
    repo_dir=None,
    env=None,
    required=False,
):
    """Attach a built provenance statement/bundle to the active CODE_HOST adapter."""
    base_env = dict(os.environ if env is None else env)
    repo = repo_dir or base_env.get("WORKLOAD") or "."
    stmt = verify_statement(statement_path, bundle_path=bundle_path)
    resolved_sha = _resolve_attach_sha(stmt, explicit_sha=sha, repo=repo)
    code_host = _safe_code_host(base_env.get("CODE_HOST"))
    target = bundle_path or statement_path
    receipt_path = out_receipt or f"{statement_path}.attestation-receipt.json"
    script = os.path.join("adapters", code_host, "attest.sh") if code_host else None

    if not script or not os.path.isfile(script):
        reason = (
            "CODE_HOST is not set"
            if not code_host
            else f"codehost adapter {code_host!r} has no attest.sh ({script})"
        )
        if required:
            raise Refused(reason)
        receipt_obj = {"stored": "skipped", "id": None, "sha": resolved_sha, "note": reason}
        entry = {
            "statement": statement_path,
            "bundle": bundle_path,
            "sha": resolved_sha,
            "code_host": code_host,
            "target": target,
            "status": "skipped",
            "attached_at": _now_iso(),
            "receipt": receipt_obj,
        }
        written = _write_receipt_additive(receipt_path, entry, explicit_out=bool(out_receipt))
        print(f"provenance: attach skipped: {reason}", file=sys.stderr)
        if written:
            print(f"provenance: receipt written: {written}", file=sys.stderr)
        return {
            "status": "skipped",
            "reason": reason,
            "sha": resolved_sha,
            "code_host": code_host,
            "receipt": receipt_obj,
            "receipt_path": written,
        }

    proc = subprocess.run(  # nosec B603 B607 - an argv list, no shell; sh runs the CodeHost adapter's attest.sh (ADR 0134)
        ["sh", script, resolved_sha, target],  # nosec B603
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        err = scrub_text((proc.stderr or proc.stdout or "").strip())
        detail = err.splitlines()[-1] if err else f"exit {proc.returncode}"
        raise Refused(f"codehost attest failed ({code_host}): {detail}")

    for line in scrub_text(proc.stderr or "").splitlines():
        if line.strip():
            print(line, file=sys.stderr)

    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if len(lines) != 1:
        raise Refused(f"codehost attest ({code_host}) returned {len(lines)} stdout lines, want 1 JSON receipt line")
    try:
        raw_receipt = json.loads(lines[0])
    except ValueError as exc:
        raise Refused(f"codehost attest ({code_host}) returned invalid JSON receipt: {exc}") from None
    if not isinstance(raw_receipt, dict):
        raise Refused(f"codehost attest ({code_host}) receipt must be a JSON object")
    receipt_obj = scrub_secrets(raw_receipt)
    if _has_unredacted_secret(receipt_obj):
        raise Refused("codehost attest receipt contains unredacted secret material")

    entry = {
        "statement": statement_path,
        "bundle": bundle_path,
        "sha": resolved_sha,
        "code_host": code_host,
        "target": target,
        "status": "attached",
        "attached_at": _now_iso(),
        "receipt": receipt_obj,
    }
    written = _write_receipt_additive(receipt_path, entry, explicit_out=bool(out_receipt))
    if receipt_obj.get("verify_cmd"):
        print(f"provenance: verify remote attestation: {receipt_obj['verify_cmd']}", file=sys.stderr)
    if receipt_obj.get("url"):
        print(f"provenance: attestation url: {receipt_obj['url']}", file=sys.stderr)
    if receipt_obj.get("note"):
        print(f"provenance: {receipt_obj['note']}", file=sys.stderr)
    if bundle_path:
        print(
            f"provenance: verify local bundle: python3 core/provenance.py verify --statement {statement_path} --bundle {bundle_path}",
            file=sys.stderr,
        )
    else:
        print(
            f"provenance: verify statement: python3 core/provenance.py verify --statement {statement_path}",
            file=sys.stderr,
        )
    if written:
        print(f"provenance: receipt written: {written}", file=sys.stderr)
    return {
        "status": "attached",
        "sha": resolved_sha,
        "code_host": code_host,
        "receipt": receipt_obj,
        "receipt_path": written,
    }


def main(argv, env=None):
    base_env = dict(os.environ if env is None else env)
    if not argv or argv[0] not in ("build", "verify", "attach"):
        print(
            "usage: provenance.py build [--attach] | verify --statement <path> [--bundle <bundle>] [--key <pub>] [--repo <dir>] | attach --statement <path> [--bundle <bundle>] [--sha <commit>] [--out-receipt <path>]",
            file=sys.stderr,
        )
        return 2

    cmd = argv[0]
    if cmd == "build":
        parser = argparse.ArgumentParser(prog="provenance.py build", add_help=False)
        parser.add_argument("--version", dest="version")
        parser.add_argument("--ticket", dest="ticket")
        parser.add_argument("--state-store", dest="state_store")
        parser.add_argument("--releases-log", dest="releases_log")
        parser.add_argument("--repo", dest="repo")
        parser.add_argument("--out-dir", dest="out_dir")
        parser.add_argument("--attach", action="store_true")
        parser.add_argument("--attach-required", dest="attach_required", action="store_true")
        parser.add_argument("--bundle", default=None)
        parser.add_argument("--sha", default=None)
        parser.add_argument("--out-receipt", dest="out_receipt", default=None)
        try:
            ns, extra = parser.parse_known_args(argv[1:])
        except SystemExit:
            return 2
        if extra:
            print(f"provenance.py build: unrecognized arguments: {' '.join(extra)}", file=sys.stderr)
            return 2
        if ns.version is not None:
            base_env["VERSION"] = ns.version
        if ns.ticket is not None:
            base_env["TICKET"] = ns.ticket
        if ns.state_store is not None:
            base_env["STATE_STORE"] = ns.state_store
        if ns.releases_log is not None:
            base_env["RELEASES_LOG"] = ns.releases_log
        if ns.repo is not None:
            base_env["WORKLOAD"] = ns.repo
        if ns.out_dir is not None:
            base_env["PROVENANCE_DIR"] = ns.out_dir
        try:
            path, stmt = build(base_env)
        except Refused as refused:
            print(refused)
            return 1
        gaps = stmt["predicate"]["missing"]
        for gap in gaps:
            print(f"missing: {gap}", file=sys.stderr)
        defer_attach = bool((base_env.get("PROVENANCE_DEFER_ATTACH") or "").strip())
        want_attach = ns.attach or ns.attach_required
        if want_attach and not defer_attach:
            req = bool(ns.attach_required or (base_env.get("PROVENANCE_ATTACH_REQUIRED") or "").strip())
            try:
                attach_statement(
                    path,
                    bundle_path=ns.bundle or None,
                    sha=ns.sha or None,
                    out_receipt=ns.out_receipt or None,
                    repo_dir=base_env.get("WORKLOAD") or None,
                    env=base_env,
                    required=req,
                )
            except Refused as refused:
                print(refused)
                return 1
        print(f"{path}\t{len(gaps)}")
        return 0

    if cmd == "attach":
        parser = argparse.ArgumentParser(prog="provenance.py attach", add_help=False)
        parser.add_argument("--statement", required=True)
        parser.add_argument("--bundle", default=None)
        parser.add_argument("--sha", default=None)
        parser.add_argument("--out-receipt", dest="out_receipt", default=None)
        parser.add_argument("--repo", default=None)
        parser.add_argument("--required", action="store_true")
        parser.add_argument("--attach-required", dest="attach_required", action="store_true")
        try:
            ns, extra = parser.parse_known_args(argv[1:])
        except SystemExit:
            print(
                "usage: provenance.py attach --statement <path> [--bundle <bundle>] [--sha <commit>] [--out-receipt <path>]",
                file=sys.stderr,
            )
            return 2
        if extra:
            print(f"provenance.py attach: unrecognized arguments: {' '.join(extra)}", file=sys.stderr)
            return 2
        req = bool(ns.required or ns.attach_required or (base_env.get("PROVENANCE_ATTACH_REQUIRED") or "").strip())
        try:
            res = attach_statement(
                ns.statement,
                bundle_path=ns.bundle or None,
                sha=ns.sha or None,
                out_receipt=ns.out_receipt or None,
                repo_dir=ns.repo or None,
                env=base_env,
                required=req,
            )
        except Refused as refused:
            print(refused)
            return 1
        if res["status"] == "skipped":
            print(f"OK: {ns.statement} attach skipped ({res['reason']})")
        else:
            stored = res["receipt"].get("stored", "unknown")
            print(f"OK: {ns.statement} attached ({res['code_host']}, stored={stored})")
        return 0

    parser = argparse.ArgumentParser(prog="provenance.py verify", add_help=False)
    parser.add_argument("--statement", required=True)
    parser.add_argument("--bundle", default=None)
    parser.add_argument("--key", default=None)
    parser.add_argument("--repo", default=None)
    try:
        ns, extra = parser.parse_known_args(argv[1:])
    except SystemExit:
        print(
            "usage: provenance.py verify --statement <path> [--bundle <bundle>] [--key <pub>] [--repo <dir>]",
            file=sys.stderr,
        )
        return 2
    if extra:
        print(f"provenance.py verify: unrecognized arguments: {' '.join(extra)}", file=sys.stderr)
        return 2
    try:
        verify_statement(
            ns.statement,
            bundle_path=ns.bundle or None,
            key_path=ns.key or base_env.get("COSIGN_PUB") or None,
            repo_dir=ns.repo or None,
        )
    except Refused as refused:
        print(refused)
        return 1
    print(f"OK: {ns.statement} verified")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
