#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Non-destructive live L1/CodeHost/Cloud readiness audit (no secret leakage).

    python3 core/live_preflight.py [--repo DIR] [--env-file FILE] [--json] [--strict]

Reads provider metadata from profiles/providers.json, level profiles from
profiles/l2.env and profiles/l3.env, and cloud plugin/workflow declarations
dynamically so core/ remains strictly vendor-free, app-free, and model-free.
Inspects environment variables and .env strictly as boolean presence (True/False),
never storing or printing secret values.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess  # nosec B404 - read-only CLI status probes with fixed argv
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "lights-out/live-preflight/1"
PLACEHOLDER_VALUES = {"", "replace-me", "changeme", "todo"}
_KEY_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_ENV_VAR_REF_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]+):-\}")
_WORKFLOW_VAR_RE = re.compile(r"\bvars\.([A-Z][A-Z0-9_]+)\b")
_GH_ACCOUNT_RE = re.compile(r"\baccount\s+([A-Za-z0-9_.-]+)")


class PreflightError(Exception):
    """Raised when required repository metadata cannot be loaded."""


def _is_non_placeholder(value: str | None) -> bool:
    cleaned = (value or "").strip()
    return bool(cleaned) and cleaned.lower() not in PLACEHOLDER_VALUES


def parse_env_presence(path: Path) -> dict[str, bool]:
    """Return ONLY {var_name: is_non_empty_non_placeholder} from an env file."""
    presence: dict[str, bool] = {}
    if not path.is_file():
        return presence
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        match = _KEY_LINE_RE.match(line)
        if not match:
            continue
        key = match.group(1)
        val = match.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        presence[key] = _is_non_placeholder(val)
    return presence


def _parse_plain_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEY_LINE_RE.match(line)
        if not match:
            continue
        val = match.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[match.group(1)] = val
    return out


def _key_status(var_name: str, dot_env: Mapping[str, bool], env: Mapping[str, str]) -> dict[str, Any]:
    in_dotenv = bool(dot_env.get(var_name))
    in_proc = _is_non_placeholder(env.get(var_name))
    sources: list[str] = []
    if in_dotenv:
        sources.append(".env")
    if in_proc:
        sources.append("env")
    return {
        "present": in_dotenv or in_proc,
        "sources": sources,
    }


def _load_providers(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "profiles" / "providers.json"
    if not path.is_file():
        raise PreflightError(f"missing provider metadata: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PreflightError(f"cannot parse {path}: {exc}") from exc
    providers = doc.get("providers") if isinstance(doc, dict) else None
    if not isinstance(providers, dict) or not providers:
        raise PreflightError(f"{path} has no providers map")
    return providers


def _codehost_env_vars(root: Path, code_host: str) -> list[str]:
    if not code_host:
        return []
    env_sh = root / "adapters" / code_host / "env.sh"
    found: list[str] = []
    if env_sh.is_file():
        text = env_sh.read_text(encoding="utf-8", errors="replace")
        for var in _ENV_VAR_REF_RE.findall(text):
            if var.endswith(("_TOKEN", "_REPO", "_URL", "_PATH", "_KEY")) and var not in found:
                found.append(var)
    if not found:
        prefix = re.sub(r"[^A-Z0-9]+", "_", code_host.upper()).strip("_")
        found = [f"{prefix}_TOKEN", f"{prefix}_REPO"]
    return found


def _cloud_env_vars(root: Path) -> list[str]:
    vars_seen: list[str] = []
    example_env = _parse_plain_env(root / ".env.example")
    for key in example_env:
        if "CLOUD" in key and key not in vars_seen:
            vars_seen.append(key)
    for wf in sorted(root.glob(".*/*/cloud-conformance.yml")):
        if wf.is_file():
            text = wf.read_text(encoding="utf-8", errors="replace")
            for var in _WORKFLOW_VAR_RE.findall(text):
                if var not in vars_seen:
                    vars_seen.append(var)
    return vars_seen


def _discover_cloud_plugins(root: Path) -> list[str]:
    cloud_dir = root / "plugins" / "cloud"
    if not cloud_dir.is_dir():
        return []
    plugins: list[str] = []
    for entry in sorted(cloud_dir.iterdir()):
        if entry.is_dir() and not entry.name.startswith(".") and (
            (entry / "deploy.sh").is_file() or (entry / "README.md").is_file()
        ):
            plugins.append(entry.name)
    return plugins


def _in_container(env: Mapping[str, str]) -> bool:
    return (
        os.path.exists("/run/.containerenv")
        or os.path.exists("/.dockerenv")
        or bool(env.get("container"))
    )


def _run_ro(argv: Sequence[str], timeout_s: int = 5) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # nosec B603 - fixed argv list, read-only status probe
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _probe_gh(in_container: bool) -> dict[str, Any]:
    exe = shutil.which("gh")
    if not exe:
        return {
            "installed": False,
            "visible_in_environment": False,
            "authenticated": False,
            "status": "not-visible-in-container" if in_container else "missing-auth",
            "account": None,
            "detail": (
                "gh CLI not on PATH inside container; run host preflight for CLI visibility"
                if in_container
                else "gh CLI not installed on PATH"
            ),
        }
    proc = _run_ro([exe, "auth", "status"])
    if proc is None:
        return {
            "installed": True,
            "visible_in_environment": True,
            "authenticated": False,
            "status": "missing-auth",
            "account": None,
            "detail": "gh auth status timed out or failed to execute",
        }
    combined = f"{proc.stdout}\n{proc.stderr}"
    match = _GH_ACCOUNT_RE.search(combined)
    account = match.group(1) if match else None
    ok = proc.returncode == 0
    return {
        "installed": True,
        "visible_in_environment": True,
        "authenticated": ok,
        "status": "ready" if ok else "missing-auth",
        "account": account,
        "detail": f"logged in as {account}" if ok and account else ("logged in" if ok else "gh not authenticated"),
    }


def _probe_gcloud(in_container: bool) -> dict[str, Any]:
    exe = shutil.which("gcloud")
    if not exe:
        return {
            "installed": False,
            "visible_in_environment": False,
            "authenticated": False,
            "status": "not-visible-in-container" if in_container else "missing-auth",
            "active_account": None,
            "accounts_count": 0,
            "configured_project": None,
            "detail": (
                "gcloud CLI not on PATH inside container; run host preflight for CLI visibility"
                if in_container
                else "gcloud CLI not installed on PATH"
            ),
        }
    proc = _run_ro([exe, "auth", "list", "--format=json"])
    active: str | None = None
    count = 0
    if proc is not None and proc.returncode == 0:
        try:
            items = json.loads(proc.stdout or "[]")
            if isinstance(items, list):
                count = len(items)
                for item in items:
                    if isinstance(item, dict) and item.get("status") == "ACTIVE":
                        active = str(item.get("account") or "") or None
                        break
        except ValueError:
            pass
    proj_proc = _run_ro([exe, "config", "get-value", "project"])
    project: str | None = None
    if proj_proc is not None and proj_proc.returncode == 0:
        cand = (proj_proc.stdout or "").strip()
        if cand and cand != "(unset)":
            project = cand
    ok = bool(active)
    return {
        "installed": True,
        "visible_in_environment": True,
        "authenticated": ok,
        "status": "ready" if ok else "missing-auth",
        "active_account": active,
        "accounts_count": count,
        "configured_project": project,
        "detail": (
            f"active account {active}" + (f" (project {project})" if project else " (project unset)")
            if ok
            else "no active gcloud account"
        ),
    }


def _probe_aws(in_container: bool, env: Mapping[str, str]) -> dict[str, Any]:
    exe = shutil.which("aws")
    oidc_env = _is_non_placeholder(env.get("AWS_ROLE_ARN")) and _is_non_placeholder(
        env.get("AWS_WEB_IDENTITY_TOKEN_FILE")
    )
    if not exe:
        return {
            "installed": False,
            "visible_in_environment": False,
            "authenticated": oidc_env,
            "status": "not-visible-in-container" if in_container else "missing-auth",
            "detail": (
                "aws CLI not on PATH inside container"
                if in_container
                else "aws CLI not installed on PATH"
            ),
        }
    proc = _run_ro([exe, "configure", "list"])
    has_creds = oidc_env
    if proc is not None and proc.returncode == 0:
        for line in (proc.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] in ("access_key", "secret_key") and "<not" not in parts[1]:
                has_creds = True
    return {
        "installed": True,
        "visible_in_environment": True,
        "authenticated": has_creds,
        "status": "ready" if has_creds else "missing-auth",
        "detail": "AWS credentials/OIDC configured" if has_creds else "no AWS credentials configured",
    }


def _probe_az(in_container: bool, env: Mapping[str, str]) -> dict[str, Any]:
    exe = shutil.which("az")
    oidc_env = _is_non_placeholder(env.get("AZURE_CLIENT_ID")) and _is_non_placeholder(
        env.get("AZURE_TENANT_ID")
    )
    if not exe:
        return {
            "installed": False,
            "visible_in_environment": False,
            "authenticated": oidc_env,
            "status": "not-visible-in-container" if in_container else "missing-auth",
            "detail": (
                "az CLI not on PATH inside container"
                if in_container
                else "az CLI not installed on PATH"
            ),
        }
    proc = _run_ro([exe, "account", "show", "--output", "json"])
    ok = proc is not None and proc.returncode == 0
    sub_name: str | None = None
    if ok and proc is not None:
        try:
            doc = json.loads(proc.stdout or "{}")
            if isinstance(doc, dict):
                sub_name = str(doc.get("name") or "") or None
        except ValueError:
            pass
    return {
        "installed": True,
        "visible_in_environment": True,
        "authenticated": ok or oidc_env,
        "status": "ready" if (ok or oidc_env) else "missing-auth",
        "subscription": sub_name,
        "detail": f"logged in ({sub_name})" if sub_name else ("logged in" if ok else "az not logged in"),
    }


def _probe_container_engine(in_container: bool, env: Mapping[str, str]) -> dict[str, Any]:
    preferred = (env.get("CONTAINER_ENGINE") or "").strip()
    candidates = [preferred] if preferred else ["podman", "docker"]
    chosen = ""
    exe = None
    for cand in candidates:
        if not cand:
            continue
        found = shutil.which(cand)
        if found:
            chosen = cand
            exe = found
            break
    if not exe:
        return {
            "engine": preferred or None,
            "installed": False,
            "visible_in_environment": False,
            "ready": False,
            "status": "not-visible-in-container" if in_container else "missing-auth",
            "detail": (
                "container engine CLI not on PATH inside container (expected inside workbench)"
                if in_container
                else "no podman or docker found on PATH"
            ),
        }
    proc = _run_ro([exe, "info"])
    ok = proc is not None and proc.returncode == 0
    return {
        "engine": chosen,
        "installed": True,
        "visible_in_environment": True,
        "ready": ok,
        "status": "ready" if ok else "missing-auth",
        "detail": f"{chosen} answers" if ok else f"{chosen} installed but daemon/machine is not answering",
    }


def audit_live_readiness(
    repo: str = ".",
    env_file: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Audit provider keys, CLI auth, and live readiness matrix without leaking secrets."""
    root = Path(repo).resolve()
    proc_env = os.environ if env is None else env
    dotenv_path = Path(env_file) if env_file else (root / ".env")
    dot_env = parse_env_presence(dotenv_path)
    in_container = _in_container(proc_env)

    providers_meta = _load_providers(root)
    l2_env = _parse_plain_env(root / "profiles" / "l2.env")
    l3_env = _parse_plain_env(root / "profiles" / "l3.env")
    cloud_plugins = _discover_cloud_plugins(root)

    providers_report: dict[str, dict[str, Any]] = {}
    ready_providers: list[str] = []
    missing_providers: list[str] = []

    for pname, info in providers_meta.items():
        if not isinstance(info, dict):
            continue
        env_var = str(info.get("env_var") or "").strip()
        if not env_var:
            continue
        ks = _key_status(env_var, dot_env, proc_env)
        status = "ready" if ks["present"] else "missing-key"
        if ks["present"]:
            ready_providers.append(pname)
        else:
            missing_providers.append(pname)
        providers_report[pname] = {
            "label": str(info.get("label") or pname),
            "env_var": env_var,
            "present": ks["present"],
            "sources": ks["sources"],
            "status": status,
            "default_harness": str(info.get("default_harness") or ""),
            "default_model": str(info.get("default_model") or ""),
            "default_profile": str(info.get("default_profile") or "l1"),
            "decide": str(info.get("decide") or "structured"),
        }

    clis = {
        "gh": _probe_gh(in_container),
        "gcloud": _probe_gcloud(in_container),
        "aws": _probe_aws(in_container, proc_env),
        "az": _probe_az(in_container, proc_env),
        "container_engine": _probe_container_engine(in_container, proc_env),
    }

    # --- M24 readiness ---
    m24_cmds = [
        "./go m24-check",
        "python3 core/m24_runner.py evaluate --data docs/scoreboard/m24-tenacity.json --json",
    ]
    for pname in ready_providers:
        pinfo = providers_report[pname]
        m24_cmds.append(
            f"PROFILE=l1 MODEL_PROVIDER={pname} MODEL={pinfo['default_model']} "
            f"AGENT_HARNESS=adk sh ci/evals.sh"
        )
    for pname in missing_providers:
        pinfo = providers_report[pname]
        m24_cmds.append(
            f"export {pinfo['env_var']}='<your-key>' && "
            f"./go record-evals PROFILE=l1 MODEL_PROVIDER={pname} MODEL={pinfo['default_model']} && "
            f"./go e2e PROFILE=l1 MODEL_PROVIDER={pname} MODEL={pinfo['default_model']} "
            f"AGENT_HARNESS={pinfo['default_harness']} DECIDE={pinfo['decide']}"
        )

    m24_ready = len(ready_providers) >= 3
    m24_matrix = {
        "title": "M24 multi-provider autonomous benchmark (>= 3 providers)",
        "status": "ready" if m24_ready else "missing-key",
        "ready_providers": ready_providers,
        "missing_providers": missing_providers,
        "provider_count_ready": len(ready_providers),
        "provider_count_total": len(providers_report),
        "detail": (
            f"{len(ready_providers)}/{len(providers_report)} provider keys present"
            + (f" (missing: {', '.join(missing_providers)})" if missing_providers else "")
        ),
        "commands": m24_cmds,
    }

    # --- M21 readiness ---
    code_host = l2_env.get("CODE_HOST", "")
    ch_vars = _codehost_env_vars(root, code_host)
    ch_env_status = {var: _key_status(var, dot_env, proc_env)["present"] for var in ch_vars}
    ch_env_ready = bool(ch_vars) and all(ch_env_status.values())
    gh_authed = bool(clis["gh"]["authenticated"])
    if ch_env_ready:
        m21_status = "ready"
        m21_detail = f"CODE_HOST={code_host} credentials configured"
    elif gh_authed:
        m21_status = "missing-key"
        missing_vars = [k for k, ok in ch_env_status.items() if not ok]
        m21_detail = (
            f"gh CLI authenticated ({clis['gh']['account'] or 'active'}), "
            f"but adapter requires {', '.join(missing_vars)} in .env or env"
        )
    elif in_container and not clis["gh"]["visible_in_environment"]:
        m21_status = "missing-key"
        m21_detail = f"missing {', '.join(ch_vars)} in .env/env (gh CLI not visible inside container)"
    else:
        m21_status = "missing-auth"
        m21_detail = f"gh CLI not authenticated and {', '.join(ch_vars)} unset"

    m21_cmds: list[str] = []
    if not gh_authed:
        m21_cmds.append("gh auth login")
    if ch_vars:
        assigns = " ".join(f"{v}='<value>'" for v in ch_vars)
        m21_cmds.append(f"export {assigns}")
    if code_host:
        m21_cmds.append(f"./go codehost-conformance CODE_HOST={code_host}")
        m21_cmds.append(f"./go e2e CODE_HOST={code_host} PROFILE=l1")

    m21_matrix = {
        "title": f"M21 remote CodeHost conformance ({code_host or 'unset'})",
        "code_host": code_host or None,
        "status": m21_status,
        "required_env": ch_env_status,
        "cli_authenticated": gh_authed,
        "detail": m21_detail,
        "commands": m21_cmds,
    }

    # --- M8 / M10 / M44 cloud conformance readiness ---
    cloud_vars = _cloud_env_vars(root)
    cloud_env_status = {var: _key_status(var, dot_env, proc_env)["present"] for var in cloud_vars}
    gcp_plugin = next((p for p in cloud_plugins if p.startswith("gcp")), cloud_plugins[0] if cloud_plugins else "")
    has_env_project = bool(
        cloud_env_status.get("GOOGLE_CLOUD_PROJECT")
        or cloud_env_status.get("GCP_PROJECT")
    )
    has_env_region = bool(
        cloud_env_status.get("GOOGLE_CLOUD_LOCATION")
        or cloud_env_status.get("GCP_REGION")
    )
    has_pipeline = any(k.endswith("_PIPELINE") and v for k, v in cloud_env_status.items())
    has_oidc = bool(
        cloud_env_status.get("CLOUD_CONFORMANCE_ENABLED")
        and cloud_env_status.get("GCP_WIF_PROVIDER")
        and cloud_env_status.get("GCP_DEPLOYER_SA")
    )
    gcloud_authed = bool(clis["gcloud"]["authenticated"])

    if (gcloud_authed and has_env_project and has_env_region and has_pipeline) or has_oidc:
        cloud_status = "ready"
        cloud_detail = "cloud CLI/OIDC and target project/region/pipeline settings present"
    elif gcloud_authed:
        cloud_status = "missing-auth"
        proj_note = (
            f", gcloud default project {clis['gcloud']['configured_project']}"
            if clis["gcloud"].get("configured_project")
            else ""
        )
        cloud_detail = (
            f"gcloud authenticated as {clis['gcloud']['active_account']}{proj_note}, "
            "but .env/env lacks target project/region/pipeline or OIDC vars (GOOGLE_CLOUD_PROJECT/GCP_PROJECT, GCP_WIF_PROVIDER)"
        )
    else:
        cloud_status = "missing-auth"
        cloud_detail = (
            "cloud CLIs not visible inside container and OIDC/project vars unset"
            if in_container and not clis["gcloud"]["visible_in_environment"]
            else "cloud CLI auth and project/OIDC federation variables are missing"
        )

    cloud_cmds: list[str] = [
        "gcloud auth login && gcloud config set project <your-project-id>",
        "./go doctor --level l2",
        "./go doctor --level l3",
    ]
    if gcp_plugin:
        cloud_cmds.append(f"./go cloud-setup CLOUD={gcp_plugin}")
        cloud_cmds.append(
            f"./go delivery-conformance DELIVERY=outside/{gcp_plugin} "
            f'ADAPTERS_PATH="$(pwd)/plugins/cloud"'
        )
    if l2_env.get("EVENT_BUS"):
        cloud_cmds.append(f"CONFORMANCE=1 EVENT_BUS={l2_env['EVENT_BUS']} sh gates/eventbus-conformance.sh")
    if l2_env.get("STATE_STORE"):
        cloud_cmds.append(f"CONFORMANCE=1 STATE_STORE={l2_env['STATE_STORE']} sh gates/statestore-conformance.sh")
    if l2_env.get("REGISTRY"):
        cloud_cmds.append(f"CONFORMANCE=1 REGISTRY={l2_env['REGISTRY']} sh gates/registry-conformance.sh")
    cloud_cmds.append("gh workflow run cloud-conformance.yml -f target=all")

    cloud_matrix = {
        "title": "M8/M10/M44 managed L2/L3 and cloud conformance",
        "status": cloud_status,
        "l2_adapters": {k: l2_env[k] for k in ("CODE_HOST", "TRACKER", "EVENT_BUS", "STATE_STORE", "DELIVERY", "METRICS", "REGISTRY") if k in l2_env},
        "l3_orchestrator": l3_env.get("ORCHESTRATOR"),
        "cloud_plugins": cloud_plugins,
        "env_vars": cloud_env_status,
        "gcloud_authenticated": gcloud_authed,
        "aws_authenticated": bool(clis["aws"]["authenticated"]),
        "az_authenticated": bool(clis["az"]["authenticated"]),
        "detail": cloud_detail,
        "commands": cloud_cmds,
    }

    return {
        "schema": SCHEMA,
        "environment": {
            "kind": "container" if in_container else "host",
            "repo": str(root),
            "dotenv_file": str(dotenv_path),
            "dotenv_exists": dotenv_path.is_file(),
        },
        "providers": providers_report,
        "clis": clis,
        "readiness_matrix": {
            "M24": m24_matrix,
            "M21": m21_matrix,
            "M8_M10_M44": cloud_matrix,
        },
    }


def _emit_human(report: dict[str, Any]) -> None:
    env_info = report["environment"]
    print(
        f"[live-preflight] env={env_info['kind']} dotenv_exists={env_info['dotenv_exists']}",
        file=sys.stderr,
    )
    for pname, pinfo in report["providers"].items():
        print(
            f"[live-preflight] provider {pname} ({pinfo['env_var']}): "
            f"present={pinfo['present']} status={pinfo['status']}",
            file=sys.stderr,
        )
    for cname, cinfo in report["clis"].items():
        print(f"[live-preflight] cli {cname}: {cinfo['status']} ({cinfo['detail']})", file=sys.stderr)
    for key, section in report["readiness_matrix"].items():
        print(f"[live-preflight] {key}: {section['status']} — {section['detail']}", file=sys.stderr)
        for cmd in section["commands"]:
            print(f"  $ {cmd}", file=sys.stderr)

    m24 = report["readiness_matrix"]["M24"]
    m21 = report["readiness_matrix"]["M21"]
    cloud = report["readiness_matrix"]["M8_M10_M44"]
    sys.stderr.flush()
    print(
        f"GO: live-preflight — env={env_info['kind']}, "
        f"providers={m24['provider_count_ready']}/{m24['provider_count_total']} ready, "
        f"M24={m24['status']}, M21={m21['status']}, cloud={cloud['status']}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="core/live_preflight.py")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = audit_live_readiness(repo=args.repo, env_file=args.env_file)
    except (PreflightError, OSError, ValueError) as exc:
        print(f"NO-GO: live-preflight — {exc}")
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _emit_human(report)
    if args.strict:
        not_ready = [
            k for k, v in report["readiness_matrix"].items() if v["status"] != "ready"
        ]
        if not_ready:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
