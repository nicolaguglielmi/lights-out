"""The effective merge and verify policy (docs/adr/0037 §5): stdlib only.

Platform defaults (policy/merge.env), then the workload's measured
MUTATION_BASELINE (its workload.env), then the workload's optional
policy.env overrides. Writes the effective policy as KEY=VALUE lines;
the risk and mutation gates and verify/slo.sh read only that file.

    python3 core/policy.py --platform policy/merge.env \
        --workload-env <contract>/workload.env \
        --overrides <contract>/policy.env --out <file>

An override file that does not exist means no overrides. Any problem in
one is exit 1 with one line on stdout naming the key, never a default.
An override looser than its default is reported on stderr and applied.
"""

import argparse
import re
import sys

try:
    from .risk import ADR_MODES, parse_policy
except ImportError:  # run as a script: core/ is sys.path[0]
    from risk import ADR_MODES, parse_policy

# key -> (kind, loosening direction). "high": a larger value is looser;
# "low": a smaller one; "set": a member the default lacks; "drop": a
# member of the default the override leaves out; "rank": a value earlier
# in its kind's order (ADR_MODES, least strict first).
OVERRIDABLE = {
    "MUTATION_MARGIN": ("fraction", "high"),
    "RISK_SIZE_MEDIUM_LINES": ("count", "high"),
    "RISK_AUTO_MERGE_LEVELS": ("levels", "set"),
    "RISK_HIGH_MIGRATIONS": ("globs", "drop"),
    "RISK_HIGH_AUTH": ("globs", "drop"),
    "RISK_HIGH_PAYMENTS": ("globs", "drop"),
    "ERROR_RATE_MAX_DELTA": ("fraction", "high"),
    "LATENCY_P95_MAX_DELTA": ("seconds", "high"),
    "MUTATION_BASELINE": ("fraction", "low"),
    "RISK_REQUIRE_ADR": ("adr-mode", "rank"),
}

# Keys the platform defaults may leave out: the baseline is the
# workload's, and a policy written before docs/adr/0115 has no
# RISK_REQUIRE_ADR, which is then off (core/risk.py).
_NO_DEFAULT_NEEDED = ("MUTATION_BASELINE", "RISK_REQUIRE_ADR")

# High-risk changes (migrations, auth, payments, the platform and an
# attached repository's contract) always go to a human.
AUTO_MERGE_LEVELS = ("low", "medium")

_KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_DECIMAL = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_COUNT = re.compile(r"^[1-9][0-9]*$")
_PROVENANCE = re.compile(r"^#\s*Provenance, (measurement|external|decision):")


class PolicyError(Exception):
    """One line, naming the key; source says which input file it is about."""

    def __init__(self, message, source="overrides"):
        super().__init__(message)
        self.source = source


def _items(value):
    return value.split(":") if value else []


def check_value(key, value):
    """Raise PolicyError unless value is well-formed for key."""
    kind = OVERRIDABLE[key][0]
    if kind in ("fraction", "seconds"):
        if not _DECIMAL.match(value):
            raise PolicyError(f"{key}={value!r} is not a decimal number")
        if kind == "fraction" and float(value) > 1:
            raise PolicyError(f"{key}={value} is above 1")
    elif kind == "count":
        if not _COUNT.match(value):
            raise PolicyError(f"{key}={value!r} is not a whole number of lines, 1 or more")
    elif kind == "adr-mode":
        if value not in ADR_MODES:
            raise PolicyError(f"{key}={value!r} is not one of {', '.join(ADR_MODES)}")
    elif kind == "levels":
        for level in _items(value):
            if level not in AUTO_MERGE_LEVELS:
                raise PolicyError(
                    f"{key} may name only {' and '.join(AUTO_MERGE_LEVELS)}, not {level!r}: "
                    "high-risk changes always go to review"
                )
    else:
        for glob in _items(value):
            if not glob or any(c.isspace() for c in glob):
                raise PolicyError(f"{key}={value!r} has an empty or blank-containing entry")


def parse_overrides(text):
    """Return [(lineno, key, value)] from a policy.env, or raise PolicyError."""
    lines = text.splitlines()
    seen = {}
    result = []
    for index, raw in enumerate(lines):
        lineno = index + 1
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEY_LINE.match(line)
        if not match:
            raise PolicyError(f"line {lineno} is not KEY=VALUE: {line!r}")
        key, value = match.groups()
        if key not in OVERRIDABLE:
            raise PolicyError(
                f"line {lineno}: unknown key {key} (overridable: {', '.join(OVERRIDABLE)})"
            )
        if key in seen:
            raise PolicyError(f"line {lineno}: {key} is set twice (first on line {seen[key]})")
        seen[key] = lineno
        block = []
        above = index - 1
        while above >= 0 and lines[above].strip().startswith("#"):
            block.append(lines[above].strip())
            above -= 1
        if not any(_PROVENANCE.match(c) for c in block):
            raise PolicyError(
                f"line {lineno}: {key} has no '# Provenance, measurement|external|decision:' "
                "comment directly above it"
            )
        try:
            check_value(key, value)
        except PolicyError as exc:
            raise PolicyError(f"line {lineno}: {exc}") from None
        result.append((lineno, key, value))
    return result


def looser(key, value, default):
    """The way value loosens default, or None."""
    direction = OVERRIDABLE[key][1]
    if direction == "high":
        return value if float(value) > float(default) else None
    if direction == "low":
        return value if float(value) < float(default) else None
    if direction == "rank":
        return value if ADR_MODES.index(value) < ADR_MODES.index(default) else None
    if direction == "set":
        added = [i for i in _items(value) if i not in _items(default)]
        return "adds " + ":".join(added) if added else None
    dropped = [i for i in _items(default) if i not in _items(value)]
    return "drops " + ":".join(dropped) if dropped else None


def effective(platform_text, workload_env_text, overrides_text):
    """Return (policy dict, [looser report]) or raise PolicyError."""
    policy = parse_policy(platform_text)
    for key in OVERRIDABLE:
        if key not in _NO_DEFAULT_NEEDED and key not in policy:
            raise PolicyError(f"no default for {key}", "platform")
    if "RISK_REQUIRE_ADR" in policy:
        try:
            check_value("RISK_REQUIRE_ADR", policy["RISK_REQUIRE_ADR"])
        except PolicyError as exc:
            raise PolicyError(str(exc), "platform") from None
    baseline = parse_policy(workload_env_text).get("MUTATION_BASELINE", "")
    if baseline:
        try:
            check_value("MUTATION_BASELINE", baseline)
        except PolicyError as exc:
            raise PolicyError(str(exc), "workload") from None
        policy["MUTATION_BASELINE"] = baseline
    reports = []
    for _, key, value in parse_overrides(overrides_text):
        default = policy.get(key)
        # An empty list is a default too (nothing auto-merges, no such
        # paths); an absent baseline is not, so nothing compares to it.
        if default is not None and (default or OVERRIDABLE[key][0] in ("levels", "globs")):
            how = looser(key, value, default)
            if how is not None:
                reports.append(f"{key}={value} is looser than the default {default or '(empty)'} ({how})")
        policy[key] = value
    return policy, reports


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", required=True)
    parser.add_argument("--workload-env", required=True)
    parser.add_argument("--overrides")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    with open(args.platform) as fh:
        platform_text = fh.read()
    with open(args.workload_env) as fh:
        workload_env_text = fh.read()
    overrides_text = ""
    if args.overrides:
        try:
            with open(args.overrides) as fh:
                overrides_text = fh.read()
        except FileNotFoundError:
            pass

    try:
        policy, reports = effective(platform_text, workload_env_text, overrides_text)
    except PolicyError as exc:
        where = {"platform": args.platform, "workload": args.workload_env}.get(exc.source, args.overrides)
        print(f"{where}: {exc}")
        return 1

    for report in reports:
        print(f"policy: {args.overrides}: {report}", file=sys.stderr)
    with open(args.out, "w") as fh:
        fh.write("# The effective policy, written by core/policy.py (docs/adr/0037 §5).\n")
        for key, value in policy.items():
            fh.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
