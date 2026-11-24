"""The evidence pack per release (docs/adr/0081): stdlib only.

Reads the release log and the ticket's document in the tower, never
writes them, and builds one JSON file in which every leaf is an entry:
{"source": S, "value": V} for evidence found where S says, or
{"source": S, "missing": W} for evidence looked for at S and not found,
W saying why. Nothing absent is filled in. core/evidence.sh signs the
file with cosign and verifies it; this file only builds it.

    VERSION=<v> STATE_STORE=<adapter> python3 core/evidence.py build
        write EVIDENCE_DIR/<v>.json (default .crew/evidence) from
        RELEASES_LOG (default .crew/releases.jsonl); print
        `<path>\\t<number missing>` on stdout and each missing path on
        stderr, exit 0; or print the one-line reason and exit 1
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

try:
    from .audit import Refused as AuditRefused, adapter_json
except ImportError:  # run as a script: core/ is sys.path[0]
    from audit import Refused as AuditRefused, adapter_json

SCHEMA = "lights-out.evidence/1"
VERSION_RE = re.compile(r"[A-Za-z0-9._-]+")
STAGE_FIELDS = ("pct", "verdict", "numbers", "started_at", "judged_at")
ANSWER_FIELDS = ("answer", "by", "at", "via", "reason", "brief_sha256", "brief_author")


class Refused(Exception):
    pass


def found(source, value):
    return {"source": source, "value": value}


def missing(source, why):
    return {"source": source, "missing": why}


def is_entry(node):
    return isinstance(node, dict) and "source" in node and ("value" in node or "missing" in node)


def entry(obj, key, source, what):
    """obj[key] as an entry: missing when the key is absent or null."""
    if isinstance(obj, dict) and obj.get(key) is not None:
        return found(source, obj[key])
    return missing(source, f"{what} carries no {key}")


def read_log(path):
    if not os.path.isfile(path):
        raise Refused(f"no release log at {path}: no release has reached a verdict here")
    lines = []
    with open(path, encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            try:
                lines.append((n, json.loads(raw)))
            except ValueError:
                raise Refused(f"{path} line {n} is not JSON") from None
    return lines


def released_versions(lines):
    seen = []
    for _, line in lines:
        version = (line.get("record") or {}).get("version") if line.get("kind") == "release" else None
        if version and version not in seen:
            seen.append(version)
    return seen


def pick(lines, version):
    """(release line number, line, earlier release line numbers, answer (n, line) or None)."""
    releases = [i for i, (_, line) in enumerate(lines)
                if line.get("kind") == "release" and isinstance(line.get("record"), dict)
                and line["record"].get("version") == version]
    if not releases:
        return None
    last = releases[-1]
    answer = None
    for n, line in lines[last + 1:]:
        if line.get("kind") == "release":
            break
        if line.get("kind") == "answer" and line.get("version") == version:
            answer = (n, line)
            break
    n, line = lines[last]
    return n, line, [lines[i][0] for i in releases[:-1]], answer


def release_section(log, n, line):
    rec = line["record"]
    at = f"{log}:{n}"
    section = {key: entry(rec, key, f"{at} .record.{key}", "the release record")
               for key in ("version", "stable", "verdict", "held_at")}
    if rec.get("held_at") is None:
        section["held_at"] = missing(f"{at} .record.held_at",
                                     f"not held for the human gate (verdict {rec.get('verdict')}, record.held_at is null)")
    section["at"] = entry(line, "at", f"{at} .at", "the release log line")
    section["ticket"] = (found(f"{at} .ticket", line["ticket"]) if line.get("ticket") else
                         missing(f"{at} .ticket", "the release log line names no ticket: "
                                 "a release run directly, not by the tower"))
    return section


def stages_section(log, n, rec):
    out = []
    for i, stage in enumerate(rec.get("stages") or []):
        at = f"{log}:{n} .record.stages[{i}]"
        what = "the record of this stage"
        fields = {key: entry(stage, key, f"{at}.{key}", what) for key in STAGE_FIELDS}
        for key, value in (stage.items() if isinstance(stage, dict) else ()):
            if key not in fields:
                fields[key] = found(f"{at}.{key}", value)
        out.append(fields)
    return out


def provenance_entry(env, version, ticket):
    """Signed SLSA/in-toto provenance bundle when present in PROVENANCE_DIR (docs/adr/0117 §5)."""
    prov_dir = (env or {}).get("PROVENANCE_DIR") or ".crew/provenance"
    for subject_id in (version, ticket):
        if not isinstance(subject_id, str) or not subject_id or not VERSION_RE.fullmatch(subject_id) or subject_id.startswith("."):
            continue
        att = os.path.join(prov_dir, f"{subject_id}.intoto.json")
        bundle = f"{att}.bundle"
        if not (os.path.isfile(att) and os.path.getsize(att) > 0 and os.path.isfile(bundle) and os.path.getsize(bundle) > 0):
            continue
        try:
            with open(att, "rb") as afh:
                att_bytes = afh.read()
            with open(bundle, "rb") as bfh:
                bundle_bytes = bfh.read()
            stmt = json.loads(att_bytes.decode("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(stmt, dict) or stmt.get("_type") != "https://in-toto.io/Statement/v1":
            continue
        return found(
            f"{att} (+ {bundle})",
            {
                "statement": att,
                "sha256": hashlib.sha256(att_bytes).hexdigest(),
                "bundle": bundle,
                "bundle_sha256": hashlib.sha256(bundle_bytes).hexdigest(),
                "predicate_type": stmt.get("predicateType"),
                "subject": stmt.get("subject"),
            },
        )
    return None


def artefacts_section(log, n, rec, env=None, ticket=None):
    at = f"{log}:{n} .record"
    image = entry(rec, "image", f"{at}.image", "the release record")
    if image.get("value") == "":
        image = missing(f"{at}.image", "the release record names no image")
    sbom = (found(f"{at}.sbom", rec["sbom"]) if rec.get("sbom") else
            missing(f"{at}.sbom", "the registry port gave no SBOM attestation for this image (record.sbom is null)"))
    dast = (found(f"{at}.dast", rec["dast"]) if rec.get("dast") else
            missing(f"{at}.dast", "DAST was off for this release (record.dast is null)"))
    out = {"image": image, "sbom": sbom, "dast": dast}
    prov = provenance_entry(env, rec.get("version"), ticket)
    if prov is not None:
        out["provenance"] = prov
    return out


def answer_section(log, n, rec, answer):
    if answer is None:
        source = f"{log} after line {n}: no kind answer line for {rec.get('version')} before the next release"
        if rec.get("verdict") == "rolled-back":
            return missing(source, "rolled back by the launchpad: no human answer was asked for")
        if rec.get("verdict") == "held":
            return missing(source, "held for the human gate: the release still waits for an answer")
        return missing(source, f"no human answer recorded (verdict {rec.get('verdict')})")
    m, line = answer
    return {key: entry(line, key, f"{log}:{m} .{key}", "the answer line") for key in ANSWER_FIELDS}


def doc_entry(doc, path, source_of, what="the document"):
    """A dotted path of the ticket's document as an entry."""
    value = doc
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    if value is None:
        return missing(source_of(path), f"{what} carries no .{path}")
    return found(source_of(path), value)


def pr_of(doc, store, ticket, log, n):
    source_of = lambda path: f"state store {store}: tickets/{ticket} .{path}"  # noqa: E731
    e = lambda path: doc_entry(doc, path, source_of)  # noqa: E731
    pr = {
        "ticket": found(f"{log}:{n} .ticket", ticket),
        "pr": {k: e(f"pr.{k}") for k in ("number", "url", "head_sha", "base_sha")},
        "merge": {"sha": e("merge.sha")},
        "gates": {k: e(f"ci.{k}") for k in ("verdict", "line", "lines", "details", "risk_level", "decision", "head_sha")},
        "rework": found(source_of("rework"), doc.get("rework") or []),
        "spec": {k: e(f"spec.{k}") for k in ("sha256", "title")},
    }
    decision = (doc.get("ci") or {}).get("decision")
    if decision == "review":
        pr["review"] = {k: e(f"review.{k}") for k in ("role", "verdict", "reason")}
    else:
        pr["review"] = missing(source_of("ci.decision"),
                               f"the gates decided {decision or 'nothing'}; no review was asked for")
    return pr


def prs_section(env, log, n, line, version):
    ticket = line.get("ticket")
    if not ticket:
        return missing(f"{log}:{n} .ticket", "the release log line names no ticket: a release run directly "
                       "(./go release), so no PR is tied to it")
    store = env.get("STATE_STORE")
    source = f"state store {store}: tickets/{ticket}"
    if not store:
        return missing(source, "STATE_STORE not set; run this via ./go evidence")
    try:
        doc = adapter_json([f"adapters/{store}/get.sh", "tickets", ticket],
                           f"no document for ticket {ticket} in {store}")
    except AuditRefused as refused:
        return missing(source, str(refused))
    released = (doc.get("release") or {}).get("version")
    if released != version:
        return missing(f"{source} .release.version",
                       f"the document of ticket {ticket} now records release {released or 'none'}, "
                       f"not {version}: a later run overwrote it")
    return [pr_of(doc, store, ticket, log, n)]


def missing_paths(node, path=""):
    if is_entry(node):
        return [path] if "missing" in node else []
    out = []
    if isinstance(node, dict):
        for key, value in node.items():
            out += missing_paths(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out += missing_paths(value, f"{path}[{i}]")
    return out


def pack(env):
    version = env.get("VERSION") or ""
    log = env.get("RELEASES_LOG") or ".crew/releases.jsonl"
    if not version or not VERSION_RE.fullmatch(version) or version.startswith("."):
        lines = read_log(log) if os.path.isfile(log) else []
        known = " ".join(released_versions(lines)) or "none"
        what = "VERSION not set" if not version else f"VERSION {version!r} is not a file name ([A-Za-z0-9._-], no leading .)"
        raise Refused(f"{what}; ./go evidence VERSION=<v>, released here: {known}")
    lines = read_log(log)
    picked = pick(lines, version)
    if picked is None:
        raise Refused(f"no release of {version} in {log}; released here: "
                      f"{' '.join(released_versions(lines)) or 'none'}")
    n, line, earlier, answer = picked
    rec = line["record"]
    sections = {
        "release": release_section(log, n, line),
        "stages": stages_section(log, n, rec),
        "artefacts": artefacts_section(log, n, rec, env=env, ticket=line.get("ticket")),
        "answer": answer_section(log, n, rec, answer),
        "prs": prs_section(env, log, n, line, version),
    }
    out = {"schema": SCHEMA, "version": version,
           "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "log": log, "release_line": n, "earlier_releases": earlier}
    out.update(sections)
    out["missing"] = missing_paths(sections)
    return out


def build(env):
    built = pack(env)
    directory = env.get("EVIDENCE_DIR") or ".crew/evidence"
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{built['version']}.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(built, ensure_ascii=False, indent=2) + "\n")
    return path, built


def main(argv, env=None):
    env = os.environ if env is None else env
    if argv != ["build"]:
        print("usage: evidence.py build", file=sys.stderr)
        return 2
    try:
        path, built = build(env)
    except Refused as refused:
        print(refused)
        return 1
    for path_missing in built["missing"]:
        print(f"missing: {path_missing}", file=sys.stderr)
    print(f"{path}\t{len(built['missing'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
