# SPDX-License-Identifier: Apache-2.0
"""Run WORKLOAD_MUTATION_SCOPE baseline once in the configured toolchain."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Sequence

try:
    from . import cmd_util
except ImportError:
    import cmd_util

_GATE = "mutation-baseline"


def _fail(msg: str) -> None:
    cmd_util.fail_msg(_GATE, msg)


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    """Validate env, execute MUTATION_CMD over the full scope, and emit summary + JSON."""
    _ = argv
    cur_env = dict(os.environ if env is None else env)
    cmd_util.need_env(cur_env, "WORKLOAD", "WORKLOAD not set; run this via ./go, not directly")
    mutation_cmd = cur_env.get("MUTATION_CMD", "")
    if not mutation_cmd:
        _fail("MUTATION_CMD is empty (docs/adr/0042 §1)")
    scope = cur_env.get("WORKLOAD_MUTATION_SCOPE", "")
    if not scope:
        _fail("WORKLOAD_MUTATION_SCOPE is empty")

    pre = cmd_util.run_sh("core/toolchain-run.sh", "--preflight", env=cur_env, capture_stdout=True)
    where = pre.stdout.strip()
    if pre.returncode != 0:
        cmd_util.nogo(_GATE, where or "the toolchain preflight failed; see stderr")

    sys.stderr.write(f"mutation-baseline: {mutation_cmd} over {scope}, in {where}\n")
    sys.stderr.flush()

    run_env = dict(cur_env)
    run_env["MUTATION_LINES"] = cur_env.get("MUTATION_LINES") or "all"

    t0 = int(time.time())
    proc = cmd_util.run_sh("core/toolchain-run.sh", mutation_cmd, env=run_env, capture_stdout=True)
    elapsed = int(time.time()) - t0

    if proc.returncode != 0:
        _fail(f"in {where}: MUTATION_CMD failed (exit {proc.returncode}); nothing measured")

    non_empty = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    last = non_empty[-1] if non_empty else ""

    bad_line = (
        f"in {where}: the run did not report total, killed and a numeric score "
        f"on its last stdout line: {last}; nothing measured"
    )
    try:
        data = json.loads(last)
    except ValueError:
        _fail(bad_line)
    if not isinstance(data, dict):
        _fail(bad_line)

    total = data.get("total")
    killed = data.get("killed")
    score = data.get("score")
    if (
        total is None
        or isinstance(total, bool)
        or not isinstance(total, (int, float))
        or killed is None
        or killed is False
        or not isinstance(score, (int, float))
        or isinstance(score, bool)
    ):
        _fail(bad_line)

    if total <= 0:
        _fail(f"in {where}: 0 mutants in {scope}; nothing measured")

    sys.stdout.write(f"{last}\n")
    sys.stdout.flush()
    sys.stderr.write(
        f"mutation-baseline: {killed}/{total} killed, score={score}, {elapsed}s wall time, in {where}\n"
    )
    sys.stderr.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
