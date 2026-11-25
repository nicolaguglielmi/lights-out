#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# outside/file-harness run.sh: cold local scripted/file AgentHarness fixture
# implementing the AgentHarness contract (ADR 0017, ADR 0021, ADR 0041,
# ADR 0058, ADR 0130) without containers or network access.
set -eu

: "${ROLE:?ROLE not set}"
: "${TICKET:?TICKET not set}"
: "${WORKSPACE:?WORKSPACE not set}"
: "${BASE:?BASE not set}"
: "${OUT:?OUT not set}"
: "${TICKET_FILE:?TICKET_FILE not set}"
: "${THINKING:?THINKING not set}"
: "${MAX_TURNS:?MAX_TURNS not set}"
: "${TIMEOUT_S:?TIMEOUT_S not set}"
: "${TOKEN_BUDGET:?TOKEN_BUDGET not set}"

for tool in git python3; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'file-harness: %s is not on PATH\n' "$tool" >&2
    exit 1
  }
done

if [ -n "${CREW_MCP:-}" ] && [ -f "$CREW_MCP/decl.json" ]; then
  printf 'file-harness: declared MCP servers are unsupported without mcp.sh (ADR 0073 §8)\n' >&2
  exit 1
fi

python3 -I - <<'PY'
import hashlib
import json
import os
import re
import subprocess
import sys
import time

env = os.environ
role = env["ROLE"]
ticket = env["TICKET"]
workspace = env["WORKSPACE"]
base = env["BASE"]
out = env["OUT"]
ticket_file = env["TICKET_FILE"]
thinking = env["THINKING"]


def fail(msg):
    sys.stderr.write(f"file-harness: {msg}\n")
    raise SystemExit(1)


def parse_pos_int(name):
    raw = env.get(name, "")
    if not re.fullmatch(r"[0-9]+", raw) or int(raw) < 1:
        fail(f"{name}={raw!r} must be a positive integer")
    return int(raw)


max_turns = parse_pos_int("MAX_TURNS")
timeout_s = parse_pos_int("TIMEOUT_S")
token_budget = parse_pos_int("TOKEN_BUDGET")

if not os.path.isdir(workspace):
    fail(f"WORKSPACE={workspace} is not a directory")
if not os.path.isfile(ticket_file):
    fail(f"TICKET_FILE={ticket_file} does not exist")

with open(ticket_file, "rb") as tfh:
    input_sha256 = hashlib.sha256(tfh.read()).hexdigest()

script_path = env.get("FILE_HARNESS_SCRIPT", "").strip()
if not script_path and env.get("FILE_HARNESS_DIR"):
    fdir = env["FILE_HARNESS_DIR"].strip()
    for cand in (
        os.path.join(fdir, ticket, f"{role}.json"),
        os.path.join(fdir, f"{ticket}.json"),
    ):
        if os.path.isfile(cand):
            script_path = cand
            break

script = {}
if script_path:
    if not os.path.isfile(script_path):
        fail(f"FILE_HARNESS_SCRIPT={script_path} not found")
    try:
        with open(script_path, encoding="utf-8") as sfh:
            loaded = json.load(sfh)
    except (OSError, ValueError) as exc:
        fail(f"invalid FILE_HARNESS_SCRIPT {script_path}: {exc}")
    if not isinstance(loaded, dict):
        fail(f"FILE_HARNESS_SCRIPT {script_path} must be a JSON object")
    script = loaded

os.makedirs(out, exist_ok=True)
stop_file = os.path.join(out, "stop")
if os.path.exists(stop_file):
    os.remove(stop_file)

started = time.monotonic()

if script.get("hang"):
    fail(f"over TIMEOUT_S ({timeout_s} s)")

turns = script.get("turns", 1)
if not isinstance(turns, int) or isinstance(turns, bool) or turns < 1:
    fail(f"invalid scripted turns {turns!r}")

tok_obj = script.get("tokens") if isinstance(script.get("tokens"), dict) else {}
tok_in = tok_obj.get("input", 120)
tok_out = tok_obj.get("output", 40)
if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (tok_in, tok_out)):
    fail("scripted token counts must be non-negative integers")
tok_total = tok_obj.get("total", tok_in + tok_out)
if not isinstance(tok_total, int) or isinstance(tok_total, bool) or tok_total < 0:
    fail("scripted tokens.total must be a non-negative integer")

provider = str(script.get("provider") or env.get("MODEL_PROVIDER") or "file").strip() or "file"
model = str(script.get("model") or env.get("MODEL") or "file-harness-v1").strip() or "file-harness-v1"
endpoint = env.get("MODEL_BASE_URL", "").strip()
harness_id = env.get("FILE_HARNESS_ID", "outside/file-harness").strip() or "outside/file-harness"


def write_run_json(exit_code):
    payload = {
        "harness": harness_id,
        "provider": provider,
        "model": model,
        "thinking": thinking,
        "turns": turns,
        "tokens": {"input": tok_in, "output": tok_out, "total": tok_total},
        "duration_s": max(0, int(time.monotonic() - started)),
        "exit_code": exit_code,
        "input_sha256": input_sha256,
        "mcp": None,
    }
    if endpoint:
        payload["endpoint"] = endpoint
    with open(os.path.join(out, "run.json"), "w", encoding="utf-8") as rfh:
        rfh.write(json.dumps(payload, indent=2) + "\n")


if turns > max_turns:
    with open(stop_file, "w", encoding="utf-8") as fh:
        fh.write("MAX_TURNS\n")
    fail(f"over MAX_TURNS ({max_turns})")

if tok_total > token_budget:
    with open(stop_file, "w", encoding="utf-8") as fh:
        fh.write("TOKEN_BUDGET\n")
    write_run_json(1)
    fail(f"over TOKEN_BUDGET ({token_budget})")

hook_lines = []
for probe in script.get("probes") or []:
    if isinstance(probe, dict):
        op = str(probe.get("op") or "tool")
        label = str(probe.get("label") or probe.get("cmd") or probe.get("path") or "blocked")
        hook_lines.append(f"DENY {op} {label}")

mode = (env.get("FILE_HARNESS_MODE") or "").strip().lower()
is_review = mode == "review" or (mode != "write" and role in ("test-reviewer", "architect"))

writes = script.get("writes")
if writes is None:
    if is_review:
        writes = []
    else:
        wpath = env.get("FILE_HARNESS_WRITE_PATH", "conformance.txt").strip() or "conformance.txt"
        wcontent = env.get("FILE_HARNESS_WRITE_CONTENT")
        if wcontent is None:
            wcontent = f"file-harness change for {ticket}\n"
        writes = [{"path": wpath, "content": wcontent}]

ws_real = os.path.realpath(workspace)
sensitive_names = {".env", "id_ed25519", "signing.key"}
for item in writes:
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        fail("scripted write entry must carry a string path")
    rel = item["path"]
    base_name = os.path.basename(rel)
    if (
        os.path.isabs(rel)
        or ".." in rel.split("/")
        or base_name in sensitive_names
        or base_name.endswith((".key", ".pem"))
    ):
        hook_lines.append(f"DENY write {rel}")
        with open(os.path.join(out, "hooks.log"), "w", encoding="utf-8") as hfh:
            hfh.write("\n".join(hook_lines) + "\n")
        fail(f"refused unsafe write path {rel!r}")
    target = os.path.realpath(os.path.join(ws_real, rel))
    if not target.startswith(ws_real + os.sep):
        hook_lines.append(f"DENY write {rel}")
        with open(os.path.join(out, "hooks.log"), "w", encoding="utf-8") as hfh:
            hfh.write("\n".join(hook_lines) + "\n")
        fail(f"write path {rel!r} escapes workspace")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as wfh:
        wfh.write(str(item.get("content", "")))
    hook_lines.append("ALLOW write -")

if not hook_lines:
    hook_lines.append("ALLOW read -")

with open(os.path.join(out, "hooks.log"), "w", encoding="utf-8") as hfh:
    hfh.write("\n".join(hook_lines) + "\n")

if script.get("final") is not None:
    final_text = str(script["final"])
elif env.get("FILE_HARNESS_FINAL_FILE") and os.path.isfile(env["FILE_HARNESS_FINAL_FILE"]):
    with open(env["FILE_HARNESS_FINAL_FILE"], encoding="utf-8") as ffh:
        final_text = ffh.read()
elif env.get("FILE_HARNESS_FINAL") is not None:
    final_text = env["FILE_HARNESS_FINAL"]
elif is_review:
    final_text = f"APPROVED\n\nOutside file-harness review passed for ticket {ticket}."
else:
    final_text = f"Apply file-harness change for {ticket}\n\nGenerated deterministically by outside/file-harness."

final_text = final_text.rstrip("\n")
with open(os.path.join(out, "final.md"), "w", encoding="utf-8") as ffh:
    ffh.write(final_text + "\n")

lines = final_text.split("\n")
title = lines[0]
rest = lines[1:]
while rest and not rest[0].strip():
    rest.pop(0)
body = "\n".join(rest).rstrip("\n")
with open(os.path.join(out, "pr.json"), "w", encoding="utf-8") as pfh:
    pfh.write(json.dumps({"title": title, "body": body}) + "\n")


def git_run(*args):
    return subprocess.run(["git", "-C", workspace, *args], capture_output=True, text=True, check=False)


status = git_run("status", "--porcelain")
if status.returncode != 0:
    fail(f"git status failed: {status.stderr.strip()}")
if status.stdout.strip():
    add_res = git_run("add", "-A")
    if add_res.returncode != 0:
        fail(f"git add failed: {add_res.stderr.strip()}")
    commit_res = git_run(
        "-c", "user.name=lights-out-crew",
        "-c", "user.email=crew@lights-out.invalid",
        "commit", "-q", "-m", title, "-m", body,
    )
    if commit_res.returncode != 0:
        fail(f"git commit failed: {commit_res.stderr.strip()}")

patch_res = subprocess.run(
    ["git", "-C", workspace, "format-patch", "--stdout", f"{base}..HEAD"],
    capture_output=True,
    check=False,
)
with open(os.path.join(out, "patch"), "wb") as pafh:
    if patch_res.returncode == 0:
        pafh.write(patch_res.stdout)

write_run_json(0)

secret_vals = []
for k, v in env.items():
    if k.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")) and isinstance(v, str) and len(v) >= 8:
        if v.lower() not in {"replace-me", "changeme"}:
            secret_vals.append((k, v.encode("utf-8")))

if secret_vals:
    for fname in sorted(os.listdir(out)):
        fpath = os.path.join(out, fname)
        if not os.path.isfile(fpath):
            continue
        with open(fpath, "rb") as afh:
            raw = afh.read()
        for k, sb in secret_vals:
            if sb in raw:
                fail(f"{k} appears in run artefact {fname}; refusing to record")
PY
