"""End-to-end OIDC/JWT & 4-Tier RBAC integration suite (ADR 0127).

Generates real RSA-2048 (RS256) and NIST P-256 (ES256) keypairs and tokens via
cryptography, serves an ephemeral loopback JWKS HTTP endpoint, and verifies:
- RS256 and ES256 live JWKS verification across Authorization, Cf-Access, and IAP headers
- Live key rotation with TTL caching and 10 s kid-miss refresh cooldown
- Expiry, future nbf/iat, alg=none, and RS256->HS256 key-confusion rejections
- SSRF rejection (non-loopback http://, 169.254.169.254, and HTTP 302 redirect to metadata)
- Full 4-tier RBAC matrix (viewer, developer, operator, launcher) + redacted JSONL audit log
- gates/production-readiness.sh integration with OIDC JWT environment variables
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import os
import pathlib
import subprocess
import tempfile
import threading
import unittest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

from core import oidc_rbac

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _b64u(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_uint(val: int, length: int | None = None) -> str:
    if length is None:
        length = max(1, (val.bit_length() + 7) // 8)
    return _b64u(val.to_bytes(length, "big"))


def _rsa_public_jwk(priv_key: rsa.RSAPrivateKey, kid: str) -> dict[str, str]:
    nums = priv_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64u_uint(nums.n),
        "e": _b64u_uint(nums.e),
    }


def _ec_public_jwk(priv_key: ec.EllipticCurvePrivateKey, kid: str) -> dict[str, str]:
    nums = priv_key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "use": "sig",
        "alg": "ES256",
        "kid": kid,
        "x": _b64u_uint(nums.x, 32),
        "y": _b64u_uint(nums.y, 32),
    }


def _mint_rs256(priv_key: rsa.RSAPrivateKey, kid: str, claims: dict) -> str:
    hdr = {"alg": "RS256", "typ": "JWT", "kid": kid}
    signing_input = f"{_b64u(json.dumps(hdr))}.{_b64u(json.dumps(claims))}".encode("ascii")
    sig = priv_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode('ascii')}.{_b64u(sig)}"


def _mint_es256(priv_key: ec.EllipticCurvePrivateKey, kid: str, claims: dict) -> str:
    hdr = {"alg": "ES256", "typ": "JWT", "kid": kid}
    signing_input = f"{_b64u(json.dumps(hdr))}.{_b64u(json.dumps(claims))}".encode("ascii")
    der_sig = priv_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r_int, s_int = utils.decode_dss_signature(der_sig)
    raw_sig = r_int.to_bytes(32, "big") + s_int.to_bytes(32, "big")
    return f"{signing_input.decode('ascii')}.{_b64u(raw_sig)}"


class _JwksServerState:
    def __init__(self, keys: list[dict[str, str]]) -> None:
        self.lock = threading.Lock()
        self.keys = list(keys)
        self.hits = 0

    def set_keys(self, keys: list[dict[str, str]]) -> None:
        with self.lock:
            self.keys = list(keys)

    def snapshot(self) -> tuple[bytes, int]:
        with self.lock:
            self.hits += 1
            return json.dumps({"keys": self.keys}).encode("utf-8"), self.hits


class OidcRbacEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rsa_k1 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.rsa_k2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.ec_k1 = ec.generate_private_key(ec.SECP256R1())
        cls.ec_k2 = ec.generate_private_key(ec.SECP256R1())
        cls.now = 1_730_000_000.0

        cls.jwks_state = _JwksServerState(
            [
                _rsa_public_jwk(cls.rsa_k1, "rsa-v1"),
                _ec_public_jwk(cls.ec_k1, "ec-v1"),
            ]
        )

        state = cls.jwks_state

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/.well-known/jwks.json":
                    payload, _ = state.snapshot()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if self.path == "/redirect-to-metadata":
                    self.send_response(302)
                    self.send_header("Location", "http://169.254.169.254/latest/meta-data/iam/")
                    self.end_headers()
                    return
                self.send_error(404)

            def log_message(self, *args: object) -> None:
                pass

        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.jwks_url = f"http://127.0.0.1:{cls.port}/.well-known/jwks.json"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        self.jwks_state.set_keys(
            [
                _rsa_public_jwk(self.rsa_k1, "rsa-v1"),
                _ec_public_jwk(self.ec_k1, "ec-v1"),
            ]
        )
        with self.jwks_state.lock:
            self.jwks_state.hits = 0

    def _claims(self, email: str, groups: list[str] | None = None, **extra: object) -> dict:
        c: dict[str, object] = {
            "iss": "https://idp.example.com/oidc",
            "aud": "lights-out-console",
            "sub": f"uid:{email}",
            "email": email,
            "iat": int(self.now) - 5,
            "nbf": int(self.now) - 5,
            "exp": int(self.now) + 300,
        }
        if groups is not None:
            c["groups"] = groups
        c.update(extra)
        return c

    def test_e2e_rs256_es256_rotation_cooldown_and_attacks(self) -> None:
        cache = oidc_rbac.JwksCache(jwks_url=self.jwks_url, ttl_s=300, min_refresh_s=10)
        policy = oidc_rbac.JwtPolicy(
            issuer="https://idp.example.com/oidc",
            audience="lights-out-console",
            leeway_s=30,
        )

        # 1. Verify RS256 and ES256 against live loopback JWKS server (1 HTTP fetch total)
        tok_rs1 = _mint_rs256(self.rsa_k1, "rsa-v1", self._claims("alice@example.com"))
        tok_ec1 = _mint_es256(self.ec_k1, "ec-v1", self._claims("iap-ops@example.com"))
        c1 = oidc_rbac.verify_jwt(tok_rs1, policy, jwks=cache, now=self.now)
        c2 = oidc_rbac.verify_jwt(tok_ec1, policy, jwks=cache, now=self.now + 1)
        self.assertEqual(c1["email"], "alice@example.com")
        self.assertEqual(c2["email"], "iap-ops@example.com")
        self.assertEqual(self.jwks_state.hits, 1)

        # 2. Rotate server JWKS to rsa-v2 & ec-v2
        self.jwks_state.set_keys(
            [
                _rsa_public_jwk(self.rsa_k2, "rsa-v2"),
                _ec_public_jwk(self.ec_k2, "ec-v2"),
            ]
        )
        tok_rs2 = _mint_rs256(self.rsa_k2, "rsa-v2", self._claims("rotated-rsa@example.com"))
        tok_ec2 = _mint_es256(self.ec_k2, "ec-v2", self._claims("rotated-ec@example.com"))

        # Within 10s cooldown (now + 3s), kid miss does NOT hit JWKS server
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "no JWK found"):
            oidc_rbac.verify_jwt(tok_rs2, policy, jwks=cache, now=self.now + 3)
        self.assertEqual(self.jwks_state.hits, 1)

        # Past 10s cooldown (now + 11s), cache refreshes on kid-miss and verifies both rotated keys!
        self.assertEqual(
            oidc_rbac.verify_jwt(tok_rs2, policy, jwks=cache, now=self.now + 11)["email"],
            "rotated-rsa@example.com",
        )
        self.assertEqual(
            oidc_rbac.verify_jwt(tok_ec2, policy, jwks=cache, now=self.now + 12)["email"],
            "rotated-ec@example.com",
        )
        self.assertEqual(self.jwks_state.hits, 2)
        # Old retired rsa-v1 key is no longer accepted after rotation
        with self.assertRaises(oidc_rbac.JwtVerificationError):
            oidc_rbac.verify_jwt(tok_rs1, policy, jwks=cache, now=self.now + 12)

        # 3. Expired token rejected
        tok_expired = _mint_rs256(
            self.rsa_k2,
            "rsa-v2",
            self._claims("expired@example.com", exp=int(self.now) - 100),
        )
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "expired"):
            oidc_rbac.verify_jwt(tok_expired, policy, jwks=cache, now=self.now + 12)

        # 4. alg=none rejected
        none_tok = f"{_b64u(json.dumps({'alg': 'none'}))}.{_b64u(json.dumps(self._claims('x@example.com')))}."
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "alg=none"):
            oidc_rbac.verify_jwt(none_tok, policy, jwks=cache, now=self.now + 12)

        # 5. RS256 -> HS256 key-confusion attack using public PEM bytes rejected
        pub_pem = self.rsa_k2.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        forged_hdr = _b64u(json.dumps({"alg": "HS256", "kid": "rsa-v2"}))
        forged_pay = _b64u(json.dumps(self._claims("attacker@example.com")))
        forged_in = f"{forged_hdr}.{forged_pay}".encode("ascii")
        forged_sig = _b64u(hmac.new(pub_pem, forged_in, hashlib.sha256).digest())
        forged_tok = f"{forged_hdr}.{forged_pay}.{forged_sig}"
        with self.assertRaises(oidc_rbac.JwtVerificationError):
            oidc_rbac.verify_jwt(forged_tok, policy, jwks=cache, now=self.now + 12)

    def test_e2e_ssrf_and_redirect_to_metadata_blocked(self) -> None:
        for unsafe in (
            "http://example.com/.well-known/jwks.json",
            "http://169.254.169.254/latest/meta-data/",
            "https://169.254.169.254/latest/meta-data/",
            "https://metadata.google.internal/computeMetadata/v1/",
        ):
            with self.assertRaises(oidc_rbac.JwksError):
                oidc_rbac.JwksCache(jwks_url=unsafe)

        # Live HTTP 302 redirect from allowed loopback server to 169.254.169.254 is blocked!
        redir_cache = oidc_rbac.JwksCache(jwks_url=f"http://127.0.0.1:{self.port}/redirect-to-metadata")
        with self.assertRaisesRegex(oidc_rbac.JwksError, "unsafe JWKS redirect"):
            redir_cache.get_jwks(now=self.now)

    def test_e2e_four_tier_rbac_and_redacted_audit_log(self) -> None:
        cache = oidc_rbac.JwksCache(jwks_url=self.jwks_url, ttl_s=300)
        with tempfile.TemporaryDirectory() as tmp:
            audit_log = os.path.join(tmp, "console-audit.jsonl")
            env = {
                "CONSOLE_AUTH": "proxy",
                "CONSOLE_VERIFY_JWT": "on",
                "CONSOLE_JWT_HEADER": "X-Goog-IAP-JWT-Assertion",
                "CONSOLE_JWKS_URL": self.jwks_url,
                "CONSOLE_JWT_ISSUER": "https://idp.example.com/oidc",
                "CONSOLE_JWT_AUDIENCE": "lights-out-console",
                "CONSOLE_RBAC": "strict",
                "CONSOLE_VIEWERS": "viewer@example.com",
                "CONSOLE_LAUNCHERS": "launcher@example.com",
                "CONSOLE_ROLE_CLAIM": "groups",
                "CONSOLE_ROLE_MAP": json.dumps(
                    {
                        "team-dev": "developer",
                        "team-ops": "operator",
                    }
                ),
                "CONSOLE_AUDIT_LOG": audit_log,
            }

            tokens = {
                "viewer": _mint_es256(self.ec_k1, "ec-v1", self._claims("viewer@example.com")),
                "developer": _mint_es256(
                    self.ec_k1, "ec-v1", self._claims("dev@example.com", groups=["team-dev"])
                ),
                "operator": _mint_es256(
                    self.ec_k1, "ec-v1", self._claims("ops@example.com", groups=["team-ops"])
                ),
                "launcher": _mint_rs256(self.rsa_k1, "rsa-v1", self._claims("launcher@example.com")),
                "unmapped": _mint_es256(self.ec_k1, "ec-v1", self._claims("nobody@example.com")),
            }

            checks = [
                # (persona, method, route, expected_status)
                ("viewer", "GET", "/v1/logbook.json", 200),
                ("viewer", "GET", "/v1/charts.json", 200),
                ("viewer", "POST", "/v1/spec-check", 200),
                ("viewer", "POST", "/v1/request", 403),
                ("viewer", "POST", "/v1/assign", 403),
                ("viewer", "POST", "/v1/go-for-launch", 403),
                ("developer", "POST", "/v1/request", 200),
                ("developer", "POST", "/v1/intake", 200),
                ("developer", "POST", "/v1/assign", 403),
                ("developer", "POST", "/v1/freeze", 403),
                ("operator", "POST", "/v1/assign", 200),
                ("operator", "POST", "/v1/freeze", 200),
                ("operator", "POST", "/v1/watch-once", 200),
                ("operator", "POST", "/v1/go-for-launch", 403),
                ("operator", "POST", "/v1/rollback", 403),
                ("launcher", "POST", "/v1/go-for-launch", 200),
                ("launcher", "POST", "/v1/rollback", 200),
                ("launcher", "POST", "/v1/freeze", 200),
                ("unmapped", "GET", "/", 403),
            ]
            for persona, method, route, want_status in checks:
                decision = oidc_rbac.evaluate_request(
                    {"X-Goog-IAP-JWT-Assertion": tokens[persona]},
                    method,
                    route,
                    env=env,
                    jwks=cache,
                    now=self.now,
                )
                self.assertEqual(
                    decision.status,
                    want_status,
                    f"{persona} {method} {route}: got {decision}",
                )

            # Missing JWT header -> 401
            missing = oidc_rbac.evaluate_request({}, "GET", "/", env=env, jwks=cache, now=self.now)
            self.assertEqual(missing.status, 401)

            # Verify JSONL audit log recorded every decision and leaked zero raw JWTs
            raw_audit = pathlib.Path(audit_log).read_text(encoding="utf-8")
            lines = [json.loads(ln) for ln in raw_audit.splitlines() if ln.strip()]
            self.assertEqual(len(lines), len(checks) + 1)
            for tok_val in tokens.values():
                self.assertNotIn(tok_val, raw_audit)

    def test_e2e_production_readiness_gate_script(self) -> None:
        # Spin up ephemeral PromQL stub on loopback so full gates/production-readiness.sh passes
        sample = json.dumps(
            {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [0, "1"]}]}}
        ).encode("utf-8")

        class PromStub(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(sample)))
                self.end_headers()
                self.wfile.write(sample)

            def log_message(self, *args: object) -> None:
                pass

        prom_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PromStub)
        prom_thread = threading.Thread(target=prom_srv.serve_forever, daemon=True)
        prom_thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                conf_dir = os.path.join(tmp, "conf")
                os.makedirs(conf_dir)
                env_file = os.path.join(tmp, "clean.env")
                pathlib.Path(env_file).write_text("CONSOLE_JWT_SECRET=replace-me\n", encoding="utf-8")
                ahash = subprocess.run(
                    ["sh", "-c", ". ci/lib.sh && adapter_hash adapters/argo-rollouts"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                pathlib.Path(conf_dir, "delivery-argo-rollouts.env").write_text(
                    f"ADAPTER=argo-rollouts\nHASH={ahash}\nAT=2026-11-24T00:00:00Z\n",
                    encoding="utf-8",
                )
                base_env = {
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": tmp,
                    "READINESS_ENV_FILE": env_file,
                    "CONFORMANCE_DIR": conf_dir,
                    "DELIVERY": "argo-rollouts",
                    "METRICS": "promql",
                    "METRICS_URL": f"http://127.0.0.1:{prom_srv.server_address[1]}",
                    "CONSOLE_AUTH": "proxy",
                    "CONSOLE_IDENTITY_HEADER": "X-Auth-Request-Email",
                    "CONSOLE_LAUNCHERS": "alice@example.com",
                    "CONSOLE_VERIFY_JWT": "on",
                    "CONSOLE_JWKS_URL": self.jwks_url,
                    "CONSOLE_JWT_ISSUER": "https://idp.example.com/oidc",
                    "CONSOLE_JWT_AUDIENCE": "lights-out-console",
                    "CONSOLE_RBAC": "strict",
                }
                ok_run = subprocess.run(
                    ["sh", "gates/production-readiness.sh"],
                    cwd=ROOT,
                    env=base_env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(ok_run.returncode, 0, ok_run.stdout + ok_run.stderr)
                self.assertTrue(ok_run.stdout.startswith("GO: production-readiness — "), ok_run.stdout)

                bad_env = dict(base_env, CONSOLE_JWKS_URL="http://169.254.169.254/latest/meta-data/")
                bad_run = subprocess.run(
                    ["sh", "gates/production-readiness.sh"],
                    cwd=ROOT,
                    env=bad_env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(bad_run.returncode, 1)
                self.assertIn("human gate", bad_run.stdout)
        finally:
            prom_srv.shutdown()
            prom_srv.server_close()
            prom_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
