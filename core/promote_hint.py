"""Post-promotion stderr guidance for core/promote.sh (ADR 0108).

Stdlib only; strictly vendor-free, app-free, and model-free. Writes strictly
to stderr so core/promote.sh keeps its exact one-line stdout verdict contract.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _read_record(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def format_hints(version: str = "", env: dict[str, str] | None = None) -> list[str]:
    """Return concise post-promotion stderr guidance lines for the active delivery target."""
    cfg = os.environ if env is None else env
    rec = _read_record(cfg.get("RELEASE_RECORD") or ".crew/release.json")
    ver = version or str(rec.get("version") or "canary")
    stable = str(rec.get("stable") or "previous stable")
    delivery = cfg.get("DELIVERY") or ""
    level = (cfg.get("LEVEL") or cfg.get("PROFILE") or "l0").lower()
    attached = bool((cfg.get("WORKLOAD_ATTACHED") or "").strip())

    lines = [f"[go-for-launch] {ver} at 100% of traffic ({delivery})"]
    is_outside = delivery.startswith("outside/")
    adapter_dir = Path("adapters") / delivery if delivery and not is_outside else None
    has_slo = bool(adapter_dir and (adapter_dir / "slo.promql").is_file())
    has_ready = bool(adapter_dir and (adapter_dir / "ready.sh").is_file())

    if not is_outside and has_ready and not has_slo and level in ("l0", "l1"):
        lines.append(
            f"[go-for-launch] note: local edge routing now sends 100% of traffic to canary {ver}; "
            f"the stable slot ({stable}) stays unchanged until your next stable build/publish."
        )
    else:
        lines.append(
            f"[go-for-launch] note: canary {ver} promoted to 100% on {delivery or 'active delivery'} "
            f"(prior stable: {stable}); verify your target rollout state."
        )
    if attached:
        lines.append(
            f"[go-for-launch] attached repository: rebuild and publish a new stable image/tag from main "
            f"(`./go up` locally or your release pipeline) so future rollbacks return to {ver}."
        )
    lines.append(f"[go-for-launch] next: ./go evidence VERSION={ver} && ./go logbook")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    version = args[0] if args else ""
    for line in format_hints(version=version):
        sys.stderr.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
