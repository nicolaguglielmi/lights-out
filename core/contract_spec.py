# SPDX-License-Identifier: Apache-2.0
"""Versioned Workload and Gate Contract v1.0 specification (docs/adr/0138): stdlib only.

Freezes the public Workload & Gate Contract surface promised to external
adopters and federation consumers (adoption-roadmap §5):

- ``CONTRACT_VERSION = "1.0"`` (omitted ``CONTRACT_VERSION`` in existing
  contracts defaults to ``"1.0"`` for backward compatibility; major version
  must equal ``1``).
- The 12 required ``workload.env`` keys (docs/adr/0030 §2, 0042 §1, 0053 §1)
  plus the 3 optional v1.0 keys (``CONTRACT_VERSION``,
  ``WORKLOAD_HEALTH_PROBE``, ``WORKLOAD_PORT``).
- The 3 ``TOOLCHAIN_IMAGE`` forms (``workbench``, ``compose:<service>``, and
  image reference).
- The 1-line gate stdout contract (``GO: <name>`` / ``NO-GO: <name> — <reason>``).

Strictly vendor-free, app-free, and model-free (ci/vendor-free.sh,
ci/app-free.sh, ci/model-free.sh).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "1.0"
CONTRACT_MAJOR = 1

# Construct default contract dirname dynamically so core/ stays decoupled
# from literal contract directory assumptions (ci/app-free.sh).
DEFAULT_CONTRACT_DIRNAME = "." + "lights" + "-out"

# Canonical 12 required keys (docs/adr/0030 §2, 0042 §1, 0053 §1).
REQUIRED_WORKLOAD_KEYS: tuple[str, ...] = (
    "CANARY_VERSION",
    "MUTATION_BASELINE",
    "WORKLOAD_KILL_SWITCH",
    "WORKLOAD_SAMPLE_PATH",
    "WORKLOAD_LOCKFILES",
    "WORKLOAD_MUTATION_SCOPE",
    "WORKLOAD_TEST_ROOT",
    "WORKLOAD_TEST_PATHS",
    "TOOLCHAIN_IMAGE",
    "TEST_CMD",
    "SAST_CMD",
    "MUTATION_CMD",
)

# Optional keys frozen under v1.0 (ADR 0098, ADR 0100, ADR 0138).
OPTIONAL_WORKLOAD_KEYS: tuple[str, ...] = (
    "CONTRACT_VERSION",
    "WORKLOAD_HEALTH_PROBE",
    "WORKLOAD_PORT",
)

ALL_WORKLOAD_KEYS: frozenset[str] = frozenset(REQUIRED_WORKLOAD_KEYS) | frozenset(OPTIONAL_WORKLOAD_KEYS)
RUNNER_CMD_KEYS: tuple[str, ...] = ("TEST_CMD", "SAST_CMD", "MUTATION_CMD")
TOOLCHAIN_FORMS: tuple[str, ...] = ("workbench", "compose:<service>", "<image-reference>")

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_VERSION_RE = re.compile(r"^([0-9]+)\.([0-9]+)(?:\.([0-9]+))?$")
_COMPOSE_SERVICE_RE = re.compile(r"^compose:([A-Za-z0-9_.-]+)$")
_IMAGE_REF_RE = re.compile(
    r"^[a-z0-9]([a-z0-9._/-]*[a-z0-9])?(:[A-Za-z0-9._-]+)?(@sha256:[0-9a-f]{64})?$"
)
_DECIMAL_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_PORT_RE = re.compile(r"^[1-9][0-9]*$")
_PROBE_HTTP_RE = re.compile(r"^http:/\S*$")
_PROBE_TCP_PORT_RE = re.compile(r"^tcp:([0-9]+)$")
_MAX_PORT = 65535
_ANSI_ESC = "\x1b"


def parse_contract_version(value: str | None) -> tuple[str, int | None, str | None]:
    """Validate CONTRACT_VERSION value; return (effective_version, major, error_or_none).

    When omitted (``value is None``), defaults to ``CONTRACT_VERSION`` ("1.0")
    for backward compatibility with existing contracts.
    """
    if value is None:
        return CONTRACT_VERSION, CONTRACT_MAJOR, None
    cleaned = value.strip()
    if not cleaned:
        return "", None, "CONTRACT_VERSION is empty; expected 1.0 (major 1)"
    match = _VERSION_RE.fullmatch(cleaned)
    if not match:
        return cleaned, None, f"CONTRACT_VERSION={cleaned!r} is malformed; expected <major>.<minor> (e.g. 1.0)"
    major = int(match.group(1))
    if major != CONTRACT_MAJOR:
        return (
            cleaned,
            major,
            f"CONTRACT_VERSION={cleaned!r} has unsupported major version {major}; supported major is {CONTRACT_MAJOR}",
        )
    return cleaned, major, None


def classify_toolchain_image(value: str) -> str | None:
    """Return 'workbench', 'compose', or 'image' for valid TOOLCHAIN_IMAGE, else None."""
    if not isinstance(value, str) or not value:
        return None
    if value == "workbench":
        return "workbench"
    if _COMPOSE_SERVICE_RE.fullmatch(value):
        return "compose"
    if _IMAGE_REF_RE.fullmatch(value):
        return "image"
    return None


def validate_gate_line(gate_name: str, rc: int, stdout: str) -> str | None:
    """Check the v1.0 one-line gate output contract; return flaw string or None."""
    lines = stdout.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != 1:
        return f"lines={len(lines)}"
    line = lines[0]
    if _ANSI_ESC in line:
        return "ansi"
    go_prefix = f"GO: {gate_name} — "
    if line == f"GO: {gate_name}" or (line.startswith(go_prefix) and len(line) > len(go_prefix)):
        return None if rc == 0 else f"GO,exit={rc}"
    nogo_prefix = f"NO-GO: {gate_name} — "
    if line.startswith(nogo_prefix) and len(line) > len(nogo_prefix):
        return None if rc == 1 else f"NO-GO,exit={rc}"
    return "format"


def parse_workload_env(text: str, *, label: str = "workload.env") -> tuple[dict[str, str], list[str], list[str]]:
    """Parse KEY=VALUE lines strictly; return (values, ordered_keys, errors)."""
    values: dict[str, str] = {}
    keys: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    dupes: set[str] = set()

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            errors.append(f"{label}:{lineno}: not KEY=VALUE: {line!r}")
            continue
        if not _KEY_RE.fullmatch(key):
            errors.append(f"{label}:{lineno}: invalid key name {key!r}")
            continue
        if key in seen:
            dupes.add(key)
        else:
            seen.add(key)
        keys.append(key)
        values[key] = value

    if dupes:
        errors.append(f"{label}: duplicate key(s): {', '.join(sorted(dupes))}")
    return values, keys, errors


def _validate_optional_fields(values: dict[str, str], label: str) -> list[str]:
    errors: list[str] = []
    if "WORKLOAD_HEALTH_PROBE" in values:
        probe = values["WORKLOAD_HEALTH_PROBE"]
        ok_probe = probe == "" or probe == "tcp" or bool(_PROBE_HTTP_RE.fullmatch(probe))
        if not ok_probe:
            m = _PROBE_TCP_PORT_RE.fullmatch(probe)
            ok_probe = bool(m and 1 <= int(m.group(1)) <= _MAX_PORT)
        if not ok_probe:
            errors.append(
                f"{label}: WORKLOAD_HEALTH_PROBE={probe!r} must be empty, http:/<path>, tcp, or tcp:<1..65535>"
            )

    if "WORKLOAD_PORT" in values:
        wport = values["WORKLOAD_PORT"]
        ok_port = wport == "" or (bool(_PORT_RE.fullmatch(wport)) and 1 <= int(wport) <= _MAX_PORT)
        if not ok_port:
            errors.append(f"{label}: WORKLOAD_PORT={wport!r} must be empty or integer 1..{_MAX_PORT}")

    return errors


def validate_workload_env_text(
    text: str,
    *,
    label: str = "workload.env",
    contract_dir: str | os.PathLike[str] | None = None,
    check_compose: bool = False,
) -> dict[str, Any]:
    """Validate workload.env contents against Contract v1.0.

    Returns a structured dictionary with ``valid``, ``contract_version``,
    ``version_explicit``, ``toolchain_form``, ``values``, and ``errors``.
    """
    values, keys, errors = parse_workload_env(text, label=label)
    actual_keys = set(values.keys())

    missing = sorted(set(REQUIRED_WORKLOAD_KEYS) - actual_keys)
    if missing:
        errors.append(f"{label}: missing required key(s): {', '.join(missing)}")

    extra = sorted(actual_keys - ALL_WORKLOAD_KEYS)
    if extra:
        errors.append(f"{label}: unknown key(s) for contract v{CONTRACT_VERSION}: {', '.join(extra)}")

    version_explicit = "CONTRACT_VERSION" in values
    eff_version, major, ver_err = parse_contract_version(values.get("CONTRACT_VERSION"))
    if ver_err:
        errors.append(f"{label}: {ver_err}")

    if "WORKLOAD_TEST_PATHS" in values and not values["WORKLOAD_TEST_PATHS"].strip(":"):
        errors.append(f"{label}: WORKLOAD_TEST_PATHS is empty")

    if "WORKLOAD_MUTATION_SCOPE" in values and not values["WORKLOAD_MUTATION_SCOPE"].strip():
        errors.append(f"{label}: WORKLOAD_MUTATION_SCOPE is empty")

    if "MUTATION_BASELINE" in values:
        baseline = values["MUTATION_BASELINE"]
        if baseline:
            if not _DECIMAL_RE.fullmatch(baseline) or not (0.0 <= float(baseline) <= 1.0):
                errors.append(f"{label}: MUTATION_BASELINE={baseline!r} must be empty or decimal in [0, 1]")

    for cmd_key in RUNNER_CMD_KEYS:
        if cmd_key in values:
            cmd_val = values[cmd_key]
            if not cmd_val:
                errors.append(f"{label}: {cmd_key} is empty")
            elif "$" in cmd_val or "#" in cmd_val:
                errors.append(f"{label}: {cmd_key} contains '$' or '#' forbidden by make include contract")

    toolchain_form = None
    if "TOOLCHAIN_IMAGE" in values:
        tc_val = values["TOOLCHAIN_IMAGE"]
        toolchain_form = classify_toolchain_image(tc_val)
        if toolchain_form is None:
            errors.append(
                f"{label}: TOOLCHAIN_IMAGE={tc_val!r} is none of workbench, compose:<service>, or an image reference"
            )
        elif toolchain_form == "compose" and check_compose and contract_dir is not None:
            compose_path = Path(contract_dir) / "compose.yaml"
            if not compose_path.is_file():
                errors.append(f"{label}: TOOLCHAIN_IMAGE={tc_val} requires {compose_path} (no plane half)")

    errors.extend(_validate_optional_fields(values, label))

    return {
        "valid": len(errors) == 0,
        "contract_version": eff_version or None,
        "contract_major": major,
        "version_explicit": version_explicit,
        "toolchain_form": toolchain_form,
        "keys": keys,
        "values": values,
        "errors": errors,
    }


def validate_workload_contract(
    path_or_dir: str | os.PathLike[str],
    *,
    contract_dir: str | os.PathLike[str] | None = None,
    check_compose: bool = False,
) -> dict[str, Any]:
    """Validate a workload.env file or contract directory against Contract v1.0."""
    target = Path(path_or_dir)
    if target.is_dir():
        env_path = target / "workload.env"
        resolved_contract_dir = Path(contract_dir) if contract_dir is not None else target
    else:
        env_path = target
        resolved_contract_dir = Path(contract_dir) if contract_dir is not None else env_path.parent

    label = env_path.as_posix()
    try:
        text = env_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "valid": False,
            "contract_version": None,
            "contract_major": None,
            "version_explicit": False,
            "toolchain_form": None,
            "keys": [],
            "values": {},
            "errors": [f"{label}: cannot read workload contract: {exc}"],
        }

    return validate_workload_env_text(
        text,
        label=label,
        contract_dir=resolved_contract_dir,
        check_compose=check_compose,
    )
