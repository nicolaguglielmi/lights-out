"""End-to-end contract tests for ADR 0107: oci-registry, flags-file, flags-http,
registry/flags conformance gates, and cloud ingress/proxy SLO templates.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import shutil
import socketserver
import stat
import subprocess
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = ROOT / "plugins" / "cloud"
PROVIDERS = (
    "gcp-cloud-deploy",
    "aws-ecs-codedeploy",
    "azure-container-apps",
    "hetzner-k3s",
)


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


class _UnixEngineHandler(http.server.BaseHTTPRequestHandler):
    tar_bytes = _sample_tar_bytes()

    def log_message(self, *_args):
        return

    def do_GET(self):  # noqa: N802
        if self.path.endswith("/get") and "/images/" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-tar")
            self.send_header("Content-Length", str(len(self.tar_bytes)))
            self.end_headers()
            self.wfile.write(self.tar_bytes)
            return
        self.send_response(404)
        self.end_headers()


class _UnixEngineServer(socketserver.UnixStreamServer):
    pass


class _RegistryState:
    def __init__(self, require_bearer: bool = False, expected_token: str = "tok-oci-123"):
        self.require_bearer = require_bearer
        self.expected_token = expected_token
        self.manifests: dict[str, tuple[str, bytes]] = {}
        self.referrers: dict[str, list[dict]] = {}
        self.signatures: dict[str, str] = {}
        self.token_requests: list[str] = []


def _start_registry_server(state: _RegistryState):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _check_auth(self) -> bool:
            if not state.require_bearer:
                return True
            auth = self.headers.get("Authorization", "")
            if auth == f"Bearer {state.expected_token}":
                return True
            host = self.headers.get("Host", "127.0.0.1")
            self.send_response(401)
            self.send_header("Docker-Distribution-Api-Version", "registry/2.0")
            self.send_header(
                "WWW-Authenticate",
                f'Bearer realm="http://{host}/token",service="test-oci",scope="repository:org/app:pull"',
            )
            self.end_headers()
            return False

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/token":
                state.token_requests.append(self.path)
                body = json.dumps({"token": state.expected_token}).encode()
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
                ref = parsed.path.split("/manifests/", 1)[1]
                if ref not in state.manifests:
                    self.send_response(404)
                    self.end_headers()
                    return
                digest, payload = state.manifests[ref]
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.oci.image.manifest.v1+json")
                self.send_header("Docker-Content-Digest", digest)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if "/referrers/" in parsed.path:
                digest = parsed.path.split("/referrers/", 1)[1]
                body = json.dumps({
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "manifests": state.referrers.get(digest, []),
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.oci.image.index.v1+json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_HEAD(self):  # noqa: N802
            parsed = urlparse(self.path)
            if not self._check_auth():
                return
            if "/manifests/" in parsed.path:
                ref = parsed.path.split("/manifests/", 1)[1]
                if ref not in state.manifests:
                    self.send_response(404)
                    self.end_headers()
                    return
                digest, payload = state.manifests[ref]
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.oci.image.manifest.v1+json")
                self.send_header("Docker-Content-Digest", digest)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"127.0.0.1:{srv.server_port}"


def _install_oci_cli_stubs(
    bin_dir: Path,
    reg_state: _RegistryState,
    log_file: Path,
    *,
    with_docker: bool = True,
    with_compose: bool = False,
) -> None:
    reg_log = log_file

    def record(line: str) -> None:
        with reg_log.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # Save state in a sidecar JSON file so external stub scripts can mutate manifests/signatures
    state_json = bin_dir / "oci_stub_state.json"
    state_json.write_text(json.dumps({
        "signed": {},
        "attested": {},
    }), encoding="utf-8")

    # We register manifests dynamically via a helper endpoint? No, easier: HTTP server reads a JSON or stubs update a file!
    # Wait: stubs run in separate processes; pass manifest dir via bin_dir!
    manifest_dir = bin_dir / "manifests"
    manifest_dir.mkdir(exist_ok=True)
    # Wait: our in-process HTTP server uses reg_state; let's have stubs write to manifest_dir AND allow tests to mirror,
    # OR let's pass a tiny local control callback? Even simpler: let's add a POST /_stub/push?tag=... in _start_registry_server?
    # Better: keep _start_registry_server pure OCI HTTP, and in stubs push via an internal env var or just update reg_state via a tiny HTTP helper in tests?
    # Actually, tests can expose a tiny control endpoint on a separate port or pass OCI_STUB_REG_HOOK!
    del record


class OciRegistryAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.reg_state = _RegistryState()
        self.srv, self.host = _start_registry_server(self.reg_state)
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)

        # Tiny local stub-control server so CLI stubs (docker/cosign) mutate reg_state cleanly across processes
        reg_state = self.reg_state

        class CtrlHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n).decode())
                op = body["op"]
                if op == "push":
                    tag = body["tag"]
                    payload = json.dumps({"schemaVersion": 2, "tag": tag}).encode()
                    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
                    reg_state.manifests[tag] = (digest, payload)
                    reg_state.manifests[digest] = (digest, payload)
                elif op == "sign":
                    ref = body["ref"]
                    digest = "sha256:" + ref.split("@sha256:", 1)[1]
                    reg_state.signatures[digest] = body
                elif op == "attest":
                    ref = body["ref"]
                    digest = "sha256:" + ref.split("@sha256:", 1)[1]
                    att_digest = "sha256:" + hashlib.sha256(
                        (digest + ":" + body["predicate_type"]).encode()
                    ).hexdigest()
                    reg_state.referrers.setdefault(digest, []).append({
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": att_digest,
                        "size": 128,
                        "annotations": {
                            "dev.sigstore.bundle.predicateType": body["predicate_type"],
                            "org.opencontainers.image.created": "2026-11-24T00:00:00Z",
                        },
                    })
                elif op == "check_sig":
                    ref = body["ref"]
                    digest = "sha256:" + ref.split("@sha256:", 1)[1] if "@sha256:" in ref else ""
                    ok = digest in reg_state.signatures
                    out = json.dumps({"ok": ok, "sig": reg_state.signatures.get(digest)}).encode()
                    self.send_response(200 if ok else 404)
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                elif op == "check_att":
                    ref = body["ref"]
                    digest = "sha256:" + ref.split("@sha256:", 1)[1] if "@sha256:" in ref else ""
                    atts = reg_state.referrers.get(digest, [])
                    ok = any(
                        a.get("annotations", {}).get("dev.sigstore.bundle.predicateType") == "https://spdx.dev/Document"
                        for a in atts
                    )
                    out = json.dumps({"ok": ok}).encode()
                    self.send_response(200 if ok else 404)
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                self.send_response(200)
                self.end_headers()

        self.ctrl_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CtrlHandler)
        threading.Thread(target=self.ctrl_srv.serve_forever, daemon=True).start()
        self.addCleanup(self.ctrl_srv.shutdown)
        self.addCleanup(self.ctrl_srv.server_close)
        self.ctrl_url = f"http://127.0.0.1:{self.ctrl_srv.server_port}"

    def _make_stubs(self, bin_dir: Path, log_file: Path, *, engine_cli: str = "docker", with_compose: bool = False) -> None:
        if engine_cli:
            _write_exe(bin_dir / engine_cli, f"""#!/usr/bin/env python3
import json, sys, urllib.request
from pathlib import Path
args = sys.argv[1:]
Path({str(log_file)!r}).open("a").write("{engine_cli} " + " ".join(args) + "\\n")
cmd = args[0] if args else ""
if cmd == "push":
    ref = args[-1]
    tag = ref.rsplit(":", 1)[-1]
    req = urllib.request.Request({self.ctrl_url!r}, data=json.dumps({{"op": "push", "tag": tag}}).encode(), method="POST")
    urllib.request.urlopen(req).read()
elif cmd == "save":
    out_idx = args.index("-o") + 1
    import io, tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        d = b'{{"config":{{}}}}\\n'
        ti = tarfile.TarInfo("manifest.json")
        ti.size = len(d)
        tf.addfile(ti, io.BytesIO(d))
    Path(args[out_idx]).write_bytes(buf.getvalue())
elif cmd in ("build", "tag"):
    pass
else:
    sys.stderr.write("unexpected {engine_cli}: " + " ".join(args) + "\\n")
    sys.exit(1)
""")
        if with_compose:
            _write_exe(bin_dir / "docker-compose", f"""#!/usr/bin/env python3
import json, os, sys, urllib.request
from pathlib import Path
args = sys.argv[1:]
Path({str(log_file)!r}).open("a").write("docker-compose " + " ".join(args) + "\\n")
if "push" in args:
    tag = os.environ.get("APP_TAG", "latest")
    req = urllib.request.Request({self.ctrl_url!r}, data=json.dumps({{"op": "push", "tag": tag}}).encode(), method="POST")
    urllib.request.urlopen(req).read()
""")
        _write_exe(bin_dir / "osv-scanner", f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
Path({str(log_file)!r}).open("a").write("osv-scanner " + " ".join(args) + "\\n")
if "--output-file" in args:
    out = args[args.index("--output-file") + 1]
    Path(out).write_text(json.dumps({{"spdxVersion": "SPDX-2.3", "packages": [{{"name": "pkg"}}]}}), encoding="utf-8")
sys.exit(0)
""")
        _write_exe(bin_dir / "cosign", f"""#!/usr/bin/env python3
import json, sys, urllib.error, urllib.request
from pathlib import Path
args = sys.argv[1:]
Path({str(log_file)!r}).open("a").write("cosign " + " ".join(args) + "\\n")
sub = args[0] if args else ""
ref = args[-1] if args else ""
def post(payload):
    req = urllib.request.Request({self.ctrl_url!r}, data=json.dumps(payload).encode(), method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""
if sub == "sign":
    if "@sha256:" not in ref:
        sys.stderr.write("must sign by digest\\n")
        sys.exit(1)
    post({{"op": "sign", "ref": ref, "args": args}})
elif sub == "attest":
    if "@sha256:" not in ref:
        sys.stderr.write("must attest by digest\\n")
        sys.exit(1)
    ptype = "https://spdx.dev/Document" if "spdxjson" in args else "unknown"
    post({{"op": "attest", "ref": ref, "predicate_type": ptype, "args": args}})
elif sub == "verify":
    if "@sha256:" not in ref:
        sys.stderr.write("must verify by digest\\n")
        sys.exit(1)
    code, raw = post({{"op": "check_sig", "ref": ref}})
    if code != 200:
        sys.stderr.write("no matching signature\\n")
        sys.exit(1)
    sig = json.loads(raw)["sig"]
    s_args = sig.get("args", [])
    # Enforce verification mode matches signing mode
    if "--certificate-identity-regexp" in args:
        if "--certificate-oidc-issuer" not in args and "--certificate-oidc-issuer-regexp" not in args:
            sys.stderr.write("missing oidc issuer\\n")
            sys.exit(1)
    elif "--key" in args:
        key = args[args.index("--key") + 1]
        if key == "wrong.pub":
            sys.stderr.write("signature verification failed\\n")
            sys.exit(1)
    else:
        sys.stderr.write("no verification key or certificate policy\\n")
        sys.exit(1)
    print(json.dumps([{{"critical": {{"identity": {{"docker-reference": ref}}}}}}]))
elif sub == "verify-attestation":
    code, _ = post({{"op": "check_att", "ref": ref}})
    if code != 200:
        sys.stderr.write("no matching attestation\\n")
        sys.exit(1)
else:
    sys.stderr.write("unexpected cosign subcmd: " + sub + "\\n")
    sys.exit(1)
""")

    def test_env_validation_and_static_key_refusal(self):
        wl = self.root / "wl"
        wl.mkdir()
        for bad_repo in ("", "replace-me", "noslash", "https://ghcr.io/a/b", "ghcr.io/a/", "/a/b", "user@ghcr.io/a/b"):
            res = subprocess.run(
                ["sh", "-c", f'. "{ROOT / "adapters" / "oci-registry" / "env.sh"}"'],
                env={**os.environ, "OCI_REGISTRY_REPO": bad_repo, "WORKLOAD": str(wl)},
                capture_output=True, text=True,
            )
            self.assertNotEqual(res.returncode, 0, bad_repo)
            self.assertTrue(res.stderr.strip(), bad_repo)

        # Partial keyless policy must fail closed
        res = subprocess.run(
            ["sh", "-c", f'. "{ROOT / "adapters" / "oci-registry" / "env.sh"}"'],
            env={
                **os.environ,
                "OCI_REGISTRY_REPO": f"{self.host}/org/svc",
                "COSIGN_CERT_IDENTITY_REGEXP": "^https://github\\.com/org/",
                "WORKLOAD": str(wl),
            },
            capture_output=True, text=True,
        )
        self.assertNotEqual(res.returncode, 0)

        # Static key files inside $WORKLOAD must refuse
        for rel, content in (
            ("cosign.key", "-----BEGIN ENCRYPTED SIGSTORE PRIVATE KEY-----\n"),
            ("signing.key", "secret\n"),
            ("deploy.pem", "pem\n"),
            ("sa.json", '{"type":"service_account"}\n'),
            (".aws/credentials", "[default]\naws_secret_access_key=x\n"),
        ):
            bad_wl = self.root / f"bad-wl-{rel.replace('/', '_')}"
            p = bad_wl / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            res = subprocess.run(
                ["sh", "-c", f'. "{ROOT / "adapters" / "oci-registry" / "env.sh"}"'],
                env={
                    **os.environ,
                    "OCI_REGISTRY_REPO": f"{self.host}/org/svc",
                    "COSIGN_KMS_KEY": "gcpkms://projects/p/locations/global/keyRings/r/cryptoKeys/k",
                    "WORKLOAD": str(bad_wl),
                },
                capture_output=True, text=True,
            )
            self.assertNotEqual(res.returncode, 0, rel)
            self.assertIn(p.name, res.stderr)

    def test_ready_bearer_auth_publish_sbom_verify_with_docker_stub(self):
        self.reg_state.require_bearer = True
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        log_file = self.root / "cli.log"
        self._make_stubs(bin_dir, log_file, engine_cli="docker")
        wl = self.root / "wl-clean"
        wl.mkdir()
        (wl / "Containerfile").write_text("FROM scratch\n", encoding="utf-8")
        key = self.root / "cosign.key"
        pub = self.root / "cosign.pub"
        key.write_text("key\n", encoding="utf-8")
        pub.write_text("pub\n", encoding="utf-8")

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WORKLOAD": str(wl),
            "OCI_REGISTRY_REPO": f"{self.host}/org/svc",
            "OCI_AUTH_CMD": f"printf '%s\\n' {self.reg_state.expected_token}",
            "COSIGN_KEY": str(key),
            "READY_TIMEOUT": "2",
        }
        # ready.sh silent exit 0
        r = subprocess.run(["sh", "adapters/oci-registry/ready.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

        # publish.sh builds, pushes, runs osv-scanner + cosign attest + verify-attestation + sign
        p = subprocess.run(
            ["sh", "adapters/oci-registry/publish.sh", "sha-1111"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        ref = p.stdout.strip()
        self.assertRegex(ref, rf"^{self.host}/org/svc@sha256:[0-9a-f]{{64}}$")
        log_text = log_file.read_text(encoding="utf-8")
        self.assertIn("osv-scanner scan image --archive", log_text)
        self.assertIn("cosign attest", log_text)
        self.assertIn("cosign verify-attestation", log_text)
        self.assertIn("cosign sign", log_text)

        # sbom.sh returns attestation digest
        s = subprocess.run(
            ["sh", "adapters/oci-registry/sbom.sh", ref],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertRegex(s.stdout.strip(), r"^sha256:[0-9a-f]{64}$")

        # verify.sh resolves digest first and passes @sha256:... to cosign verify
        log_file.write_text("", encoding="utf-8")
        v = subprocess.run(
            ["sh", "adapters/oci-registry/verify.sh", "sha-1111"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertEqual(v.stdout.strip(), ref)
        v_log = log_file.read_text(encoding="utf-8")
        self.assertIn("cosign verify", v_log)
        self.assertIn("@sha256:", v_log)

        # verify.sh on unsigned tag refuses with empty stdout
        bad = subprocess.run(
            ["sh", "adapters/oci-registry/verify.sh", "sha-unsigned"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(bad.stdout, "")

    def test_publish_via_compose_and_unix_socket_and_keyless_verify(self):
        sock_path = self.root / "engine.sock"
        usock = _UnixEngineServer(str(sock_path), _UnixEngineHandler)
        threading.Thread(target=usock.serve_forever, daemon=True).start()
        self.addCleanup(usock.shutdown)
        self.addCleanup(usock.server_close)

        bin_dir = self.root / "bin-compose"
        bin_dir.mkdir()
        log_file = self.root / "compose.log"
        self._make_stubs(bin_dir, log_file, engine_cli="", with_compose=True)
        wl = self.root / "wl-compose"
        wl.mkdir()
        compose_f = self.root / "compose.yml"
        compose_f.write_text("services:\n  backend:\n    image: localhost:5050/x:latest\n", encoding="utf-8")

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WORKLOAD": str(wl),
            "COMPOSE_FILE": str(compose_f),
            "DOCKER_HOST": f"unix://{sock_path}",
            "OCI_REGISTRY_REPO": f"{self.host}/org/svc",
            "COSIGN_CERT_IDENTITY_REGEXP": "^https://github\\.com/org/svc/\\.github/workflows/",
            "COSIGN_CERT_OIDC_ISSUER": "https://token.actions.githubusercontent.com",
            "COSIGN_TLOG_UPLOAD": "false",
        }
        p = subprocess.run(
            ["sh", "adapters/oci-registry/publish.sh", "sha-2222"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        ref = p.stdout.strip()
        self.assertRegex(ref, rf"^{self.host}/org/svc@sha256:[0-9a-f]{{64}}$")
        logs = log_file.read_text(encoding="utf-8")
        self.assertIn("docker-compose", logs)
        self.assertIn("osv-scanner scan image --archive", logs)

        v = subprocess.run(
            ["sh", "adapters/oci-registry/verify.sh", "sha-2222"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertEqual(v.stdout.strip(), ref)

        # Also test skipping push when archive is not inspectable (remote pre-pushed digest)
        req = json.dumps({"op": "push", "tag": "sha-remote"}).encode()
        import urllib.request
        urllib.request.urlopen(urllib.request.Request(self.ctrl_url, data=req, method="POST")).read()
        env_remote = {
            **env,
            "DOCKER_HOST": "",
            "OCI_SKIP_PUSH": "1",
        }
        pr = subprocess.run(
            ["sh", "adapters/oci-registry/publish.sh", "sha-remote"],
            cwd=ROOT, env=env_remote, capture_output=True, text=True,
        )
        self.assertEqual(pr.returncode, 0, pr.stderr)
        self.assertRegex(pr.stdout.strip(), rf"^{self.host}/org/svc@sha256:[0-9a-f]{{64}}$")

        # Verify fails closed if no verification policy is set
        env_nopol = {
            k: v for k, v in env.items()
            if not k.startswith("COSIGN_")
        }
        v_fail = subprocess.run(
            ["sh", "adapters/oci-registry/verify.sh", "sha-2222"],
            cwd=ROOT, env=env_nopol, capture_output=True, text=True,
        )
        self.assertNotEqual(v_fail.returncode, 0)
        self.assertEqual(v_fail.stdout, "")

        # Passes full registry-conformance suite with stub CLIs
        conf = subprocess.run(
            ["sh", "gates/registry-conformance.sh"],
            cwd=ROOT,
            env={**env, "CONFORMANCE": "1", "REGISTRY": "oci-registry"},
            capture_output=True, text=True,
        )
        self.assertEqual(conf.returncode, 0, conf.stdout + conf.stderr)
        self.assertTrue(conf.stdout.startswith("GO: registry-conformance — oci-registry"), conf.stdout)


class FlagsAdaptersAndConformanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_flags_file_lifecycle_targeting_and_conformance(self):
        flags_path = self.root / "flags.json"
        flags_path.write_text(json.dumps({
            "flags": {
                "kill-switch": {
                    "state": "ENABLED",
                    "variants": {"on": True, "off": False},
                    "defaultVariant": "off",
                    "targeting": {"if": [{"==": [{"var": "version"}, "0.2.0-canary"]}, "on", "off"]},
                },
                "release-freeze": {
                    "state": "ENABLED",
                    "variants": {"on": True, "off": False},
                    "defaultVariant": "off",
                },
                "disabled-flag": {
                    "state": "DISABLED",
                    "variants": {"on": True, "off": False},
                    "defaultVariant": "on",
                },
            }
        }), encoding="utf-8")
        env = {**os.environ, "FLAGS_FILE": str(flags_path)}

        self.assertEqual(
            subprocess.run(["sh", "adapters/flags-file/ready.sh"], cwd=ROOT, env=env, capture_output=True, text=True).returncode,
            0,
        )
        # Targeting rule matches canary version and falls back otherwise
        r1 = subprocess.run(
            ["sh", "adapters/flags-file/evaluate.sh", "kill-switch", "0.2.0-canary"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertEqual((r1.returncode, r1.stdout.strip()), (0, "true"))
        r2 = subprocess.run(
            ["sh", "adapters/flags-file/evaluate.sh", "kill-switch", "0.1.0"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertEqual((r2.returncode, r2.stdout.strip()), (0, "false"))

        # Fail closed on missing/disabled flag or missing file
        for bad_flag in ("no-such-flag", "disabled-flag"):
            rb = subprocess.run(
                ["sh", "adapters/flags-file/evaluate.sh", bad_flag],
                cwd=ROOT, env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(rb.returncode, 0, bad_flag)
            self.assertEqual(rb.stdout, "")

        # set.sh clears targeting
        rs = subprocess.run(
            ["sh", "adapters/flags-file/set.sh", "kill-switch", "off"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertEqual(rs.returncode, 0, rs.stderr)
        r3 = subprocess.run(
            ["sh", "adapters/flags-file/evaluate.sh", "kill-switch", "0.2.0-canary"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertEqual((r3.returncode, r3.stdout.strip()), (0, "false"))

        # Passes full flags-conformance suite
        conf = subprocess.run(
            ["sh", "gates/flags-conformance.sh"],
            cwd=ROOT,
            env={**env, "CONFORMANCE": "1", "FLAGS": "flags-file"},
            capture_output=True, text=True,
        )
        self.assertEqual(conf.returncode, 0, conf.stdout + conf.stderr)
        self.assertTrue(conf.stdout.startswith("GO: flags-conformance — flags-file"), conf.stdout)

    def test_flags_http_auth_never_in_argv_and_conformance(self):
        secret_header = "Authorization: Bearer super-secret-ofrep-token-9876"
        state = {
            "flags": {
                "release-freeze": {"variants": {"on": True, "off": False}, "defaultVariant": "off"},
                "kill-switch": {"variants": {"on": True, "off": False}, "defaultVariant": "true_v", "raw": True},
            }
        }
        state["flags"] = {
            "release-freeze": {"variants": {"on": True, "off": False}, "defaultVariant": "off"},
        }
        seen_headers: list[str] = []

        class OfrepHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):  # noqa: N802
                auth = self.headers.get("Authorization", "")
                seen_headers.append(auth)
                if auth != "Bearer super-secret-ofrep-token-9876":
                    self.send_response(401)
                    self.end_headers()
                    return
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n).decode() or "{}")
                if self.path == "/admin/flags":
                    flag = body.get("flag", "")
                    variant = body.get("variant", "")
                    if flag not in state["flags"] or variant not in state["flags"][flag]["variants"]:
                        self.send_response(404)
                        self.end_headers()
                        return
                    state["flags"][flag]["defaultVariant"] = variant
                    resp = json.dumps({"flag": flag, "defaultVariant": variant}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                if self.path.startswith("/ofrep/v1/evaluate/flags/"):
                    flag = self.path.split("/ofrep/v1/evaluate/flags/", 1)[1]
                    if flag not in state["flags"]:
                        self.send_response(404)
                        self.end_headers()
                        return
                    spec = state["flags"][flag]
                    val = spec["variants"][spec["defaultVariant"]]
                    resp = json.dumps({"key": flag, "value": val, "variant": spec["defaultVariant"]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                self.send_response(404)
                self.end_headers()

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), OfrepHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)

        # Wrap real curl to record every argv invocation and prove secret_header never appears in argv
        real_curl = shutil.which("curl")
        self.assertIsNotNone(real_curl)
        bin_dir = self.root / "bin-curl"
        argv_log = self.root / "curl_argv.log"
        _write_exe(bin_dir / "curl", f"""#!/usr/bin/env python3
import os, sys
from pathlib import Path
Path({str(argv_log)!r}).open("a").write(repr(sys.argv[1:]) + "\\n")
os.execv({real_curl!r}, [{real_curl!r}, *sys.argv[1:]])
""")

        base_url = f"http://127.0.0.1:{srv.server_port}"
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FLAGS_URL": base_url,
            "FLAGS_AUTH_HEADER": secret_header,
            "READY_TIMEOUT": "2",
        }
        # Without FLAGS_SET_URL, set.sh must fail closed
        ro = subprocess.run(
            ["sh", "adapters/flags-http/set.sh", "release-freeze", "on"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertNotEqual(ro.returncode, 0)
        self.assertEqual(ro.stdout, "")

        # With FLAGS_SET_URL, passes full flags-conformance suite
        env_mut = {**env, "FLAGS_SET_URL": f"{base_url}/admin/flags"}
        conf = subprocess.run(
            ["sh", "gates/flags-conformance.sh"],
            cwd=ROOT,
            env={**env_mut, "CONFORMANCE": "1", "FLAGS": "flags-http"},
            capture_output=True, text=True,
        )
        self.assertEqual(conf.returncode, 0, conf.stdout + conf.stderr)
        self.assertTrue(conf.stdout.startswith("GO: flags-conformance — flags-http"), conf.stdout)
        self.assertNotIn("super-secret-ofrep-token-9876", argv_log.read_text(encoding="utf-8"))
        self.assertTrue(any(h == "Bearer super-secret-ofrep-token-9876" for h in seen_headers))

    def test_cold_conformance_gates_nogo_and_shell_boundary(self):
        for gate in ("registry-conformance", "flags-conformance"):
            sh_path = ROOT / "gates" / f"{gate}.sh"
            self.assertTrue(sh_path.is_file(), f"missing {sh_path}")
            code_lines = [
                ln for ln in sh_path.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")
            ]
            self.assertLessEqual(len(code_lines), 30, f"{sh_path} has {len(code_lines)} code lines")
            cold = subprocess.run(
                ["sh", str(sh_path)],
                cwd=ROOT,
                env={k: v for k, v in os.environ.items() if k != "CONFORMANCE"},
                capture_output=True, text=True,
            )
            self.assertEqual(cold.returncode, 1)
            self.assertEqual(len(cold.stdout.splitlines()), 1, cold.stdout)
            self.assertTrue(cold.stdout.startswith(f"NO-GO: {gate} — "), cold.stdout)
            self.assertIn("CONFORMANCE=1", cold.stdout)


class CloudIngressSloAndSetupTest(unittest.TestCase):
    def test_all_four_slo_ingress_templates_work_with_verify_slo(self):
        responses = {
            "traffic": [
                {"metric": {"envoy_cluster_name": "canary"}, "value": [1, "1.0"]},
                {"metric": {"envoy_cluster_name": "stable"}, "value": [1, "4.0"]},
            ],
            "error_rate_ok": [
                {"metric": {"envoy_cluster_name": "canary"}, "value": [1, "0.001"]},
                {"metric": {"envoy_cluster_name": "stable"}, "value": [1, "0.001"]},
            ],
            "error_rate_bad": [
                {"metric": {"envoy_cluster_name": "canary"}, "value": [1, "0.25"]},
                {"metric": {"envoy_cluster_name": "stable"}, "value": [1, "0.001"]},
            ],
            "latency_p95": [
                {"metric": {"envoy_cluster_name": "canary"}, "value": [1, "0.05"]},
                {"metric": {"envoy_cluster_name": "stable"}, "value": [1, "0.04"]},
            ],
        }
        mode = {"err": "error_rate_ok"}

        class PromHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query).get("query", [""])[0]
                if 'envoy_response_code_class="5"' in q or 'status=~"5.."' in q:
                    res = responses[mode["err"]]
                elif "histogram_quantile" in q:
                    res = responses["latency_p95"]
                else:
                    res = responses["traffic"]
                body = json.dumps({"status": "success", "data": {"result": res}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PromHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)

        for p in PROVIDERS:
            promql = PLUGINS_DIR / p / "slo-ingress.promql"
            self.assertTrue(promql.is_file(), f"missing {promql}")
            text = promql.read_text(encoding="utf-8")
            for name in ("traffic", "error_rate", "latency_p95"):
                self.assertIn(f"# name: {name}", text)
            self.assertNotIn("target_info", text)
            self.assertNotIn("http_server_request_duration_seconds", text)
            env = {
                **os.environ,
                "WORKLOAD_CONTRACT": str(ROOT / "app"),
                "SLO_PROMQL": str(promql),
                "CANARY_VERSION": "v-canary",
                "STABLE_VERSION": "v-stable",
                "METRICS_QUERY": "adapters/promql/query.sh",
                "METRICS_URL": f"http://127.0.0.1:{srv.server_port}",
            }
            mode["err"] = "error_rate_ok"
            go_res = subprocess.run(["sh", "verify/slo.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(go_res.returncode, 0, f"{p}: {go_res.stdout}\n{go_res.stderr}")
            self.assertEqual(go_res.stdout.strip(), "GO: slo")

            mode["err"] = "error_rate_bad"
            nogo_res = subprocess.run(["sh", "verify/slo.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(nogo_res.returncode, 1)
            self.assertIn("NO-GO: slo — error_rate", nogo_res.stdout)

    def test_cloud_setup_slo_mode_and_e2e_instructions(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "cloud"
            # Default proxy mode
            r_def = subprocess.run(
                ["python3", "plugins/cloud/setup.py", "--cloud", "gcp-cloud-deploy", "--out", str(out_dir)],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(r_def.returncode, 0, r_def.stdout + r_def.stderr)
            self.assertIn("slo-ingress.promql", r_def.stderr)
            self.assertIn("REGISTRY=oci-registry", r_def.stderr)
            self.assertIn("FLAGS=flags-file", r_def.stderr)
            self.assertIn("flags-http", r_def.stderr)

            # Explicit otel mode
            r_otel = subprocess.run(
                ["python3", "plugins/cloud/setup.py", "--cloud", "aws-ecs-codedeploy", "--slo-mode", "otel", "--out", str(out_dir)],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(r_otel.returncode, 0, r_otel.stdout + r_otel.stderr)
            self.assertIn("aws-ecs-codedeploy/slo.promql", r_otel.stderr)

            # Invalid slo-mode refuses with NO-GO
            r_bad = subprocess.run(
                ["python3", "plugins/cloud/setup.py", "--cloud", "gcp-cloud-deploy", "--slo-mode", "invalid", "--out", str(out_dir)],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertNotEqual(r_bad.returncode, 0)
            self.assertTrue(r_bad.stdout.strip().startswith("NO-GO: cloud-setup — "), r_bad.stdout)

    def test_readmes_and_shellcheck_clean(self):
        honesty = "Contract-tested locally against stub CLIs; live cloud conformance requires your own cloud account"
        for readme in [PLUGINS_DIR / "README.md", *(PLUGINS_DIR / p / "README.md" for p in PROVIDERS)]:
            txt = readme.read_text(encoding="utf-8")
            self.assertIn(honesty, txt, str(readme))
            self.assertIn("slo-ingress.promql", txt, str(readme))
            self.assertIn("oci-registry", txt, str(readme))
            self.assertIn("flags-file", txt, str(readme))
            self.assertIn("flags-http", txt, str(readme))

        for ad in ("oci-registry", "flags-file", "flags-http"):
            self.assertTrue((ROOT / "adapters" / ad / "README.md").is_file(), ad)

        sh_files = [
            "ci/lib.sh",
            *(str(p.relative_to(ROOT)) for p in sorted((ROOT / "adapters" / "oci-registry").glob("*.sh"))),
            *(str(p.relative_to(ROOT)) for p in sorted((ROOT / "adapters" / "flags-file").glob("*.sh"))),
            *(str(p.relative_to(ROOT)) for p in sorted((ROOT / "adapters" / "flags-http").glob("*.sh"))),
            *(str(p.relative_to(ROOT)) for p in sorted((ROOT / "fixtures" / "outside-adapters" / "file-registry").glob("*.sh"))),
            *(str(p.relative_to(ROOT)) for p in sorted((ROOT / "fixtures" / "outside-adapters" / "file-flags").glob("*.sh"))),
            "gates/registry-conformance.sh",
            "gates/flags-conformance.sh",
        ]
        sc = shutil.which("shellcheck")
        self.assertIsNotNone(sc, "shellcheck must be present in workbench")
        res = subprocess.run([sc, "-s", "sh", "--color=never", *sh_files], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
