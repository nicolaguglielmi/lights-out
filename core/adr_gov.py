"""Decision-record lifecycle (docs/adr/0115 §1-§3): stdlib only.

Numbers, drafts and checks the architectural decision records a repository
keeps under docs/adr/ (or adr/), and judges a git range against the
policy's RISK_REQUIRE_ADR:

- `next_number(adr_dir)`: the next free four-digit number.
- `scaffold(adr_dir, title, ...)`: writes a new, valid record; its path.
- `validate_adr(text)`: the record's defects, [] when it is valid.
- `check_range_adr(repo, base, head, policy)`: `Verdict(go, reason,
  details)` for the range base..head of repo.
"""

import collections
import datetime
import os
import re
import subprocess  # nosec B404 - git only, argv lists (docs/adr/0115 §1)

try:
    from . import risk
except ImportError:  # run as a script: core/ is sys.path[0]
    import risk

Verdict = collections.namedtuple("Verdict", "go reason details")

MODES = ("off", "high", "structural")

_RECORD_NAME = re.compile(r"^(\d{4})-.+\.md$")
_RECORD_PATH = re.compile(r"^(?:docs/)?adr/\d{4}-[^/]+\.md$")
_REFERENCE = re.compile(r"\bADR[- ](\d{4})\b", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s")
_SECTION = re.compile(r"^#{1,2}\s")
_REQUIRED = (
    ("Context", re.compile(r"^##\s+Context\b")),
    ("Decision", re.compile(r"^##\s+Decision\b")),
    ("Alternatives", re.compile(r"^##\s+Alternatives\b")),
)
_PROVENANCE_HEADING = re.compile(r"^##\s+Provenance\b")

# §2: what is set aside before looking for a number -- ADR references and
# paths, record file names, dates, section marks, versions, list ordinals.
_NOT_A_THRESHOLD = re.compile(
    r"ADR[- ]?\d{4}"
    r"|[\w./-]*adr/\d{4}[\w./-]*"
    r"|\b\d{4}-[a-z0-9-]+\.md\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|§\s*\d+(?:\.\d+)*"
    r"|\bv?\d+\.\d+\.\d+\b"
    r"|^\s*\d+[.)]\s",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
_THRESHOLD_WORD = re.compile(
    r"\b(thresholds?|limits?|caps?|ceilings?|floors?|budgets?|margins?|timeouts?|quotas?|baselines?"
    r"|max|maximum|min|minimum|at most|at least|no more than|no less than|no fewer than|up to|exceeds?)\b",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(r"\b[A-Z][A-Z0-9_]*=\d")
_JUSTIFIED = re.compile(
    r"\b(provenance|because|measured|measurement|derived|source|rationale|justified|justification)\b"
    r"|ADR[- ]?\d{4}|adr/\d{4}",
    re.IGNORECASE,
)


def _next_from(names):
    numbers = [int(m.group(1)) for m in map(_RECORD_NAME.match, names) if m]
    return f"{max(numbers, default=0) + 1:04d}"


def next_number(adr_dir):
    """The next record number in adr_dir, zero-padded: one past the largest
    `NNNN-*.md`, never a gap (a published number may be referenced)."""
    try:
        names = os.listdir(adr_dir)
    except FileNotFoundError:
        names = []
    return _next_from(names)


def _slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _block(value):
    if isinstance(value, (list, tuple)):
        return "\n".join(f"- {item}" for item in value)
    return str(value).strip()


def render(number, title, context, decision, alternatives, consequences,
           provenance=None, status="proposed", date=None):
    """The record's Markdown text, as `scaffold` writes it."""
    date = date or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    if provenance is None or provenance == [] or provenance == "":
        provenance = "This record introduces no numeric threshold."
    parts = [
        f"# ADR {number} — {title.strip()}",
        f"- Status: {status}\n- Date: {date}",
        f"## Context\n\n{_block(context)}",
        f"## Decision\n\n{_block(decision)}",
        f"## Alternatives rejected\n\n{_block(alternatives)}",
        f"## Consequences\n\n{_block(consequences)}",
        f"## Provenance\n\n{_block(provenance)}",
    ]
    return "\n\n".join(parts) + "\n"


def scaffold(adr_dir, title, context, decision, alternatives, consequences,
             provenance=None, status="proposed", date=None):
    """Write `<adr_dir>/<NNNN>-<slug>.md` and return its path.

    Never overwrites; raises ValueError, writing nothing, on an empty title
    or on a record `validate_adr` would reject.
    """
    slug = _slug(title)
    if not slug:
        raise ValueError(f"title {title!r} has no letter or digit to name the file by")
    number = next_number(adr_dir)
    text = render(number, title, context, decision, alternatives, consequences, provenance, status, date)
    defects = validate_adr(text)
    if defects:
        raise ValueError("the record would not be valid: " + "; ".join(defects))
    os.makedirs(adr_dir, exist_ok=True)
    path = os.path.join(adr_dir, f"{number}-{slug}.md")
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _sections(lines):
    """[(heading line, [(lineno, line)])] split at `#` and `##` headings,
    fenced code left out: a `###` subsection is part of its section's body."""
    sections = [(None, [])]
    fenced = False
    for lineno, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if _SECTION.match(line):
            sections.append((line, []))
        else:
            sections[-1][1].append((lineno, line))
    return sections


def _numbers(line):
    return _NUMBER.findall(_NOT_A_THRESHOLD.sub(" ", line))


def validate_adr(text):
    """The record's defects (docs/adr/0115 §1-§2), [] when it is valid."""
    defects = []
    sections = _sections(text.splitlines())
    for name, pattern in _REQUIRED:
        bodies = [body for heading, body in sections if heading and pattern.match(heading)]
        if not bodies:
            defects.append(f"missing section: ## {name}")
        elif not any(line.strip() and not _HEADING.match(line) for body in bodies for _, line in body):
            defects.append(f"empty section: ## {name}")
    provenance_numbers = set()
    for heading, body in sections:
        if heading and _PROVENANCE_HEADING.match(heading):
            for _, line in body:
                provenance_numbers.update(_numbers(line))
    for heading, body in sections:
        if heading and _PROVENANCE_HEADING.match(heading):
            continue
        paragraph = []
        for lineno, line in body + [(None, "")]:
            if line.strip() and not _HEADING.match(line):
                paragraph.append((lineno, line))
                continue
            justified = _JUSTIFIED.search(" ".join(l for _, l in paragraph))
            for n, l in paragraph:
                numbers = _numbers(l)
                if not numbers or not (_THRESHOLD_WORD.search(l) or _ASSIGNMENT.search(l)):
                    continue
                if justified or set(numbers) & provenance_numbers:
                    continue
                defects.append(f"line {n}: numeric threshold without provenance: {l.strip()}")
            paragraph = []
    return defects


def _git(repo, *args):
    got = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,  # nosec B603 B607
                         check=False)
    if got.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {got.stderr.strip()}")
    return got.stdout


def check_range_adr(repo, base, head, policy):
    """Judge base..head of repo against RISK_REQUIRE_ADR (docs/adr/0115 §3).

    Records are judged only when one is required. At `high`, a commit
    message naming ADR-NNNN satisfies it only if every record so named
    exists at head under docs/adr/ or adr/ and is valid. Returns
    Verdict(go, reason, details). Raises ValueError when git cannot read
    the range.
    """
    mode = policy.get("RISK_REQUIRE_ADR") or "off"
    if mode not in MODES:
        return Verdict(False, f"RISK_REQUIRE_ADR={mode!r} is not one of {', '.join(MODES)}", [])
    if mode == "off":
        return Verdict(True, "RISK_REQUIRE_ADR=off: no decision record is required", [])
    numstat = [line for line in _git(repo, "diff", "--no-renames", "--numstat", base, head).splitlines() if line]
    changes = [line.split("\t", 1) for line in
               _git(repo, "diff", "--no-renames", "--name-status", base, head).splitlines() if line]
    added = [path for status, path in changes if status == "A"]
    records = [path for status, path in changes if status in ("A", "M") and _RECORD_PATH.match(path)]
    result = risk.classify(numstat, policy, added_paths=added)
    if not result["adr_required"]:
        return Verdict(True, f"RISK_REQUIRE_ADR={mode}: no decision record is required at risk level "
                             f"{result['level']}", [])
    defects, valid = [], []
    for path in records:
        found = validate_adr(_git(repo, "show", f"{head}:{path}"))
        defects += [f"{path}: {defect}" for defect in found]
        if not found:
            valid.append(path)
    if defects:
        return Verdict(False, "a decision record in the range is incomplete", defects)
    if valid:
        return Verdict(True, f"RISK_REQUIRE_ADR={mode}: recorded in {' '.join(valid)}", [])
    if mode == "high":
        if _git(repo, "cat-file", "-t", base).strip() == "tree":
            messages = _git(repo, "log", "--format=%B", head)
        else:
            messages = _git(repo, "log", "--format=%B", f"{base}..{head}")
        refs = sorted(set(_REFERENCE.findall(messages)))
        if refs:
            listed = _git(repo, "ls-tree", "-r", "--name-only", head, "--", "docs/adr/", "adr/").splitlines()
            problems = []
            for ref in refs:
                found = [p for p in listed if _RECORD_PATH.match(p) and os.path.basename(p).startswith(f"{ref}-")]
                if not found:
                    problems.append(f"commit message names ADR-{ref}, but no docs/adr/ or adr/{ref}-*.md exists "
                                    f"at {head}")
                for path in found:
                    problems += [f"ADR-{ref} {path}: {d}" for d in validate_adr(_git(repo, "show", f"{head}:{path}"))]
            if problems:
                return Verdict(False, "RISK_REQUIRE_ADR=high: a referenced decision record is missing or incomplete",
                               problems)
            return Verdict(True, "RISK_REQUIRE_ADR=high: referenced by commit message as "
                                 + " ".join(f"ADR-{ref}" for ref in refs), [])
    triggers = [path for signal in result["signals"] if result["level"] == "high" for path in signal["paths"]]
    if mode == "structural":
        triggers += [path for path in risk.new_manifests(added) if path not in triggers]
    listed = _git(repo, "ls-tree", "--name-only", head, "docs/adr/").splitlines()
    number = _next_from(os.path.basename(name) for name in listed)
    accepted = "a new or updated record under docs/adr/ or adr/"
    if mode == "high":
        accepted += ", or a commit message naming ADR-NNNN"
    details = [f"needs a decision record: {path}" for path in triggers]
    details.append(f"add {accepted} (next free number: docs/adr/{number}-<title>.md)")
    return Verdict(False, f"RISK_REQUIRE_ADR={mode}: the range changes structural paths and records no decision",
                   details)
