#!/usr/bin/env python3
"""Spike only (never merged): a scripted Gemini endpoint for agy.

Modes, from SPIKE_MODE:
  script  answer from $SPIKE_WORK/script.json: a list of steps, the step a
          request gets being the number of functionResponse parts in it.
          {"op":"shell","cmd":...} | {"op":"final","text":...} | {"op":"hang"}
          | {"op":"loop"} (a harmless shell call on every request, forever).
  proxy   forward to https://generativelanguage.googleapis.com and record the
          modelVersion each response carries.
Every request is logged to $SPIKE_WORK/requests.jsonl (path without the key
query parameter, header names only) and its body to bodies/NNNN.json.
"""
import json, os, re, sys, threading, time, urllib.request, ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qsl, urlencode

WORK = os.environ.get("SPIKE_WORK", ".")
MODE = os.environ.get("SPIKE_MODE", "script")
UPSTREAM = "https://generativelanguage.googleapis.com"
LOCK = threading.Lock()
N = [0]


def clean(path):
    p = urlsplit(path)
    q = [(k, v) for k, v in parse_qsl(p.query) if k != "key"]
    return p.path + ("?" + urlencode(q) if q else "")


def log(rec, raw):
    with LOCK:
        N[0] += 1
        rec["n"] = N[0]
        os.makedirs(os.path.join(WORK, "bodies"), exist_ok=True)
        if raw:
            with open(os.path.join(WORK, "bodies", "%04d.json" % N[0]), "wb") as f:
                f.write(raw)
        with open(os.path.join(WORK, "requests.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")


def tools_of(body):
    out = []
    for t in body.get("tools") or []:
        out += t.get("functionDeclarations") or t.get("function_declarations") or []
    return out


def responses_in(body):
    n = 0
    for c in body.get("contents") or []:
        for p in c.get("parts") or []:
            if "functionResponse" in p or "function_response" in p:
                n += 1
    return n


def shell_call(tools, cmd, cwd):
    """Builds a call to whichever declared tool runs a command, filling its
    required arguments from their names; logs nothing it cannot fill."""
    cand = [t for t in tools if re.search(r"run_command|shell|bash|command", t.get("name", ""), re.I)]
    if not cand:
        return None
    t = cand[0]
    params = t.get("parameters") or t.get("parametersJsonSchema") or {}
    props = params.get("properties") or {}
    args = {}
    for k, s in props.items():
        typ = (s.get("type") or "").lower()
        if re.search(r"command", k, re.I):
            args[k] = cmd
        elif re.search(r"cwd|dir", k, re.I):
            args[k] = cwd
        elif k in (params.get("required") or []):
            args[k] = 0 if typ in ("integer", "number") else (False if typ == "boolean" else "spike")
    if "WaitMsBeforeAsync" in props:
        args["WaitMsBeforeAsync"] = 10000
    return {"name": t["name"], "args": args}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        log({"method": "GET", "path": clean(self.path), "headers": sorted(self.headers.keys())}, None)
        if MODE == "proxy":
            return self.forward(None)
        self.send_json(200, {"models": []})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        rec = {"method": "POST", "path": clean(self.path), "headers": sorted(self.headers.keys()),
               "has_key": bool(self.headers.get("x-goog-api-key") or "key=" in self.path)}
        if MODE == "proxy":
            rec["served"] = self.forward(raw)
            log(rec, raw)
            return
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        tools = tools_of(body)
        rec["tool_names"] = [t.get("name") for t in tools]
        if tools and not os.path.exists(os.path.join(WORK, "tools.json")):
            with open(os.path.join(WORK, "tools.json"), "w") as f:
                json.dump(tools, f)
        m = re.search(r"/models/([^/:]+):", self.path)
        served = (m.group(1) if m else "unknown") + "-spike"
        rec["served"] = served
        if not tools or ":countTokens" in self.path:
            log(rec, raw)
            return self.reply({"text": "ok"}, served, (0, 0))
        with open(os.path.join(WORK, "script.json")) as f:
            script = json.load(f)
        cwd = os.environ.get("SPIKE_CWD", ".")
        idx = responses_in(body)
        step = script[min(idx, len(script) - 1)]
        rec["step"] = idx
        rec["op"] = step["op"]
        log(rec, raw)
        if step["op"] == "hang":
            time.sleep(3600)
            return
        if step["op"] == "tool":
            return self.reply({"functionCall": {"name": step["name"], "args": step["args"]}}, served, (900, 100))
        if step["op"] == "final":
            return self.reply({"text": step["text"]}, served, (900, 100))
        cmd = step.get("cmd") or "echo loop %d" % idx
        call = shell_call(tools, cmd, cwd)
        if call is None:
            return self.reply({"text": "no shell tool declared"}, served, (900, 100))
        return self.reply({"functionCall": call}, served, (900, 100))

    def reply(self, part, served, tok):
        chunk = {"candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP", "index": 0}],
                 "usageMetadata": {"promptTokenCount": tok[0], "candidatesTokenCount": tok[1],
                                   "totalTokenCount": tok[0] + tok[1]},
                 "modelVersion": served}
        path = urlsplit(self.path)
        if path.path.endswith(":streamGenerateContent"):
            if ("alt", "sse") in parse_qsl(path.query):
                data = ("data: " + json.dumps(chunk) + "\r\n\r\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self.send_json(200, [chunk])
        self.send_json(200, chunk)

    def send_json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def forward(self, raw):
        hdr = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "accept-encoding", "connection")}
        req = urllib.request.Request(UPSTREAM + self.path, data=raw, headers=hdr, method=self.command)
        try:
            resp = urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=300)
            status, body, ctype = resp.status, resp.read(), resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            status, body, ctype = e.code, e.read(), e.headers.get("Content-Type", "application/json")
        served = sorted(set(re.findall(rb'"modelVersion"\s*:\s*"([^"]+)"', body)))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return [s.decode() for s in served] + ["status=%d" % status]


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8089
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
