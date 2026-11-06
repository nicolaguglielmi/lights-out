#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""egress: the one way out of the `crew` network (docs/adr/0046 §2).

A forward proxy for the CLI harnesses, which sit on an internal network with
no route out. It understands the two forms a client sends a proxy, CONNECT
host:port (a tunnel; TLS stays end to end and opaque here) and absolute-form
HTTP (`POST http://host:port/path`, rewritten to origin-form), and opens a
connection only to an exact `host:port` listed in the allow file, which is
re-read on every connection so a harness switch never restarts this process.
A missing or empty file allows nothing. It resolves names itself: a client
picks a name, never an address. One stderr line per decision:
`ALLOW <host:port>`, `DENY <host:port>`, or `FAIL <host:port> <error>`.

Names no provider: the list comes from core/egress.sh, which takes it from
the adapter and the workload's contract. Python standard library only: it
runs on the workbench image, as the conformance stub does (docs/adr/0041 §8).

Usage: egress.py <port>; EGRESS_DIR (default /work) holds `allow` (read) and
`ready` (written once listening: this container's hostname, so a caller can
tell this process's file from an earlier container's).
"""
import os
import socket
import sys
import threading

DIR = os.environ.get("EGRESS_DIR", "/work")
ALLOW_FILE = os.path.join(DIR, "allow")
HEAD_MAX = 65536
HEAD_TIMEOUT_S = 30  # decision: a client that sends no request head in 30 s is dropped


def log(line):
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def allowed():
    try:
        with open(ALLOW_FILE) as f:
            return {w.strip().lower() for w in f.read().split() if w.strip()}
    except OSError:
        return set()


def refuse(c, code, reason, why):
    body = ("egress: " + why + "\n").encode()
    try:
        c.sendall(b"HTTP/1.1 %d %s\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n"
                  b"Connection: close\r\n\r\n" % (code, reason, len(body)) + body)
    except OSError:
        pass
    c.close()


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def target(method, uri):
    """-> (host, port, origin-form path or None for CONNECT), or None."""
    if method == "CONNECT":
        host, sep, port = uri.rpartition(":")
        return (host, port, None) if sep else None
    if uri.lower().startswith("http://"):
        authority, _, path = uri[7:].partition("/")
        host, sep, port = authority.rpartition(":")
        if not sep or "]" in port:
            host, port = authority, "80"
        return host, port, "/" + path
    return None


def handle(c):
    c.settimeout(HEAD_TIMEOUT_S)
    buf = b""
    try:
        while b"\r\n\r\n" not in buf:
            data = c.recv(65536)
            if not data:
                c.close()
                return
            buf += data
            if len(buf) > HEAD_MAX:
                return refuse(c, 400, b"Bad Request", "request head too large")
    except OSError:
        c.close()
        return
    head, rest = buf.split(b"\r\n\r\n", 1)
    lines = head.split(b"\r\n")
    try:
        method, uri, version = lines[0].decode("latin-1").split(" ")
    except ValueError:
        return refuse(c, 400, b"Bad Request", "not a request line")
    t = target(method, uri)
    if t is None:
        return refuse(c, 400, b"Bad Request", "not a proxy request")
    host, port, path = t
    hostport = (host + ":" + port).lower()
    if hostport not in allowed():
        log("DENY " + hostport)
        return refuse(c, 403, b"Forbidden", hostport + " is not allowed")
    try:
        up = socket.create_connection((host, int(port)), timeout=10)
        up.settimeout(None)
    except (OSError, ValueError) as e:
        log("FAIL %s %s" % (hostport, e))
        return refuse(c, 502, b"Bad Gateway", hostport + " is unreachable")
    log("ALLOW " + hostport)
    c.settimeout(None)
    try:
        if path is None:
            c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            lines[0] = ("%s %s %s" % (method, path, version)).encode("latin-1")
            rest = b"\r\n".join(lines) + b"\r\n\r\n" + rest
        if rest:
            up.sendall(rest)
    except OSError:
        up.close()
        c.close()
        return
    threading.Thread(target=pipe, args=(up, c), daemon=True).start()
    pipe(c, up)


def main():
    port = int(sys.argv[1])
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(128)
    tmp = os.path.join(DIR, "ready.tmp")
    with open(tmp, "w") as f:
        f.write(socket.gethostname() + "\n")
    os.replace(tmp, os.path.join(DIR, "ready"))
    log("egress: listening on %d" % port)
    while True:
        c, _ = s.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
