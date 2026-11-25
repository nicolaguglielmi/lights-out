#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""console: the business client's view of the record (docs/adr/0047).

    GET  /                   console/index.html, workstation views
    GET  /v1/logbook.json    the logbook, as core/logbook.sh wrote it, with an ETag
    GET  /v1/console.json    this console and this request: mode, auth, identity
    GET  /v1/events          SSE stream of logbook ETag and read-state updates (docs/adr/0124)
    GET  /v1/charts.json     time-series metrics from CHARTS via core/console_ops.sh (docs/adr/0125)
    POST /v1/go-for-launch   the first write: core/promote.sh, as ./go promote runs it, with
                             the human's reason, required for a rollback (docs/adr/0079 §2)
    POST /v1/answer          the second: ./go answer's recipe (docs/adr/0055 §2)
    POST /v1/spec-check      writes nothing: a draft request through gates/spec.sh (docs/adr/0059 §4)
    POST /v1/request         the third: the draft as TICKETS_DIR/<id>.md, then ./go request's recipe
    POST /v1/intake          the fourth: a client's words, then ./go intake's recipe (docs/adr/0090 §8)
    POST /v1/intake-confirm  the fifth: the decisions on a proposed intake, ./go intake-confirm's recipe
    POST /v1/assign          assigns an opened ticket via `make assign TICKET=<id>` (docs/adr/0125)
    POST /v1/resume          resumes a stranded ticket via `make resume TICKET=<id>` (docs/adr/0125)
    POST /v1/watch-once      drains one orchestrator batch via `make watch ONCE=1` (docs/adr/0125)
    POST /v1/rollback        emergency rollback via core/console_ops.sh (docs/adr/0125)
    POST /v1/freeze          release-freeze toggle via core/console_ops.sh (docs/adr/0125)

It reads one document, the logbook, and nothing behind it: the tower, the
plane, the policy files and the project map reach it only through
core/logbook.sh. Its three writes are go-for-launch through
core/promote.sh, the same script and the same checks as `./go promote`;
the answer to a ticket's open question through `make answer`, the recipe
`./go answer` runs; and a new ticket's request, which only a draft the
spec gate passes reaches, through `make request`; each records who pressed
and `via: console`. An intake (docs/adr/0090 §8) starts from a client's
words through `make intake`, its questions are answered as a ticket's are,
and its proposed tickets are confirmed, edited or dropped through `make
intake-confirm`; both record who pressed and `via: console` too. The spec
check runs the gate on a draft in a temporary directory and deletes it.
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
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# absolute(), not resolve(): a throwaway root with console/ and core/
# symlinked runs its own core/, as ci/promote.sh's root does.
ROOT = pathlib.Path(__file__).absolute().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import oidc_rbac  # noqa: E402

PAGE = ROOT / "console" / "index.html"
COMMITTED_SNAPSHOT = ROOT / "console" / "snapshot" / "logbook.json"
# Provenance, decision: ADR 0047 §4 takes the board's 2 s (ADR 0035 §3):
# a canary shift or a press shows within one beat, at one reader run per
# beat whatever the number of tabs.
POLL_S = float(os.environ.get("CONSOLE_POLL_S", "2"))
# Provenance, decision: ADR 0124, 50 ms floor avoids tight zero-timeout
# Condition spin loops while allowing fast heartbeat checks in E2E tests.
MIN_SSE_HEARTBEAT_S = 0.05
# Provenance, decision: ADR 0124, 15 s is one quarter of standard 60 s
# reverse-proxy idle stream timeouts (deploy/proxy/, ADR 0111).
SSE_HEARTBEAT_S = max(
    MIN_SSE_HEARTBEAT_S,
    float(os.environ.get("SSE_HEARTBEAT_S") or os.environ.get("CONSOLE_SSE_HEARTBEAT_S") or "15.0"),
)
LOOPBACK = ("127.0.0.1", "::1", "localhost")
# Provenance, decision: docs/adr/0059 §4, twice the answer's 8 KiB for a
# request's several criteria; the check reads one file, no network.
DRAFT_MAX = 16384
CHECK_TIMEOUT_S = 10
OPS_TIMEOUT_S = 30
MAX_REASON_LEN = 512
TICKET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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
        self.jwt_verify = False
        self.jwt_header = (e.get("CONSOLE_JWT_HEADER") or "Authorization").strip() or "Authorization"
        self.jwt_policy = None
        self.jwks = None
        try:
            self.jwt_verify = oidc_rbac.is_jwt_verification_enabled(e)
            if self.auth == "proxy" and self.jwt_verify:
                self.jwt_policy = oidc_rbac.JwtPolicy.from_env(e)
                if (e.get("CONSOLE_JWKS_URL") or "").strip() or (e.get("CONSOLE_JWKS_FILE") or "").strip():
                    self.jwks = oidc_rbac.JwksCache.from_env(e)
            oidc_rbac.resolve_role("config-probe", {}, e)
        except oidc_rbac.OidcRbacError as exc:
            refuse(str(exc))
        if self.auth == "proxy" and not self.jwt_verify and not self.header:
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
state = {"bytes": None, "book": None, "etag": None, "read_at": None, "read_error": None, "seq": 0}
state_cond = threading.Condition()
class Press:
    """One press at a time. The thread that holds it lets go before it
    answers (send() releases it): a press sent on a 200 must find it free
    (2026-11-25: assign, rollback and freeze answered first, and the next
    press got 409 under load)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._mine = threading.local()

    def acquire(self, blocking=True):
        if self._lock.acquire(blocking):
            self._mine.held = True
            return True
        return False

    def release(self):
        if getattr(self._mine, "held", False):
            self._mine.held = False
            self._lock.release()


press = Press()
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
            err_msg = line or (err[-1] if err else f"core/logbook.sh exit {p.returncode}")
            with state_cond:
                changed = state["read_error"] != err_msg
                state["read_error"] = err_msg
                if changed:
                    state["seq"] += 1
                    state_cond.notify_all()
            return
        data = pathlib.Path(out).read_bytes()
        parsed_book = json.loads(data)
        new_tag = etag(data)
        now_at = utc()
        with state_cond:
            changed = (new_tag != state["etag"]) or (state["read_error"] is not None)
            state.update(bytes=data, book=parsed_book, etag=new_tag, read_at=now_at, read_error=None)
            if changed:
                state["seq"] += 1
                state_cond.notify_all()
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        err_msg = f"console: reading the logbook failed: {exc}"
        with state_cond:
            changed = state["read_error"] != err_msg
            state["read_error"] = err_msg
            if changed:
                state["seq"] += 1
                state_cond.notify_all()


def refresh(out):
    while True:
        read_once(out)
        time.sleep(POLL_S)


def load_snapshot():
    data = pathlib.Path(CFG.snapshot).read_bytes()
    parsed_book = json.loads(data)
    with state_cond:
        state.update(bytes=data, book=parsed_book, etag=etag(data), read_at=None, read_error=None,
                     seq=state["seq"] + 1)
        state_cond.notify_all()


def etag(data):
    return '"' + hashlib.sha256(data).hexdigest() + '"'


def sse_frame():
    """Format one SSE event frame (`logbook` or `pending`); caller holds state_cond."""
    mode = "snapshot" if CFG.snapshot else "live"
    payload = {
        "etag": state["etag"],
        "mode": mode,
        "read_at": state["read_at"],
        "read_error": state["read_error"],
    }
    event_name = "pending" if state["etag"] is None else "logbook"
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")


def identity(headers):
    """(identity or None, error status or None) for this request."""
    if CFG.auth == "proxy":
        if CFG.jwt_verify:
            raw_jwt, err = oidc_rbac.extract_jwt(headers, CFG.jwt_header)
            if err:
                return None, 401
            try:
                claims = oidc_rbac.verify_jwt(raw_jwt, CFG.jwt_policy, jwks=CFG.jwks)
            except oidc_rbac.OidcRbacError:
                return None, 401
            who = oidc_rbac.identity_from_claims(claims, os.environ)
            return (who, None) if who else (None, 401)
        who = oidc_rbac._get_header(headers, CFG.header)
        who = str(who).strip() if who is not None else ""
        return (who, None) if who else (None, 401)
    return ("unauthenticated", None)


def may_launch(who):
    """(bool, reason) from this console's own rules; the record's are the logbook's."""
    if CFG.snapshot:
        commit = ((state["book"] or {}).get("source") or {}).get("commit") or "an unknown commit"
        return False, f"snapshot of {commit}, no plane behind it"
    if CFG.auth == "proxy":
        if who in CFG.launchers or oidc_rbac.resolve_role(who, None, os.environ) == "launcher":
            return True, None
        return False, f"{who} is not allowed to press go-for-launch (CONSOLE_LAUNCHERS)"
    return True, None


def _can_launch(who, role):
    """(bool, reason) combining snapshot check with resolved RBAC role."""
    if CFG.snapshot:
        return may_launch(who)
    if CFG.auth == "proxy":
        allowed, why = oidc_rbac.authorize(role, "POST", "/v1/go-for-launch")
        if allowed:
            return True, None
        if not CFG.jwt_verify and (os.environ.get("CONSOLE_RBAC") or "").strip().lower() != "strict":
            return False, f"{who} is not allowed to press go-for-launch (CONSOLE_LAUNCHERS)"
        return False, why
    allowed, why = oidc_rbac.authorize(role, "POST", "/v1/go-for-launch")
    return (True, None) if allowed else (False, why)


def _ticket_resumable(ticket_obj):
    """True when the logbook shows a stranded or failed-step ticket."""
    if not isinstance(ticket_obj, dict):
        return False
    outcome = ticket_obj.get("outcome")
    if isinstance(outcome, dict) and outcome.get("verdict") == "stranded":
        return True
    if ticket_obj.get("stranded"):
        return True
    history = ticket_obj.get("history") or []
    last = history[-1] if history and isinstance(history[-1], dict) else {}
    return bool(last.get("error"))


class Handler(BaseHTTPRequestHandler):
    server_version = "lights-out-console"

    def send(self, code, body, kind="application/json", tag=None):
        press.release()  # a no-op unless this thread holds it
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        if tag:
            self.send_header("ETag", tag)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def stream_events(self, once):
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            with state_cond:
                seen_seq = state["seq"]
                initial = sse_frame()
            self.wfile.write(initial)
            self.wfile.flush()
            if once:
                return
            while not getattr(self.server, "shutting_down", False):
                with state_cond:
                    if state["seq"] == seen_seq and not getattr(self.server, "shutting_down", False):
                        state_cond.wait(timeout=SSE_HEARTBEAT_S)
                    if getattr(self.server, "shutting_down", False):
                        return
                    changed = state["seq"] != seen_seq
                    seen_seq = state["seq"]
                    chunk = sse_frame() if changed else b": keepalive\n\n"
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        known_get = path in (
            "/",
            "/index.html",
            "/v1/events",
            "/v1/logbook.json",
            "/v1/console.json",
            "/v1/charts.json",
        )
        if not known_get:
            who, err = identity(self.headers)
            if err:
                if os.environ.get("CONSOLE_AUDIT_LOG"):
                    oidc_rbac.append_audit_event(
                        event="request_authz",
                        decision="deny",
                        identity=None,
                        role=None,
                        method="GET",
                        route=path,
                        status=err,
                        reason="authentication failed",
                    )
                hdr = CFG.jwt_header if CFG.jwt_verify else CFG.header
                return self.send(err, {"error": f"no {hdr} header: reach the console through its proxy"})
            return self.send(404, {"error": "not found"})
        decision = oidc_rbac.evaluate_request(self.headers, "GET", path, env=os.environ, jwks=CFG.jwks)
        if not decision.ok:
            return self.send(decision.status, {"error": decision.reason})
        who = decision.identity
        if path in ("/", "/index.html"):
            return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/v1/events":
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            once = bool(CFG.snapshot) or ("1" in qs.get("once", ()))
            return self.stream_events(once)
        if path == "/v1/logbook.json":
            if state["bytes"] is None:
                return self.send(503, {"error": state["read_error"] or "console: first read still running"})
            data, tag = state["bytes"], state["etag"]
            if self.headers.get("If-None-Match") == tag:
                return self.send(304, b"", tag=tag)
            return self.send(200, data, tag=tag)
        if path == "/v1/console.json":
            can, reason = _can_launch(who, decision.role)
            raw_persona = (os.environ.get("CONSOLE_PERSONA") or "").strip().lower()
            default_persona = raw_persona if raw_persona in ("junior", "senior") else "junior"
            return self.send(200, {
                "mode": "snapshot" if CFG.snapshot else "live",
                "auth": CFG.auth,
                "identity": who if CFG.auth == "proxy" else "loopback" if CFG.auth == "loopback" else None,
                "role": decision.role,
                "default_persona": default_persona,
                "can_launch": can,
                "reason": reason,
                "read_at": state["read_at"],
                "read_error": state["read_error"],
                "snapshot": CFG.snapshot or None,
            })
        if path == "/v1/charts.json":
            return self.charts()
        self.send(404, {"error": "not found"})

    def do_POST(self):
        route = self.path.split("?")[0]
        if route not in (
            "/v1/go-for-launch",
            "/v1/answer",
            "/v1/spec-check",
            "/v1/request",
            "/v1/intake",
            "/v1/intake-confirm",
            "/v1/assign",
            "/v1/resume",
            "/v1/watch-once",
            "/v1/rollback",
            "/v1/freeze",
        ):
            return self.send(404, {"error": "not found"})
        decision = oidc_rbac.evaluate_request(self.headers, "POST", route, env=os.environ, jwks=CFG.jwks)
        if not decision.ok:
            return self.send(decision.status, {"error": decision.reason})
        who = decision.identity if CFG.auth == "proxy" else "unauthenticated"
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return self.send(415, {"error": "Content-Type must be application/json"})
        host = self.headers.get("Host") or ""
        origin = self.headers.get("Origin") or ""
        if host not in CFG.hosts or origin not in (f"http://{host}", f"https://{host}"):
            return self.send(403, {"error": "Origin and Host must be this console's own"})
        if route == "/v1/answer":
            return self.reply(who)
        if route in ("/v1/spec-check", "/v1/request"):
            return self.draft(who, route == "/v1/request")
        if route == "/v1/intake":
            return self.intake(who)
        if route == "/v1/intake-confirm":
            return self.confirm(who)
        if route == "/v1/assign":
            return self.assign_ticket()
        if route == "/v1/resume":
            return self.resume_ticket()
        if route == "/v1/watch-once":
            return self.watch_once()
        if route == "/v1/rollback":
            return self.ops_rollback(who, decision.role)
        if route == "/v1/freeze":
            return self.ops_freeze(who)
        can, reason = _can_launch(who, decision.role)
        if not can:
            return self.send(403, {"error": reason})
        try:
            length = int(self.headers.get("Content-Length") or "0")
            body = json.loads(self.rfile.read(min(length, 4096)) or b"null")
            answer = body["answer"]
            version, held_at = body["version"], body["held_at"]
            sha = body.get("brief_sha256")
            reason = body.get("reason")
            assert answer in ("GO", "NO-GO", "PREPARE") and isinstance(version, str)
            assert answer == "PREPARE" or isinstance(sha, str)
            assert reason is None or isinstance(reason, str)
        except (ValueError, KeyError, TypeError, AssertionError):
            return self.send(400, {"error": "the body must be {answer: GO|NO-GO|PREPARE, version, held_at, brief_sha256, reason}"})
        reason = (reason or "").strip()
        if answer == "NO-GO" and not reason:
            return self.send(400, {"error": "a rollback needs a reason (docs/adr/0079 §2); nothing moved"})
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
                env.update(GO_FOR_LAUNCH=answer, GO_FOR_LAUNCH_BRIEF_SHA256=sha, GO_FOR_LAUNCH_REASON=reason)
            p = subprocess.run(["sh", "core/promote.sh"], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=600)
            sys.stderr.write(p.stderr)
            line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else f"core/promote.sh exit {p.returncode}"
            read_once(self.server.logbook_file)
            return self.send(200, {"line": line, "exit": p.returncode})
        finally:
            press.release()

    def reply(self, who):
        """The answer to a ticket's open question (docs/adr/0055 §2): only the
        question the logbook shows open, then `make answer`, as ./go answer."""
        if CFG.snapshot:
            return self.send(403, {"error": "snapshot: no tower behind it, nothing can be answered"})
        try:
            length = int(self.headers.get("Content-Length") or "0")
            body = json.loads(self.rfile.read(min(length, 8192)) or b"null")
            ticket, index, text = body["ticket"], body["question"], body["answer"].strip()
            assert isinstance(ticket, str) and isinstance(index, int) and text
        except (ValueError, KeyError, TypeError, AttributeError, AssertionError):
            return self.send(400, {"error": "the body must be {ticket, question: <index>, answer: <text>}"})
        if not press.acquire(blocking=False):
            return self.send(409, {"error": "another press is running"})
        try:
            read_once(self.server.logbook_file)
            book = state["book"] or {}
            # A ticket's open question, or an intake's (docs/adr/0090 §2).
            listed = [x for key in ("tickets", "intakes") if isinstance(book.get(key), list) for x in book[key]]
            t = next((x for x in listed if x.get("id") == ticket), None)
            qs = (t or {}).get("questions") or []
            if not t or t.get("state") not in ("questions", "intake-questions") or index != len(qs) - 1 \
                    or qs[index].get("answer"):
                return self.send(409, {"error": f"ticket {ticket} has no open question {index}; nothing recorded"})
            env = {k: v for k, v in os.environ.items() if not k.startswith("ANSWER")}
            env.update(ANSWER=text, ANSWER_BY=who, ANSWER_VIA="console")
            p = subprocess.run(["make", "--no-print-directory", "answer", f"TICKET={ticket}"], cwd=ROOT, env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
            sys.stderr.write(p.stderr)
            line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else f"make answer exit {p.returncode}"
            read_once(self.server.logbook_file)
            return self.send(200, {"line": line, "exit": p.returncode})
        finally:
            press.release()

    def draft(self, who, save):
        """The editor (docs/adr/0059 §4): the check runs gates/spec.sh on the
        draft in a temporary directory and writes nothing; the request runs
        the check again, then writes TICKETS_DIR/<id>.md and `make request`."""
        if CFG.auth == "public":
            return self.send(403, {"error": "CONSOLE_AUTH=public: no draft is checked or requested here"})
        if save and CFG.snapshot:
            return self.send(403, {"error": "snapshot: no tower behind it, nothing can be requested"})
        try:
            length = int(self.headers.get("Content-Length") or "0")
            assert 0 < length <= DRAFT_MAX
            fields = json.loads(self.rfile.read(length))
            assert isinstance(fields, dict) and isinstance(fields.get("title"), str)
        except (ValueError, TypeError, AssertionError):
            return self.send(400, {"error": f"the body must be a JSON object of at most {DRAFT_MAX} bytes: "
                                   "{title, context, criteria: [{given, when, then}], perimeter, feature, priority}"})
        tickets = os.environ.get("TICKETS_DIR") or "tickets"
        if not save:
            code, body = checked(fields, tickets)
            return self.send(code, body)
        if not press.acquire(blocking=False):
            return self.send(409, {"error": "another press is running"})
        try:
            code, body = checked(fields, tickets)
            if code != 200:
                return self.send(code, body)
            if not body["line"].startswith("GO: spec"):
                return self.send(409, {"error": "the spec gate refuses this draft; nothing written",
                                       "line": body["line"]})
            with tempfile.TemporaryDirectory(prefix="lights-out-request-") as tmp:
                pathlib.Path(tmp, "fields.json").write_text(json.dumps(fields), encoding="utf-8")
                p = subprocess.run(["python3", "core/spec.py", "draft", os.path.join(tmp, "fields.json"), tickets],
                                   cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                   timeout=CHECK_TIMEOUT_S)
            if p.returncode != 0:
                return self.send(409, {"error": (p.stderr.strip().splitlines() or ["the draft was not written"])[-1]})
            ticket = p.stdout.strip()
            env = {k: v for k, v in os.environ.items() if not k.startswith("REQUEST")}
            env.update(REQUEST_BY=who, REQUEST_VIA="console")
            p = subprocess.run(["make", "--no-print-directory", "request", f"TICKET={ticket}"], cwd=ROOT, env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
            sys.stderr.write(p.stderr)
            line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else f"make request exit {p.returncode}"
            read_once(self.server.logbook_file)
            return self.send(200, {"ticket": ticket, "line": line, "exit": p.returncode})
        finally:
            press.release()

    def run_make(self, argv, env):
        """ARGV, a `make` recipe as ./go runs it; its last stdout line and exit."""
        p = subprocess.run(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=900)
        sys.stderr.write(p.stderr)
        line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else f"{' '.join(argv)} exit {p.returncode}"
        read_once(self.server.logbook_file)
        return line, p.returncode

    def read_json_body(self, limit=DRAFT_MAX):
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return None, "invalid Content-Length"
        if length <= 0 or length > limit:
            return None, f"the body must be a JSON object of 1..{limit} bytes"
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            return None, "the body is not valid JSON"
        if not isinstance(data, dict):
            return None, "the body must be a JSON object"
        return data, None

    def run_ops(self, argv):
        """Run control-plane ops wrapper cleanly and refresh logbook state."""
        try:
            p = subprocess.run(
                argv,
                cwd=ROOT,
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=OPS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return f"core/console_ops.sh timed out after {OPS_TIMEOUT_S}s", 124
        sys.stderr.write(p.stderr)
        line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else f"core/console_ops.sh exit {p.returncode}"
        read_once(self.server.logbook_file)
        return line, p.returncode

    def charts(self):
        """Time-series metrics endpoint (docs/adr/0125 §2)."""
        if CFG.snapshot:
            return self.send(200, {"ok": False, "error": "snapshot mode: no live charts"})
        try:
            p = subprocess.run(
                ["sh", "core/console_ops.sh", "charts"],
                cwd=ROOT,
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=OPS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return self.send(504, {"ok": False, "error": f"core/console_ops.sh charts timed out after {OPS_TIMEOUT_S}s"})
        sys.stderr.write(p.stderr)
        try:
            payload = json.loads(p.stdout)
        except ValueError:
            return self.send(502, {"ok": False, "error": "core/console_ops.sh charts returned invalid JSON"})
        if not isinstance(payload, dict):
            return self.send(502, {"ok": False, "error": "core/console_ops.sh charts returned non-object JSON"})
        return self.send(200, payload)

    def _reject_unavailable_write(self, action):
        if CFG.auth == "public":
            return 403, f"CONSOLE_AUTH=public: {action} is disabled"
        if CFG.snapshot:
            return 403, f"snapshot: no tower behind it, cannot {action}"
        return None, None

    def assign_ticket(self):
        code, msg = self._reject_unavailable_write("assign")
        if code:
            return self.send(code, {"error": msg})
        body, err = self.read_json_body()
        if err:
            return self.send(400, {"error": err})
        ticket = body.get("ticket")
        if not isinstance(ticket, str) or not TICKET_ID.fullmatch(ticket):
            return self.send(400, {"error": "the body must be {ticket: <id>}"})
        if not press.acquire(blocking=False):
            return self.send(409, {"error": "another press is running"})
        try:
            read_once(self.server.logbook_file)
            tickets = (state["book"] or {}).get("tickets")
            t = next((x for x in tickets if isinstance(x, dict) and x.get("id") == ticket), None) \
                if isinstance(tickets, list) else None
            if not t or t.get("state") != "opened":
                return self.send(409, {"error": f"ticket {ticket} is not opened; nothing assigned"})
            line, rc = self.run_make(["make", "--no-print-directory", "assign", f"TICKET={ticket}"], os.environ.copy())
            return self.send(200, {"ticket": ticket, "line": line, "exit": rc})
        finally:
            press.release()

    def resume_ticket(self):
        code, msg = self._reject_unavailable_write("resume")
        if code:
            return self.send(code, {"error": msg})
        body, err = self.read_json_body()
        if err:
            return self.send(400, {"error": err})
        ticket = body.get("ticket")
        if not isinstance(ticket, str) or not TICKET_ID.fullmatch(ticket):
            return self.send(400, {"error": "the body must be {ticket: <id>}"})
        if not press.acquire(blocking=False):
            return self.send(409, {"error": "another press is running"})
        try:
            read_once(self.server.logbook_file)
            tickets = (state["book"] or {}).get("tickets")
            t = next((x for x in tickets if isinstance(x, dict) and x.get("id") == ticket), None) \
                if isinstance(tickets, list) else None
            if not _ticket_resumable(t):
                return self.send(409, {"error": f"ticket {ticket} is not stranded or failed; nothing resumed"})
            line, rc = self.run_make(["make", "--no-print-directory", "resume", f"TICKET={ticket}"], os.environ.copy())
            return self.send(200, {"ticket": ticket, "line": line, "exit": rc})
        finally:
            press.release()

    def watch_once(self):
        code, msg = self._reject_unavailable_write("watch")
        if code:
            return self.send(code, {"error": msg})
        body, err = self.read_json_body()
        if err or body is None:
            return self.send(400, {"error": err or "the body must be {}"})
        if not press.acquire(blocking=False):
            return self.send(409, {"error": "another press is running"})
        try:
            line, rc = self.run_make(["make", "--no-print-directory", "watch", "ONCE=1"], os.environ.copy())
            return self.send(200, {"line": line, "exit": rc})
        finally:
            press.release()

    def ops_rollback(self, who, role=None):
        code, msg = self._reject_unavailable_write("rollback")
        if code:
            return self.send(code, {"error": msg})
        can, denied = _can_launch(who, role) if role is not None else may_launch(who)
        if not can:
            return self.send(403, {"error": denied})
        body, err = self.read_json_body()
        if err:
            return self.send(400, {"error": err})
        raw_reason = body.get("reason")
        reason = " ".join(raw_reason.split()) if isinstance(raw_reason, str) else ""
        if not reason or len(reason) > MAX_REASON_LEN:
            return self.send(400, {"error": f"reason must be a non-empty string of at most {MAX_REASON_LEN} characters"})
        if not press.acquire(blocking=False):
            return self.send(409, {"error": "another press is running"})
        try:
            line, rc = self.run_ops(["sh", "core/console_ops.sh", "rollback", "--reason", reason, "--by", who])
        finally:
            press.release()  # before the answer: a press sent on the 200 must find the lock free
        return self.send(200, {"line": line, "exit": rc})

    def ops_freeze(self, who):
        code, msg = self._reject_unavailable_write("freeze")
        if code:
            return self.send(code, {"error": msg})
        body, err = self.read_json_body()
        if err:
            return self.send(400, {"error": err})
        st = body.get("state")
        if st not in ("on", "off"):
            return self.send(400, {"error": "state must be 'on' or 'off'"})
        raw_reason = body.get("reason")
        reason = " ".join(raw_reason.split()) if isinstance(raw_reason, str) else ""
        if not reason or len(reason) > MAX_REASON_LEN:
            return self.send(400, {"error": f"reason must be a non-empty string of at most {MAX_REASON_LEN} characters"})
        if not press.acquire(blocking=False):
            return self.send(409, {"error": "another press is running"})
        try:
            line, rc = self.run_ops(["sh", "core/console_ops.sh", "freeze", "--state", st, "--reason", reason, "--by", who])
        finally:
            press.release()  # before the answer: a press sent on the 200 must find the lock free
        return self.send(200, {"state": st, "line": line, "exit": rc})

    def intake(self, who):
        """A client's words (docs/adr/0090 §8): `make intake` with the text as
        INTAKE_FILE, the next free id, who pressed and `via: console`."""
        if CFG.auth == "public":
            return self.send(403, {"error": "CONSOLE_AUTH=public: no intake is started here"})
        if CFG.snapshot:
            return self.send(403, {"error": "snapshot: no tower behind it, no intake can start"})
        try:
            length = int(self.headers.get("Content-Length") or "0")
            assert 0 < length <= DRAFT_MAX
            body = json.loads(self.rfile.read(length))
            text = body["text"]
            assert isinstance(text, str) and text.strip()
        except (ValueError, KeyError, TypeError, AssertionError):
            return self.send(400, {"error": f"the body must be {{text}}, a non-empty string, at most {DRAFT_MAX} bytes"})
        if not press.acquire(blocking=False):
            return self.send(409, {"error": "another press is running"})
        try:
            with tempfile.TemporaryDirectory(prefix="lights-out-intake-") as tmp:
                words = pathlib.Path(tmp, "words.md")
                words.write_text(text, encoding="utf-8")
                env = {k: v for k, v in os.environ.items() if not k.startswith("INTAKE") and k not in ("TEXT", "TICKET")}
                env.update(INTAKE_FILE=str(words), INTAKE_BY=who, INTAKE_VIA="console")
                line, rc = self.run_make(["make", "--no-print-directory", "intake"], env)
            return self.send(200, {"line": line, "exit": rc})
        finally:
            press.release()

    def confirm(self, who):
        """The decisions on a proposed intake (docs/adr/0090 §5, §8): only an
        intake the logbook shows at `proposed`, one decision per proposed ticket."""
        if CFG.snapshot:
            return self.send(403, {"error": "snapshot: no tower behind it, nothing can be confirmed"})
        try:
            length = int(self.headers.get("Content-Length") or "0")
            assert 0 < length <= DRAFT_MAX
            body = json.loads(self.rfile.read(length))
            intake, decisions = body["intake"], body["decisions"]
            assert isinstance(intake, str) and isinstance(decisions, list)
        except (ValueError, KeyError, TypeError, AssertionError):
            return self.send(400, {"error": "the body must be {intake, decisions: [\"confirm\" | \"drop\" | {edit: {...}}]}"})
        if not press.acquire(blocking=False):
            return self.send(409, {"error": "another press is running"})
        try:
            read_once(self.server.logbook_file)
            listed = (state["book"] or {}).get("intakes")
            t = next((x for x in listed if x.get("id") == intake), None) if isinstance(listed, list) else None
            if not t or t.get("state") != "proposed" or len(t.get("proposal") or []) != len(decisions):
                return self.send(409, {"error": f"intake {intake} is not proposed with {len(decisions)} tickets; nothing confirmed"})
            env = {k: v for k, v in os.environ.items() if not k.startswith(("CONFIRM", "DECISIONS"))}
            env.update(DECISIONS=json.dumps(decisions), CONFIRM_BY=who, CONFIRM_VIA="console")
            line, rc = self.run_make(["make", "--no-print-directory", "intake-confirm", f"TICKET={intake}"], env)
            return self.send(200, {"line": line, "exit": rc})
        finally:
            press.release()

    def log_message(self, *args):
        pass


def checked(fields, tickets):
    """(status, body): the draft under the next free id, in a temporary
    directory, and the one line gates/spec.sh prints for it."""
    with tempfile.TemporaryDirectory(prefix="lights-out-draft-") as tmp:
        pathlib.Path(tmp, "fields.json").write_text(json.dumps(fields), encoding="utf-8")
        out = os.path.join(tmp, "draft")
        os.mkdir(out)
        try:
            p = subprocess.run(["python3", "core/spec.py", "draft", os.path.join(tmp, "fields.json"), tickets, out],
                               cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=CHECK_TIMEOUT_S)
            if p.returncode != 0:
                return 400, {"error": (p.stderr.strip().splitlines() or ["the draft was refused"])[-1]}
            ticket = p.stdout.strip()
            spec = os.path.join(out, f"{ticket}.md")
            g = subprocess.run(["sh", "gates/spec.sh", spec], cwd=ROOT, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=CHECK_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return 504, {"error": f"the spec check took more than {CHECK_TIMEOUT_S} s"}
        line = g.stdout.strip().splitlines()[-1] if g.stdout.strip() else f"gates/spec.sh exit {g.returncode}"
        return 200, {"ticket": ticket, "markdown": pathlib.Path(spec).read_text(encoding="utf-8"), "line": line}


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
    server.daemon_threads = True
    server.shutting_down = False
    server.logbook_file = out
    mode = f"snapshot of {CFG.snapshot}" if CFG.snapshot else "live"
    print(f"console: http://localhost:{CFG.port}  ({mode}, auth {CFG.auth}; Ctrl-C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutting_down = True
        with state_cond:
            state_cond.notify_all()


if __name__ == "__main__":
    main()
