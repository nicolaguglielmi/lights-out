"""Autonomous ## Network spec-split planner and validator (docs/adr/0120): stdlib only.

    python3 core/spec_split.py check <ticket.md> [--json]
    python3 core/spec_split.py split <ticket.md> [--in-place] [--json]
    python3 core/spec_split.py compose <proposal.json> [--json]

Partitions a ticket's ## Perimeter into disjoint `impl` (implementation and
documentation) and `tests` (test files) sub-perimeters, validates 100%
perimeter coverage, member bounds (<= MAX_NETWORK_MEMBERS), and zero pairwise
glob overlap matching adapters/network/run.sh and core/assign.py.
Strictly vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import posixpath
import re
import sys
from typing import Any, Sequence

try:
    from . import assign, spec
except ImportError:  # run as a script: core/ is sys.path[0]
    import assign
    import spec

# Provenance, external/decision: docs/adr/0036 §2 ("The number": ~250 req/day
# divided by MAX_TURNS=60 is 4.2 -> 4) and docs/adr/0120.
MAX_NETWORK_MEMBERS = 4
# Provenance, decision: docs/adr/0120 — at least 1 impl/doc path and 1 test path
# are needed to split a ticket into disjoint `impl` and `tests` members.
MIN_SPLITTABLE_PATHS = 2

NETWORK_HEADING = "## Network"
PERIMETER_HEADING = "## Perimeter"
_MEMBER_ID_RE = re.compile(r"^[a-z0-9-]+$")
_TEST_PATH_PATTERNS = ("tests/*", "*/tests/*", "test/*", "*/test/*")
_TEST_BASE_PATTERNS = (
    "test_*",
    "*_test.*",
    "*.spec.*",
    "*.test.*",
    "*Test.*",
    "*Tests.*",
    "*_spec.*",
)

_DEFAULT_BRIEFS = {
    "impl": (
        "Implement the acceptance criteria in the implementation and documentation "
        "paths below. Do not add or change any test: another worker writes the tests "
        "independently from this ticket without seeing your code."
    ),
    "tests": (
        "Write tests that verify the acceptance criteria cleanly from this ticket "
        "alone, in the test paths below. Another worker implements the code at the "
        "same time without seeing your tests: verify observable behaviour and do not "
        "modify implementation or documentation code."
    ),
}


def clean_glob(path: str) -> str:
    """Normalize surrounding whitespace and Markdown backticks from a perimeter item."""
    return str(path or "").strip().strip("`").strip()


def _strip_dot_slash(path: str) -> str:
    out = path
    while out.startswith("./"):
        out = out[2:]
    return out


def classify_path(path: str) -> str:
    """Classify a perimeter path or glob as `'tests'` or `'impl'`."""
    cleaned = _strip_dot_slash(clean_glob(path))
    if not cleaned:
        return "impl"
    trimmed = cleaned.rstrip("/")
    if trimmed in ("tests", "test") or trimmed.endswith(("/tests", "/test")):
        return "tests"
    for pat in _TEST_PATH_PATTERNS:
        if fnmatch.fnmatchcase(cleaned, pat):
            return "tests"
    base = posixpath.basename(trimmed)
    for pat in _TEST_BASE_PATTERNS:
        if fnmatch.fnmatchcase(base, pat):
            return "tests"
    return "impl"


def globs_overlap(a: str, b: str) -> bool:
    """True when globs A and B can match a common path (adapters/network/run.sh + core/assign.py)."""
    ga, gb = clean_glob(a), clean_glob(b)
    if not ga or not gb:
        return False
    return fnmatch.fnmatchcase(ga, gb) or fnmatch.fnmatchcase(gb, ga) or assign.overlaps(ga, gb)


def _ticket_body_lines(markdown: str) -> list[str]:
    """Return lines belonging to the ticket itself (stopping before appended `---` brief blocks)."""
    lines = markdown.splitlines()
    out: list[str] = []
    seen_heading = False
    for line in lines:
        if line.startswith("## "):
            seen_heading = True
        if seen_heading and (line.strip() == "---" or (line.startswith("# ") and not line.startswith("## "))):
            break
        out.append(line)
    return out


def extract_perimeter(markdown: str) -> list[str]:
    """Extract cleaned `## Perimeter` globs from ticket markdown."""
    out: list[str] = []
    seen: set[str] = set()
    inside = False
    for line in _ticket_body_lines(markdown):
        stripped = line.rstrip()
        if stripped == PERIMETER_HEADING or stripped.startswith(PERIMETER_HEADING + " "):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside and line.startswith("- "):
            g = clean_glob(line[2:])
            if g and g not in seen:
                seen.add(g)
                out.append(g)
    return out


def extract_network(markdown: str) -> list[dict[str, Any]] | None:
    """Parse an existing `## Network` section into [{id, brief, paths}], or None when absent."""
    lines = _ticket_body_lines(markdown)
    inside = False
    found = False
    members: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    brief_lines: list[str] = []

    def _flush_brief() -> None:
        nonlocal brief_lines
        if current is not None:
            current["brief"] = "\n".join(brief_lines).strip()
            brief_lines = []

    for line in lines:
        stripped = line.rstrip()
        if stripped == NETWORK_HEADING or stripped.startswith(NETWORK_HEADING + " "):
            inside = True
            found = True
            continue
        if inside and line.startswith("## "):
            break
        if not inside:
            continue
        if line.startswith("### "):
            _flush_brief()
            mid = line[4:].strip().split()[0] if line[4:].strip() else ""
            current = {"id": mid, "brief": "", "paths": []}
            members.append(current)
            continue
        if current is not None:
            if line.startswith("- "):
                g = clean_glob(line[2:])
                if g:
                    current["paths"].append(g)
            else:
                brief_lines.append(stripped)
    _flush_brief()
    return members if found else None


def _glob_covered_by(candidate: str, container_globs: Sequence[str]) -> bool:
    cg = clean_glob(candidate)
    for pg in container_globs:
        clean_pg = clean_glob(pg)
        if cg == clean_pg or fnmatch.fnmatchcase(cg, clean_pg):
            return True
    return False


def _default_brief(member_id: str, criteria: Sequence[str] | None = None) -> str:
    if member_id in _DEFAULT_BRIEFS:
        return _DEFAULT_BRIEFS[member_id]
    count = len([c for c in (criteria or []) if str(c).strip()])
    suffix = f" ({count} acceptance criteria)" if count else ""
    return f"Complete the `{member_id}` portion of this ticket{suffix} within its declared paths only."


def normalize_network(
    network: Any,
    perimeter: Sequence[str] | None = None,
    criteria: Sequence[str] | None = None,
    max_members: int = MAX_NETWORK_MEMBERS,
) -> list[dict[str, Any]]:
    """Normalize a network specification (list or dict) into validated member dicts."""
    if isinstance(network, dict) and "members" in network:
        raw_list = network.get("members")
    elif isinstance(network, dict):
        ordered_keys = [k for k in ("impl", "tests") if k in network] + [
            k for k in network if k not in ("impl", "tests")
        ]
        raw_list = []
        for mid in ordered_keys:
            val = network[mid]
            if isinstance(val, list):
                raw_list.append({"id": str(mid), "brief": _default_brief(str(mid), criteria), "paths": val})
            elif isinstance(val, dict):
                raw_list.append(
                    {
                        "id": str(val.get("id") or mid),
                        "brief": str(val.get("brief") or _default_brief(str(mid), criteria)).strip(),
                        "paths": val.get("paths") if val.get("paths") is not None else (val.get("perimeter") or []),
                    }
                )
            else:
                raise ValueError(f"network member {mid!r} must be a path list or object")
    elif isinstance(network, list):
        raw_list = network
    else:
        raise ValueError("network must be a list of member objects or a member mapping")

    if not isinstance(raw_list, list):
        raise ValueError("network members must be a list")

    members: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_list, 1):
        if not isinstance(item, dict):
            raise ValueError(f"network member #{idx} is not an object")
        mid = str(item.get("id") or "").strip()
        raw_paths = item.get("paths") if item.get("paths") is not None else (item.get("perimeter") or [])
        if not isinstance(raw_paths, list) or not all(isinstance(p, str) for p in raw_paths):
            raise ValueError(f"network member {mid or f'#{idx}'} paths must be a list of strings")
        seen_p: set[str] = set()
        paths: list[str] = []
        for p in raw_paths:
            cg = clean_glob(p)
            if cg and cg not in seen_p:
                seen_p.add(cg)
                paths.append(cg)
        brief = str(item.get("brief") or _default_brief(mid, criteria)).strip()
        members.append({"id": mid, "brief": brief, "paths": paths})

    errors = validate_network_members(members, perimeter=perimeter, max_members=max_members)
    if errors:
        raise ValueError("; ".join(errors))
    return members


def validate_network_members(
    members: Sequence[dict[str, Any]],
    perimeter: Sequence[str] | None = None,
    max_members: int = MAX_NETWORK_MEMBERS,
) -> list[str]:
    """Validate member count, ids, non-empty paths, zero overlap, and 100% perimeter coverage."""
    errors: list[str] = []
    if not isinstance(max_members, int) or max_members < 1 or max_members > MAX_NETWORK_MEMBERS:
        return [f"max_members must be between 1 and {MAX_NETWORK_MEMBERS}, got {max_members!r}"]
    if not members:
        return ["network declares no member"]
    if len(members) > max_members:
        return [f"network declares {len(members)} members, at most {max_members}"]

    seen_ids: set[str] = set()
    all_member_globs: list[str] = []
    for idx, member in enumerate(members, 1):
        mid = str(member.get("id") or "").strip()
        if not mid or not _MEMBER_ID_RE.match(mid):
            errors.append(f"member #{idx} id {mid!r} is not [a-z0-9-]+")
            continue
        if mid in seen_ids:
            errors.append(f"member {mid} is declared twice")
        seen_ids.add(mid)
        paths = [clean_glob(p) for p in (member.get("paths") or member.get("perimeter") or []) if clean_glob(p)]
        if not paths:
            errors.append(f"member {mid} declares no perimeter")
        all_member_globs.extend(paths)

    for i in range(len(members)):
        a_id = str(members[i].get("id") or f"#{i + 1}")
        a_paths = [clean_glob(p) for p in (members[i].get("paths") or members[i].get("perimeter") or []) if clean_glob(p)]
        for j in range(i + 1, len(members)):
            b_id = str(members[j].get("id") or f"#{j + 1}")
            b_paths = [
                clean_glob(p) for p in (members[j].get("paths") or members[j].get("perimeter") or []) if clean_glob(p)
            ]
            for ga in a_paths:
                for gb in b_paths:
                    if globs_overlap(ga, gb):
                        errors.append(f"members {a_id} and {b_id} overlap: {ga} ~ {gb}")

    if perimeter is not None:
        clean_perim = [clean_glob(p) for p in perimeter if clean_glob(p)]
        for member in members:
            mid = str(member.get("id") or "")
            for mg in [clean_glob(p) for p in (member.get("paths") or member.get("perimeter") or []) if clean_glob(p)]:
                if not _glob_covered_by(mg, clean_perim):
                    errors.append(f"member {mid} path {mg} is outside ## Perimeter")
        for pg in clean_perim:
            if not _glob_covered_by(pg, all_member_globs):
                errors.append(f"perimeter path {pg} is uncovered by ## Network members")

    return errors


def _propose_with_reason(
    perimeter: Sequence[str],
    criteria: Sequence[str] | None = None,
    max_members: int = MAX_NETWORK_MEMBERS,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not isinstance(max_members, int) or max_members < 2 or max_members > MAX_NETWORK_MEMBERS:
        return None, f"max_members {max_members!r} cannot form a split within 2..{MAX_NETWORK_MEMBERS}"

    clean: list[str] = []
    seen: set[str] = set()
    for raw in perimeter or []:
        if not isinstance(raw, str):
            continue
        g = clean_glob(raw)
        if g and g not in seen:
            seen.add(g)
            clean.append(g)

    if len(clean) < MIN_SPLITTABLE_PATHS:
        return None, f"unsplittable: need at least {MIN_SPLITTABLE_PATHS} distinct perimeter paths, got {len(clean)}"

    impl_paths = [p for p in clean if classify_path(p) == "impl"]
    test_paths = [p for p in clean if classify_path(p) == "tests"]

    if not impl_paths:
        return None, "unsplittable: no implementation or documentation paths in ## Perimeter"
    if not test_paths:
        return None, "unsplittable: no test paths in ## Perimeter"

    for ga in impl_paths:
        for gb in test_paths:
            if globs_overlap(ga, gb):
                return None, f"unsplittable: impl path {ga} overlaps test path {gb}"

    members = [
        {"id": "impl", "brief": _default_brief("impl", criteria), "paths": impl_paths},
        {"id": "tests", "brief": _default_brief("tests", criteria), "paths": test_paths},
    ]
    errors = validate_network_members(members, perimeter=clean, max_members=max_members)
    if errors:
        return None, f"unsplittable: {'; '.join(errors)}"
    return members, None


def propose_network_split(
    perimeter: Sequence[str],
    criteria: Sequence[str] | None = None,
    max_members: int = MAX_NETWORK_MEMBERS,
) -> list[dict[str, Any]] | None:
    """Partition PERIMETER into disjoint `impl` and `tests` members, or return None if unsplittable."""
    members, _ = _propose_with_reason(perimeter, criteria=criteria, max_members=max_members)
    return members


def render_network_section(members: Any) -> str:
    """Format a validated `## Network` Markdown section compatible with adapters/network/run.sh."""
    normalized = normalize_network(members)
    lines = [NETWORK_HEADING]
    for member in normalized:
        lines.extend(["", f"### {member['id']}"])
        brief = str(member.get("brief") or "").strip()
        if brief:
            lines.extend(["", *brief.splitlines()])
        lines.append("")
        for path in member["paths"]:
            lines.append(f"- `{path}`")
    return "\n".join(lines).rstrip("\n") + "\n"


def inject_network_section(
    markdown: str,
    members: Any = None,
    max_members: int = MAX_NETWORK_MEMBERS,
) -> str:
    """Inject `## Network` after `## Perimeter` when absent; preserve existing `## Network`."""
    if extract_network(markdown) is not None:
        return markdown

    perimeter = extract_perimeter(markdown)
    if members is None:
        criteria = spec.criteria_of(markdown)
        members = propose_network_split(perimeter, criteria=criteria, max_members=max_members)
        if members is None:
            return markdown
    else:
        members = normalize_network(members, perimeter=perimeter or None, max_members=max_members)

    block_lines = render_network_section(members).rstrip("\n").splitlines()
    lines = markdown.splitlines()

    perim_start = None
    perim_end = None
    for idx, line in enumerate(lines):
        stripped = line.rstrip()
        if perim_start is None:
            if stripped == PERIMETER_HEADING or stripped.startswith(PERIMETER_HEADING + " "):
                perim_start = idx
        else:
            if line.startswith("## ") or line.strip() == "---" or (line.startswith("# ") and not line.startswith("## ")):
                perim_end = idx
                break
    if perim_start is not None and perim_end is None:
        perim_end = len(lines)

    if perim_end is not None:
        head = list(lines[:perim_end])
        while head and not head[-1].strip():
            head.pop()
        tail = list(lines[perim_end:])
        while tail and not tail[0].strip():
            tail.pop(0)
        combined = head + [""] + block_lines + ([""] + tail if tail else [])
    else:
        head = list(lines)
        while head and not head[-1].strip():
            head.pop()
        combined = head + ([""] if head else []) + block_lines

    return "\n".join(combined).rstrip("\n") + "\n"


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _compose_from_proposal(data: Any) -> tuple[list[dict[str, Any]], str]:
    if isinstance(data, dict) and "perimeter" in data and "network" not in data and "impl" not in data and "members" not in data:
        members, why = _propose_with_reason(data.get("perimeter") or [], criteria=data.get("criteria"))
        if members is None:
            raise ValueError(why or "proposal is unsplittable")
        return members, render_network_section(members)
    if isinstance(data, dict) and "network" in data:
        members = normalize_network(
            data["network"],
            perimeter=data.get("perimeter"),
            criteria=data.get("criteria"),
        )
        return members, render_network_section(members)
    members = normalize_network(data)
    return members, render_network_section(members)


def _cmd_check(path: str, as_json: bool) -> int:
    text = _read_text(path)
    perimeter = extract_perimeter(text)
    existing = extract_network(text)
    if existing is not None:
        errors = validate_network_members(existing, perimeter=perimeter or None)
        if errors:
            msg = "; ".join(errors)
            if as_json:
                print(json.dumps({"ok": False, "mode": "existing", "error": msg}))
            else:
                print(f"spec-split: {msg}", file=sys.stderr)
            return 1
        if as_json:
            print(json.dumps({"ok": True, "mode": "existing", "members": existing}))
        else:
            print(f"valid network ({len(existing)} members)")
        return 0

    members, why = _propose_with_reason(perimeter, criteria=spec.criteria_of(text))
    if members is None:
        if as_json:
            print(json.dumps({"ok": False, "mode": "split", "error": why}))
        else:
            print(f"spec-split: {why}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps({"ok": True, "mode": "split", "members": members}))
    else:
        summary = ", ".join(f"{m['id']}={len(m['paths'])}" for m in members)
        print(f"splittable ({summary})")
    return 0


def _cmd_split(path: str, in_place: bool, as_json: bool) -> int:
    text = _read_text(path)
    perimeter = extract_perimeter(text)
    existing = extract_network(text)
    if existing is not None:
        errors = validate_network_members(existing, perimeter=perimeter or None)
        if errors:
            print(f"spec-split: {'; '.join(errors)}", file=sys.stderr)
            return 1
        if as_json:
            print(json.dumps({"status": "preserved", "members": existing, "markdown": text}))
        elif not in_place:
            sys.stdout.write(text)
        return 0

    members, why = _propose_with_reason(perimeter, criteria=spec.criteria_of(text))
    if members is None:
        print(f"spec-split: {why}", file=sys.stderr)
        return 1
    updated = inject_network_section(text, members=members)
    if in_place:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(updated)
    if as_json:
        print(json.dumps({"status": "split", "members": members, "markdown": updated}))
    elif not in_place:
        sys.stdout.write(updated)
    return 0


def _cmd_compose(path: str, as_json: bool) -> int:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    members, rendered = _compose_from_proposal(data)
    if as_json:
        print(json.dumps({"members": members, "markdown": rendered}))
    else:
        sys.stdout.write(rendered)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="core/spec_split.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="Validate ## Network or check splittability of a ticket")
    p_check.add_argument("ticket")
    p_check.add_argument("--json", action="store_true")

    p_split = sub.add_parser("split", help="Inject disjoint ## Network section into a ticket")
    p_split.add_argument("ticket")
    p_split.add_argument("--in-place", action="store_true")
    p_split.add_argument("--json", action="store_true")

    p_compose = sub.add_parser("compose", help="Render ## Network section from proposal JSON")
    p_compose.add_argument("proposal")
    p_compose.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "check":
            return _cmd_check(args.ticket, args.json)
        if args.cmd == "split":
            return _cmd_split(args.ticket, args.in_place, args.json)
        if args.cmd == "compose":
            return _cmd_compose(args.proposal, args.json)
    except (OSError, ValueError) as exc:
        print(f"spec-split: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
