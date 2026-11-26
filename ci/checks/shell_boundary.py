# SPDX-License-Identifier: Apache-2.0
"""ci-shell-boundary: where shell is allowed in core/, gates/, and ci/.

Enforces docs/adr/0052 §4 (`core/`), docs/adr/0084 §5 (`gates/`),
docs/adr/0131 (zero `core/`/`gates/` debt), and docs/adr/0094 §2 / 0136
(`ci/` ratchet activated):
  - Every non-host `.sh` file under core/, gates/, and ci/ has at most 30
    non-blank, non-comment code lines unless listed in that directory's debt
    table (which may only shrink, never rise).
  - Unlisted file > 30 code lines: NO-GO (move logic to Python).
  - Listed file > its debt ceiling: NO-GO (move logic to Python).
  - Listed file < its debt ceiling: NO-GO (lower its debt entry).
  - Listed file <= 30 code lines, or listed host/debt file missing: NO-GO
    (remove its entry).

Proves all clauses fail on a planted temp tree (covering core/, gates/, and
ci/) before judging the real repository.
Run by ci/shell-boundary.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

LIMIT = 30

CORE_GATES_HOST: tuple[str, ...] = (
    "core/doctor.sh",
    "core/attach.sh",
    "core/preflight.sh",
    "core/progress.sh",
)
CORE_GATES_DEBT: dict[str, int] = {}

# ci/lib.sh is sourced by host-side scripts too; ci/engine-compat.sh runs on the host.
CI_HOST: tuple[str, ...] = (
    "ci/lib.sh",
    "ci/engine-compat.sh",
)
CI_DEBT: dict[str, int] = {
    "ci/agents.sh": 443,
    "ci/assign.sh": 145,
    "ci/attach.sh": 357,
    "ci/codehost.sh": 423,
    "ci/decide.sh": 440,
    "ci/dev-phase.sh": 112,
    "ci/evals.sh": 66,
    "ci/first-run.sh": 191,
    "ci/gates-light.sh": 162,
    "ci/harness-shape.sh": 387,
    "ci/harness.sh": 66,
    "ci/hooks.sh": 305,
    "ci/init.sh": 371,
    "ci/intake.sh": 453,
    "ci/logbook.sh": 2310,
    "ci/mainline.sh": 351,
    "ci/metrics.sh": 352,
    "ci/network.sh": 144,
    "ci/outside.sh": 684,
    "ci/package.sh": 603,
    "ci/perimeter.sh": 92,
    "ci/plugin.sh": 821,
    "ci/postmortem.sh": 733,
    "ci/promote.sh": 398,
    "ci/range.sh": 642,
    "ci/readiness.sh": 429,
    "ci/release.sh": 260,
    "ci/review.sh": 351,
    "ci/sast-diff.sh": 225,
    "ci/screen.sh": 355,
    "ci/store-bus.sh": 198,
    "ci/toolchain.sh": 172,
    "ci/tower.sh": 637,
    "ci/wiki.sh": 271,
    "ci/workloads.sh": 463,
}


def code_lines(path: Path) -> int:
    """Count non-blank, non-comment lines (equivalent to `awk '!/^[[:space:]]*(#|$)/'`)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return sum(1 for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))


def _scan_scope(
    root: Path,
    dirs: tuple[str, ...],
    host: tuple[str, ...] | set[str],
    debt: dict[str, int],
    limit: int = LIMIT,
) -> list[str]:
    host_set = set(host)
    findings: list[str] = []
    seen_files: list[Path] = []
    for d in dirs:
        base = root / d
        if base.is_dir():
            seen_files.extend(p for p in base.rglob("*.sh") if p.is_file())
    for path in sorted(seen_files):
        rel = path.relative_to(root).as_posix()
        if rel in host_set:
            continue
        n = code_lines(path)
        ceiling = debt.get(rel)
        if ceiling is None:
            if n > limit:
                findings.append(f"{rel} has {n} code lines, over {limit}: move its logic to Python")
        elif n <= limit:
            findings.append(f"{rel} has {n} code lines, at most {limit}: remove its debt entry")
        elif n > ceiling:
            findings.append(f"{rel} grew to {n} code lines past its debt of {ceiling}: move logic to Python")
        elif n < ceiling:
            findings.append(f"{rel} is down to {n} code lines: lower its debt entry from {ceiling}")

    for rel in [*host, *debt.keys()]:
        if not (root / rel).is_file():
            findings.append(f"{rel} is listed and gone: remove its entry")
    return findings


def flaws(
    root: Path,
    *,
    core_gates_host: tuple[str, ...] = CORE_GATES_HOST,
    core_gates_debt: dict[str, int] = CORE_GATES_DEBT,
    ci_host: tuple[str, ...] = CI_HOST,
    ci_debt: dict[str, int] = CI_DEBT,
    limit: int = LIMIT,
) -> list[str]:
    """Return sorted shell-boundary ratchet violations across core/, gates/, and ci/."""
    found = _scan_scope(root, ("core", "gates"), core_gates_host, core_gates_debt, limit)
    found.extend(_scan_scope(root, ("ci",), ci_host, ci_debt, limit))
    return sorted(found)


def _write_lines(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f": {i}\n\n# a comment\n" for i in range(count))
    path.write_text(body, encoding="utf-8")


def self_test() -> str | None:
    """Prove every ratchet clause fails on a planted temp tree across core/, gates/, and ci/."""
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp)
        planted = {
            "core/steps/ok.sh": 30,
            "core/new.sh": 31,
            "core/grown.sh": 33,
            "core/shrunk.sh": 31,
            "core/paid.sh": 5,
            "core/host.sh": 90,
            "gates/ok.sh": 30,
            "gates/new.sh": 31,
            "gates/suite/grown.sh": 40,
            "gates/shrunk.sh": 38,
            "ci/ok.sh": 30,
            "ci/sub/ok.sh": 30,
            "ci/host.sh": 112,
            "ci/new.sh": 31,
            "ci/grown.sh": 41,
            "ci/shrunk.sh": 39,
            "ci/paid.sh": 12,
        }
        for rel, n in planted.items():
            _write_lines(probe / rel, n)

        got = flaws(
            probe,
            core_gates_host=("core/host.sh", "core/gone_host.sh"),
            core_gates_debt={
                "core/grown.sh": 32,
                "core/shrunk.sh": 32,
                "core/paid.sh": 40,
                "core/gone.sh": 50,
                "gates/suite/grown.sh": 39,
                "gates/shrunk.sh": 39,
            },
            ci_host=("ci/host.sh", "ci/gone_host.sh"),
            ci_debt={
                "ci/grown.sh": 40,
                "ci/shrunk.sh": 40,
                "ci/paid.sh": 45,
                "ci/gone.sh": 50,
            },
        )
        want = sorted(
            [
                "ci/gone.sh is listed and gone: remove its entry",
                "ci/gone_host.sh is listed and gone: remove its entry",
                "ci/grown.sh grew to 41 code lines past its debt of 40: move logic to Python",
                "ci/new.sh has 31 code lines, over 30: move its logic to Python",
                "ci/paid.sh has 12 code lines, at most 30: remove its debt entry",
                "ci/shrunk.sh is down to 39 code lines: lower its debt entry from 40",
                "core/gone.sh is listed and gone: remove its entry",
                "core/gone_host.sh is listed and gone: remove its entry",
                "core/grown.sh grew to 33 code lines past its debt of 32: move logic to Python",
                "core/new.sh has 31 code lines, over 30: move its logic to Python",
                "core/paid.sh has 5 code lines, at most 30: remove its debt entry",
                "core/shrunk.sh is down to 31 code lines: lower its debt entry from 32",
                "gates/new.sh has 31 code lines, over 30: move its logic to Python",
                "gates/shrunk.sh is down to 38 code lines: lower its debt entry from 39",
                "gates/suite/grown.sh grew to 40 code lines past its debt of 39: move logic to Python",
            ]
        )
        if got != want:
            return "\n".join(["got:", *got, "want:", *want])
    return None


def judge(root: Path | None = None) -> tuple[str, bool]:
    """Run planted-tree self-test and check real repo shell-boundary compliance."""
    repo_root = Path(root) if root is not None else Path.cwd()
    st_err = self_test()
    if st_err is not None:
        return (
            "the check does not catch what docs/adr/0052 §4, docs/adr/0084 §5, and docs/adr/0094 §2 say it does\n"
            + st_err,
            False,
        )

    found = flaws(repo_root)
    if found:
        head = f"{found[0]} (and more, see stderr)" if len(found) > 1 else found[0]
        return "\n".join([head, *found]), False
    return "", True


def main() -> int:
    text, ok = judge()
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
