#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""console: the business client's view of the record (docs/adr/0047).

    GET  /                   console/index.html, five screens
    GET  /v1/logbook.json    the logbook, as core/logbook.sh wrote it
    GET  /v1/console.json    this console and this request: mode, auth, identity
    POST /v1/go-for-launch   the one write: core/promote.sh, as ./go promote runs it

It reads one document, the logbook, and nothing behind it: the tower, the
plane and the policy files reach it only through core/logbook.sh. Its one
write is go-for-launch through core/promote.sh, the same script and the
same checks as `./go promote`, with who pressed and `via: console`.
Every rule below is in docs/plans/console.md, "Decided at build".

LIVE (default): core/logbook.sh runs in one loop every CONSOLE_POLL_S
seconds into a file this process owns. SNAPSHOT (CONSOLE_SNAPSHOT=<file>):
that file is served, the reader never runs, and nothing can be pressed.

CONSOLE_AUTH, declared or the console does not start (§5):
  loopback  default; only when published on 127.0.0.1, ::1 or localhost.
  proxy     the identity is the CONSOLE_IDENTITY_HEADER a trusted proxy
            sets; a request without it gets 401; only CONSOLE_LAUNCHERS
            (comma-separated, verbatim) may press.
  public    only with CONSOLE_SNAPSHOT=console/snapshot/logbook.json.
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# absolute(), not resolve(): a throwaway root with console/ and core/
# symlinked runs its own core/, as ci/promote.sh's root does.
ROOT = pathlib.Path(__file__).absolute().parent.parent
PAGE = ROOT / "console" / "index.html"
COMMITTED_SNAPSHOT = ROOT / "console" / "snapshot" / "logbook.json"
# Provenance, decision: ADR 0047 §4 takes the board's 2 s (ADR 0035 §3):
# a canary shift or a press shows within one beat, at one reader run per
# beat whatever the number of tabs.
POLL_S = float(os.environ.get("CONSOLE_POLL_S", "2"))
LOOPBACK = ("127.0.0.1", "::1", "localhost")


def refuse(line):
    print(f"console: refusing to start — {line}", file=sys.stderr)
    sys.exit(1)


class Config:
    def __init__(self):
        e = os.environ
        self.listen = int(e.get("CONSOLE_LISTEN") or "8098")
        self.port = e.get("CONSOLE_PORT") or str(self.listen)
        self.bind = e.get("CONSOLE_BIND") or "127.0.0.1"
        self.publish = e.get("CONSOLE_PUBLISH") or self.bind
        self.auth = e.get("CONSOLE_AUTH") or "loopback"
        self.header = e.get("CONSOLE_IDENTITY_HEADER") or ""
        self.launchers = [x.strip() for x in (e.get("CONSOLE_LAUNCHERS") or "").split(",") if x.strip()]
        self.snapshot = e.get("CONSOLE_SNAPSHOT") or ""
        extra = [h.strip() for h in (e.get("CONSOLE_HOSTS") or "").split(",") if h.strip()]
        self.hosts = {f"localhost:{self.port}", f"127.0.0.1:{self.port}", f"[::1]:{self.port}"} | set(extra)

        if self.auth not in ("loopback", "proxy", "public"):
            refuse(f"CONSOLE_AUTH={self.auth} is not loopback, proxy or public")
        if self.auth == "loopback" and self.publish not in LOOPBACK:
            refuse(f"CONSOLE_AUTH=loopback, but CONSOLE_PUBLISH={self.publish} is not loopback; "
                   "set CONSOLE_AUTH=proxy behind a trusted proxy")
        if self.auth == "proxy" and not self.header:
            refuse("CONSOLE_AUTH=proxy needs CONSOLE_IDENTITY_HEADER, the header the proxy sets")
        if self.auth == "public":
            if not self.snapshot or pathlib.Path(self.snapshot).absolute() != COMMITTED_SNAPSHOT:
                refuse("CONSOLE_AUTH=public serves only CONSOLE_SNAPSHOT=console/snapshot/logbook.json")
        if self.snapshot:
            try:
                book = json.loads(pathlib.Path(self.snapshot).read_bytes())
                assert isinstance(book, dict) and str(book.get("schema", "")).startswith("lights-out/logbook/")
            except (OSError, ValueError, AssertionError):
                refuse(f"CONSOLE_SNAPSHOT={self.snapshot} is not a readable logbook")


CFG = None
state = {"bytes": None, "book": None, "read_at": None, "read_error": None}
press = threading.Lock()
reading = threading.Lock()  # the loop and a press never run the reader at once


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_once(out):
    """Run the reader once into out; keep the last good logbook on failure."""
    with reading:
        _read_once(out)


def _read_once(out):
    try:
        p = subprocess.run(["sh", "core/logbook.sh"], cwd=ROOT, capture_output=True, text=True,
                           timeout=60, env={**os.environ, "LOGBOOK_FILE": out})
        line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
        if p.returncode != 0:
            err = p.stderr.strip().splitlines()
            state["read_error"] = line or (err[-1] if err else f"core/logbook.sh exit {p.returncode}")
            return
        data = pathlib.Path(out).read_bytes()
        state.update(bytes=data, book=json.loads(data), read_at=utc(), read_error=None)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        state["read_error"] = f"console: reading the logbook failed: {exc}"


def refresh(out):
    while True:
        read_once(out)
        time.sleep(POLL_S)


def load_snapshot():
    data = pathlib.Path(CFG.snapshot).read_bytes()
    state.update(bytes=data, book=json.loads(data), read_at=None, read_error=None)


def identity(headers):
    """(identity or None, error status or None) for this request."""
    if CFG.auth == "proxy":
        who = headers.get(CFG.header)
        return (who, None) if who else (None, 401)
    return ("unauthenticated", None)


def may_launch(who):
    """(bool, reason) from this console's own rules; the record's are the logbook's."""
    if CFG.snapshot:
        commit = ((state["book"] or {}).get("source") or {}).get("commit") or "an unknown commit"
        return False, f"snapshot of {commit}, no plane behind it"
    if CFG.auth == "proxy" and who not in CFG.launchers:
        return False, f"{who} is not allowed to press go-for-launch (CONSOLE_LAUNCHERS)"
    return True, None


class Handler(BaseHTTPRequestHandler):
    server_version = "lights-out-console"

    def send(self, code, body, kind="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        who, err = identity(self.headers)
        if err:
            return self.send(err, {"error": f"no {CFG.header} header: reach the console through its proxy"})
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/v1/logbook.json":
            if state["bytes"] is None:
                return self.send(503, {"error": state["read_error"] or "console: first read still running"})
            return self.send(200, state["bytes"])
        if path == "/v1/console.json":
            can, reason = may_launch(who)
            return self.send(200, {
                "mode": "snapshot" if CFG.snapshot else "live",
                "auth": CFG.auth,
                "identity": who if CFG.auth == "proxy" else "loopback" if CFG.auth == "loopback" else None,
                "can_launch": can,
                "reason": reason,
                "read_at": state["read_at"],
                "read_error": state["read_error"],
                "snapshot": CFG.snapshot or None,
            })
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/v1/go-for-launch":
            return self.send(404, {"error": "not found"})
        who, err = identity(self.headers)
        if err:
            return self.send(err, {"error": f"no {CFG.header} header: reach the console through its proxy"})
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return self.send(415, {"error": "Content-Type must be application/json"})
        host = self.headers.get("Host") or ""
        origin = self.headers.get("Origin") or ""
        if host not in CFG.hosts or origin not in (f"http://{host}", f"https://{host}"):
            return self.send(403, {"error": "Origin and Host must be this console's own"})
        can, reason = may_launch(who)
        if not can:
            return self.send(403, {"error": reason})
        try:
            length = int(self.headers.get("Content-Length") or "0")
            body = json.loads(self.rfile.read(min(length, 4096)) or b"null")
            answer = body["answer"]
            version, held_at = body["version"], body["held_at"]
            sha = body.get("brief_sha256")
            assert answer in ("GO", "NO-GO", "PREPARE") and isinstance(version, str)
            assert answer == "PREPARE" or isinstance(sha, str)
        except (ValueError, KeyError, TypeError, AssertionError):
            return self.send(400, {"error": "the body must be {answer: GO|NO-GO|PREPARE, version, held_at, brief_sha256}"})
        if not press.acquire(blocking=False):
            return self.send(409, {"error": "another press is running"})
        try:
            read_once(self.server.logbook_file)  # judge against the record as it is now
            launch = (state["book"] or {}).get("launch") or {}
            record = launch.get("record") if isinstance(launch.get("record"), dict) else {}
            brief = launch.get("brief") or {}
            ready = launch.get("preparable") if answer == "PREPARE" else launch.get("pressable")
            if not ready or record.get("version") != version or record.get("held_at") != held_at \
                    or (answer != "PREPARE" and brief.get("sha256") != sha):
                return self.send(409, {"error": launch.get("disabled_reason")
                                       or "the record or the brief changed since this page read it; nothing moved"})
            env = {k: v for k, v in os.environ.items() if not k.startswith("GO_FOR_LAUNCH")}
            env.update(GO_FOR_LAUNCH_BY=who, GO_FOR_LAUNCH_VIA="console")
            if answer != "PREPARE":
                env.update(GO_FOR_LAUNCH=answer, GO_FOR_LAUNCH_BRIEF_SHA256=sha)
            p = subprocess.run(["sh", "core/promote.sh"], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=600)
            sys.stderr.write(p.stderr)
            line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else f"core/promote.sh exit {p.returncode}"
            read_once(self.server.logbook_file)
            return self.send(200, {"line": line, "exit": p.returncode})
        finally:
            press.release()

    def log_message(self, *args):
        pass


def main():
    global CFG
    CFG = Config()
    workdir = tempfile.mkdtemp(prefix="lights-out-console-")
    out = os.path.join(workdir, "logbook.json")
    if CFG.snapshot:
        load_snapshot()
    else:
        threading.Thread(target=refresh, args=(out,), daemon=True).start()
    server = ThreadingHTTPServer((CFG.bind, CFG.listen), Handler)
    server.logbook_file = out
    mode = f"snapshot of {CFG.snapshot}" if CFG.snapshot else "live"
    print(f"console: http://localhost:{CFG.port}  ({mode}, auth {CFG.auth}; Ctrl-C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
