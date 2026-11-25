# SPDX-License-Identifier: Apache-2.0
"""Configure .crew/egress/allow and wait until the egress proxy is ready."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Sequence

try:
    from . import cmd_util
except ImportError:
    import cmd_util

_VALID_HOST_CHARS = re.compile(r"^[a-z0-9.:-]+$")


def _fail(msg: str) -> None:
    cmd_util.fail_msg("egress", msg)


def resolve_hosts(raw_hosts: str, env: dict[str, str]) -> list[str]:
    """Resolve and validate allowlist entries from *raw_hosts*, MODEL_BASE_URL, and crew-egress.txt."""
    hosts = raw_hosts
    model_url = env.get("MODEL_BASE_URL", "")
    if model_url:
        scheme, _, rest = model_url.partition("://")
        if not rest:
            scheme = model_url
            rest = model_url
        authority = rest.split("/", 1)[0]
        if ":" in authority:
            hosts = authority
        elif scheme == "https":
            hosts = f"{authority}:443"
        else:
            hosts = f"{authority}:80"

    contract = env.get("WORKLOAD_CONTRACT", "")
    if contract:
        egress_file = Path(contract) / "crew-egress.txt"
        if egress_file.is_file():
            extra_lines = [
                ln for ln in egress_file.read_text(encoding="utf-8").splitlines()
                if not ln.lstrip().startswith("#")
            ]
            extra = " ".join(" ".join(extra_lines).split())
            hosts = f"{hosts} {extra}"

    entries: list[str] = []
    for entry in hosts.split():
        if (
            not _VALID_HOST_CHARS.match(entry)
            or entry.startswith(":")
            or entry.endswith(":")
            or entry.count(":") > 1
        ):
            _fail(f"'{entry}' is not a lowercase name:port (docs/adr/0046 §4)")
        if ":" not in entry:
            _fail(f"'{entry}' has no port (docs/adr/0046 §4)")
        entries.append(entry)

    if not entries:
        _fail("no host to allow: the adapter's EGRESS is empty (docs/adr/0046 §4)")
    return entries


def main(
    argv: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
    *,
    max_wait: int = 30,
    poll_interval: float = 1.0,
) -> int:
    """Write .crew/egress/allow, start egress proxy, and wait for container readiness."""
    args = list(sys.argv[1:] if argv is None else argv)
    cur_env = dict(os.environ if env is None else env)
    raw_hosts = args[0] if args else ""
    entries = resolve_hosts(raw_hosts, cur_env)

    egress_dir = Path(".crew") / "egress"
    cmd_util.atomic_write(egress_dir / "allow", "\n".join(entries) + "\n")

    up_cmd = ["docker-compose", "--profile", "crew", "up", "-d", "egress"]
    rc = cmd_util.run_cmd(up_cmd, env=cur_env, stdout_to_stderr=True).returncode
    if rc != 0:
        rc = cmd_util.run_cmd(up_cmd, env=cur_env, stdout_to_stderr=True).returncode
        if rc != 0:
            _fail("could not start the egress proxy (docker-compose --profile crew up -d egress)")

    ready_path = egress_dir / "ready"
    waited = 0
    while True:
        ps = cmd_util.run_cmd(
            ["docker-compose", "--profile", "crew", "ps", "-q", "egress"],
            env=cur_env,
            capture_stdout=True,
            suppress_stderr=True,
        )
        cid_lines = ps.stdout.splitlines()
        cid = cid_lines[0][:12] if cid_lines else ""
        try:
            ready = ready_path.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError:
            ready = ""
        if cid and cid == ready:
            return 0
        waited += 1
        if waited > max_wait:
            _fail(
                f"the egress proxy did not report ready within {max_wait} s "
                f"(container {cid or 'none'}, ready file {ready or 'none'})"
            )
        time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
