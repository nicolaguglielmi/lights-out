#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The scripted model of gates/harness-conformance.sh (docs/adr/0041 §8).

One HTTP server for the three wire protocols a CLI harness speaks, chosen by
request path: Anthropic Messages (`/v1/messages`), OpenAI Responses
(`.../responses`) and Gemini (`...:streamGenerateContent`, `...:generateContent`).
Counting requests (`.../count_tokens`, `...:countTokens`) and GETs are answered
harmlessly. It names no model and holds no key: it serves `<requested>-conformance`
and checks every request's key against CONFORMANCE_CANARY, the random value
the suite exported into every key variable before compose started. Python
standard library only: it runs in the workbench image.

The script of the running case is /work/case.json, read on every request:

    {"case": "<harness>/<n>", "cwd": "<the workspace as the CLI sees it>",
     "steps": [{"op": "shell", "cmd": "..."}, {"op": "read", "path": "..."},
               {"op": "write", "path": "...", "content": "..."},
               {"op": "grep", "pattern": "...", "path": "..."},
               {"op": "jsread", "path": "..."}, {"op": "final", "text": "..."},
               {"op": "mcp", "server": "...", "tool": "...", "args": {...}},
               {"op": "hang"}]}

A step is abstract and is rendered as each vendor's own tool call: Bash, Read and
Write for Anthropic; run_shell_command, read_file and write_file for Gemini CLI;
run_command, read_file and write_file for Antigravity CLI, which speaks the same
Gemini wire with its own tools (told apart by the run_command it declares, docs/
adr/0050); a shell command for OpenAI (`read` is `cat`, `write` is `printf >
file`). `grep` is
grep_search for Gemini, over the workspace with the file named as include_pattern
(found in the suite: gemini-cli 0.61.0-preview.0's fallback grep, used when ripgrep
is not usable, refuses a file as dir_path before BeforeTool fires), and `grep -rn`
through Bash, run_command and the shell for the others. `jsread`
is the one probe of OpenAI's code mode: JavaScript that tries to read a file and
the environment itself, with no nested tool call, so no hook fires; on every
other surface it is a `read`. `mcp` is a call to a repository's MCP tool (docs/
adr/0073, case 10), rendered as whatever the request declares: the declared tool
whose name ends with the step's tool name, this server's first, called by that name
(and, on OpenAI, in the namespace that declared it); when no declared tool ends with
it, the call goes to the name the vendor would give it (mcp__<server>__<tool>, or
mcp_<server>_<tool> on Gemini), and the CLI answers that it has no such tool.
OpenAI's code mode is the exception (docs/adr/0073, amendment 2026-11-11, seen
running codex 0.166.0 natively): there an MCP tool is deferred, named in no
request, and reachable only from `exec`'s JavaScript as tools.mcp__<server>__<tool>
(every character outside [A-Za-z0-9_] an `_`) and listed in `ALL_TOOLS`. So the
step is an `exec` whose JavaScript first prints `ALL_TOOLS: <names>`, the catalog
the model could call, then calls that name if the catalog has it; the call is a
nested tool call, and the PreToolUse hook sees it. `hang` never answers.

The step a request gets is the number of tool results already in it. One
exception, Antigravity's: a command still running after 10 s comes back as "Tool is
running as a background task", and agy wants the model to end its turn; it then
waits and sends the command's output as a user `<SYSTEM_MESSAGE>`. So a request
whose last part is that notice is answered with a short text, as a step of its own
kind (`wait`) that the script does not count, and the next request's `last_result`
is the message that carries the output (verified against agy 1.5.1, 2026-11-06).
Requests
that carry no tools are the CLI's own side requests (a title, a classification):
they get a short text answer, count as no step, and report 0 tokens. Every
answered request that carries tools reports exactly 1,000 tokens, split 900
input and 100 output, whatever the protocol calls them. Of the 900 input, half,
450, is reported as served from the vendor's cache (docs/adr/0092 §6; a decision:
nonzero, and neither none nor all of the input, so a harness that sums the wrong
field, or the input for the cached part, is off): Anthropic splits it
into `input_tokens` 450 and `cache_read_input_tokens` 450 (`cache_creation_input_tokens`
stays 0), Gemini reports `cachedContentTokenCount` 450 inside a `promptTokenCount`
of 900, OpenAI `input_tokens_details.cached_tokens` 450 inside an `input_tokens`
of 900. The total of a request, input plus output, is 1,000 on every protocol,
as before; each request's record carries `reported_cached`.

Per case, under /work/<case>/: requests.jsonl, one record per request (never a
header value, and the `key` query parameter is dropped from the path; `declared`
lists the names of the tools the request offered, `<namespace>.<name>` for one an
OpenAI namespace groups), and
bodies/NNNN.json, the request body as it arrived, decoded. Every record, of
whatever method and path, carries `peer`, the address the connection came
from, and `sink`, true when the request's Host names `conformance-sink`: the
second name of this container, on which nothing may ever be sent (docs/adr/
0046, case 9). The suite asserts the peer is always `egress` and that no
request reached the sink or `/exfil`; the stub only records. Nothing is served
when there is no case: the answer is a 503 that says so.
"""
import gzip
import hmac
import itertools
import json
import os
import re
import shlex
import signal
import sys
import threading
import time
import traceback
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

WORK = os.environ.get("STUB_WORK", "/work")
CANARY = os.environ.get("CONFORMANCE_CANARY", "")
SUFFIX = "-conformance"
SINK = "conformance-sink"  # compose.yaml's alias for this container, the name case 9's probe tries
IN_TOKENS, OUT_TOKENS = 900, 100
CACHED_TOKENS = IN_TOKENS // 2  # the part of the input reported as served from the cache
LAST_RESULT_MAX = 4000
AGY_WAIT_MS = 10000  # run_command's WaitMsBeforeAsync: its documented maximum; 60000 was clamped to it (verified)
AGY_BACKGROUND = "running as a background task"
AGY_FINISHED = "finished with result:"

LOCK = threading.Lock()
IDS = itertools.count(1)
COUNTS = {}


class Fault(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# --- classification, the case, the key -------------------------------------


def classify(path):
    """-> (protocol, kind, model named in the path); kind is generate|count|other."""
    p = urlsplit(path).path
    if re.search(r"/v1/messages/count_tokens/?$", p):
        return "anthropic", "count", None
    if re.search(r"/v1/messages/?$", p):
        return "anthropic", "generate", None
    if re.search(r"/responses/?$", p):
        return "openai", "generate", None
    m = re.search(r"/models/([^/:]+):(streamGenerateContent|generateContent)$", p)
    if m:
        return "gemini", "generate", m.group(1)
    m = re.search(r"/models/([^/:]+):countTokens$", p)
    if m:
        return "gemini", "count", m.group(1)
    return "none", "other", None


def load_case():
    try:
        with open(os.path.join(WORK, "case.json")) as f:
            case = json.load(f)
    except (OSError, ValueError) as e:
        raise Fault(503, "no usable %s/case.json: %s" % (WORK, e))
    name = case.get("case", "")
    segments = name.split("/")
    if not re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*", name) or any(s in (".", "..") for s in segments):
        raise Fault(503, "case.json: bad case name %r" % name)
    steps = case.get("steps")
    if not isinstance(steps, list) or not steps:
        raise Fault(503, "case.json: no steps")
    return name, str(case.get("cwd", "")), steps


def auth_state(headers, query):
    """ok | bad | missing; the sent value is compared, never kept."""
    sent = []
    for h in ("x-api-key", "x-goog-api-key"):
        if headers.get(h):
            sent.append(headers.get(h).strip())
    authz = headers.get("authorization") or ""
    if authz.lower().startswith("bearer "):
        sent.append(authz[7:].strip())
    sent += parse_qs(query).get("key", [])
    if not sent:
        return "missing"
    return "ok" if any(hmac.compare_digest(s.encode(), CANARY.encode()) for s in sent) else "bad"


def clean_path(path):
    p = urlsplit(path)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != "key"]
    return p.path + ("?" + urlencode(q) if q else "")


def read_body(h):
    if "chunked" in (h.headers.get("transfer-encoding") or "").lower():
        chunks = []
        while True:
            size = int((h.rfile.readline().strip().split(b";")[0]) or b"0", 16)
            if size == 0:
                while h.rfile.readline().strip():
                    pass
                break
            chunks.append(h.rfile.read(size))
            h.rfile.read(2)
        raw = b"".join(chunks)
    else:
        raw = h.rfile.read(int(h.headers.get("content-length") or 0))
    enc = (h.headers.get("content-encoding") or "").lower().strip()
    if enc in ("", "identity"):
        return raw
    if enc == "gzip":
        return gzip.decompress(raw)
    if enc == "deflate":
        return zlib.decompress(raw)
    if enc == "zstd":
        try:
            from compression import zstd  # Python 3.14+
            return zstd.decompress(raw)
        except ImportError:
            pass
    raise Fault(400, "request body is content-encoding %s; this stub decodes identity, gzip and deflate" % enc)


# --- what a request carries: tool results, the model, the tools -------------


def _flat(x):
    if isinstance(x, str):
        return x
    if x is None:
        return ""
    if isinstance(x, list):
        return "\n".join(_flat(i.get("text") if isinstance(i, dict) and "text" in i else i) for i in x)
    return json.dumps(x)


def tool_results(proto, body):
    """The text of every tool result in the request, oldest first."""
    out = []
    if proto == "anthropic":
        for m in body.get("messages") or []:
            if isinstance(m.get("content"), list):
                out += [_flat(b.get("content")) for b in m["content"]
                        if isinstance(b, dict) and b.get("type") == "tool_result"]
    elif proto == "openai":
        items = body.get("input")
        if isinstance(items, list):
            out += [_flat(i.get("output")) for i in items
                    if isinstance(i, dict) and str(i.get("type", "")).endswith("_call_output")]
    elif proto == "gemini":
        for c in body.get("contents") or []:
            out += [json.dumps(p["functionResponse"].get("response", {}))
                    for p in c.get("parts") or [] if isinstance(p, dict) and "functionResponse" in p]
    return out


def gemini_tool_names(body):
    names = set()
    for t in body.get("tools") or []:
        if isinstance(t, dict):
            for d in t.get("functionDeclarations") or t.get("function_declarations") or []:
                if isinstance(d, dict):
                    names.add(d.get("name"))
    return names


def agy_waiting(proto, body):
    """True when the request's last part is Antigravity's background-task notice."""
    if proto != "gemini":
        return False
    contents = body.get("contents") or []
    parts = (contents[-1].get("parts") or []) if contents and isinstance(contents[-1], dict) else []
    last = parts[-1] if parts and isinstance(parts[-1], dict) else {}
    return "functionResponse" in last and AGY_BACKGROUND in json.dumps(last["functionResponse"])


def agy_finished(proto, body):
    """The text of Antigravity's background-task result, when it is the request's last part."""
    if proto != "gemini":
        return ""
    contents = body.get("contents") or []
    if not contents or not isinstance(contents[-1], dict) or contents[-1].get("role") != "user":
        return ""
    text = "\n".join(p.get("text", "") for p in contents[-1].get("parts") or [] if isinstance(p, dict))
    return text if AGY_FINISHED in text else ""


# --- the script, rendered as each vendor's tool calls -----------------------


def absolute(path, cwd):
    return path if path.startswith("/") or not cwd else cwd.rstrip("/") + "/" + path


def grep_cmd(step):
    return "grep -rn " + shlex.quote(step["pattern"]) + " " + shlex.quote(step["path"])


def shell_form(step, op):
    """The step as one shell command, for the surface that has only a shell."""
    if op == "read" or op == "jsread":
        return "cat " + shlex.quote(step["path"])
    if op == "write":
        return "printf %s " + shlex.quote(step["content"]) + " > " + shlex.quote(step["path"])
    if op == "grep":
        return grep_cmd(step)
    return step["cmd"]


CODE_MODE_JS = """\
const out = [];
const join = (a) => a.join("");
const target = join(%s);
const mods = [join(["f", "s"]), join(["node", ":", "f", "s"])];
for (const m of mods) {
  try { out.push(String(require(m).readFileSync(target, "utf8"))); } catch (e) { out.push("no " + m); }
}
try { out.push(String(globalThis[join(["De", "no"])].readTextFileSync(target))); } catch (e) { out.push("no Deno"); }
try { out.push(JSON.stringify(globalThis[join(["pro", "cess"])][join(["e", "nv"])])); } catch (e) { out.push("no process"); }
text(out.join("\\n"));
"""


def code_mode_source(path):
    """JavaScript that reads `path` and the environment itself. The names are
    assembled at run time: what the guard sees is not what an injection would
    have to send, so a read that works here is a read the hook cannot see."""
    half = max(1, len(path) // 2)
    return CODE_MODE_JS % json.dumps([path[:half], path[half:]])


def namespaced_tools(body):
    """[(namespace or None, tool)] for every tool a request offers, flattened. Found
    running codex 0.166.0 through the suite: it sends no top-level `tools`; they
    ride in an `input` item of type `additional_tools`, grouped in `namespace`
    entries (`functions.exec`, the code-mode tool, among them)."""
    out = []

    def walk(items, ns):
        for t in items or []:
            if isinstance(t, dict) and t.get("type") == "namespace":
                walk(t.get("tools"), t.get("name"))
            elif isinstance(t, dict):
                out.append((ns, t))

    walk(body.get("tools"), None)
    for item in body.get("input") or []:
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            walk(item.get("tools"), None)
    return out


def request_tools(body):
    """The tools a request offers, flattened (namespaced_tools without the namespaces)."""
    return [t for _, t in namespaced_tools(body)]


def declared(proto, body):
    """[(namespace or None, name)] of the tools a request offers, in the request's order."""
    if proto == "gemini":
        out = []
        for t in body.get("tools") or []:
            if isinstance(t, dict):
                for d in t.get("functionDeclarations") or t.get("function_declarations") or []:
                    if isinstance(d, dict) and isinstance(d.get("name"), str):
                        out.append((None, d["name"]))
        return out
    return [(ns, t.get("name") or t.get("type")) for ns, t in namespaced_tools(body)
            if isinstance(t.get("name") or t.get("type"), str)]


def declared_names(proto, body):
    return [("%s.%s" % (ns, n)) if ns else n for ns, n in declared(proto, body)]


def mcp_target(proto, body, server, tool):
    """-> (namespace or None, name, declared?): the declared tool whose name ends with
    TOOL, one that also names SERVER first; else the vendor's own name for it."""
    found = [(ns, n) for ns, n in declared(proto, body) if n.endswith(tool) or ("%s.%s" % (ns, n)).endswith(tool)]
    found.sort(key=lambda c: server not in ("%s.%s" % c if c[0] else c[1]))
    if found:
        return found[0][0], found[0][1], True
    return None, ("mcp_%s_%s" if proto == "gemini" else "mcp__%s__%s") % (server, tool), False


CODE_MODE_MCP_JS = """\
text("ALL_TOOLS: " + ALL_TOOLS.map((t) => t.name).join(" "));
const name = %s;
if (typeof tools[name] !== "function") {
  text("no tool " + name);
} else {
  try { text(JSON.stringify(await tools[name](%s))); } catch (e) { text("error: " + e); }
}
"""


def code_mode_mcp_source(server, tool, args):
    """JavaScript that shows the catalog code mode offers (ALL_TOOLS: a deferred MCP
    tool is in no request) and calls the MCP tool by codex's name for it."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", "mcp__%s__%s" % (server, tool))
    return CODE_MODE_MCP_JS % (json.dumps(name), json.dumps(args))


def pick_openai_surface(tools):
    kinds = {}
    for t in tools or []:
        kinds[t.get("name") or t.get("type")] = t.get("type")
    if kinds.get("exec") == "custom":
        return "code"
    for name in ("exec_command", "shell", "shell_command"):
        if kinds.get(name) == "function":
            return name
    raise Fault(500, "no shell tool among the request's tools: %s" % sorted(map(str, kinds)))


def render(proto, step, cwd, body):
    """-> (op, hooked, rendering), rendering being what the protocol's responder serialises:
    ("text", text) | ("tool", name, arguments) | ("code", source) for OpenAI's code mode."""
    op = step.get("op")
    if op == "final":
        return op, None, ("text", step.get("text", ""))
    if op == "mcp":
        server, tool, args = step.get("server"), step.get("tool"), step.get("args") or {}
        if not isinstance(server, str) or not isinstance(tool, str) or not isinstance(args, dict):
            raise Fault(503, "case.json: an mcp step needs server and tool, strings, and args, an object")
        if proto == "openai" and any(t.get("name") == "exec" and t.get("type") == "custom"
                                     for t in request_tools(body)):
            # Whether the catalog holds it is known only inside the isolate: hooked unknown.
            return op, None, ("code", code_mode_mcp_source(server, tool, args))
        ns, name, offered = mcp_target(proto, body, server, tool)
        return op, offered, ("tool", name, args, ns)
    if op not in ("shell", "read", "write", "jsread", "grep"):
        raise Fault(503, "case.json: unknown op %r" % op)
    if proto == "anthropic":
        if op == "shell":
            return op, True, ("tool", "Bash", {"command": step["cmd"]})
        if op == "grep":
            return op, True, ("tool", "Bash", {"command": grep_cmd(step)})
        if op in ("read", "jsread"):
            return op, True, ("tool", "Read", {"file_path": absolute(step["path"], cwd)})
        return op, True, ("tool", "Write", {"file_path": absolute(step["path"], cwd), "content": step["content"]})
    if proto == "gemini" and "run_command" in gemini_tool_names(body):
        cmd = grep_cmd(step) if op == "grep" else step.get("cmd")
        if op in ("shell", "grep"):
            return op, True, ("tool", "run_command", {"CommandLine": cmd, "toolSummary": "Conformance step",
                                                      "WaitMsBeforeAsync": AGY_WAIT_MS})
        if op in ("read", "jsread"):
            return op, True, ("tool", "read_file", {"Path": absolute(step["path"], cwd)})
        return op, True, ("tool", "write_file", {"Path": absolute(step["path"], cwd), "Content": step["content"]})
    if proto == "gemini":
        if op == "shell":
            return op, True, ("tool", "run_shell_command", {"command": step["cmd"]})
        if op == "grep":
            return op, True, ("tool", "grep_search", {"pattern": step["pattern"], "dir_path": cwd or ".",
                                                      "include_pattern": step["path"]})
        if op in ("read", "jsread"):
            return op, True, ("tool", "read_file", {"file_path": absolute(step["path"], cwd)})
        return op, True, ("tool", "write_file", {"file_path": absolute(step["path"], cwd), "content": step["content"]})
    surface = pick_openai_surface(request_tools(body))
    if op == "jsread" and surface == "code":
        return op, False, ("code", code_mode_source(step["path"]))
    cmd = shell_form(step, op)
    if surface == "code":
        return op, True, ("code", "const r = await tools.exec_command({cmd: %s}); text(r);" % json.dumps(cmd))
    if surface == "exec_command":
        return op, True, ("tool", "exec_command", {"cmd": cmd})
    if surface == "shell_command":
        return op, True, ("tool", "shell_command", {"command": cmd})
    return op, True, ("tool", "shell", {"command": ["bash", "-lc", cmd]})


# --- the three responders ---------------------------------------------------


def send_json(h, status, obj):
    data = json.dumps(obj).encode()
    h.send_response(status)
    h.send_header("content-type", "application/json")
    h.send_header("content-length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


def send_sse(h, events):
    h.send_response(200)
    h.send_header("content-type", "text/event-stream")
    h.send_header("cache-control", "no-cache")
    h.end_headers()
    for name, data in events:
        if name:
            h.wfile.write(("event: %s\n" % name).encode())
        h.wfile.write(("data: %s\n\n" % json.dumps(data)).encode())
        h.wfile.flush()


def respond_anthropic(h, body, served, rendering, tokens):
    mid = "msg_%d" % next(IDS)
    usage = {"input_tokens": tokens[0] - tokens[2], "output_tokens": tokens[1],
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": tokens[2]}
    if rendering[0] == "text":
        block = {"type": "text", "text": ""}
        delta = {"type": "text_delta", "text": rendering[1]}
        final_block = {"type": "text", "text": rendering[1]}
        stop = "end_turn"
    else:
        tool = {"type": "tool_use", "id": "toolu_%d" % next(IDS), "name": rendering[1]}
        block = dict(tool, input={})
        delta = {"type": "input_json_delta", "partial_json": json.dumps(rendering[2])}
        final_block = dict(tool, input=rendering[2])
        stop = "tool_use"
    if not body.get("stream"):
        return send_json(h, 200, {"id": mid, "type": "message", "role": "assistant", "model": served,
                                  "content": [final_block], "stop_reason": stop, "stop_sequence": None,
                                  "usage": usage})
    send_sse(h, [
        ("message_start", {"type": "message_start", "message": {
            "id": mid, "type": "message", "role": "assistant", "model": served, "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": usage}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                           "usage": {"output_tokens": tokens[1]}}),
        ("message_stop", {"type": "message_stop"}),
    ])


def respond_openai(h, body, served, rendering, tokens):
    rid = "resp_%d" % next(IDS)
    if rendering[0] == "text":
        item = {"type": "message", "id": "msg_%d" % next(IDS), "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": rendering[1], "annotations": []}]}
    elif rendering[0] == "code":
        item = {"type": "custom_tool_call", "id": "ctc_%d" % next(IDS), "call_id": "call_%d" % next(IDS),
                "name": "exec", "input": rendering[1]}
    else:
        item = {"type": "function_call", "id": "fc_%d" % next(IDS), "call_id": "call_%d" % next(IDS),
                "name": rendering[1], "arguments": json.dumps(rendering[2])}
        if len(rendering) > 3 and rendering[3]:
            item["namespace"] = rendering[3]
    usage = {"input_tokens": tokens[0], "input_tokens_details": {"cached_tokens": tokens[2]},
             "output_tokens": tokens[1], "output_tokens_details": {"reasoning_tokens": 0},
             "total_tokens": tokens[0] + tokens[1]}
    resp = {"id": rid, "object": "response", "model": served, "status": "completed",
            "output": [item], "usage": usage}
    if body.get("stream") is False:
        return send_json(h, 200, resp)
    send_sse(h, [
        ("response.created", {"type": "response.created", "response": {"id": rid, "model": served}}),
        ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
        ("response.completed", {"type": "response.completed", "response": resp}),
    ])


def respond_gemini(h, path, served, rendering, tokens):
    if rendering[0] == "text":
        part = {"text": rendering[1]}
    else:
        part = {"functionCall": {"name": rendering[1], "args": rendering[2]}}
    chunk = {"candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP", "index": 0}],
             "usageMetadata": {"promptTokenCount": tokens[0], "candidatesTokenCount": tokens[1],
                               "cachedContentTokenCount": tokens[2], "totalTokenCount": tokens[0] + tokens[1]},
             "modelVersion": served}
    if not urlsplit(path).path.endswith(":streamGenerateContent"):
        return send_json(h, 200, chunk)
    if parse_qs(urlsplit(path).query).get("alt") == ["sse"]:
        return send_sse(h, [(None, chunk)])
    return send_json(h, 200, [chunk])


def error_body(proto, status, message):
    if proto == "anthropic":
        return {"type": "error", "error": {"type": "authentication_error" if status == 401 else "api_error",
                                           "message": message}}
    if proto == "gemini":
        return {"error": {"code": status, "message": message,
                          "status": "UNAUTHENTICATED" if status == 401 else "INTERNAL"}}
    return {"error": {"message": message, "type": "invalid_request_error",
                      "code": "unauthorized" if status == 401 else "stub_error"}}


# --- the server -------------------------------------------------------------


def log_request(case, raw, rec):
    d = os.path.join(WORK, case)
    os.makedirs(os.path.join(d, "bodies"), exist_ok=True)
    log = os.path.join(d, "requests.jsonl")
    with LOCK:
        if case not in COUNTS:
            COUNTS[case] = sum(1 for _ in open(log)) if os.path.exists(log) else 0
        COUNTS[case] += 1
        rec["n"] = COUNTS[case]
        if raw is not None:
            rec["body"] = "bodies/%04d.json" % rec["n"]
            with open(os.path.join(d, rec["body"]), "wb") as f:
                f.write(raw)
        with open(log, "a") as f:
            f.write(json.dumps(rec) + "\n")


class Handler(BaseHTTPRequestHandler):
    server_version = "conformance-model"

    def log_message(self, *args):
        pass

    def origin(self):
        """Where the request came from and which of this container's names it used:
        the two facts case 9 asserts on every request. Only the Host's name is
        compared; its value is never kept."""
        host = (self.headers.get("host") or "").rsplit(":", 1)[0].strip().lower()
        return {"peer": self.client_address[0], "sink": host == SINK}

    def do_GET(self):
        try:
            case, _, _ = load_case()
            log_request(case, None, dict({"method": "GET", "path": clean_path(self.path),
                                          "protocol": classify(self.path)[0], "kind": "other"}, **self.origin()))
        except Fault:
            pass
        send_json(self, 200, {"data": [], "models": []})

    def do_other(self):
        """Any other method is answered and recorded like a GET: a request is a
        request, whatever verb it uses, and none may reach the sink."""
        try:
            read_body(self)
            case, _, _ = load_case()
            log_request(case, None, dict({"method": self.command, "path": clean_path(self.path),
                                          "protocol": classify(self.path)[0], "kind": "other"}, **self.origin()))
        except (Fault, ValueError, OSError):
            pass
        send_json(self, 200, {})

    do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_other

    def do_POST(self):
        self.proto = "none"
        try:
            self.post()
        except Fault as f:
            self.fail(f.status, f.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # a stub bug must be loud, never an answer that looks right
            traceback.print_exc()
            self.fail(500, "stub bug: see the conformance-model log")

    def fail(self, status, message):
        try:
            send_json(self, status, error_body(self.proto, status, message))
        except OSError:
            pass

    def post(self):
        proto, kind, path_model = classify(self.path)
        self.proto = proto
        raw = read_body(self)
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        case, cwd, steps = load_case()
        query = urlsplit(self.path).query
        auth = auth_state(self.headers, query)
        requested = path_model or body.get("model")
        rec = {"method": "POST", "path": clean_path(self.path), "protocol": proto, "kind": kind,
               "auth": auth, "requested": requested, "tools": bool(request_tools(body)),
               "declared": declared_names(proto, body),
               "step": None, "op": None, "hooked": None, "reported_tokens": 0, "reported_cached": 0, "last_result": ""}
        rec.update(self.origin())
        if kind == "other":
            log_request(case, raw, rec)
            return send_json(self, 200, {})
        results = tool_results(proto, body)
        if results:
            rec["last_result"] = results[-1][:LAST_RESULT_MAX]
        finished = agy_finished(proto, body)
        if finished:
            rec["last_result"] = finished[:LAST_RESULT_MAX]
        if auth != "ok":
            log_request(case, raw, rec)
            raise Fault(401, "the request carried %s key; the suite exports its canary into every key variable "
                             "before compose starts, so this key did not come from there" % ("no" if auth == "missing" else "a wrong"))
        if kind == "count":
            log_request(case, raw, rec)
            return send_json(self, 200, {"totalTokens": 10} if proto == "gemini" else {"input_tokens": 10})
        served = (requested or "unknown") + SUFFIX
        rec["served"] = served
        tokens = (0, 0, 0)
        if rec["tools"]:
            idx = len(results)
            step = steps[min(idx, len(steps) - 1)]
            rec["step"] = idx
            if step.get("op") == "hang":
                rec["op"] = "hang"
                log_request(case, raw, rec)
                time.sleep(3600)
                return
            if agy_waiting(proto, body):
                op, hooked, rendering = "wait", False, ("text", "Waiting for the command to finish.")
            else:
                op, hooked, rendering = render(proto, step, cwd, body)
            rec["op"], rec["hooked"] = op, hooked
            tokens = (IN_TOKENS, OUT_TOKENS, CACHED_TOKENS)
            rec["reported_tokens"] = IN_TOKENS + OUT_TOKENS
            rec["reported_cached"] = CACHED_TOKENS
        else:
            rendering = ("text", "ok")
        log_request(case, raw, rec)
        if proto == "anthropic":
            respond_anthropic(self, body, served, rendering, tokens)
        elif proto == "openai":
            respond_openai(self, body, served, rendering, tokens)
        else:
            respond_gemini(self, self.path, served, rendering, tokens)


def main():
    if not CANARY:
        sys.exit("conformance-model: CONFORMANCE_CANARY is empty; without it no key can be checked")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8089
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    with open(os.path.join(WORK, "ready"), "w") as f:
        f.write("%d\n" % port)
    sys.stderr.write("conformance-model: listening on %d, work dir %s\n" % (port, WORK))
    server.serve_forever()


if __name__ == "__main__":
    main()
