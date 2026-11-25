# SPDX-License-Identifier: Apache-2.0
"""ci-contract-v1: Contract v1.0 freeze validation (docs/adr/0138).

Proves Contract v1.0 enforcement catches planted defects (missing required
keys, extra unknown keys, duplicate keys, unsupported CONTRACT_VERSION=2.0 /
0.9 / malformed, invalid TOOLCHAIN_IMAGE forms, missing compose.yaml for
compose:<service>, invalid runner commands, invalid WORKLOAD_PORT /
WORKLOAD_HEALTH_PROBE / MUTATION_BASELINE, and broken 1-line gate output)
BEFORE validating the repository's own `.lights-out/` and workload fixture
contracts.

Run by ci/contract-v1.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

try:
    from core import contract_spec
except ImportError:  # pragma: no cover - fallback when executed directly
    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    from core import contract_spec

_BASE_CONTRACT_LINES = (
    "CANARY_VERSION=0.2.0-canary",
    "MUTATION_BASELINE=0.80",
    "WORKLOAD_KILL_SWITCH=canary-switch",
    "WORKLOAD_SAMPLE_PATH=/health",
    "WORKLOAD_LOCKFILES=uv.lock",
    "WORKLOAD_MUTATION_SCOPE=src/",
    "WORKLOAD_TEST_ROOT=",
    "WORKLOAD_TEST_PATHS=tests/test_*.py",
    "TOOLCHAIN_IMAGE=workbench",
    "TEST_CMD=pytest -q",
    "SAST_CMD=bandit -r src -q",
    "MUTATION_CMD=sh .lights-out/runners/mutation.sh src",
)


def _render_contract(
    overrides: dict[str, str | None] | None = None,
    extra_lines: tuple[str, ...] = (),
) -> str:
    entries: list[tuple[str, str]] = []
    for raw in _BASE_CONTRACT_LINES:
        key, _, val = raw.partition("=")
        if overrides and key in overrides:
            replacement = overrides[key]
            if replacement is None:
                continue
            entries.append((key, replacement))
        else:
            entries.append((key, val))
    if overrides:
        for key, val in overrides.items():
            if val is not None and not any(k == key for k, _ in entries):
                entries.append((key, val))
    lines = [f"{k}={v}" for k, v in entries]
    lines.extend(extra_lines)
    return "\n".join(lines) + "\n"


def self_test() -> str | None:
    """Prove Contract v1.0 catches every planted defect and accepts valid v1.0 shapes."""
    if contract_spec.CONTRACT_VERSION != "1.0":
        return f"expected CONTRACT_VERSION=='1.0', got {contract_spec.CONTRACT_VERSION!r}"
    if len(contract_spec.REQUIRED_WORKLOAD_KEYS) != 12:
        return f"expected 12 required keys, got {len(contract_spec.REQUIRED_WORKLOAD_KEYS)}"

    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp)

        # Positive 1: legacy v1.0 contract omitting CONTRACT_VERSION defaults to "1.0".
        omitted_path = probe / "omitted" / "workload.env"
        omitted_path.parent.mkdir(parents=True)
        omitted_path.write_text(_render_contract(), encoding="utf-8")
        res_omitted = contract_spec.validate_workload_contract(omitted_path, check_compose=True)
        if not res_omitted["valid"] or res_omitted["contract_version"] != "1.0" or res_omitted["version_explicit"]:
            return f"self-test failed: omitted CONTRACT_VERSION rejected: {res_omitted}"

        # Positive 2: explicit CONTRACT_VERSION=1.0 + optional keys + image digest.
        explicit_path = probe / "explicit" / "workload.env"
        explicit_path.parent.mkdir(parents=True)
        explicit_path.write_text(
            _render_contract(
                {
                    "CONTRACT_VERSION": "1.0",
                    "WORKLOAD_PORT": "8080",
                    "WORKLOAD_HEALTH_PROBE": "http:/healthz",
                    "TOOLCHAIN_IMAGE": "docker.io/library/python:3.13@sha256:" + ("a" * 64),
                }
            ),
            encoding="utf-8",
        )
        res_explicit = contract_spec.validate_workload_contract(explicit_path, check_compose=True)
        if not res_explicit["valid"] or res_explicit["contract_version"] != "1.0" or not res_explicit["version_explicit"]:
            return f"self-test failed: explicit v1.0 contract rejected: {res_explicit}"

        # Positive 3: compose:<service> with compose.yaml present.
        compose_dir = probe / "compose_ok"
        compose_dir.mkdir(parents=True)
        (compose_dir / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        (compose_dir / "workload.env").write_text(
            _render_contract({"TOOLCHAIN_IMAGE": "compose:backend-test"}),
            encoding="utf-8",
        )
        res_compose = contract_spec.validate_workload_contract(compose_dir, check_compose=True)
        if not res_compose["valid"] or res_compose["toolchain_form"] != "compose":
            return f"self-test failed: compose:<service> with compose.yaml rejected: {res_compose}"

        # Planted negative cases: every defect must be refused with a matching message.
        planted_cases: list[tuple[str, Path, str, str]] = []

        missing_req = probe / "missing_req" / "workload.env"
        missing_req.parent.mkdir(parents=True)
        planted_cases.append(("missing required key", missing_req, _render_contract({"TEST_CMD": None}), "missing required key"))

        missing_paths = probe / "missing_paths" / "workload.env"
        missing_paths.parent.mkdir(parents=True)
        planted_cases.append(
            ("empty WORKLOAD_TEST_PATHS", missing_paths, _render_contract({"WORKLOAD_TEST_PATHS": ""}), "WORKLOAD_TEST_PATHS")
        )

        extra_key = probe / "extra_key" / "workload.env"
        extra_key.parent.mkdir(parents=True)
        planted_cases.append(
            ("unknown extra key", extra_key, _render_contract(extra_lines=("UNEXPECTED_V2_KEY=1",)), "unknown key")
        )

        dupe_key = probe / "dupe_key" / "workload.env"
        dupe_key.parent.mkdir(parents=True)
        planted_cases.append(
            ("duplicate key", dupe_key, _render_contract(extra_lines=("TEST_CMD=pytest",)), "duplicate key")
        )

        bad_ver_2 = probe / "bad_ver_2" / "workload.env"
        bad_ver_2.parent.mkdir(parents=True)
        planted_cases.append(
            ("CONTRACT_VERSION=2.0", bad_ver_2, _render_contract({"CONTRACT_VERSION": "2.0"}), "unsupported major version 2")
        )

        bad_ver_0 = probe / "bad_ver_0" / "workload.env"
        bad_ver_0.parent.mkdir(parents=True)
        planted_cases.append(
            ("CONTRACT_VERSION=0.9", bad_ver_0, _render_contract({"CONTRACT_VERSION": "0.9"}), "unsupported major version 0")
        )

        bad_ver_str = probe / "bad_ver_str" / "workload.env"
        bad_ver_str.parent.mkdir(parents=True)
        planted_cases.append(
            ("CONTRACT_VERSION=invalid", bad_ver_str, _render_contract({"CONTRACT_VERSION": "invalid"}), "malformed")
        )

        bad_ver_empty = probe / "bad_ver_empty" / "workload.env"
        bad_ver_empty.parent.mkdir(parents=True)
        planted_cases.append(
            ("empty CONTRACT_VERSION", bad_ver_empty, _render_contract({"CONTRACT_VERSION": ""}), "CONTRACT_VERSION is empty")
        )

        bad_tc_invalid = probe / "bad_tc_invalid" / "workload.env"
        bad_tc_invalid.parent.mkdir(parents=True)
        planted_cases.append(
            ("invalid TOOLCHAIN_IMAGE", bad_tc_invalid, _render_contract({"TOOLCHAIN_IMAGE": "Not An Image"}), "TOOLCHAIN_IMAGE")
        )

        bad_tc_compose = probe / "bad_tc_compose" / "workload.env"
        bad_tc_compose.parent.mkdir(parents=True)
        planted_cases.append(
            ("compose: without service", bad_tc_compose, _render_contract({"TOOLCHAIN_IMAGE": "compose:"}), "TOOLCHAIN_IMAGE")
        )

        bad_tc_nocompose = probe / "bad_tc_nocompose" / "workload.env"
        bad_tc_nocompose.parent.mkdir(parents=True)
        planted_cases.append(
            (
                "compose: without compose.yaml",
                bad_tc_nocompose,
                _render_contract({"TOOLCHAIN_IMAGE": "compose:backend-test"}),
                "requires",
            )
        )

        bad_cmd_dollar = probe / "bad_cmd_dollar" / "workload.env"
        bad_cmd_dollar.parent.mkdir(parents=True)
        planted_cases.append(
            ("runner command with $", bad_cmd_dollar, _render_contract({"SAST_CMD": "bandit $TARGET"}), "SAST_CMD")
        )

        bad_port = probe / "bad_port" / "workload.env"
        bad_port.parent.mkdir(parents=True)
        planted_cases.append(
            ("invalid WORKLOAD_PORT", bad_port, _render_contract({"WORKLOAD_PORT": "65536"}), "WORKLOAD_PORT")
        )

        bad_probe = probe / "bad_probe" / "workload.env"
        bad_probe.parent.mkdir(parents=True)
        planted_cases.append(
            ("invalid WORKLOAD_HEALTH_PROBE", bad_probe, _render_contract({"WORKLOAD_HEALTH_PROBE": "ftp:/x"}), "WORKLOAD_HEALTH_PROBE")
        )

        bad_baseline = probe / "bad_baseline" / "workload.env"
        bad_baseline.parent.mkdir(parents=True)
        planted_cases.append(
            ("invalid MUTATION_BASELINE", bad_baseline, _render_contract({"MUTATION_BASELINE": "1.2"}), "MUTATION_BASELINE")
        )

        for label, path, text, needle in planted_cases:
            path.write_text(text, encoding="utf-8")
            got = contract_spec.validate_workload_contract(path, check_compose=True)
            if got["valid"]:
                return f"self-test failed: planted defect '{label}' was accepted"
            joined = "; ".join(got["errors"])
            if needle not in joined:
                return f"self-test failed: planted defect '{label}' gave {joined!r}, expected {needle!r}"

    # Verify frozen 1-line gate output checks catch violations.
    if contract_spec.validate_gate_line("contract-v1", 0, "GO: contract-v1 — ok\n") is not None:
        return "self-test failed: valid GO line rejected"
    if contract_spec.validate_gate_line("contract-v1", 1, "NO-GO: contract-v1 — bad\n") is not None:
        return "self-test failed: valid NO-GO line rejected"
    for gate_case, (rc, out, want) in {
        "two-lines": (0, "GO: x\nGO: x\n", "lines=2"),
        "ansi": (0, "GO: x — \x1b[31mred\n", "ansi"),
        "go-exit-1": (1, "GO: x\n", "GO,exit=1"),
        "nogo-exit-0": (0, "NO-GO: x — bad\n", "NO-GO,exit=0"),
        "wrong-name": (0, "GO: other\n", "format"),
    }.items():
        flaw = contract_spec.validate_gate_line("x", rc, out)
        if flaw != want:
            return f"self-test failed: gate output case {gate_case} gave {flaw!r}, want {want!r}"

    return None


def discover_repo_contracts(repo_root: Path) -> list[Path]:
    """Return canonical repo and fixture workload.env paths in deterministic order."""
    found: list[Path] = []
    seen: set[Path] = set()
    candidates = [repo_root / ".lights-out" / "workload.env"]
    candidates.extend(sorted(repo_root.glob("*/workload.env")))
    candidates.extend(sorted(repo_root.glob("workloads/*/.lights-out/workload.env")))
    for path in candidates:
        if path.is_file():
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(path)
    return found


def judge(root: Path | None = None) -> tuple[str, bool]:
    """Run planted-failure self-test, then validate all repo/fixture contracts."""
    st_err = self_test()
    if st_err is not None:
        return st_err, False

    repo_root = Path(root) if root is not None else Path.cwd()
    contracts = discover_repo_contracts(repo_root)
    if not contracts:
        return f"no workload.env contracts found under {repo_root}", False

    failures: list[str] = []
    for path in contracts:
        res = contract_spec.validate_workload_contract(path, check_compose=True)
        if not res["valid"]:
            rel = path.relative_to(repo_root).as_posix()
            failures.extend(f"{rel}: {err}" for err in res["errors"])

    if failures:
        return "\n".join([f"{len(failures)} contract defect(s) under v{contract_spec.CONTRACT_VERSION}", *failures]), False

    return f"v{contract_spec.CONTRACT_VERSION} frozen, {len(contracts)}/{len(contracts)} contracts valid", True


def main(argv: list[str] | None = None) -> int:
    _ = argv
    text, ok = judge()
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
