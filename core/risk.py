"""Rules-based PR risk classifier (docs/adr/0016): stdlib only, no engine.

Reads `git diff --numstat` lines and the policy file (policy/merge.env) and
classifies a git range into level/decision/signals. Rules, not points: the
level is the single highest-severity signal that matched, never a sum of
weights. Usable both as a library and as a CLI:

    git diff --numstat <base> <head> \
        | python3 core/risk.py --policy policy/merge.env
"""

import fnmatch
import json
import sys

# All four are "high" severity (docs/adr/0016 §2): this order only controls
# the order signals are listed when more than one matches, never which one
# "wins" -- there is no winner among them.
_HIGH_SIGNAL_KEYS = (
    ("migrations", "RISK_HIGH_MIGRATIONS"),
    ("auth", "RISK_HIGH_AUTH"),
    ("payments", "RISK_HIGH_PAYMENTS"),
    ("platform", "RISK_HIGH_PLATFORM"),
)

# RISK_REQUIRE_ADR's values, least strict first (docs/adr/0115 §3).
ADR_MODES = ("off", "high", "structural")

# docs/adr/0115 §3: an added file with one of these basenames is a new
# package manifest, lockfile, container or compose manifest -- structural
# under RISK_REQUIRE_ADR=structural. Provenance, external: the file names
# each ecosystem's own tooling documents.
_MANIFEST_GLOBS = (
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "pnpm-workspace.yaml", "bun.lock", "bun.lockb",
    "pyproject.toml", "requirements*.txt", "setup.py", "setup.cfg", "poetry.lock", "uv.lock",
    "Pipfile", "Pipfile.lock",
    "go.mod", "go.sum", "go.work",
    "Cargo.toml", "Cargo.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.lockfile",
    "Gemfile", "Gemfile.lock", "composer.json", "composer.lock",
    "Containerfile", "Dockerfile", "compose.yaml", "compose.yml",
)


def new_manifests(added_paths):
    """The added paths that are a manifest or lockfile (docs/adr/0115 §3)."""
    return _unique_in_order(
        path for path in added_paths
        if any(fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], glob) for glob in _MANIFEST_GLOBS)
    )


def parse_policy(text):
    """Six-line KEY=VALUE parser: policy/merge.env's own shape (docs/adr/0016 §6).

    Split on the first `=`, strip whitespace, skip blank lines and lines
    starting with `#`. No quoting, no expansion.
    """
    policy = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        policy[key.strip()] = value.strip()
    return policy


def _split_globs(value):
    return [item for item in value.split(":") if item]


def _unique_in_order(paths):
    seen = set()
    result = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def _parse_numstat_line(line):
    """`added<TAB>deleted<TAB>path`; binary files show `-` for both counts."""
    parts = line.split("\t")
    if len(parts) != 3:
        raise ValueError(f"malformed numstat line: {line!r}")
    added_s, deleted_s, path = parts
    if not path:
        raise ValueError(f"malformed numstat line: {line!r}")
    try:
        added = 0 if added_s == "-" else int(added_s)
        deleted = 0 if deleted_s == "-" else int(deleted_s)
    except ValueError:
        raise ValueError(f"malformed numstat line: {line!r}") from None
    return added, deleted, path


def classify(numstat_lines, policy, added_paths=()):
    """Classify a git range from its `--numstat` lines and the merge policy.

    Rules, not points (docs/adr/0016 §2): level is the single
    highest-severity signal that matched, evaluated in this order --
    migrations/auth/payments/platform (all "high"), then size ("medium"),
    else "low". Raises ValueError on a numstat line that does not parse as
    `added<TAB>deleted<TAB>path`.

    `adr_required` (docs/adr/0115 §4) is true when the policy's
    RISK_REQUIRE_ADR asks for a decision record: `high` on level high,
    `structural` on level high or a new manifest among `added_paths` (the
    range's added files); absent or `off`, never. Raises ValueError on any
    other RISK_REQUIRE_ADR value.
    """
    adr_mode = policy.get("RISK_REQUIRE_ADR") or "off"
    if adr_mode not in ADR_MODES:
        raise ValueError(f"RISK_REQUIRE_ADR={adr_mode!r} is not one of {', '.join(ADR_MODES)}")
    entries = [_parse_numstat_line(line) for line in numstat_lines]

    high_signals = []
    for name, key in _HIGH_SIGNAL_KEYS:
        patterns = _split_globs(policy.get(key, ""))
        matched = _unique_in_order(
            path
            for _, _, path in entries
            if any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
        )
        if matched:
            high_signals.append({"name": name, "paths": matched})

    exclude_patterns = _split_globs(policy.get("RISK_SIZE_EXCLUDE", ""))
    all_paths = _unique_in_order(path for _, _, path in entries)
    excluded = [
        path
        for path in all_paths
        if any(fnmatch.fnmatch(path, pattern) for pattern in exclude_patterns)
    ]
    excluded_set = set(excluded)
    size_lines = sum(
        added + deleted
        for added, deleted, path in entries
        if path not in excluded_set
    )
    threshold = int(policy.get("RISK_SIZE_MEDIUM_LINES", "0"))

    if high_signals:
        level = "high"
        signals = high_signals
    elif size_lines >= threshold:
        level = "medium"
        non_excluded = [path for path in all_paths if path not in excluded_set]
        signals = [{"name": "size", "paths": non_excluded}]
    else:
        level = "low"
        signals = []

    auto_merge_levels = set(_split_globs(policy.get("RISK_AUTO_MERGE_LEVELS", "")))
    decision = "auto-merge" if level in auto_merge_levels else "review"

    if adr_mode == "high":
        adr_required = level == "high"
    elif adr_mode == "structural":
        adr_required = level == "high" or bool(new_manifests(added_paths))
    else:
        adr_required = False

    return {
        "level": level,
        "decision": decision,
        "signals": signals,
        "size": {
            "lines": size_lines,
            "threshold": threshold,
            "excluded": excluded,
        },
        "adr_required": adr_required,
    }


def _main(argv):
    if len(argv) != 2 or argv[0] != "--policy":
        print("usage: risk.py --policy <path>", file=sys.stderr)
        return 1

    try:
        with open(argv[1]) as fh:
            policy = parse_policy(fh.read())
    except OSError as exc:
        print(f"risk: cannot read policy file: {exc}", file=sys.stderr)
        return 1

    lines = sys.stdin.read().splitlines()

    try:
        result = classify(lines, policy)
    except ValueError as exc:
        print(f"risk: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
