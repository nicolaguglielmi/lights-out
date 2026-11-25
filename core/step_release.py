# SPDX-License-Identifier: Apache-2.0
"""Release tower step: publish merge commit and drive launchpad release."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

try:
    from . import cmd_util
except ImportError:
    import cmd_util

_STEP = "step-release"


def _publish_merge(
    workload: str,
    merge_sha: str,
    canary_tag: str,
    cur_env: dict[str, str],
) -> int:
    """Create a throwaway worktree at *merge_sha* and run ``make publish``."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "merge"
        add_proc = cmd_util.git_cmd(
            workload, "worktree", "add", "-q", "--detach", str(src), merge_sha,
            env=cur_env, stdout_to_stderr=True,
        )
        if add_proc.returncode != 0:
            cmd_util.fail_msg(_STEP, f"could not check out {merge_sha}")

        try:
            sys.stderr.write(
                f"[release] publishing {canary_tag}, built from the merge {merge_sha} (docs/adr/0060 §5)\n"
            )
            sys.stderr.flush()

            pub_env = dict(cur_env)
            for key in ("APP_TAG", "CANARY_TAG", "CANARY_IMAGE"):
                pub_env.pop(key, None)

            if cur_env.get("WORKLOAD_ATTACHED"):
                return cmd_util.run_cmd(
                    [
                        "make", "--no-print-directory", "publish",
                        f"TAG={canary_tag}", f"WORKLOAD={src}",
                    ],
                    env=pub_env,
                    suppress_stdout=True,
                ).returncode

            inside_proc = cmd_util.git_cmd(
                workload, "rev-parse", "--show-prefix", env=cur_env, capture_stdout=True
            )
            inside = inside_proc.stdout.rstrip("\r\n").rstrip("/")
            head_sha = cmd_util.git_cmd(
                workload, "rev-parse", "HEAD", env=cur_env, capture_stdout=True
            ).stdout.rstrip("\r\n")

            if cmd_util.git_cmd(
                src,
                "restore",
                f"--source={head_sha}",
                "--",
                "Makefile",
                "compose.yaml",
                "profiles",
                "adapters",
                env=cur_env,
            ).returncode != 0:
                return 1

            pub_env["COSIGN_KEY"] = f"{os.getcwd()}/.crew/cosign.key"
            return cmd_util.run_cmd(
                [
                    "make", "-C", str(src), "--no-print-directory", "publish",
                    f"TAG={canary_tag}", f"WORKLOAD={inside}",
                ],
                env=pub_env,
                suppress_stdout=True,
            ).returncode
        finally:
            cmd_util.git_cmd(
                workload, "worktree", "remove", "--force", str(src),
                env=cur_env, suppress_stdout=True, suppress_stderr=True,
            )


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    """Publish merge commit, run launchpad release with live logging, and emit step JSON."""
    _ = argv
    cur_env = dict(os.environ if env is None else env)
    ticket = cmd_util.need_env(cur_env, "TICKET", "TICKET not set")
    canary_version = cmd_util.need_env(cur_env, "CANARY_VERSION", "CANARY_VERSION not set")
    doc_file = cmd_util.need_env(
        cur_env, "DOC_FILE", "DOC_FILE not set; run this via core/tower.sh, not directly"
    )
    code_host = cmd_util.need_env(
        cur_env, "CODE_HOST", "CODE_HOST not set; run this via ./go, not directly"
    )
    release_record = cur_env.get("RELEASE_RECORD") or ".crew/release.json"

    doc = cmd_util.read_json(doc_file)
    merge_sha = ""
    if isinstance(doc, dict):
        mainline = doc.get("mainline")
        merge = doc.get("merge")
        if isinstance(mainline, dict) and mainline.get("green"):
            merge_sha = str(mainline["green"])
        elif isinstance(merge, dict) and merge.get("sha"):
            merge_sha = str(merge["sha"])
    if not merge_sha:
        cmd_util.fail_msg(_STEP, f"{doc_file} has no merge.sha")

    canary_tag = f"sha-{merge_sha}"
    workload = cmd_util.need_env(
        cur_env, "WORKLOAD", "WORKLOAD not set; run this via ./go, not directly"
    )
    clone_proc = cmd_util.run_sh(
        Path("adapters") / code_host / "clone-url.sh", env=cur_env, capture_stdout=True
    )
    if clone_proc.returncode != 0:
        cmd_util.fail_msg(_STEP, f"could not fetch the default branch from {code_host}")
    clone_url = clone_proc.stdout.rstrip("\r\n")

    if cmd_util.git_cmd(workload, "fetch", "-q", clone_url, "HEAD", env=cur_env).returncode != 0:
        cmd_util.fail_msg(_STEP, f"could not fetch the default branch from {code_host}")
    if cmd_util.git_cmd(
        workload, "merge-base", "--is-ancestor", merge_sha, "FETCH_HEAD",
        env=cur_env, suppress_stderr=True,
    ).returncode != 0:
        cmd_util.fail_msg(_STEP, f"merge {merge_sha} is not on the default branch of {code_host}")

    if _publish_merge(workload, merge_sha, canary_tag, cur_env) != 0:
        cmd_util.fail_msg(_STEP, f"publish of {canary_tag} failed; see above")

    log_path = Path(".crew") / ticket / "release.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    record_path = Path(release_record)
    record_path.unlink(missing_ok=True)

    rel_env = dict(cur_env)
    rel_env["RELEASE_TICKET"] = ticket
    rc, verdict_raw = cmd_util.run_tee(
        [
            "make", "--no-print-directory", "release",
            f"CANARY_VERSION={canary_version}", f"CANARY_TAG={canary_tag}",
        ],
        log_path,
        env=rel_env,
    )
    verdict = verdict_raw.rstrip("\r\n")
    if not verdict:
        cmd_util.fail_msg(_STEP, f"release printed no verdict; see {log_path}")

    recorded = ""
    rec_data: Any = None
    if record_path.is_file():
        try:
            rec_data = cmd_util.read_json(record_path)
            if isinstance(rec_data, dict) and rec_data.get("verdict"):
                recorded = str(rec_data["verdict"])
        except (OSError, ValueError):
            rec_data = None

    if rc == 0:
        state = "held"
    elif recorded == "rolled-back":
        state = "rolled-back"
    else:
        cmd_util.fail_msg(
            _STEP, f"release did not reach a verdict stage ({verdict}); see {log_path}"
        )

    numbers = [
        ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.startswith("slo: ")
    ]
    if isinstance(rec_data, dict):
        rec_fields: dict[str, Any] = {
            "image": rec_data.get("image") or "",
            "sbom": rec_data.get("sbom", None),
            "dast": rec_data.get("dast", None),
        }
    else:
        rec_fields = {"image": ""}

    release_obj: dict[str, Any] = {
        "version": canary_version,
        "verdict": verdict,
        "numbers": numbers,
        **rec_fields,
    }
    payload = {"state": state, "release": release_obj}
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
