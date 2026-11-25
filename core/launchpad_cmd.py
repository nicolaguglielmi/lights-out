# SPDX-License-Identifier: Apache-2.0
"""Vendor-free release controller orchestrating verification, staging, 5%, and 25%."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

try:
    from . import cmd_util
except ImportError:
    import cmd_util

_GATE = "launchpad"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(
    argv: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """Run staging, 5%, and 25% canary progression, holding at 25% or rolling back."""
    _ = argv
    cur_env = dict(os.environ if env is None else env)
    canary_version = cmd_util.need_env(cur_env, "CANARY_VERSION", "CANARY_VERSION not set")
    canary_tag = cmd_util.need_env(cur_env, "CANARY_TAG", "CANARY_TAG not set")
    delivery_name = cmd_util.need_env(
        cur_env, "DELIVERY", "DELIVERY not set; run this via ./go, not directly"
    )
    metrics_name = cmd_util.need_env(
        cur_env, "METRICS", "METRICS not set; run this via ./go, not directly"
    )
    registry_name = cmd_util.need_env(
        cur_env, "REGISTRY", "REGISTRY not set; run this via ./go, not directly"
    )
    flags_name = cmd_util.need_env(
        cur_env, "FLAGS", "FLAGS not set; run this via ./go, not directly"
    )

    stage_seconds = cur_env.get("STAGE_SECONDS") or "60"
    plane_host = cur_env.get("PLANE_HOST") or "host.containers.internal"
    app_url = cur_env.get("APP_URL") or f"http://{plane_host}:8080"
    release_record = cur_env.get("RELEASE_RECORD") or ".crew/release.json"
    dast_on = cur_env.get("DAST") or "off"

    delivery_dir = Path("adapters") / delivery_name
    cur_env["STAGE_SECONDS"] = stage_seconds
    cur_env["PLANE_HOST"] = plane_host
    cur_env["APP_URL"] = app_url
    cur_env["RELEASE_RECORD"] = release_record
    cur_env["METRICS_QUERY"] = f"adapters/{metrics_name}/query.sh"
    cur_env["CANARY_VERSION"] = canary_version

    stages = "[]"
    image = ""
    dast = ""

    def rollback(*, stdout_to_stderr: bool = False) -> None:
        rc = cmd_util.run_sh(
            delivery_dir / "rollback.sh", env=cur_env, stdout_to_stderr=stdout_to_stderr
        ).returncode
        if rc != 0:
            raise SystemExit(rc)

    def record(verdict: str, held_at: str = "", reason: str = "") -> None:
        sbom = ""
        if image:
            sbom_proc = cmd_util.run_sh(
                Path("adapters") / registry_name / "sbom.sh",
                image,
                env=cur_env,
                capture_stdout=True,
                suppress_stderr=True,
            )
            if sbom_proc.returncode == 0:
                sbom = sbom_proc.stdout.rstrip("\r\n")
        rec_env = dict(cur_env)
        rec_env["RELEASE_REASON"] = reason
        rec_proc = cmd_util.run_py(
            "core/releases.py",
            "record",
            verdict,
            held_at,
            stages,
            image,
            sbom,
            dast,
            env=rec_env,
        )
        if rec_proc.returncode != 0:
            raise SystemExit(rec_proc.returncode)

    def fail_stage(stage: str, reason: str) -> None:
        rollback(stdout_to_stderr=True)
        record("rolled-back", "", f"{stage}: {reason}")
        cmd_util.nogo(_GATE, f"{stage}: {reason} → rolled back")

    def check_frozen(stage: str | None = None) -> None:
        freeze_proc = cmd_util.run_sh(
            Path("adapters") / flags_name / "evaluate.sh",
            "release-freeze",
            env=cur_env,
            capture_stdout=True,
        )
        freeze = freeze_proc.stdout.rstrip("\r\n") if freeze_proc.returncode == 0 else ""
        if freeze == "false":
            return
        if freeze == "true":
            reason = "release-freeze is on"
        else:
            reason = "cannot read release-freeze from the Flags port; refusing to release"
        if stage is None:
            cmd_util.nogo(_GATE, reason)
        fail_stage(stage, reason)

    rollback()

    status_proc = cmd_util.run_sh(
        delivery_dir / "status.sh", env=cur_env, capture_stdout=True
    )
    if status_proc.returncode != 0:
        cmd_util.nogo(_GATE, f"could not read status from {delivery_dir}/status.sh; run ./go up")

    try:
        status_data = json.loads(status_proc.stdout)
    except ValueError:
        status_data = None
    stable_val = status_data.get("stable") if isinstance(status_data, dict) else None
    if stable_val is None or stable_val is False:
        cmd_util.nogo(_GATE, f"{delivery_dir}/status.sh answered without a stable version")
    stable_version = str(stable_val)
    cur_env["STABLE_VERSION"] = stable_version

    check_frozen()

    verify_proc = cmd_util.run_sh(
        Path("adapters") / registry_name / "verify.sh",
        canary_tag,
        env=cur_env,
        capture_stdout=True,
    )
    if verify_proc.returncode != 0:
        cmd_util.nogo(_GATE, "unsigned image")
    image = verify_proc.stdout.rstrip("\r\n")

    if dast_on == "on" and not (delivery_dir / "canary-url.sh").is_file():
        cmd_util.nogo(
            _GATE,
            f"DAST=on, and DELIVERY={delivery_name} has no canary-url.sh to reach the canary alone",
        )

    record("in-progress")
    sys.stderr.write(
        f"[launchpad] staging: deploying {canary_version} at {image} (stable is {stable_version})\n"
    )
    sys.stderr.flush()
    if cmd_util.run_sh(
        delivery_dir / "deploy.sh", canary_version, image, env=cur_env, stdout_to_stderr=True
    ).returncode != 0:
        fail_stage("staging", "deploy did not become healthy")

    if dast_on == "on":
        dast_proc = cmd_util.run_sh("verify/dast.sh", env=cur_env, capture_stdout=True)
        dast = dast_proc.stdout.rstrip("\r\n")
        if dast_proc.returncode != 0:
            fail_stage("staging", cmd_util.strip_prefix(dast, "NO-GO: dast — "))

    for pct in ("5", "25"):
        check_frozen(f"{pct}%")

        if cmd_util.run_sh(
            delivery_dir / "shift.sh", pct, env=cur_env, stdout_to_stderr=True
        ).returncode != 0:
            fail_stage(f"{pct}%", "shift did not take")

        started_at = _utc_now()
        running_proc = cmd_util.run_py(
            "core/releases.py",
            "running",
            stages,
            pct,
            started_at,
            stage_seconds,
            image,
            dast,
            env=cur_env,
        )
        if running_proc.returncode != 0:
            raise SystemExit(running_proc.returncode)

        sleep_fn(float(stage_seconds))

        slo_proc = cmd_util.run_sh(
            "verify/slo.sh", env=cur_env, capture_stdout=True, capture_stderr=True
        )
        slo_out = slo_proc.stdout.rstrip("\r\n")
        slo_err = slo_proc.stderr
        if slo_err:
            sys.stderr.write(slo_err)
            sys.stderr.flush()

        stage_proc = cmd_util.run_py(
            "core/releases.py",
            "stage",
            stages,
            pct,
            slo_out,
            started_at,
            env=cur_env,
            input_text=slo_err,
            capture_stdout=True,
        )
        if stage_proc.returncode != 0:
            raise SystemExit(stage_proc.returncode)
        stages = stage_proc.stdout.rstrip("\r\n")

        if slo_proc.returncode != 0:
            fail_stage(f"{pct}%", cmd_util.strip_prefix(slo_out, "NO-GO: slo — "))
        sys.stderr.write(f"[launchpad] {pct}%: GO\n")
        sys.stderr.flush()

    record("held", "25")
    sys.stderr.write(
        f"[launchpad] 25%: GO — held; ./go promote runs go-for-launch (record at {release_record})\n"
    )
    sys.stderr.flush()
    return cmd_util.go(_GATE)


if __name__ == "__main__":
    raise SystemExit(main())
