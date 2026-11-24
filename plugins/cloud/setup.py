#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Stage Terraform + OIDC starter files for a cloud Delivery plugin."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PLUGINS_DIR = Path(__file__).resolve().parent

PROVIDERS: dict[str, tuple[str, ...]] = {
    "gcp-cloud-deploy": ("gcloud", "terraform", "jq", "curl"),
    "aws-ecs-codedeploy": ("aws", "terraform", "jq", "curl"),
    "azure-container-apps": ("az", "terraform", "jq", "curl"),
    "hetzner-k3s": ("kubectl", "hcloud", "terraform", "jq", "curl"),
}


def _stage_templates(plugin_dir: Path, target_dir: Path) -> None:
    """Copy infra/ and ci/ templates from the plugin directory into target_dir."""
    target_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("infra", "ci"):
        src = plugin_dir / sub
        if not src.is_dir():
            raise FileNotFoundError(f"missing template directory: {src}")
        shutil.copytree(src, target_dir / sub, dirs_exist_ok=True)


def _check_clis(clis: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Return (present, missing) tool lists found on PATH."""
    present: list[str] = []
    missing: list[str] = []
    for tool in clis:
        if shutil.which(tool):
            present.append(tool)
        else:
            missing.append(tool)
    return present, missing


def main(argv: list[str] | None = None) -> int:
    """Validate CLOUD, inspect CLI availability, stage infra/ci templates, and report."""
    parser = argparse.ArgumentParser(description="Stage a cloud Delivery plugin starter.")
    parser.add_argument("--cloud", default=os.environ.get("CLOUD", ""), help="Cloud plugin name")
    parser.add_argument(
        "--out",
        default=os.environ.get("CLOUD_SETUP_DIR", ".crew/cloud"),
        help="Output parent directory (default: .crew/cloud)",
    )
    args = parser.parse_args(argv)

    cloud = (args.cloud or "").strip()
    valid = ", ".join(PROVIDERS)
    if not cloud:
        print(f"NO-GO: cloud-setup — CLOUD is required; pass CLOUD=<provider> ({valid})")
        return 1
    if cloud not in PROVIDERS:
        print(f"NO-GO: cloud-setup — unknown CLOUD={cloud!r}; expected one of: {valid}")
        return 1

    plugin_dir = PLUGINS_DIR / cloud
    target_dir = Path(args.out) / cloud
    try:
        _stage_templates(plugin_dir, target_dir)
    except OSError as exc:
        print(f"NO-GO: cloud-setup — failed to stage {cloud}: {exc}")
        return 1

    present, missing = _check_clis(PROVIDERS[cloud])
    print(f"[cloud-setup] provider={cloud} staged={target_dir}", file=sys.stderr)
    print(
        f"[cloud-setup] CLIs present: {', '.join(present) if present else 'none'}; "
        f"missing: {', '.join(missing) if missing else 'none'}",
        file=sys.stderr,
    )
    print("[cloud-setup] next steps (review Terraform + OIDC starter; never commit static keys):", file=sys.stderr)
    print(f"  1. Review {target_dir}/infra/main.tf, variables.tf, outputs.tf and apply with Terraform", file=sys.stderr)
    print(f"  2. Wire keyless OIDC workflow from {target_dir}/ci/github-oidc.yml or gitlab-oidc.yml", file=sys.stderr)
    print(f"  3. Export ADAPTERS_PATH={PLUGINS_DIR} and provider env vars, then run:", file=sys.stderr)
    print(f"     ./go delivery-conformance DELIVERY=outside/{cloud} ADAPTERS_PATH={PLUGINS_DIR}", file=sys.stderr)

    print(
        f"GO: cloud-setup — {cloud} staged in {target_dir}; "
        f"run ./go delivery-conformance DELIVERY=outside/{cloud} ADAPTERS_PATH={PLUGINS_DIR}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
