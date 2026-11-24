#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Detect CodeHost configuration from a repository's remote URL (ADR 0101).

Standard-library only, strictly vendor-free and app-free: parses SCP-like SSH,
ssh://, https://, and http:// remote URLs into normalized REMOTE_* fields,
strips embedded credentials, probes adapters/*/detect-remote.sh in sorted
order, and writes the first exit-0 adapter's KEY=VALUE lines.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess  # nosec B404 - fixed-argv git and adapter detect-remote probes
import sys
import urllib.parse

_SCP_RE = re.compile(r"^([^/@:\s]+)(?::([^/@\s]*))?@([^/:\s]+|\[[0-9A-Fa-f:]+\]):(.+)$")
_KEY_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_SECRET_SUFFIXES = ("_TOKEN", "_KEY", "_SECRET", "_PASSWORD")
_EXACT_SECRETS = ("METRICS_AUTH_HEADER",)
_USERINFO_URL_RE = re.compile(r"://[^/\s]*@")


class RemoteError(Exception):
    """Raised when detection encounters an invalid repository or probe output."""


def _normalize_path(raw_path: str) -> tuple[str, str, str] | None:
    stripped = raw_path.strip("/")
    if stripped.endswith(".git"):
        stripped = stripped[:-4]
    stripped = stripped.strip("/")
    if not stripped:
        return None
    parts = stripped.split("/")
    if len(parts) < 2:
        return None
    if any(seg in ("", ".", "..") for seg in parts):
        return None
    return "/".join(parts), "/".join(parts[:-1]), parts[-1]


def parse_remote_url(raw_url: str) -> dict[str, str] | None:
    """Parse a git remote URL into normalized fields, stripping embedded credentials."""
    raw = (raw_url or "").strip()
    if not raw or any(ch.isspace() or ord(ch) < 32 for ch in raw):
        return None

    if "://" in raw:
        try:
            parsed = urllib.parse.urlsplit(raw)
        except ValueError:
            return None
        scheme = parsed.scheme.lower()
        if scheme not in ("ssh", "https", "http"):
            return None
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return None
        try:
            port_num = parsed.port
        except ValueError:
            return None
        if port_num is not None and not (1 <= port_num <= 65535):
            return None
        port_str = str(port_num) if port_num is not None else ""
        norm = _normalize_path(parsed.path)
        if norm is None:
            return None
        norm_path, owner, repo = norm
        host_fmt = f"[{hostname}]" if ":" in hostname else hostname
        host_port = f"{host_fmt}:{port_str}" if port_str else host_fmt
        if scheme == "ssh" and parsed.username:
            netloc = f"{parsed.username}@{host_port}"
        else:
            netloc = host_port
        clean_url = urllib.parse.urlunsplit((scheme, netloc, parsed.path, "", ""))
        return {
            "url": clean_url,
            "scheme": scheme,
            "host": hostname,
            "port": port_str,
            "path": norm_path,
            "owner": owner,
            "repo": repo,
        }

    if raw.startswith(("/", "./", "../", "~")):
        return None
    match = _SCP_RE.match(raw)
    if not match:
        return None
    user, _password, raw_host, raw_path = match.groups()
    host = raw_host.lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        return None
    norm = _normalize_path(raw_path)
    if norm is None:
        return None
    norm_path, owner, repo = norm
    clean_url = f"{user}@{raw_host.lower()}:{raw_path}"
    return {
        "url": clean_url,
        "scheme": "ssh",
        "host": host,
        "port": "",
        "path": norm_path,
        "owner": owner,
        "repo": repo,
    }


def remote_env_vars(info: dict[str, str]) -> dict[str, str]:
    """Map normalized remote info dict to REMOTE_* environment variables."""
    return {
        "REMOTE_URL": info["url"],
        "REMOTE_SCHEME": info["scheme"],
        "REMOTE_HOST": info["host"],
        "REMOTE_PORT": info["port"],
        "REMOTE_PATH": info["path"],
        "REMOTE_OWNER": info["owner"],
        "REMOTE_REPO": info["repo"],
    }


def git_remote_url(repo: str, remote: str = "origin") -> str | None:
    """Return the configured URL of REMOTE in REPO, or None if unset."""
    if not os.path.isdir(repo):
        raise RemoteError(f"{repo} is not a directory")
    check = subprocess.run(  # nosec B603 B607 - fixed git argv, no shell
        ["git", "-C", repo, "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode != 0:
        raise RemoteError(f"{repo} is not a git repository")
    got = subprocess.run(  # nosec B603 B607 - fixed git argv, no shell
        ["git", "-C", repo, "remote", "get-url", remote],
        capture_output=True,
        text=True,
        check=False,
    )
    if got.returncode != 0:
        return None
    return got.stdout.strip()


def discover_probes(adapters_dir: str) -> list[str]:
    """Return sorted paths to adapters/*/detect-remote.sh scripts."""
    if not os.path.isdir(adapters_dir):
        return []
    probes: list[str] = []
    for name in sorted(os.listdir(adapters_dir)):
        probe = os.path.join(adapters_dir, name, "detect-remote.sh")
        if os.path.isfile(probe):
            probes.append(probe)
    return probes


def parse_adapter_output(stdout: str, probe_path: str) -> list[tuple[str, str]]:
    """Validate KEY=VALUE output from an adapter detect-remote probe."""
    pairs: list[tuple[str, str]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEY_LINE_RE.match(line)
        if not match:
            raise RemoteError(f"{probe_path}: invalid KEY=VALUE line {line!r}")
        key, value = match.groups()
        if key.endswith(_SECRET_SUFFIXES) or key in _EXACT_SECRETS:
            raise RemoteError(f"{probe_path}: refusing to persist secret key {key}")
        if any(ord(ch) < 32 for ch in value) or _USERINFO_URL_RE.search(value):
            raise RemoteError(f"{probe_path}: unsafe value for {key}")
        pairs.append((key, value))
    return pairs


def probe_remote(
    info: dict[str, str],
    adapters_dir: str = "adapters",
    env: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Run adapters/*/detect-remote.sh and return first exit-0 KEY=VALUE list."""
    child_env = dict(os.environ if env is None else env)
    child_env.update(remote_env_vars(info))
    for probe in discover_probes(adapters_dir):
        run = subprocess.run(  # nosec B603 B607 - fixed sh argv for adapter probe
            ["sh", probe],
            env=child_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if run.returncode != 0:
            continue
        pairs = parse_adapter_output(run.stdout, probe)
        if pairs:
            return pairs
    return []


def _remove_if_exists(path: str | None) -> None:
    if path and os.path.lexists(path):
        os.remove(path)


def _write_env_file(path: str, pairs: list[tuple[str, str]]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("# Written by core/remote.py (ADR 0101). Explicit .env overrides this file.\n")
            for key, value in pairs:
                fh.write(f"{key}={value}\n")
        os.replace(tmp, path)
    except BaseException:
        _remove_if_exists(tmp)
        raise


def detect(
    repo: str,
    remote: str = "origin",
    adapters_dir: str = "adapters",
    out: str | None = None,
    env: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Detect remote code host config for REPO and optionally write OUT."""
    raw_url = git_remote_url(repo, remote)
    if not raw_url:
        _remove_if_exists(out)
        return []
    info = parse_remote_url(raw_url)
    if info is None:
        _remove_if_exists(out)
        return []
    try:
        pairs = probe_remote(info, adapters_dir=adapters_dir, env=env)
    except RemoteError:
        _remove_if_exists(out)
        raise
    if not pairs:
        _remove_if_exists(out)
        return []
    if out:
        _write_env_file(out, pairs)
    return pairs


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Detect CodeHost settings from a git remote.")
    sub = parser.add_subparsers(dest="command", required=True)
    det = sub.add_parser("detect", help="Probe adapters for a repository remote")
    det.add_argument("repo", help="Path to git repository")
    det.add_argument("--remote", default="origin", help="Git remote name (default: origin)")
    det.add_argument("--adapters", default="adapters", help="Path to adapters directory")
    det.add_argument("--out", default=None, help="Output KEY=VALUE file path")
    args = parser.parse_args(argv)

    try:
        pairs = detect(args.repo, remote=args.remote, adapters_dir=args.adapters, out=args.out)
    except RemoteError as exc:
        print(f"remote: {exc}", file=sys.stderr)
        return 1
    for key, value in pairs:
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
