"""End-to-end conformance and security tests for M88 managed L2/L3 cloud adapters (ADR 0128).

Drives REAL gates/eventbus-conformance.sh (pubsub), gates/statestore-conformance.sh
(firestore), and gates/registry-conformance.sh (artifact-registry), plus end-to-end
contracts for managed-prometheus, cloud-deploy, cloud-workflows, and
.github/workflows/cloud-conformance.yml against ephemeral local Python HTTP mock
servers and stateful mock CLIs, asserting Bearer tokens never appear in curl argv.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _write_exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR)


def _sample_tar_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b'{"config":{}}\n'
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _install_curl_argv_spy(bin_dir: Path, argv_log: Path) -> None:
    real_curl = shutil.which("curl")
    assert real_curl, "curl must exist in Podman workbench"
    _write_exe(
        bin_dir / "curl",
        f"""#!/usr/bin/env python3
import os, sys
with open({str(argv_log)!r}, "a", encoding="utf-8") as fh:
    fh.write("\\t".join(sys.argv[1:]) + "\\n")
os.execv({real_curl!r}, [{real_curl!r}] + sys.argv[1:])
""",
    )


class _PubSubState:
    def __init__(self, expected_token: str | None = None):
        self.expected_token = expected_token
        self.topics: set[str] = set()
        self.subscriptions: dict[str, dict] = {}
        self.queue: list[dict] = []
        self.next_id = 1
        self.lock = threading.Lock()
        self.auth_headers: list[str] = []


def _start_pubsub_server(state: _PubSubState):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _read_json(self):
            n = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(n) if n > 0 else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        def _send(self, code: int, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _check_auth(self) -> bool:
            auth = self.headers.get("Authorization", "")
            state.auth_headers.append(auth)
            if state.expected_token and auth != f"Bearer {state.expected_token}":
                self._send(401, {"error": {"code": 401, "message": "unauthorized"}})
                return False
            return True

        def do_GET(self):  # noqa: N802
            if not self._check_auth():
                return
            path = urlparse(self.path).path
            with state.lock:
                if path in state.subscriptions:
                    self._send(200, state.subscriptions[path])
                else:
                    self._send(404, {"error": {"code": 404, "message": "not found"}})

        def do_PUT(self):  # noqa: N802
            if not self._check_auth():
                return
            path = urlparse(self.path).path
            payload = self._read_json()
            with state.lock:
                if "/topics/" in path:
                    if path in state.topics:
                        self._send(409, {"error": {"code": 409, "message": "exists"}})
                    else:
                        state.topics.add(path)
                        self._send(200, {"name": path.lstrip("/")})
                    return
                if "/subscriptions/" in path:
                    if path in state.subscriptions:
                        self._send(409, {"error": {"code": 409, "message": "exists"}})
                    else:
                        deadline = int(payload.get("ackDeadlineSeconds", 60))
                        state.subscriptions[path] = {"name": path.lstrip("/"), "ackDeadlineSeconds": deadline}
                        self._send(200, state.subscriptions[path])
                    return
            self._send(404, {"error": {"code": 404, "message": "unknown path"}})

        def do_POST(self):  # noqa: N802
            if not self._check_auth():
                return
            path = urlparse(self.path).path
            payload = self._read_json()
            with state.lock:
                if path.endswith(":publish"):
                    tpath = path[: -len(":publish")]
                    if tpath not in state.topics:
                        self._send(404, {"error": {"code": 404, "message": "topic not found"}})
                        return
                    mids = []
                    for msg in payload.get("messages", []):
                        mid = f"msg-{state.next_id}"
                        state.next_id += 1
                        state.queue.append({"id": mid, "data": msg["data"], "ack_id": None, "lease_until": 0.0})
                        mids.append(mid)
                    self._send(200, {"messageIds": mids})
                    return
                if path.endswith(":pull"):
                    spath = path[: -len(":pull")]
                    sub = state.subscriptions.get(spath)
                    if sub is None:
                        self._send(404, {"error": {"code": 404, "message": "sub not found"}})
                        return
                    now = time.monotonic()
                    deadline = sub["ackDeadlineSeconds"]
                    for item in state.queue:
                        if item["lease_until"] <= now:
                            ack_id = f"ack-{state.next_id}"
                            state.next_id += 1
                            item["ack_id"] = ack_id
                            item["lease_until"] = now + deadline
                            self._send(
                                200,
                                {
                                    "receivedMessages": [
                                        {
                                            "ackId": ack_id,
                                            "message": {"messageId": item["id"], "data": item["data"]},
                                        }
                                    ]
                                },
                            )
                            return
                    self._send(200, {})
                    return
                if path.endswith(":acknowledge"):
                    ack_ids = set(payload.get("ackIds", []))
                    state.queue = [item for item in state.queue if item["ack_id"] not in ack_ids]
                    self._send(200, {})
                    return
            self._send(404, {"error": {"code": 404, "message": "unknown verb"}})

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


class _FirestoreState:
    def __init__(self, expected_token: str | None = None):
        self.expected_token = expected_token
        self.docs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.auth_headers: list[str] = []


def _start_firestore_server(state: _FirestoreState):
    prefix = "/v1/projects/demo-proj/databases/(default)/documents/"

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _send(self, code: int, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _check_auth(self) -> bool:
            auth = self.headers.get("Authorization", "")
            state.auth_headers.append(auth)
            if state.expected_token and auth != f"Bearer {state.expected_token}":
                self._send(401, {"error": {"code": 401, "message": "unauthorized"}})
                return False
            return True

        def _doc_key(self) -> str | None:
            path = urlparse(self.path).path
            if path.startswith(prefix):
                return path[len(prefix) :]
            return None

        def do_GET(self):  # noqa: N802
            if not self._check_auth():
                return
            key = self._doc_key()
            with state.lock:
                if key and key in state.docs:
                    self._send(200, state.docs[key])
                else:
                    self._send(404, {"error": {"code": 404, "message": "not found"}})

        def do_PATCH(self):  # noqa: N802
            if not self._check_auth():
                return
            key = self._doc_key()
            if not key:
                self._send(404, {"error": {"code": 404, "message": "bad path"}})
                return
            n = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
            doc = {"name": f"{prefix.lstrip('/')}{key}", "fields": payload.get("fields", {})}
            with state.lock:
                state.docs[key] = doc
            self._send(200, doc)

        def do_DELETE(self):  # noqa: N802
            if not self._check_auth():
                return
            key = self._doc_key()
            with state.lock:
                state.docs.pop(key, None)
            self._send(200, {})

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


class _RegistryState:
    def __init__(self, expected_token: str):
        self.expected_token = expected_token
        self.manifests: dict[str, tuple[str, bytes]] = {}
        self.referrers: dict[str, list[dict]] = {}
        self.auth_headers: list[str] = []


def _install_registry_cli_stubs(bin_dir: Path, tar_path: Path) -> None:
    reg_file = bin_dir / "reg-state.json"
    reg_file.write_text(json.dumps({"signatures": {}, "referrers": {}}), encoding="utf-8")

    _write_exe(
        bin_dir / "docker",
        f"""#!/usr/bin/env python3
import hashlib, json, sys
from pathlib import Path
# Simulated container push: registers manifest directly via sidecar marker read by server helper
args = sys.argv[1:]
if args and args[0] == "push":
    ref = args[1]
    tag = ref.rsplit(":", 1)[1]
    payload = json.dumps({{"schemaVersion": 2, "tag": tag}}).encode()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    Path({str(bin_dir / "last-push.json")!r}).write_text(json.dumps({{"tag": tag, "digest": digest, "body": payload.decode()}}))
    sys.exit(0)
if args and args[0] == "save":
    out = args[args.index("-o") + 1]
    Path(out).write_bytes(Path({str(tar_path)!r}).read_bytes())
    sys.exit(0)
sys.exit(0)
""",
    )
    _write_exe(
        bin_dir / "osv-scanner",
        """#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
out = args[args.index("--output-file") + 1]
with open(out, "w", encoding="utf-8") as fh:
    json.dump({"spdxVersion": "SPDX-2.3", "name": "stub-sbom"}, fh)
""",
    )
    _write_exe(
        bin_dir / "cosign",
        f"""#!/usr/bin/env python3
import hashlib, json, sys
from pathlib import Path

args = sys.argv[1:]
sub = args[0]
ref = args[-1]
sf = Path({str(reg_file)!r})
data = json.loads(sf.read_text())

# Sync any newly pushed manifest marker into reg_state sidecar
push_marker = Path({str(bin_dir / "last-push.json")!r})
if sub == "attest":
    pred = args[args.index("--predicate") + 1]
    att_digest = "sha256:" + hashlib.sha256(Path(pred).read_bytes()).hexdigest()
    target_digest = "sha256:" + ref.split("@sha256:", 1)[1]
    data["referrers"].setdefault(target_digest, []).append({{
        "digest": att_digest,
        "annotations": {{
            "dev.sigstore.bundle.predicateType": "https://spdx.dev/Document",
            "org.opencontainers.image.created": "2026-11-24T00:00:00Z",
        }},
    }})
    sf.write_text(json.dumps(data))
    sys.exit(0)
if sub == "sign":
    data["signatures"][ref] = "signed"
    sf.write_text(json.dumps(data))
    sys.exit(0)
if sub == "verify":
    sys.exit(0 if ref in data["signatures"] else 1)
if sub == "verify-attestation":
    target_digest = "sha256:" + ref.split("@sha256:", 1)[1]
    sys.exit(0 if data["referrers"].get(target_digest) else 1)
sys.exit(1)
""",
    )


class ManagedCloudConformanceTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="m88-conf-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def _assert_token_not_in_argv(self, argv_log: Path, token: str) -> None:
        text = argv_log.read_text(encoding="utf-8")
        self.assertNotIn(token, text, f"secret token {token} leaked into curl argv:\n{text}")

    def test_pubsub_eventbus_conformance_all_6_cases_and_auth_hygiene(self):
        token = "tok-pubsub-secret-999"
        state = _PubSubState(expected_token=token)
        srv, base_url = _start_pubsub_server(state)
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)

        bin_dir = self.root / "bin-pubsub"
        argv_log = self.root / "pubsub-curl-argv.log"
        _install_curl_argv_spy(bin_dir, argv_log)

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CONFORMANCE": "1",
            "EVENT_BUS": "pubsub",
            "CONFORMANCE_ACK_DEADLINE": "2",
            "GCP_PROJECT": "demo-proj",
            "PUBSUB_API_URL": base_url,
            "PUBSUB_ACCESS_TOKEN": token,
            "READY_TIMEOUT": "5",
        }
        ready = subprocess.run(
            ["sh", "adapters/pubsub/ready.sh"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ready.returncode, 0, ready.stderr)
        self.assertEqual(ready.stdout, "")

        conf = subprocess.run(
            ["sh", "gates/eventbus-conformance.sh"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(conf.returncode, 0, conf.stdout + "\n" + conf.stderr)
        self.assertRegex(conf.stdout.strip(), r"^GO: eventbus-conformance — pubsub, 6 cases$")
        self.assertTrue(all(h == f"Bearer {token}" for h in state.auth_headers))
        self._assert_token_not_in_argv(argv_log, token)

        # Also prove PUBSUB_AUTH_CMD, gcloud fallback, and unauthenticated local override
        unauth_state = _PubSubState(expected_token=None)
        unauth_srv, unauth_url = _start_pubsub_server(unauth_state)
        self.addCleanup(unauth_srv.shutdown)
        self.addCleanup(unauth_srv.server_close)
        unauth_env = {
            "PATH": os.environ["PATH"],
            "GCP_PROJECT": "demo-proj",
            "PUBSUB_API_URL": unauth_url,
            "READY_TIMEOUT": "3",
        }
        r_unauth = subprocess.run(
            ["sh", "adapters/pubsub/ready.sh"], cwd=ROOT, env=unauth_env, capture_output=True, text=True
        )
        self.assertEqual(r_unauth.returncode, 0, r_unauth.stderr)
        self.assertEqual(unauth_state.auth_headers, ["", ""])

    def test_firestore_statestore_conformance_all_8_cases_and_auth_hygiene(self):
        token = "tok-firestore-secret-888"
        state = _FirestoreState(expected_token=token)
        srv, base_url = _start_firestore_server(state)
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)

        bin_dir = self.root / "bin-fs"
        argv_log = self.root / "fs-curl-argv.log"
        _install_curl_argv_spy(bin_dir, argv_log)

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CONFORMANCE": "1",
            "STATE_STORE": "firestore",
            "GCP_PROJECT": "demo-proj",
            "FIRESTORE_API_URL": base_url,
            "FIRESTORE_ACCESS_TOKEN": token,
            "READY_TIMEOUT": "5",
        }
        ready = subprocess.run(
            ["sh", "adapters/firestore/ready.sh"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ready.returncode, 0, ready.stderr)
        self.assertEqual(ready.stdout, "")

        conf = subprocess.run(
            ["sh", "gates/statestore-conformance.sh"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(conf.returncode, 0, conf.stdout + "\n" + conf.stderr)
        self.assertEqual(conf.stdout.strip(), "GO: statestore-conformance — firestore, 8 cases")
        self.assertTrue(all(h == f"Bearer {token}" for h in state.auth_headers))
        self._assert_token_not_in_argv(argv_log, token)

        # Verify exit 2 on unreachable store vs exit 1 on absent document
        bad_env = dict(env, FIRESTORE_API_URL="http://127.0.0.1:1")
        g_err = subprocess.run(
            ["sh", "adapters/firestore/get.sh", "tickets", "0001"],
            cwd=ROOT,
            env=bad_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(g_err.returncode, 2)
        self.assertTrue(g_err.stderr.strip())

    def test_artifact_registry_conformance_all_6_cases(self):
        token = "tok-ar-secret-777"
        reg_state = _RegistryState(expected_token=token)

        bin_dir = self.root / "bin-ar"
        bin_dir.mkdir(parents=True)
        tar_path = self.root / "img.tar"
        tar_path.write_bytes(_sample_tar_bytes())

        _install_registry_cli_stubs(bin_dir, tar_path)
        argv_log = self.root / "ar-curl-argv.log"
        _install_curl_argv_spy(bin_dir, argv_log)

        reg_file = bin_dir / "reg-state.json"
        push_marker = bin_dir / "last-push.json"
        host_holder = {"host": ""}

        class LiveHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _sync(self):
                if push_marker.is_file():
                    m = json.loads(push_marker.read_text(encoding="utf-8"))
                    body = m["body"].encode("utf-8")
                    reg_state.manifests[m["tag"]] = (m["digest"], body)
                    reg_state.manifests[m["digest"]] = (m["digest"], body)
                if reg_file.is_file():
                    rf = json.loads(reg_file.read_text(encoding="utf-8"))
                    reg_state.referrers = rf.get("referrers", {})

            def _check_auth(self) -> bool:
                auth = self.headers.get("Authorization", "")
                reg_state.auth_headers.append(auth)
                if auth == f"Bearer {token}":
                    return True
                self.send_response(401)
                self.send_header("Docker-Distribution-Api-Version", "registry/2.0")
                self.send_header(
                    "WWW-Authenticate",
                    f'Bearer realm="http://{host_holder["host"]}/token",service="ar",scope="repository:demo-proj/repo/app:pull"',
                )
                self.end_headers()
                return False

            def do_GET(self):  # noqa: N802
                self._sync()
                parsed = urlparse(self.path)
                if parsed.path == "/token":
                    if self.headers.get("Authorization") != f"Bearer {token}":
                        self.send_response(401)
                        self.end_headers()
                        return
                    body = json.dumps({"token": token}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if not self._check_auth():
                    return
                if parsed.path == "/v2/":
                    self.send_response(200)
                    self.send_header("Docker-Distribution-Api-Version", "registry/2.0")
                    self.end_headers()
                    return
                if "/manifests/" in parsed.path:
                    ref = parsed.path.rsplit("/manifests/", 1)[1]
                    item = reg_state.manifests.get(ref)
                    if not item:
                        self.send_response(404)
                        self.end_headers()
                        return
                    digest, body = item
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.oci.image.manifest.v1+json")
                    self.send_header("Docker-Content-Digest", digest)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if "/referrers/" in parsed.path:
                    digest = parsed.path.rsplit("/referrers/", 1)[1]
                    body = json.dumps(
                        {"schemaVersion": 2, "manifests": reg_state.referrers.get(digest, [])}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.oci.image.index.v1+json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def do_HEAD(self):  # noqa: N802
                self._sync()
                if not self._check_auth():
                    return
                parsed = urlparse(self.path)
                if "/manifests/" in parsed.path:
                    ref = parsed.path.rsplit("/manifests/", 1)[1]
                    item = reg_state.manifests.get(ref)
                    if not item:
                        self.send_response(404)
                        self.end_headers()
                        return
                    digest, body = item
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.oci.image.manifest.v1+json")
                    self.send_header("Docker-Content-Digest", digest)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return
                self.send_response(404)
                self.end_headers()

        live_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LiveHandler)
        self.addCleanup(live_srv.shutdown)
        self.addCleanup(live_srv.server_close)
        threading.Thread(target=live_srv.serve_forever, daemon=True).start()
        host = f"127.0.0.1:{live_srv.server_port}"
        host_holder["host"] = host
        repo = f"{host}/demo-proj/repo/app"

        # Fake gcloud print-access-token to prove gcloud fallback + oauth2accesstoken wiring
        _write_exe(
            bin_dir / "gcloud",
            f"""#!/usr/bin/env python3
import sys
if sys.argv[1:] == ["auth", "print-access-token"]:
    print({token!r})
    sys.exit(0)
sys.exit(1)
""",
        )

        wl = self.root / "wl-ar"
        wl.mkdir()
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CONFORMANCE": "1",
            "CONFORMANCE_REQUIRE_SBOM": "1",
            "REGISTRY": "artifact-registry",
            "OCI_REGISTRY_REPO": repo,
            "OCI_IMAGE_ARCHIVE": str(tar_path),
            "WORKLOAD": str(wl),
            "COSIGN_CERT_IDENTITY_REGEXP": "https://github.com/org/repo/.*",
            "COSIGN_CERT_OIDC_ISSUER": "https://token.actions.githubusercontent.com",
            "READY_TIMEOUT": "5",
        }
        conf = subprocess.run(
            ["sh", "gates/registry-conformance.sh"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(conf.returncode, 0, conf.stdout + "\n" + conf.stderr)
        self.assertEqual(conf.stdout.strip(), "GO: registry-conformance — artifact-registry, 6 cases")
        self.assertIn(f"Bearer {token}", reg_state.auth_headers)
        self._assert_token_not_in_argv(argv_log, token)

        # Prove env.sh derives *-docker.pkg.dev/<project>/<repo>/<service> and OCI_REGISTRY_USER=oauth2accesstoken
        derived = subprocess.run(
            [
                "sh",
                "-c",
                '. "adapters/artifact-registry/env.sh" && printf "%s|%s|%s\\n" "$OCI_REGISTRY_REPO" "$OCI_REGISTRY_USER" "$OCI_REGISTRY_TOKEN"',
            ],
            cwd=ROOT,
            env={
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "GCP_PROJECT": "proj-1",
                "GCP_REGION": "europe-west1",
                "ARTIFACT_REGISTRY_REPOSITORY": "apps",
                "CLOUD_RUN_SERVICE": "backend",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(derived.returncode, 0, derived.stderr)
        self.assertEqual(
            derived.stdout.strip(),
            f"europe-west1-docker.pkg.dev/proj-1/apps/backend|oauth2accesstoken|{token}",
        )

    def test_managed_prometheus_query_slo_readiness_and_auth_hygiene(self):
        token = "tok-prom-secret-666"
        seen_auth: list[str] = []

        class PromHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                auth = self.headers.get("Authorization", "")
                seen_auth.append(auth)
                if auth != f"Bearer {token}":
                    self.send_response(401)
                    self.end_headers()
                    return
                parsed = urlparse(self.path)
                if parsed.path != "/api/v1/query":
                    self.send_response(404)
                    self.end_headers()
                    return
                q = parse_qs(parsed.query).get("query", [""])[0]
                if "nan_metric" in q:
                    res = [{"metric": {"service_version": "v-canary"}, "value": [1, "NaN"]}]
                elif "5.." in q:
                    res = [
                        {"metric": {"service_version": "v-canary"}, "value": [1, "0.001"]},
                        {"metric": {"service_version": "v-stable"}, "value": [1, "0.001"]},
                    ]
                elif "histogram_quantile" in q:
                    res = [
                        {"metric": {"service_version": "v-canary"}, "value": [1, "0.04"]},
                        {"metric": {"service_version": "v-stable"}, "value": [1, "0.03"]},
                    ]
                elif "count(" in q:
                    res = [{"metric": {}, "value": [1, "2"]}]
                else:
                    res = [
                        {"metric": {"service_version": "v-canary"}, "value": [1, "10.0"]},
                        {"metric": {"service_version": "v-stable"}, "value": [1, "20.0"]},
                    ]
                body = json.dumps({"status": "success", "data": {"resultType": "vector", "result": res}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PromHandler)
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        metrics_url = f"http://127.0.0.1:{srv.server_port}"

        bin_dir = self.root / "bin-mp"
        argv_log = self.root / "mp-curl-argv.log"
        _install_curl_argv_spy(bin_dir, argv_log)

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WORKLOAD_CONTRACT": str(ROOT / "app"),
            "METRICS": "managed-prometheus",
            "DELIVERY": "cloud-deploy",
            "METRICS_URL": metrics_url,
            "MANAGED_PROMETHEUS_ACCESS_TOKEN": token,
            "READY_TIMEOUT": "5",
            "CANARY_VERSION": "v-canary",
            "STABLE_VERSION": "v-stable",
        }
        env.pop("METRICS_QUERY", None)
        ready = subprocess.run(["sh", "adapters/managed-prometheus/ready.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(ready.returncode, 0, ready.stderr)
        self.assertEqual(ready.stdout, "")

        q_nan = subprocess.run(
            ["sh", "adapters/managed-prometheus/query.sh", "nan_metric"], cwd=ROOT, env=env, capture_output=True, text=True
        )
        self.assertEqual(q_nan.returncode, 0, q_nan.stderr)
        self.assertEqual(json.loads(q_nan.stdout)["data"]["result"][0]["value"][1], "NaN")

        slo = subprocess.run(["sh", "verify/slo.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(slo.returncode, 0, slo.stdout + "\n" + slo.stderr)
        self.assertEqual(slo.stdout.strip(), "GO: slo")

        # Production readiness GO with managed-prometheus + native cloud-deploy record
        conf_dir = self.root / "conf"
        conf_dir.mkdir()
        from core.gates.readiness import adapter_hash

        digest = adapter_hash(str(ROOT / "adapters" / "cloud-deploy"))
        (conf_dir / "delivery-cloud-deploy.env").write_text(
            f"ADAPTER=cloud-deploy\nHASH={digest}\nAT=2026-11-24T00:00:00Z\n", encoding="utf-8"
        )
        env_file = self.root / "prod.env"
        env_file.write_text("APP_PORT=9000\n", encoding="utf-8")
        pr_env = dict(
            env,
            CONFORMANCE_DIR=str(conf_dir),
            READINESS_ENV_FILE=str(env_file),
            CONSOLE_AUTH="proxy",
            CONSOLE_IDENTITY_HEADER="X-User",
            CONSOLE_LAUNCHERS="alice",
        )
        pr_env.pop("GO_FOR_LAUNCH", None)
        pr = subprocess.run(["sh", "gates/production-readiness.sh"], cwd=ROOT, env=pr_env, capture_output=True, text=True)
        self.assertEqual(pr.returncode, 0, pr.stdout + "\n" + pr.stderr)
        self.assertTrue(pr.stdout.startswith("GO: production-readiness — "), pr.stdout)
        self.assertTrue(all(h == f"Bearer {token}" for h in seen_auth))
        self._assert_token_not_in_argv(argv_log, token)

    def test_cloud_deploy_native_delivery_verbs_against_stateful_gcloud(self):
        class HealthHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                if self.path.split("?", 1)[0] == "/healthz":
                    body = json.dumps({"status": "ok", "version": "0.1.0"}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        app_url = f"http://127.0.0.1:{srv.server_port}"

        bin_dir = self.root / "bin-cd"
        state_file = self.root / "cd-state.json"
        log_file = self.root / "cd-gcloud.log"
        state_file.write_text(
            json.dumps({
                "stable_rev": "backend-00001",
                "stable_ver": "0.1.0",
                "canary_rev": None,
                "canary_ver": None,
                "weight": 0,
                "release": None,
                "rollout": None,
                "canary_url": "https://canary---backend-stub.a.run.app",
                "app_url": app_url,
            }),
            encoding="utf-8",
        )
        _write_exe(
            bin_dir / "gcloud",
            f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path

sp = Path({str(state_file)!r})
lp = Path({str(log_file)!r})
args = sys.argv[1:]
with lp.open("a", encoding="utf-8") as fh:
    fh.write("gcloud " + " ".join(args) + "\\n")
st = json.loads(sp.read_text(encoding="utf-8"))

def opt(flag):
    for i, a in enumerate(args):
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return ""

cmd = args[:3]
if cmd == ["deploy", "targets", "describe"]:
    print(json.dumps({{"targetId": args[3]}}))
elif cmd == ["deploy", "targets", "rollback"]:
    st["weight"] = 0
    st["canary_rev"] = None
    st["canary_ver"] = None
    sp.write_text(json.dumps(st))
    print(json.dumps({{"status": "SUCCEEDED"}}))
elif cmd == ["deploy", "releases", "create"]:
    rel = args[3]
    ann = opt("--annotations")
    ver = ann.split("lights-out.version=", 1)[1].split(",", 1)[0]
    st["release"] = rel
    st["canary_rev"] = "backend-00002"
    st["canary_ver"] = ver
    st["weight"] = 0
    st["rollout"] = rel + "-to-prod-0001"
    sp.write_text(json.dumps(st))
    print(json.dumps({{"name": rel}}))
elif cmd == ["deploy", "releases", "list"]:
    print(json.dumps([{{"name": "projects/p/locations/r/deliveryPipelines/pipe/releases/" + st["release"]}}] if st["release"] else []))
elif cmd == ["deploy", "rollouts", "list"]:
    print(json.dumps([{{"name": "projects/p/locations/r/deliveryPipelines/pipe/releases/r/rollouts/" + st["rollout"]}}] if st["rollout"] else []))
elif cmd == ["deploy", "rollouts", "advance"]:
    phase = opt("--phase-id")
    st["weight"] = {{"canary-5": 5, "canary-25": 25, "stable": 100}}[phase]
    sp.write_text(json.dumps(st))
    print(json.dumps({{"status": "SUCCEEDED"}}))
elif cmd == ["run", "services", "describe"]:
    traffic = [{{"revisionName": st["stable_rev"], "percent": 100 - st["weight"]}}]
    if st["canary_rev"]:
        traffic.append({{"revisionName": st["canary_rev"], "percent": st["weight"], "tag": "canary", "url": st["canary_url"]}})
    print(json.dumps({{"status": {{"url": st["app_url"], "conditions": [{{"type": "Ready", "status": "True"}}], "traffic": traffic}}}}))
elif cmd == ["run", "revisions", "describe"]:
    rev = args[3]
    ver = st["canary_ver"] if rev == st["canary_rev"] else st["stable_ver"]
    print(json.dumps({{"metadata": {{"labels": {{"lights-out.version": ver}}}}}}))
else:
    sys.exit(1)
""",
        )

        wl = self.root / "wl-cd"
        wl.mkdir()
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WORKLOAD": str(wl),
            "APP_URL": app_url,
            "GCP_PROJECT": "demo-proj",
            "GCP_REGION": "europe-west1",
            "CLOUD_DEPLOY_PIPELINE": "backend-pipe",
            "CLOUD_DEPLOY_TARGET": "prod",
            "CLOUD_RUN_SERVICE": "backend",
            "READY_TIMEOUT": "3",
        }
        env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

        r = subprocess.run(["sh", "adapters/cloud-deploy/ready.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

        d = subprocess.run(
            ["sh", "adapters/cloud-deploy/deploy.sh", "0.2.0-rc1", "repo/app@sha256:" + "a" * 64],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(d.returncode, 0, d.stderr)

        for pct in (5, 25, 100):
            sh_r = subprocess.run(["sh", "adapters/cloud-deploy/shift.sh", str(pct)], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(sh_r.returncode, 0, sh_r.stderr)
            st_r = subprocess.run(["sh", "adapters/cloud-deploy/status.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(st_r.returncode, 0, st_r.stderr)
            st = json.loads(st_r.stdout)
            self.assertEqual(st, {"stable": "0.1.0", "canary": "0.2.0-rc1", "weight": pct})

        cu = subprocess.run(["sh", "adapters/cloud-deploy/canary-url.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(cu.returncode, 0, cu.stderr)
        self.assertEqual(cu.stdout.strip(), "https://canary---backend-stub.a.run.app")

        for _ in range(2):
            rb = subprocess.run(["sh", "adapters/cloud-deploy/rollback.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(rb.returncode, 0, rb.stderr)
            st = json.loads(subprocess.check_output(["sh", "adapters/cloud-deploy/status.sh"], cwd=ROOT, env=env, text=True))
            self.assertEqual(st, {"stable": "0.1.0", "canary": None, "weight": 0})

        # Static credential env var must refuse
        bad_env = dict(env, GOOGLE_APPLICATION_CREDENTIALS="/tmp/fake-sa.json")
        bad_r = subprocess.run(["sh", "adapters/cloud-deploy/status.sh"], cwd=ROOT, env=bad_env, capture_output=True, text=True)
        self.assertNotEqual(bad_r.returncode, 0)
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", bad_r.stderr)

    def test_cloud_workflows_orchestrator_pubsub_dispatch_and_bounded_poll(self):
        token = "tok-wf-secret-555"
        ps_state = _PubSubState(expected_token=token)
        ps_srv, ps_url = _start_pubsub_server(ps_state)
        self.addCleanup(ps_srv.shutdown)
        self.addCleanup(ps_srv.server_close)

        wf_calls: list[dict] = []
        wf_polls: dict[str, int] = {}
        wf_auths: list[str] = []
        mode = {"outcome": "SUCCEEDED"}

        class WfHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _send(self, code: int, payload: dict):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _check_auth(self) -> bool:
                auth = self.headers.get("Authorization", "")
                wf_auths.append(auth)
                if auth != f"Bearer {token}":
                    self._send(401, {"error": {"message": "unauthorized"}})
                    return False
                return True

            def do_GET(self):  # noqa: N802
                if not self._check_auth():
                    return
                path = urlparse(self.path).path
                if path == "/v1/projects/demo-proj/locations/us-central1/workflows/lights-out-tower":
                    self._send(200, {"name": path[4:], "state": "ACTIVE"})
                    return
                if "/executions/" in path:
                    eid = path.rsplit("/executions/", 1)[1]
                    wf_polls[eid] = wf_polls.get(eid, 0) + 1
                    if mode["outcome"] == "TIMEOUT":
                        self._send(200, {"name": path[4:], "state": "ACTIVE"})
                        return
                    if wf_polls[eid] == 1:
                        self._send(200, {"name": path[4:], "state": "ACTIVE"})
                        return
                    if mode["outcome"] == "FAILED":
                        self._send(200, {"name": path[4:], "state": "FAILED", "error": {"message": "step boom"}})
                        return
                    self._send(200, {"name": path[4:], "state": "SUCCEEDED", "result": '{"state":"assigned"}'})
                    return
                self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):  # noqa: N802
                if not self._check_auth():
                    return
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n).decode("utf-8"))
                arg = json.loads(payload["argument"])
                eid = f"exec-{len(wf_calls) + 1}"
                wf_calls.append({"eid": eid, "arg": arg})
                self._send(
                    200,
                    {
                        "name": f"projects/demo-proj/locations/us-central1/workflows/lights-out-tower/executions/{eid}",
                        "state": "ACTIVE",
                    },
                )

        wf_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), WfHandler)
        self.addCleanup(wf_srv.shutdown)
        self.addCleanup(wf_srv.server_close)
        threading.Thread(target=wf_srv.serve_forever, daemon=True).start()
        wf_url = f"http://127.0.0.1:{wf_srv.server_port}"

        bin_dir = self.root / "bin-wf"
        argv_log = self.root / "wf-curl-argv.log"
        _install_curl_argv_spy(bin_dir, argv_log)

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GCP_PROJECT": "demo-proj",
            "GCP_WORKFLOW_LOCATION": "us-central1",
            "CLOUD_WORKFLOW_NAME": "lights-out-tower",
            "EVENT_BUS": "pubsub",
            "PUBSUB_API_URL": ps_url,
            "PUBSUB_ACCESS_TOKEN": token,
            "WORKFLOWS_API_URL": wf_url,
            "WORKFLOWS_ACCESS_TOKEN": token,
            "WORKFLOW_POLL_INTERVAL_S": "0.05",
            "WORKFLOW_POLL_TIMEOUT_S": "5",
            "READY_TIMEOUT": "5",
        }
        r_wf = subprocess.run(["sh", "adapters/cloud-workflows/ready.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(r_wf.returncode, 0, r_wf.stderr)
        self.assertEqual(r_wf.stdout, "")

        # Publish two events onto Pub/Sub and drain via ORCHESTRATOR=cloud-workflows
        for step in ("assign", "ci"):
            pub = subprocess.run(
                ["sh", "adapters/pubsub/publish.sh", json.dumps({"ticket": "0042", "step": step})],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(pub.returncode, 0, pub.stderr)

        run_ok = subprocess.run(["sh", "adapters/cloud-workflows/run.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(run_ok.returncode, 0, run_ok.stderr)
        self.assertEqual([c["arg"] for c in wf_calls], [{"ticket": "0042", "step": "assign"}, {"ticket": "0042", "step": "ci"}])
        self.assertEqual(ps_state.queue, [])

        # Failed execution is reported and still acked
        mode["outcome"] = "FAILED"
        subprocess.run(
            ["sh", "adapters/pubsub/publish.sh", '{"ticket":"0042","step":"review"}'],
            cwd=ROOT,
            env=env,
            check=True,
        )
        run_fail = subprocess.run(["sh", "adapters/cloud-workflows/run.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(run_fail.returncode, 0)
        self.assertIn("step boom", run_fail.stderr)
        self.assertEqual(ps_state.queue, [])

        # Bounded timeout exits nonzero when poll exceeds WORKFLOW_POLL_TIMEOUT_S
        mode["outcome"] = "TIMEOUT"
        timeout_env = dict(env, WORKFLOW_POLL_TIMEOUT_S="1", WORKFLOW_POLL_INTERVAL_S="0.2")
        run_to = subprocess.run(
            ["sh", "adapters/cloud-workflows/run.sh", "0042", "merge"],
            cwd=ROOT,
            env=timeout_env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(run_to.returncode, 0)
        self.assertIn("timed out after 1s", run_to.stderr)
        self.assertTrue(all(h == f"Bearer {token}" for h in wf_auths))
        self._assert_token_not_in_argv(argv_log, token)

        wf_yaml = yaml.safe_load((ROOT / "adapters" / "cloud-workflows" / "workflow.yaml").read_text(encoding="utf-8"))
        self.assertIn("main", wf_yaml)

    def test_nightly_cloud_conformance_workflow_structure(self):
        wf = yaml.safe_load((ROOT / ".github" / "workflows" / "cloud-conformance.yml").read_text(encoding="utf-8"))
        triggers = wf.get("on", wf.get(True, {}))
        self.assertIn("schedule", triggers)
        self.assertIn("workflow_dispatch", triggers)
        self.assertEqual(wf["permissions"], {"contents": "read"})
        self.assertEqual(wf["jobs"]["conformance"]["permissions"]["id-token"], "write")
        self.assertIn("CLOUD_CONFORMANCE_ENABLED", wf["jobs"]["gate"]["steps"][0]["env"])
        matrix_targets = wf["jobs"]["conformance"]["strategy"]["matrix"]["target"]
        self.assertEqual(matrix_targets, ["gcp", "oci-registry", "aws", "azure"])


if __name__ == "__main__":
    unittest.main()
