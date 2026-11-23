"""Minimal uninstrumented HTTP service for ADR 0098 proxy verification proof."""

from __future__ import annotations

import http.server
import os


def route(path: str) -> tuple[int, bytes]:
    if path == "/ok":
        return 200, b"ok\n"
    if path == "/":
        return 200, b"plain\n"
    return 404, b"not found\n"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        code, body = route(self.path.split("?", 1)[0])
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
