# SPDX-License-Identifier: Apache-2.0
"""AgentHarness runner integrating the Google Antigravity Python SDK.

Drives coding roles (`dev-worker`), review roles (`test-reviewer`,
`architect` with `WORKSPACE`), and conversational roles (`product`,
`ops-triage`, `release`, `architect` perimeter) through `google.antigravity`:
- `Agent`, `LocalAgentConfig`, `types.CapabilitiesConfig`, `types.BudgetConfig`
- `@hooks.pre_tool_call_decide` bridging every SDK tool call to
  `adapters/gemini-cli/hooks/guard.sh` so `OUT/hooks.log` carries uniform
  `ALLOW`/`DENY` entries
- Writes `OUT/run.json` BEFORE failing on budget exhaustion (ADR 0087)
- Refuses loudly naming the missing `google-antigravity` package when absent
  (never falling back to hidden mocks).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import sqlite3
import subprocess  # nosec B404 - fixed git and guard.sh argv lists, shell=False
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

REVIEW_ROLES = ("test-reviewer", "architect")
CONVERSATIONAL_ROLES = ("product", "ops-triage", "release")
DISALLOWED_BUILTIN_NAMES = (
    ("SEARCH_WEB", "search_web"),
    ("READ_URL_CONTENT", "read_url_content"),
    ("GENERATE_IMAGE", "generate_image"),
    ("START_SUBAGENT", "start_subagent"),
)
THINKING_LEVELS = ("low", "medium", "high")
PLACEHOLDER_KEY = "replace-me"


class HarnessError(RuntimeError):
    """Controlled failure emitted as one stderr line."""


def _repo_root() -> Path:
    override = os.environ.get("LIGHTS_OUT_ROOT")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[3]


def _adapter_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_env_map(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, _, v = stripped.partition("=")
        values[k.strip()] = v.strip()
    return values


def _resolve_thinking(thinking: str) -> str:
    lvl = thinking.strip().lower()
    if lvl not in THINKING_LEVELS:
        raise HarnessError(f"THINKING={thinking!r} must be low, medium, or high")
    thinking_env = _adapter_root() / "thinking.env"
    if not thinking_env.is_file():
        raise HarnessError(f"{thinking_env} is missing")
    key = f"GEMINI_{lvl.upper()}"
    mapping = _read_env_map(thinking_env)
    val = mapping.get(key, "")
    if not val:
        raise HarnessError(f"{thinking_env} has no {key}= line")
    return val


def _parse_pos_int(env: dict[str, str], name: str, default: int | None = None) -> int | None:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    if not re.fullmatch(r"[0-9]+", raw) or int(raw) < 1:
        raise HarnessError(f"{name}={raw!r} must be a positive integer")
    return int(raw)


def _load_sdk():
    stub_val = os.environ.get("AGY_SDK_STUB", "")
    conf_val = os.environ.get("CONFORMANCE", "")
    if stub_val == "1" and conf_val != "1":
        raise HarnessError("AGY_SDK_STUB=1 requires CONFORMANCE=1")
    try:
        import google.antigravity as agy
        from google.antigravity import Agent, LocalAgentConfig, types
        from google.antigravity.hooks import hooks
    except ImportError as exc:
        raise HarnessError(
            f"missing python package 'google-antigravity' ({exc}); install google-antigravity to run antigravity-sdk"
        ) from None
    try:
        from google.antigravity.hooks import policy
    except ImportError:
        policy = None
    return agy, Agent, LocalAgentConfig, types, hooks, policy


def _kwargs_accepted(fn: Any, candidates: dict[str, Any]) -> dict[str, Any]:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return dict(candidates)
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(candidates)
    return {k: v for k, v in candidates.items() if k in params}


def _hook_result(types_mod: Any, allow: bool, reason: str | None = None) -> Any:
    cls = getattr(types_mod, "HookResult", None)
    if cls is None:
        return {"allow": allow, "reason": reason}
    kwargs: dict[str, Any] = {"allow": allow}
    if reason is not None:
        kwargs["reason"] = reason
    try:
        return cls(**_kwargs_accepted(cls, kwargs))
    except TypeError:
        return cls(allow=allow)


def _extract_tokens(usage: Any) -> dict[str, int]:
    if usage is None:
        return {"input": 0, "output": 0, "total": 0}
    if isinstance(usage, dict):
        prompt = int(usage.get("prompt_token_count") or usage.get("input") or 0)
        cand = int(usage.get("candidates_token_count") or usage.get("output") or 0)
        thoughts = int(usage.get("thoughts_token_count") or 0)
        total = int(usage.get("total_token_count") or usage.get("total") or (prompt + cand + thoughts))
        cached = usage.get("cached_content_token_count", usage.get("cached"))
    else:
        prompt = int(getattr(usage, "prompt_token_count", 0) or 0)
        cand = int(getattr(usage, "candidates_token_count", 0) or 0)
        thoughts = int(getattr(usage, "thoughts_token_count", 0) or 0)
        total = int(getattr(usage, "total_token_count", 0) or (prompt + cand + thoughts))
        cached = getattr(usage, "cached_content_token_count", None)
    out = {"input": prompt, "output": cand + thoughts, "total": total}
    if cached is not None:
        out["cached"] = int(cached)
    return out


async def _resolve_response_text(response: Any) -> str:
    if response is None:
        return ""
    text_attr = getattr(response, "text", None)
    if callable(text_attr):
        res = text_attr()
        if inspect.isawaitable(res):
            res = await res
        return str(res or "")
    if isinstance(text_attr, str):
        return text_attr
    content = getattr(response, "content", None)
    return str(content or "")


def _classify_stop(stop_reason: Any, types_mod: Any, tokens: dict[str, int],
                   token_budget: int | None, turns: int, max_turns: int | None) -> str | None:
    sr_cls = getattr(types_mod, "StopReason", None)
    if stop_reason is not None:
        if sr_cls is not None:
            for attr in ("MAX_TOTAL_TOKENS_EXCEEDED", "MAX_INPUT_TOKENS_EXCEEDED", "MAX_OUTPUT_TOKENS_EXCEEDED"):
                if hasattr(sr_cls, attr) and stop_reason == getattr(sr_cls, attr):
                    return "TOKEN_BUDGET"
            for attr in ("MAX_MODEL_CALLS_EXCEEDED", "MAX_TOOL_CALLS_EXCEEDED"):
                if hasattr(sr_cls, attr) and stop_reason == getattr(sr_cls, attr):
                    return "MAX_TURNS"
        sr_str = str(stop_reason).upper()
        if "TOKEN" in sr_str and "EXCEEDED" in sr_str:
            return "TOKEN_BUDGET"
        if ("MODEL_CALLS" in sr_str or "TOOL_CALLS" in sr_str or "MAX_TURNS" in sr_str) and "EXCEEDED" in sr_str:
            return "MAX_TURNS"
    if token_budget is not None and tokens.get("total", 0) > token_budget:
        return "TOKEN_BUDGET"
    if max_turns is not None and turns > max_turns:
        return "MAX_TURNS"
    return None


def _conversational_tools(role: str, recorded_calls: list[dict[str, Any]]) -> list[Any]:
    def record(name: str, args: dict[str, Any]) -> None:
        recorded_calls.append({"name": name, "args": args})

    if role == "product":
        def ask_product_owner(question: str) -> dict:
            """Ask the product owner the single blocking question."""
            record("ask_product_owner", {"question": question})
            return {"status": "recorded", "question": question}

        def confirm_acceptance(criteria: list[str]) -> dict:
            """Confirm testable Given/When/Then acceptance criteria."""
            record("confirm_acceptance", {"criteria": criteria})
            return {"status": "recorded", "criteria": criteria}

        def propose_ticket(title: str, problem: str, criteria: list[str], feature: str, priority: str) -> dict:
            """Propose one ticket for an intake request."""
            record(
                "propose_ticket",
                {"title": title, "problem": problem, "criteria": criteria, "feature": feature, "priority": priority},
            )
            return {"status": "recorded", "title": title}

        return [ask_product_owner, confirm_acceptance, propose_ticket]

    if role == "ops-triage":
        def file_incident(title: str, hypotheses: list[dict[str, Any]]) -> dict:
            """Open the incident with ranked hypotheses."""
            record("file_incident", {"title": title, "hypotheses": hypotheses})
            return {"status": "recorded", "title": title, "hypotheses": hypotheses}

        def propose_rollback(version: str, reason: str) -> dict:
            """Propose rolling back canary version."""
            record("propose_rollback", {"version": version, "reason": reason})
            return {"status": "recorded", "action": "rollback", "version": version, "reason": reason}

        def propose_hold(reason: str) -> dict:
            """Propose holding without rollback."""
            record("propose_hold", {"reason": reason})
            return {"status": "recorded", "action": "hold", "reason": reason}

        def file_postmortem(title: str, timeline: list[dict[str, Any]],
                            causes: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict:
            """File postmortem from an incident document."""
            record("file_postmortem", {"title": title, "timeline": timeline, "causes": causes, "actions": actions})
            return {"status": "recorded", "title": title, "timeline": timeline, "causes": causes, "actions": actions}

        return [file_incident, file_postmortem, propose_rollback, propose_hold]

    if role == "release":
        def submit_brief(version: str, brief: str) -> dict:
            """Submit the promotion brief for version."""
            record("submit_brief", {"version": version, "brief": brief})
            return {"status": "recorded", "version": version, "brief": brief}

        def decline_brief(reason: str) -> dict:
            """Decline the promotion brief with reason."""
            record("decline_brief", {"reason": reason})
            return {"status": "recorded", "reason": reason}

        return [submit_brief, decline_brief]

    if role == "architect":
        def find_files(queries: list[str]) -> dict:
            """Search project map INTAKE_MAP for matching files."""
            record("find_files", {"queries": queries})
            scope = os.environ.get("INTAKE_SCOPE", "").strip("/")
            db_path = os.environ.get("INTAKE_MAP", "")
            if not db_path or not os.path.isfile(db_path):
                return {"error": "no project map for this run", "results": []}
            if not isinstance(queries, list) or not queries or len(queries) > 10:
                return {"error": "give between 1 and 10 queries", "results": []}
            db = sqlite3.connect(f"file:{quote(os.path.abspath(db_path))}?mode=ro", uri=True)
            results = []
            try:
                for q in queries:
                    rows = db.execute(
                        "SELECT name FROM node WHERE kind IN ('file','test','page','adr') "
                        "AND instr(lower(name), lower(?)) > 0 ORDER BY name",
                        (str(q or ""),),
                    ).fetchall()
                    paths = [r[0] for r in rows if not scope or r[0] == scope or r[0].startswith(scope + "/")]
                    results.append({"query": q, "paths": paths[:40], "more": max(0, len(paths) - 40)})
            finally:
                db.close()
            return {"results": results}

        def propose_perimeter(ticket: int, paths: list[str]) -> dict:
            """Propose the perimeter paths for ticket number TICKET."""
            record("propose_perimeter", {"ticket": ticket, "paths": paths})
            return {"status": "recorded", "ticket": ticket, "paths": paths}

        return [find_files, propose_perimeter]

    return []


def _role_prompt(repo_root: Path, role: str, has_workspace: bool) -> str:
    prompt_file = "perimeter.md" if (role == "architect" and not has_workspace) else "prompt.md"
    path = repo_root / "roles" / role / prompt_file
    if not path.is_file():
        raise HarnessError(f"roles/{role}/{prompt_file} not found")
    return path.read_text(encoding="utf-8")


def _build_run_json(*, provider: str, model: str, thinking: str, turns: int,
                    tokens: dict[str, int], duration_s: float, exit_code: int,
                    input_sha256: str, endpoint: str, stop: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "harness": "antigravity-sdk",
        "provider": provider,
        "model": model,
        "thinking": thinking,
        "turns": turns,
        "tokens": tokens,
        "duration_s": round(duration_s, 1),
        "exit_code": exit_code,
        "input_sha256": input_sha256,
        "mcp": None,
    }
    if endpoint:
        payload["endpoint"] = endpoint
    if stop:
        payload["stop"] = stop
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _git_run(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607 - explicit git argv list, shell=False
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _check_secret_leak(out_dir: Path, api_key: str) -> None:
    if not api_key or len(api_key) < 8 or api_key.lower() in (PLACEHOLDER_KEY, "changeme"):
        return
    needle = api_key.encode("utf-8")
    for p in sorted(out_dir.iterdir()):
        if not p.is_file():
            continue
        if needle in p.read_bytes():
            raise HarnessError(f"GEMINI_API_KEY appears in run artefact {p.name}; refusing to record")


async def _execute_session(
    *,
    role: str,
    thinking_level: str,
    model: str,
    api_key: str,
    endpoint: str,
    system_instructions: str,
    messages: list[str],
    workspace: Path | None,
    out_dir: Path,
    max_turns: int | None,
    token_budget: int | None,
    repo_root: Path,
) -> tuple[str, dict[str, Any], list[dict[str, Any]], str | None]:
    agy, Agent, LocalAgentConfig, types, hooks, policy = _load_sdk()
    guard_sh = repo_root / "adapters" / "gemini-cli" / "hooks" / "guard.sh"
    if not guard_sh.is_file():
        raise HarnessError(f"{guard_sh} is missing")

    mcp_allow = os.environ.get("CREW_MCP_ALLOW", "")
    recorded_calls: list[dict[str, Any]] = []
    disallowed_map = {}
    builtin_cls = getattr(types, "BuiltinTools", None)
    if builtin_cls is not None:
        for enum_name, tool_name in DISALLOWED_BUILTIN_NAMES:
            if hasattr(builtin_cls, enum_name):
                disallowed_map[enum_name] = getattr(builtin_cls, enum_name)
    disallowed_names = {name for _, name in DISALLOWED_BUILTIN_NAMES}

    @hooks.pre_tool_call_decide
    async def guard_pre_tool(data: Any) -> Any:
        if isinstance(data, dict):
            tool_name = str(data.get("name") or data.get("tool_name") or "unknown")
            tool_args = data.get("args") if data.get("args") is not None else data.get("tool_input")
        else:
            tool_name = str(getattr(data, "name", None) or getattr(data, "tool_name", None) or "unknown")
            tool_args = getattr(data, "args", None)
            if tool_args is None:
                tool_args = getattr(data, "tool_input", None)
        if not isinstance(tool_args, dict):
            tool_args = {} if tool_args is None else {"value": str(tool_args)}

        if tool_name in disallowed_names:
            with (out_dir / "hooks.log").open("a", encoding="utf-8") as hfh:
                hfh.write(f"DENY {tool_name} not-offered\n")
            return _hook_result(types, False, f"denied: {tool_name} is not offered by this harness")

        hook_env = dict(os.environ, CREW_OUT=str(out_dir), CREW_MCP_ALLOW=mcp_allow)
        payload = json.dumps({"tool_name": tool_name, "tool_input": tool_args})
        proc = subprocess.run(  # nosec B603 B607 - explicit guard.sh argv list
            ["sh", str(guard_sh)],
            input=payload,
            env=hook_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return _hook_result(types, True)
        reason = proc.stderr.strip() or f"denied by guard ({proc.returncode})"
        return _hook_result(types, False, reason)

    is_coding = workspace is not None and role not in REVIEW_ROLES
    is_conversational = workspace is None

    cap_kwargs: dict[str, Any] = {}
    if hasattr(types, "AgentBehavior"):
        cap_kwargs["agent_behavior"] = getattr(types.AgentBehavior, "AUTONOMOUS", "AUTONOMOUS")
    if disallowed_map:
        cap_kwargs["disabled_tools"] = list(disallowed_map.values())
    if is_coding and hasattr(types, "RunCommandConfig"):
        cap_kwargs["run_command_config"] = types.RunCommandConfig(
            **_kwargs_accepted(types.RunCommandConfig, {"enable_sandbox": True})
        )
    capabilities = (
        types.CapabilitiesConfig(**_kwargs_accepted(types.CapabilitiesConfig, cap_kwargs))
        if hasattr(types, "CapabilitiesConfig")
        else None
    )

    budget_kwargs: dict[str, Any] = {}
    if max_turns is not None:
        budget_kwargs["max_model_calls"] = max_turns
    if token_budget is not None:
        budget_kwargs["max_total_tokens"] = token_budget
    budget_config = (
        types.BudgetConfig(**_kwargs_accepted(types.BudgetConfig, budget_kwargs))
        if hasattr(types, "BudgetConfig") and budget_kwargs
        else None
    )

    policies = None
    if policy is not None:
        if is_coding and hasattr(policy, "allow_all"):
            policies = [policy.allow_all()]
        elif not is_coding and hasattr(policy, "deny"):
            policies = [policy.deny("run_command")]

    tools = _conversational_tools(role, recorded_calls) if is_conversational else None

    with tempfile.TemporaryDirectory(prefix="agy-sdk-state-") as state_dir:
        cfg_kwargs: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "system_instructions": system_instructions,
            "hooks": [guard_pre_tool],
            "app_data_dir": str(Path(state_dir).resolve()),
            "thinking_level": thinking_level,
        }
        if capabilities is not None:
            cfg_kwargs["capabilities"] = capabilities
        if budget_config is not None:
            cfg_kwargs["budget_config"] = budget_config
        if policies is not None:
            cfg_kwargs["policies"] = policies
        if workspace is not None:
            cfg_kwargs["workspaces"] = [str(workspace)]
        if tools:
            cfg_kwargs["tools"] = tools

        if endpoint and hasattr(agy, "GeminiAPIEndpoint"):
            ep_cls = agy.GeminiAPIEndpoint
            opts = None
            if hasattr(types, "GeminiModelOptions"):
                opts = types.GeminiModelOptions(
                    **_kwargs_accepted(types.GeminiModelOptions, {"thinking_level": thinking_level})
                )
            ep_kwargs = {"base_url": endpoint, "url": endpoint}
            if opts is not None:
                ep_kwargs["options"] = opts
            cfg_kwargs["endpoint"] = ep_cls(**_kwargs_accepted(ep_cls, ep_kwargs))

        config = LocalAgentConfig(**_kwargs_accepted(LocalAgentConfig, cfg_kwargs))

        final_text = ""
        last_response = None
        async with Agent(config=config) as agent:
            for msg in messages:
                last_response = await agent.chat(msg)
                chunk = await _resolve_response_text(last_response)
                if chunk:
                    final_text = chunk

            conv = getattr(agent, "conversation", None)
            usage = getattr(conv, "total_usage", None) or getattr(last_response, "usage_metadata", None)
            tokens = _extract_tokens(usage)
            served_model = (
                getattr(last_response, "model_version", None)
                or getattr(last_response, "model", None)
                or getattr(conv, "model", None)
                or model
            )
            steps = getattr(conv, "steps", None)
            raw_turns = getattr(conv, "turns", None) or getattr(last_response, "turns", None)
            if isinstance(raw_turns, int) and raw_turns >= 1:
                turns = raw_turns
            elif isinstance(steps, list) and steps:
                turns = len(steps)
            else:
                turns = max(1, len(messages))
            stop_reason = (
                getattr(last_response, "stop_reason", None)
                or getattr(conv, "last_turn_stop_reason", None)
            )
            extra_calls = getattr(last_response, "tool_calls", None) or getattr(conv, "tool_calls", None)
            if isinstance(extra_calls, list) and not recorded_calls:
                for tc in extra_calls:
                    if isinstance(tc, dict) and tc.get("name"):
                        recorded_calls.append({"name": str(tc["name"]), "args": dict(tc.get("args") or {})})
                    elif getattr(tc, "name", None):
                        recorded_calls.append({"name": str(tc.name), "args": dict(getattr(tc, "args", None) or {})})

        stop = _classify_stop(stop_reason, types, tokens, token_budget, turns, max_turns)
        stats = {"model": str(served_model), "turns": turns, "tokens": tokens}
        return final_text, stats, recorded_calls, stop


def main(argv: list[str] | None = None) -> int:
    _ = argv
    env = os.environ
    role = env.get("ROLE", "").strip()
    out_raw = env.get("OUT", "").strip()
    thinking = env.get("THINKING", "").strip()
    if not role or not out_raw or not thinking:
        print("harness-antigravity-sdk: ROLE, OUT, and THINKING must be set", file=sys.stderr)
        return 1

    out_dir = Path(out_raw)
    out_dir.mkdir(parents=True, exist_ok=True)
    stop_file = out_dir / "stop"
    if stop_file.exists():
        stop_file.unlink()

    ws_raw = env.get("WORKSPACE", "").strip()
    workspace = Path(ws_raw) if ws_raw else None
    has_workspace = workspace is not None

    try:
        provider = env.get("MODEL_PROVIDER", "").strip()
        if provider != "gemini":
            raise HarnessError(f"MODEL_PROVIDER={provider!r} is not gemini")
        model = env.get("MODEL", "").strip()
        if not model:
            raise HarnessError("MODEL is empty")
        api_key = env.get("GEMINI_API_KEY", "").strip()
        if not api_key or api_key == PLACEHOLDER_KEY:
            raise HarnessError("GEMINI_API_KEY is missing or placeholder")
        thinking_level = _resolve_thinking(thinking)
        endpoint = env.get("MODEL_BASE_URL", "").strip()

        if has_workspace:
            assert workspace is not None
            if not workspace.is_dir():
                raise HarnessError(f"WORKSPACE={workspace} is not a directory")
            base = env.get("BASE", "").strip()
            ticket_file = Path(env.get("TICKET_FILE", ""))
            if not base or not ticket_file.is_file():
                raise HarnessError("BASE and TICKET_FILE must both be set and valid")
            max_turns = _parse_pos_int(env, "MAX_TURNS")
            token_budget = _parse_pos_int(env, "TOKEN_BUDGET")
            input_bytes = ticket_file.read_bytes()
            messages = [
                input_bytes.decode("utf-8")
                + "\n---\n\nWork only inside this repository checkout. Do not run git push or "
                "git commit yourself: committing and pushing are not your job. When you are done, "
                "your final message must contain exactly what your role instructions require."
            ]
        else:
            base = ""
            input_file = Path(env.get("INPUT", ""))
            if not input_file.is_file():
                raise HarnessError(f"INPUT={input_file} is not a file")
            max_turns = _parse_pos_int(env, "ADK_MAX_TURNS") or _parse_pos_int(env, "MAX_TURNS")
            token_budget = _parse_pos_int(env, "TOKEN_BUDGET")
            input_bytes = input_file.read_bytes()
            answers_raw = env.get("ANSWERS", "").strip()
            answers = json.loads(answers_raw and Path(answers_raw).read_text(encoding="utf-8") or "[]")
            if not isinstance(answers, list) or not all(isinstance(a, str) for a in answers):
                raise HarnessError(f"ANSWERS {answers_raw} is not a JSON list of strings")
            messages = [input_bytes.decode("utf-8"), *answers]

        input_sha256 = hashlib.sha256(input_bytes).hexdigest()
        repo_root = _repo_root()
        system_instructions = _role_prompt(repo_root, role, has_workspace)

        started = time.monotonic()
        final_text, stats, calls, stop = asyncio.run(
            _execute_session(
                role=role,
                thinking_level=thinking_level,
                model=model,
                api_key=api_key,
                endpoint=endpoint,
                system_instructions=system_instructions,
                messages=messages,
                workspace=workspace,
                out_dir=out_dir,
                max_turns=max_turns,
                token_budget=token_budget,
                repo_root=repo_root,
            )
        )
        duration_s = max(0.0, time.monotonic() - started)

        # Write OUT/run.json BEFORE failing on budget exhaustion (ADR 0087 §2).
        if stop == "TOKEN_BUDGET":
            stop_file.write_text("TOKEN_BUDGET\n", encoding="utf-8")
            _write_json(
                out_dir / "run.json",
                _build_run_json(
                    provider=provider,
                    model=stats["model"],
                    thinking=thinking,
                    turns=stats["turns"],
                    tokens=stats["tokens"],
                    duration_s=duration_s,
                    exit_code=1,
                    input_sha256=input_sha256,
                    endpoint=endpoint,
                    stop="TOKEN_BUDGET",
                ),
            )
            raise HarnessError(f"over TOKEN_BUDGET ({token_budget})")

        if stop == "MAX_TURNS":
            stop_file.write_text("MAX_TURNS\n", encoding="utf-8")
            _write_json(
                out_dir / "run.json",
                _build_run_json(
                    provider=provider,
                    model=stats["model"],
                    thinking=thinking,
                    turns=stats["turns"],
                    tokens=stats["tokens"],
                    duration_s=duration_s,
                    exit_code=1,
                    input_sha256=input_sha256,
                    endpoint=endpoint,
                    stop="MAX_TURNS",
                ),
            )
            raise HarnessError(f"over MAX_TURNS ({max_turns})")

        final_stripped = final_text.rstrip("\n")
        if has_workspace:
            assert workspace is not None
            is_coding = role not in REVIEW_ROLES
            if is_coding:
                status_proc = _git_run(workspace, "status", "--porcelain")
                if status_proc.returncode != 0:
                    raise HarnessError(f"git status failed: {status_proc.stderr.strip()}")
                committed = False
                if status_proc.stdout.strip():
                    add_proc = _git_run(workspace, "add", "-A")
                    if add_proc.returncode != 0:
                        raise HarnessError(f"git add failed: {add_proc.stderr.strip()}")
                    commit_proc = _git_run(
                        workspace,
                        "-c", "user.name=lights-out-crew",
                        "-c", "user.email=crew@lights-out.invalid",
                        "commit", "-q", "-m", "WIP: incomplete run, no final message",
                    )
                    if commit_proc.returncode != 0:
                        raise HarnessError(f"git commit failed: {commit_proc.stderr.strip()}")
                    committed = True

                if not final_stripped:
                    raise HarnessError("no final agent message in response")

                lines = final_stripped.split("\n")
                title = lines[0]
                rest = lines[1:]
                while rest and not rest[0].strip():
                    rest.pop(0)
                body = "\n".join(rest).rstrip("\n")

                if committed:
                    amend_args = ["-c", "user.name=lights-out-crew", "-c", "user.email=crew@lights-out.invalid",
                                  "commit", "-q", "--amend", "-m", title]
                    if body:
                        amend_args.extend(["-m", body])
                    amend_proc = _git_run(workspace, *amend_args)
                    if amend_proc.returncode != 0:
                        raise HarnessError(f"git commit --amend failed: {amend_proc.stderr.strip()}")

                (out_dir / "final.md").write_text(final_stripped + "\n", encoding="utf-8")
                _write_json(out_dir / "pr.json", {"title": title, "body": body})
                patch_proc = subprocess.run(  # nosec B603 B607 - explicit git argv list
                    ["git", "-C", str(workspace), "format-patch", "--stdout", f"{base}..HEAD"],
                    capture_output=True,
                    check=False,
                )
                (out_dir / "patch").write_bytes(patch_proc.stdout if patch_proc.returncode == 0 else b"")
            else:
                if not final_stripped:
                    raise HarnessError("no final agent message in response")
                (out_dir / "final.md").write_text(final_stripped + "\n", encoding="utf-8")
        else:
            _write_json(out_dir / "calls.json", calls)
            (out_dir / "final.md").write_text(final_stripped + ("\n" if final_stripped else ""), encoding="utf-8")

        _write_json(
            out_dir / "run.json",
            _build_run_json(
                provider=provider,
                model=stats["model"],
                thinking=thinking,
                turns=stats["turns"],
                tokens=stats["tokens"],
                duration_s=duration_s,
                exit_code=0,
                input_sha256=input_sha256,
                endpoint=endpoint,
                stop=None,
            ),
        )
        _check_secret_leak(out_dir, api_key)
        return 0
    except HarnessError as exc:
        print(f"harness-antigravity-sdk: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # fail closed in one stderr line
        msg = " ".join(str(exc).splitlines()) or type(exc).__name__
        print(f"harness-antigravity-sdk: {type(exc).__name__}: {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
