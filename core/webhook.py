"""Authenticated alert webhook receiver and structural normalizer (docs/adr/0109): stdlib only.

Normalizes structural JSON alert envelopes (alerts-array envelopes, nested
incident envelopes, and flat alert objects) into `lights-out.alert/1` files,
and optionally dispatches alerts naming a version to core/triage.sh. Strictly
vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import signal
import subprocess  # nosec B404 - invokes core/triage.sh when --dispatch is enabled
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "lights-out.alert/1"
PAYLOAD_MAX_BYTES = 65536  # 64 KiB cap (docs/adr/0109)
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8097
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_str(*values: Any) -> str | None:
    for val in values:
        if isinstance(val, str):
            stripped = val.strip()
            if stripped:
                return stripped
    return None


def _iso_time(val: Any) -> str | None:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return datetime.fromtimestamp(float(val), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(val, str):
        stripped = val.strip()
        return stripped or None
    return None


def _normalize_alerts_array(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_alerts = data["alerts"]
    if not isinstance(raw_alerts, list):
        raise ValueError("alerts field must be a JSON array")
    common_labels = data.get("commonLabels") if isinstance(data.get("commonLabels"), dict) else {}
    common_ann = data.get("commonAnnotations") if isinstance(data.get("commonAnnotations"), dict) else {}
    out: list[dict[str, Any]] = []
    for item in raw_alerts:
        if not isinstance(item, dict):
            raise ValueError("each entry in alerts must be a JSON object")
        labels = dict(common_labels)
        if isinstance(item.get("labels"), dict):
            labels.update(item["labels"])
        ann = dict(common_ann)
        if isinstance(item.get("annotations"), dict):
            ann.update(item["annotations"])
        headline = _first_str(
            ann.get("summary"),
            ann.get("message"),
            item.get("headline"),
            labels.get("alertname"),
            item.get("title"),
        ) or "alert"
        summary = _first_str(ann.get("description"), ann.get("summary"), item.get("summary"), headline) or headline
        severity = (_first_str(labels.get("severity"), item.get("severity")) or "unknown").lower()
        status = (_first_str(item.get("status"), data.get("status")) or "firing").lower()
        version = _first_str(
            item.get("version"),
            labels.get("version"),
            labels.get("service_version"),
            labels.get("canary_version"),
        )
        stable = _first_str(item.get("stable"), labels.get("stable"), labels.get("stable_version"))
        starts_at = _iso_time(item.get("startsAt") or item.get("starts_at") or item.get("timestamp"))
        entry: dict[str, Any] = {
            "headline": headline,
            "severity": severity,
            "status": status,
            "version": version,
            "stable": stable,
            "summary": summary,
            "labels": labels,
            "starts_at": starts_at,
        }
        for opt in ("stage", "chain"):
            if opt in item:
                entry[opt] = item[opt]
        out.append(entry)
    return out


def _normalize_incident_envelope(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    inc = data["incident"]
    if not isinstance(inc, dict):
        raise ValueError("incident field must be a JSON object")
    meta = inc.get("metadata") if isinstance(inc.get("metadata"), dict) else {}
    user_labels = meta.get("user_labels") if isinstance(meta.get("user_labels"), dict) else {}
    sys_labels = meta.get("system_labels") if isinstance(meta.get("system_labels"), dict) else {}
    res = inc.get("resource") if isinstance(inc.get("resource"), dict) else {}
    res_labels = res.get("labels") if isinstance(res.get("labels"), dict) else {}
    labels: dict[str, Any] = {**res_labels, **sys_labels, **user_labels}
    doc_obj = inc.get("documentation") if isinstance(inc.get("documentation"), dict) else {}
    headline = _first_str(
        inc.get("summary"),
        inc.get("condition_name"),
        inc.get("policy_name"),
        inc.get("incident_id"),
    ) or "incident"
    summary = _first_str(inc.get("summary"), doc_obj.get("content"), inc.get("condition_name"), headline) or headline
    severity = (_first_str(inc.get("severity"), labels.get("severity")) or "unknown").lower()
    status = (_first_str(inc.get("state"), inc.get("status")) or "open").lower()
    version = _first_str(
        inc.get("version"),
        labels.get("version"),
        labels.get("service_version"),
        labels.get("canary_version"),
        data.get("version"),
    )
    stable = _first_str(inc.get("stable"), labels.get("stable"), labels.get("stable_version"))
    starts_at = _iso_time(inc.get("started_at") if inc.get("started_at") is not None else inc.get("created_at"))
    return [
        {
            "headline": headline,
            "severity": severity,
            "status": status,
            "version": version,
            "stable": stable,
            "summary": summary,
            "labels": labels,
            "starts_at": starts_at,
        }
    ]


def _normalize_flat_object(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    headline = _first_str(data.get("headline"), data.get("alert"), data.get("title"), data.get("summary"))
    version = _first_str(data.get("version"))
    if not headline and not version:
        raise ValueError("unrecognized alert payload shape")
    headline = headline or f"alert {version}"
    summary = _first_str(data.get("summary"), data.get("message"), data.get("description"), headline) or headline
    severity = (_first_str(data.get("severity")) or "unknown").lower()
    status = (_first_str(data.get("status")) or "firing").lower()
    stable = _first_str(data.get("stable"))
    labels = dict(data["labels"]) if isinstance(data.get("labels"), dict) else {}
    starts_at = _iso_time(data.get("starts_at") or data.get("startsAt") or data.get("timestamp"))
    entry: dict[str, Any] = {
        "headline": headline,
        "severity": severity,
        "status": status,
        "version": version,
        "stable": stable,
        "summary": summary,
        "labels": labels,
        "starts_at": starts_at,
    }
    for opt in ("stage", "chain"):
        if opt in data:
            entry[opt] = data[opt]
    return [entry]


def normalize_payload(data: Any, received_at: str | None = None) -> dict[str, Any]:
    """Normalize supported alert payload JSON shapes into lights-out.alert/1."""
    if not isinstance(data, dict):
        raise ValueError("alert payload must be a JSON object")

    if "alerts" in data and data.get("schema") == SCHEMA and isinstance(data["alerts"], list):
        alerts = list(data["alerts"])
    elif "alerts" in data:
        alerts = _normalize_alerts_array(data)
    elif "incident" in data:
        alerts = _normalize_incident_envelope(data)
    else:
        alerts = _normalize_flat_object(data)

    return {
        "schema": SCHEMA,
        "received_at": received_at or _utc_now(),
        "alerts": alerts,
    }


def write_normalized(doc: Mapping[str, Any], out_dir: str, stem: str | None = None) -> Path:
    """Persist normalized alert document under out_dir and return the written path."""
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    raw_bytes = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if stem:
        clean = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "alert"
        filename = f"{clean}.json"
    else:
        stamp = re.sub(r"[^0-9T]+", "", str(doc.get("received_at") or _utc_now())).lower()
        digest = hashlib.sha256(raw_bytes).hexdigest()[:8]
        filename = f"alert-{stamp}-{digest}.json"
    out_path = target_dir / filename
    out_path.write_bytes(raw_bytes)
    return out_path


def _mitigate(item_path: Path, env: Mapping[str, str]) -> dict[str, Any]:
    """Policy-gated mitigation of the incident core/triage.sh just wrote (docs/adr/0121 §3)."""
    try:
        from . import triage
    except ImportError:  # run as a script: core/ is sys.path[0]
        import triage
    incident_path = os.path.join((env.get("TRIAGE_OUT") or "").strip() or os.path.join(".crew", "triage"),
                                 item_path.stem, "incident.json")
    record_path = (env.get("RELEASE_RECORD") or "").strip() or os.path.join(".crew", "release.json")
    try:
        return triage.mitigate(incident_path, record_path, env)
    except OSError as exc:  # a failed audit or record write must not take the server down
        print(f"[webhook] mitigation not completed: {exc}", file=sys.stderr)
        return {"status": "error", "reason": str(exc)}


def _dispatch_alerts(
    doc: Mapping[str, Any],
    base_path: Path,
    env: Mapping[str, str],
) -> list[dict[str, Any]]:
    script = os.path.join("core", "triage.sh")
    results: list[dict[str, Any]] = []
    alerts = doc.get("alerts") or []
    for idx, alert in enumerate(alerts, 1):
        version = alert.get("version") if isinstance(alert, dict) else None
        if not version:
            print(f"[webhook] alert #{idx} has no version; skipping triage dispatch", file=sys.stderr)
            results.append({"index": idx, "status": "skipped", "reason": "no version"})
            continue
        if not os.path.isfile(script):
            print(f"[webhook] alert #{idx}: {script} not found; skipping triage dispatch", file=sys.stderr)
            results.append({"index": idx, "version": version, "status": "skipped", "reason": "triage.sh missing"})
            continue
        item_path = base_path.with_name(f"{base_path.stem}-item-{idx}.json")
        item_path.write_text(json.dumps(alert, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        triage_env = dict(env)
        triage_env["ALERT"] = str(item_path)
        proc = subprocess.run(  # nosec B603 B607 - fixed core/triage.sh script, ADR 0052
            ["sh", script],
            env=triage_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        line = (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else ""
        status = "ok" if proc.returncode == 0 else "error"
        if proc.returncode != 0:
            print(f"[webhook] triage dispatch for {item_path} exited {proc.returncode}: {line}", file=sys.stderr)
        result = {"index": idx, "version": version, "status": status, "line": line}
        if status == "ok" and env.get("AUTO_MITIGATE_ON_TRIAGE") == "on":
            result["mitigation"] = _mitigate(item_path, env)
        results.append(result)
    return results


class WebhookServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        out_dir: str,
        auth_bearer: str,
        dispatch: bool,
        env: Mapping[str, str],
    ) -> None:
        self.out_dir = out_dir
        self.auth_bearer = auth_bearer
        self.dispatch = dispatch
        self.env = dict(env)
        self._seq = 0
        self._seq_lock = threading.Lock()
        super().__init__(server_address, WebhookHandler)

    def next_stem(self, stamp: str) -> str:
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        compact = re.sub(r"[^0-9A-Za-z]+", "", stamp).lower()
        return f"alert-{compact}-{seq:04d}"


class WebhookHandler(BaseHTTPRequestHandler):
    server: WebhookServer
    server_version = "lights-out-webhook"

    def _send_json(self, code: int, payload: Mapping[str, Any], headers: Mapping[str, str] | None = None) -> None:
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = self.server.auth_bearer
        if not expected:
            return True
        raw = self.headers.get("Authorization") or ""
        scheme, sep, provided = raw.partition(" ")
        if not sep or scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(provided.strip().encode("utf-8"), expected.encode("utf-8"))

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path not in ("/", "/webhook", "/alerts"):
            self._send_json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"}, {"WWW-Authenticate": "Bearer"})
            return
        ct = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ct and ct != "application/json" and not ct.endswith("+json"):
            self._send_json(415, {"error": "Content-Type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "-1")
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return
        if length <= 0:
            self._send_json(400, {"error": "non-empty JSON body with Content-Length required"})
            return
        if length > PAYLOAD_MAX_BYTES:
            self._send_json(413, {"error": f"payload exceeds {PAYLOAD_MAX_BYTES} bytes"})
            return
        raw = self.rfile.read(min(length, PAYLOAD_MAX_BYTES + 1))
        if len(raw) > PAYLOAD_MAX_BYTES:
            self._send_json(413, {"error": f"payload exceeds {PAYLOAD_MAX_BYTES} bytes"})
            return
        try:
            parsed = json.loads(raw.decode("utf-8"))
            doc = normalize_payload(parsed)
        except (UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, {"error": f"invalid alert payload: {exc}"})
            return

        written = write_normalized(doc, self.server.out_dir, stem=self.server.next_stem(doc["received_at"]))
        dispatched = _dispatch_alerts(doc, written, self.server.env) if self.server.dispatch else []
        self._send_json(
            200,
            {
                "schema": SCHEMA,
                "written": str(written),
                "count": len(doc["alerts"]),
                "dispatched": dispatched,
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_server(
    bind: str = DEFAULT_BIND,
    port: int = DEFAULT_PORT,
    out_dir: str | None = None,
    secret: str | None = None,
    dispatch: bool = False,
    env: Mapping[str, str] | None = None,
) -> WebhookServer:
    """Create and validate a WebhookServer, failing closed if non-loopback lacks ALERT_WEBHOOK_TOKEN."""
    cfg = os.environ if env is None else env
    bearer = (secret if secret is not None else (cfg.get("ALERT_WEBHOOK_TOKEN") or "")).strip()
    if bind not in LOOPBACK_HOSTS and not bearer:
        raise ValueError(f"refusing non-loopback bind {bind!r} without ALERT_WEBHOOK_TOKEN")
    dest = out_dir or (cfg.get("ALERT_WEBHOOK_OUT") or "").strip() or os.path.join(".crew", "alerts")
    return WebhookServer((bind, port), dest, bearer, dispatch, cfg)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    cfg = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="core/webhook.py")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--normalize", metavar="FILE", help="Normalize a JSON alert payload file")
    mode.add_argument("--serve", action="store_true", help="Serve HTTP alert webhook")
    parser.add_argument("--out", default=(cfg.get("ALERT_WEBHOOK_OUT") or "").strip() or os.path.join(".crew", "alerts"))
    parser.add_argument("--bind", default=DEFAULT_BIND)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--dispatch", action="store_true")
    args = parser.parse_args(argv)

    if args.normalize:
        src = Path(args.normalize)
        if not src.is_file():
            print(f"webhook: no alert payload at {src}", file=sys.stderr)
            return 1
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            doc = normalize_payload(data)
        except (OSError, ValueError) as exc:
            print(f"webhook: invalid payload in {src}: {exc}", file=sys.stderr)
            return 1
        written = write_normalized(doc, args.out, stem=src.stem)
        print(str(written))
        return 0

    try:
        server = create_server(
            bind=args.bind,
            port=args.port,
            out_dir=args.out,
            dispatch=args.dispatch,
            env=cfg,
        )
    except (ValueError, OSError) as exc:
        print(f"webhook: {exc}", file=sys.stderr)
        return 1

    actual_port = server.server_address[1]
    if not server.auth_bearer:
        print("webhook: warning — ALERT_WEBHOOK_TOKEN unset; loopback-only mode", file=sys.stderr, flush=True)
    print(
        f"webhook: listening on http://{args.bind}:{actual_port} (out={server.out_dir}, dispatch={args.dispatch})",
        file=sys.stderr,
        flush=True,
    )

    def _request_shutdown(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    prev_term = signal.getsignal(signal.SIGTERM)
    prev_int = signal.getsignal(signal.SIGINT)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _request_shutdown)
        signal.signal(signal.SIGINT, _request_shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, prev_term)
            signal.signal(signal.SIGINT, prev_int)
    return 0


if __name__ == "__main__":
    sys.exit(main())
