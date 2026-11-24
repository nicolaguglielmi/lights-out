"""Specification tests for core/oidc_rbac.py and ADR 0127."""

import ast
import base64
import hashlib
import hmac
import json
import os
import pathlib
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

from core import adr_gov, oidc_rbac

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADR_0127 = ROOT / "docs" / "adr" / "0127-cryptographic-oidc-jwt-and-console-rbac.md"
OIDC_MODULE = ROOT / "core" / "oidc_rbac.py"


def b64u(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_int(value, length=None):
    if length is None:
        length = max(1, (value.bit_length() + 7) // 8)
    return b64u(value.to_bytes(length, "big"))


def rsa_jwk_from_priv(priv_key, kid="rsa-1"):
    nums = priv_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": b64u_int(nums.n),
        "e": b64u_int(nums.e),
    }


def ec_jwk_from_priv(priv_key, kid="ec-1"):
    nums = priv_key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "use": "sig",
        "alg": "ES256",
        "kid": kid,
        "x": b64u_int(nums.x, 32),
        "y": b64u_int(nums.y, 32),
    }


def sign_rs256(header, payload, priv_key):
    signing_input = f"{b64u(json.dumps(header))}.{b64u(json.dumps(payload))}".encode("ascii")
    sig = priv_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode('ascii')}.{b64u(sig)}"


def sign_es256(header, payload, priv_key, *, der=False):
    signing_input = f"{b64u(json.dumps(header))}.{b64u(json.dumps(payload))}".encode("ascii")
    der_sig = priv_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    if der:
        return f"{signing_input.decode('ascii')}.{b64u(der_sig)}"
    r_int, s_int = utils.decode_dss_signature(der_sig)
    raw_sig = r_int.to_bytes(32, "big") + s_int.to_bytes(32, "big")
    return f"{signing_input.decode('ascii')}.{b64u(raw_sig)}"


def sign_hs256(header, payload, key_bytes):
    if isinstance(key_bytes, str):
        key_bytes = key_bytes.encode("utf-8")
    signing_input = f"{b64u(json.dumps(header))}.{b64u(json.dumps(payload))}".encode("ascii")
    sig = hmac.new(key_bytes, signing_input, hashlib.sha256).digest()
    return f"{signing_input.decode('ascii')}.{b64u(sig)}"


class AdrAndStdlibBoundaryTest(unittest.TestCase):
    def test_adr_0127_passes_adr_gov_validation(self):
        self.assertTrue(ADR_0127.is_file(), "ADR 0127 file missing")
        text = ADR_0127.read_text(encoding="utf-8")
        self.assertEqual(adr_gov.validate_adr(text), [])
        self.assertIn("## Provenance", text)

    def test_oidc_rbac_module_is_pure_stdlib_and_zero_subprocess(self):
        source = OIDC_MODULE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        stdlib = sys.stdlib_module_names
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])
        for mod in imported:
            self.assertIn(mod, stdlib, f"non-stdlib import {mod!r} in core/oidc_rbac.py")
        self.assertNotIn("subprocess", imported)


class JwtCryptoVerificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rsa_2048 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.rsa_other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.rsa_1024 = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        cls.ec_p256 = ec.generate_private_key(ec.SECP256R1())
        cls.ec_other = ec.generate_private_key(ec.SECP256R1())
        cls.ec_p384 = ec.generate_private_key(ec.SECP384R1())
        cls.now = 1_700_000_000
        cls.policy = oidc_rbac.JwtPolicy(
            issuer="https://idp.example.com",
            audience="lights-out-console",
            leeway_s=60,
        )

    def base_claims(self, **overrides):
        claims = {
            "iss": "https://idp.example.com",
            "aud": "lights-out-console",
            "sub": "user-42",
            "email": "alice@example.com",
            "iat": self.now - 10,
            "nbf": self.now - 10,
            "exp": self.now + 300,
        }
        claims.update(overrides)
        return claims

    def test_rs256_valid_and_tamper_rejected(self):
        jwk = rsa_jwk_from_priv(self.rsa_2048, kid="k-rsa")
        jwks = {"keys": [jwk]}
        jwt_str = sign_rs256({"alg": "RS256", "typ": "JWT", "kid": "k-rsa"}, self.base_claims(), self.rsa_2048)
        claims = oidc_rbac.verify_jwt(jwt_str, self.policy, jwks=jwks, now=self.now)
        self.assertEqual(claims["email"], "alice@example.com")

        # Flip one bit in signature
        h_b64, p_b64, s_b64 = jwt_str.split(".")
        sig_raw = bytearray(base64.urlsafe_b64decode(s_b64 + "==="))
        sig_raw[-1] ^= 0x01
        bad_sig = f"{h_b64}.{p_b64}.{b64u(bytes(sig_raw))}"
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "RS256 signature verification failed"):
            oidc_rbac.verify_jwt(bad_sig, self.policy, jwks=jwks, now=self.now)

        # Modify payload without re-signing
        tampered_payload = b64u(json.dumps(self.base_claims(email="mallory@example.com")))
        with self.assertRaises(oidc_rbac.JwtVerificationError):
            oidc_rbac.verify_jwt(f"{h_b64}.{tampered_payload}.{s_b64}", self.policy, jwks=jwks, now=self.now)

        # Wrong RSA key
        wrong_jwks = {"keys": [rsa_jwk_from_priv(self.rsa_other, kid="k-rsa")]}
        with self.assertRaises(oidc_rbac.JwtVerificationError):
            oidc_rbac.verify_jwt(jwt_str, self.policy, jwks=wrong_jwks, now=self.now)

    def test_rs256_rejects_weak_modulus_under_2048_bits(self):
        weak_jwk = rsa_jwk_from_priv(self.rsa_1024, kid="weak-1024")
        jwt_str = sign_rs256({"alg": "RS256", "kid": "weak-1024"}, self.base_claims(), self.rsa_1024)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "2048 bits"):
            oidc_rbac.verify_jwt(jwt_str, self.policy, jwks={"keys": [weak_jwk]}, now=self.now)

    def test_rs256_rejects_corrupted_pkcs1_v15_block(self):
        # Forge an RSA-2048 block using raw private exponent d with invalid PKCS#1 v1.5 padding (00 02 ...)
        priv_nums = self.rsa_2048.private_numbers()
        pub_nums = priv_nums.public_numbers
        k = 256
        h_b64 = b64u(json.dumps({"alg": "RS256", "kid": "k-rsa"}))
        p_b64 = b64u(json.dumps(self.base_claims()))
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")
        digest = hashlib.sha256(signing_input).digest()
        digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
        bad_em = b"\x00\x02" + (b"\xff" * (k - len(digest_info) - 3)) + b"\x00" + digest_info
        forged_sig_int = pow(int.from_bytes(bad_em, "big"), priv_nums.d, pub_nums.n)
        forged_jwt = f"{h_b64}.{p_b64}.{b64u(forged_sig_int.to_bytes(k, 'big'))}"
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "RS256 signature verification failed"):
            oidc_rbac.verify_jwt(
                forged_jwt,
                self.policy,
                jwks={"keys": [rsa_jwk_from_priv(self.rsa_2048, kid="k-rsa")]},
                now=self.now,
            )

    def test_es256_valid_raw_and_der_and_iap_claims(self):
        jwk = ec_jwk_from_priv(self.ec_p256, kid="iap-key-1")
        iap_policy = oidc_rbac.JwtPolicy(
            issuer="https://cloud.google.com/iap",
            audience="/projects/123/global/backendServices/456",
        )
        iap_claims = {
            "iss": "https://cloud.google.com/iap",
            "aud": "/projects/123/global/backendServices/456",
            "sub": "accounts.google.com:10987654321",
            "email": "sre@example.com",
            "iat": self.now - 5,
            "exp": self.now + 300,
        }
        raw_jwt = sign_es256({"alg": "ES256", "kid": "iap-key-1"}, iap_claims, self.ec_p256, der=False)
        verified = oidc_rbac.verify_jwt(raw_jwt, iap_policy, jwks={"keys": [jwk]}, now=self.now)
        self.assertEqual(verified["email"], "sre@example.com")

        # Canonical DER ECDSA signature normalization & verification
        der_jwt = sign_es256({"alg": "ES256", "kid": "iap-key-1"}, iap_claims, self.ec_p256, der=True)
        verified_der = oidc_rbac.verify_jwt(der_jwt, iap_policy, jwks={"keys": [jwk]}, now=self.now)
        self.assertEqual(verified_der["sub"], "accounts.google.com:10987654321")

        # Verify der_to_raw_p256 helper round-trips
        der_sig_bytes = base64.urlsafe_b64decode(der_jwt.split(".")[2] + "===")
        raw_64 = oidc_rbac.der_to_raw_p256(der_sig_bytes)
        self.assertEqual(len(raw_64), 64)

    def test_es256_rejects_zero_rs_off_curve_and_wrong_crv(self):
        jwk = ec_jwk_from_priv(self.ec_p256, kid="ec-1")
        h_b64 = b64u(json.dumps({"alg": "ES256", "kid": "ec-1"}))
        p_b64 = b64u(json.dumps(self.base_claims()))

        # Psychic signature r=0, s=0 (CVE-2022-21449 class)
        zero_sig = b64u(b"\x00" * 64)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "out of range"):
            oidc_rbac.verify_jwt(f"{h_b64}.{p_b64}.{zero_sig}", self.policy, jwks={"keys": [jwk]}, now=self.now)

        # r=n, s=1 out of range
        n_bytes = oidc_rbac._P256_N.to_bytes(32, "big") + (1).to_bytes(32, "big")
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "out of range"):
            oidc_rbac.verify_jwt(f"{h_b64}.{p_b64}.{b64u(n_bytes)}", self.policy, jwks={"keys": [jwk]}, now=self.now)

        # Off-curve public key point (y perturbed by +1 mod p)
        bad_jwk = dict(jwk)
        nums = self.ec_p256.public_key().public_numbers()
        bad_y = (nums.y + 1) % oidc_rbac._P256_P
        bad_jwk["y"] = b64u_int(bad_y, 32)
        valid_jwt = sign_es256({"alg": "ES256", "kid": "ec-1"}, self.base_claims(), self.ec_p256)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "not on NIST P-256 curve"):
            oidc_rbac.verify_jwt(valid_jwt, self.policy, jwks={"keys": [bad_jwk]}, now=self.now)

        # Wrong curve in JWK (P-384)
        wrong_crv_jwk = dict(jwk, crv="P-384")
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "crv='P-256'"):
            oidc_rbac.verify_jwt(valid_jwt, self.policy, jwks={"keys": [wrong_crv_jwk]}, now=self.now)

    def test_hs256_requires_explicit_secret_and_blocks_key_confusion(self):
        hs_jwt = sign_hs256({"alg": "HS256"}, self.base_claims(), "explicit-symmetric-key")

        # Rejected when policy.jwt_secret is unset, even with an oct JWK
        oct_jwk = {"kty": "oct", "alg": "HS256", "k": b64u("explicit-symmetric-key")}
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "HS256 is disabled"):
            oidc_rbac.verify_jwt(hs_jwt, self.policy, jwks={"keys": [oct_jwk]}, now=self.now)

        # Accepted when policy.jwt_secret is explicitly set and jwks is None
        hs_policy = oidc_rbac.JwtPolicy(
            issuer="https://idp.example.com",
            audience="lights-out-console",
            jwt_secret="explicit-symmetric-key",
        )
        verified = oidc_rbac.verify_jwt(hs_jwt, hs_policy, jwks=None, now=self.now)
        self.assertEqual(verified["sub"], "user-42")

        # Key-confusion attack: sign HS256 using RSA public PEM or JWK n, verify against RSA JWKS
        rsa_jwk = rsa_jwk_from_priv(self.rsa_2048, kid="k-rsa")
        pub_pem = self.rsa_2048.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        confused_jwt = sign_hs256({"alg": "HS256", "kid": "k-rsa"}, self.base_claims(), pub_pem)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "HS256 is disabled"):
            oidc_rbac.verify_jwt(confused_jwt, self.policy, jwks={"keys": [rsa_jwk]}, now=self.now)
        # Even if jwt_secret is ALSO set on policy, asymmetric JWKS never falls back to HS256!
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "key confusion blocked"):
            oidc_rbac.verify_jwt(confused_jwt, hs_policy, jwks={"keys": [rsa_jwk]}, now=self.now)

    def test_hard_rejections_alg_none_oversized_claims_and_mismatch(self):
        rsa_jwks = {"keys": [rsa_jwk_from_priv(self.rsa_2048, kid="k-rsa")]}
        p_b64 = b64u(json.dumps(self.base_claims()))

        # alg=none (all case variants)
        for none_alg in ("none", "NONE", "None"):
            h_b64 = b64u(json.dumps({"alg": none_alg}))
            for tok in (f"{h_b64}.{p_b64}.", f"{h_b64}.{p_b64}.e30"):
                with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "alg=none"):
                    oidc_rbac.verify_jwt(tok, self.policy, jwks=rsa_jwks, now=self.now)

        # Unsupported alg & critical header
        bad_alg = f"{b64u(json.dumps({'alg': 'RS384'}))}.{p_b64}.e30"
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "unsupported JWT alg"):
            oidc_rbac.verify_jwt(bad_alg, self.policy, jwks=rsa_jwks, now=self.now)

        crit_jwt = sign_rs256({"alg": "RS256", "kid": "k-rsa", "crit": ["exp"]}, self.base_claims(), self.rsa_2048)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "crit"):
            oidc_rbac.verify_jwt(crit_jwt, self.policy, jwks=rsa_jwks, now=self.now)

        # Oversized > 16384 bytes
        huge = sign_rs256({"alg": "RS256", "kid": "k-rsa"}, self.base_claims(pad="x" * 17000), self.rsa_2048)
        self.assertGreater(len(huge), 16384)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "exceeds maximum 16384 bytes"):
            oidc_rbac.verify_jwt(huge, self.policy, jwks=rsa_jwks, now=self.now)

        # Missing exp, boolean exp, expired beyond leeway, valid within leeway
        no_exp = {k: v for k, v in self.base_claims().items() if k != "exp"}
        tok_no_exp = sign_rs256({"alg": "RS256", "kid": "k-rsa"}, no_exp, self.rsa_2048)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "missing required 'exp'"):
            oidc_rbac.verify_jwt(tok_no_exp, self.policy, jwks=rsa_jwks, now=self.now)

        bool_exp = sign_rs256({"alg": "RS256", "kid": "k-rsa"}, self.base_claims(exp=True), self.rsa_2048)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "JSON number"):
            oidc_rbac.verify_jwt(bool_exp, self.policy, jwks=rsa_jwks, now=self.now)

        within_skew = sign_rs256({"alg": "RS256", "kid": "k-rsa"}, self.base_claims(exp=self.now - 30), self.rsa_2048)
        self.assertEqual(oidc_rbac.verify_jwt(within_skew, self.policy, jwks=rsa_jwks, now=self.now)["sub"], "user-42")

        expired = sign_rs256({"alg": "RS256", "kid": "k-rsa"}, self.base_claims(exp=self.now - 61), self.rsa_2048)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "expired"):
            oidc_rbac.verify_jwt(expired, self.policy, jwks=rsa_jwks, now=self.now)

        # Future nbf and future iat
        future_nbf = sign_rs256({"alg": "RS256", "kid": "k-rsa"}, self.base_claims(nbf=self.now + 120), self.rsa_2048)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "nbf is in the future"):
            oidc_rbac.verify_jwt(future_nbf, self.policy, jwks=rsa_jwks, now=self.now)

        future_iat = sign_rs256({"alg": "RS256", "kid": "k-rsa"}, self.base_claims(iat=self.now + 120), self.rsa_2048)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "iat.*future"):
            oidc_rbac.verify_jwt(future_iat, self.policy, jwks=rsa_jwks, now=self.now)

        # iss mismatch and aud list vs mismatch
        bad_iss = sign_rs256({"alg": "RS256", "kid": "k-rsa"}, self.base_claims(iss="https://evil.example"), self.rsa_2048)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "'iss' mismatch"):
            oidc_rbac.verify_jwt(bad_iss, self.policy, jwks=rsa_jwks, now=self.now)

        aud_list = sign_rs256(
            {"alg": "RS256", "kid": "k-rsa"},
            self.base_claims(aud=["other-service", "lights-out-console"]),
            self.rsa_2048,
        )
        self.assertEqual(oidc_rbac.verify_jwt(aud_list, self.policy, jwks=rsa_jwks, now=self.now)["sub"], "user-42")

        bad_aud = sign_rs256({"alg": "RS256", "kid": "k-rsa"}, self.base_claims(aud=["other-service"]), self.rsa_2048)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "'aud' does not match"):
            oidc_rbac.verify_jwt(bad_aud, self.policy, jwks=rsa_jwks, now=self.now)

        # RS256 token against EC JWK (algorithm/kty mismatch)
        ec_jwks = {"keys": [ec_jwk_from_priv(self.ec_p256, kid="k-rsa")]}
        rs_tok = sign_rs256({"alg": "RS256", "kid": "k-rsa"}, self.base_claims(), self.rsa_2048)
        with self.assertRaisesRegex(oidc_rbac.JwtVerificationError, "algorithm/kty mismatch"):
            oidc_rbac.verify_jwt(rs_tok, self.policy, jwks=ec_jwks, now=self.now)


class JwksCacheAndSsrfTest(unittest.TestCase):
    def test_ssrf_url_guard(self):
        allowed = (
            "https://idp.example.com/.well-known/jwks.json",
            "https://www.gstatic.com/iap/verify/public_key-jwk",
            "http://127.0.0.1:8080/jwks.json",
            "http://localhost:8080/jwks.json",
            "http://[::1]:8080/jwks.json",
        )
        for url in allowed:
            ok, why = oidc_rbac.validate_jwks_url(url)
            self.assertTrue(ok, f"{url} rejected: {why}")

        blocked = (
            "http://idp.example.com/jwks.json",
            "http://10.0.0.1/jwks.json",
            "http://192.168.1.10/jwks.json",
            "http://127.0.0.2:8080/jwks.json",
            "http://0.0.0.0:8080/jwks.json",
            "http://169.254.169.254/latest/meta-data/",
            "https://169.254.169.254/latest/meta-data/",
            "https://[::ffff:169.254.169.254]/jwks.json",
            "https://[fe80::1]/jwks.json",
            "https://[fd00:ec2::254]/latest/meta-data/",
            "https://100.100.100.200/latest/meta-data/",
            "https://metadata.google.internal/computeMetadata/v1/",
            "https://user:secret@idp.example.com/jwks.json",
            "file:///etc/passwd",
            "ftp://idp.example.com/jwks.json",
        )
        for url in blocked:
            ok, _ = oidc_rbac.validate_jwks_url(url)
            self.assertFalse(ok, f"unsafe URL {url} was accepted")
            with self.assertRaises(oidc_rbac.JwksError):
                oidc_rbac.JwksCache(jwks_url=url)

    def test_jwks_cache_file_ttl_and_10s_kid_miss_cooldown(self):
        k1 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        k2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        doc_v1 = {"keys": [rsa_jwk_from_priv(k1, kid="kid-1")]}
        doc_v2 = {"keys": [rsa_jwk_from_priv(k1, kid="kid-1"), rsa_jwk_from_priv(k2, kid="kid-2")]}

        with tempfile.TemporaryDirectory() as tmp:
            jwks_path = os.path.join(tmp, "jwks.json")
            with open(jwks_path, "w", encoding="utf-8") as fh:
                json.dump(doc_v1, fh)

            # Trying to set min_refresh_s=0.1 still clamps to 10.0s floor
            cache = oidc_rbac.JwksCache(jwks_file=jwks_path, ttl_s=300, min_refresh_s=0.1)
            self.assertEqual(cache.min_refresh_s, 10.0)

            t0 = 1_000_000.0
            got1 = cache.get_jwks(kid="kid-1", now=t0)
            self.assertEqual(len(got1["keys"]), 1)
            self.assertEqual(cache.fetch_count, 1)

            # Rotate file on disk to doc_v2
            with open(jwks_path, "w", encoding="utf-8") as fh:
                json.dump(doc_v2, fh)

            # Unknown kid within <10s cooldown does NOT re-read
            got_early = cache.get_jwks(kid="kid-2", now=t0 + 4.0)
            self.assertEqual(cache.fetch_count, 1)
            self.assertEqual(len(got_early["keys"]), 1)

            # At t0 + 10.5s (>= 10s cooldown, still < 300s TTL), kid-miss refreshes!
            got_rotated = cache.get_jwks(kid="kid-2", now=t0 + 10.5)
            self.assertEqual(cache.fetch_count, 2)
            self.assertEqual({k["kid"] for k in got_rotated["keys"]}, {"kid-1", "kid-2"})

            # Known kid within TTL makes zero extra fetches
            cache.get_jwks(kid="kid-2", now=t0 + 100.0)
            self.assertEqual(cache.fetch_count, 2)

            # After 300s TTL expires, fetches again
            cache.get_jwks(kid="kid-2", now=t0 + 315.0)
            self.assertEqual(cache.fetch_count, 3)


class RbacExtractionRoutesAndAuditTest(unittest.TestCase):
    def test_extract_jwt_headers(self):
        tok, err = oidc_rbac.extract_jwt({"authorization": "Bearer eyJ.a.b"}, "Authorization")
        self.assertEqual((tok, err), ("eyJ.a.b", None))

        tok, err = oidc_rbac.extract_jwt({"Authorization": "Basic dXNlcjpwYXNz"}, "Authorization")
        self.assertIsNone(tok)
        self.assertIn("Bearer", err)

        tok, err = oidc_rbac.extract_jwt({}, "Cf-Access-Jwt-Assertion")
        self.assertIsNone(tok)
        self.assertIn("missing Cf-Access-Jwt-Assertion", err)

        tok, err = oidc_rbac.extract_jwt({"cf-access-jwt-assertion": "eyJ.cf.sig"}, "Cf-Access-Jwt-Assertion")
        self.assertEqual((tok, err), ("eyJ.cf.sig", None))

        tok, err = oidc_rbac.extract_jwt({"X-Goog-IAP-JWT-Assertion": "eyJ.iap.sig"}, "X-Goog-IAP-JWT-Assertion")
        self.assertEqual((tok, err), ("eyJ.iap.sig", None))

    def test_authorize_all_17_routes_across_4_tiers(self):
        viewer_routes = [
            ("GET", "/"),
            ("GET", "/index.html"),
            ("GET", "/v1/logbook.json"),
            ("GET", "/v1/console.json"),
            ("GET", "/v1/events?once=1"),
            ("GET", "/v1/charts.json"),
            ("POST", "/v1/spec-check"),
        ]
        dev_routes = [
            ("POST", "/v1/answer"),
            ("POST", "/v1/request"),
            ("POST", "/v1/intake"),
            ("POST", "/v1/intake-confirm"),
        ]
        op_routes = [
            ("POST", "/v1/assign"),
            ("POST", "/v1/resume"),
            ("POST", "/v1/watch-once"),
            ("POST", "/v1/freeze"),
        ]
        launcher_routes = [
            ("POST", "/v1/go-for-launch"),
            ("POST", "/v1/rollback"),
        ]
        self.assertEqual(len(viewer_routes) + len(dev_routes) + len(op_routes) + len(launcher_routes), 17)

        matrix = {
            None: set(),
            "viewer": set(viewer_routes),
            "developer": set(viewer_routes + dev_routes),
            "operator": set(viewer_routes + dev_routes + op_routes),
            "launcher": set(viewer_routes + dev_routes + op_routes + launcher_routes),
        }
        all_routes = viewer_routes + dev_routes + op_routes + launcher_routes
        for role, expected_allowed in matrix.items():
            for method, route in all_routes:
                ok, reason = oidc_rbac.authorize(role, method, route)
                self.assertIsInstance(reason, str)
                self.assertEqual(
                    ok,
                    (method, route) in expected_allowed,
                    f"role={role} {method} {route}: got {ok} ({reason})",
                )

        # Unknown route fails closed even for launcher
        ok, reason = oidc_rbac.authorize("launcher", "POST", "/v1/unknown-admin")
        self.assertFalse(ok)
        self.assertIn("not in the governed", reason)

    def test_resolve_role_hierarchy_claims_legacy_and_strict(self):
        # Legacy mode with only CONSOLE_LAUNCHERS: launchers -> launcher, others -> developer
        legacy_env = {"CONSOLE_LAUNCHERS": "alice@example.com"}
        self.assertEqual(oidc_rbac.resolve_role("alice@example.com", None, legacy_env), "launcher")
        self.assertEqual(oidc_rbac.resolve_role("bob@example.com", None, legacy_env), "developer")

        # Strict mode: explicit allowlists + JWT claims + CONSOLE_ROLE_MAP -> highest tier wins
        strict_env = {
            "CONSOLE_RBAC": "strict",
            "CONSOLE_VIEWERS": "v@example.com,multi@example.com",
            "CONSOLE_DEVELOPERS": "d@example.com",
            "CONSOLE_OPERATORS": "o@example.com",
            "CONSOLE_LAUNCHERS": "l@example.com",
            "CONSOLE_ROLE_CLAIM": "realm_access.roles",
            "CONSOLE_ROLE_MAP": json.dumps(
                {
                    "audit-ro": "viewer",
                    "eng-team": "developer",
                    "sre-oncall": "operator",
                    "release-captains": "launcher",
                }
            ),
        }
        self.assertEqual(oidc_rbac.resolve_role("v@example.com", {}, strict_env), "viewer")
        self.assertEqual(oidc_rbac.resolve_role("d@example.com", {}, strict_env), "developer")
        self.assertEqual(oidc_rbac.resolve_role("o@example.com", {}, strict_env), "operator")
        self.assertEqual(oidc_rbac.resolve_role("l@example.com", {}, strict_env), "launcher")
        # multi@example.com is in CONSOLE_VIEWERS, plus has sre-oncall in JWT -> promoted to operator!
        self.assertEqual(
            oidc_rbac.resolve_role(
                "multi@example.com",
                {"realm_access": {"roles": ["audit-ro", "sre-oncall"]}},
                strict_env,
            ),
            "operator",
        )
        # Unknown identity under CONSOLE_RBAC=strict -> None
        self.assertIsNone(oidc_rbac.resolve_role("stranger@example.com", {"realm_access": {"roles": []}}, strict_env))
        self.assertIsNone(oidc_rbac.resolve_role("", {}, strict_env))

    def test_append_audit_event_redacts_jwts_and_secrets(self):
        fake_jwt = f"{b64u('{\"alg\":\"RS256\"}')}.{b64u('{\"sub\":\"alice\"}')}.{b64u('signature')}"
        with tempfile.TemporaryDirectory() as tmp:
            audit_file = os.path.join(tmp, "sub", "audit.jsonl")
            entry = oidc_rbac.append_audit_event(
                audit_file,
                event="jwt_verify_failed",
                decision="deny",
                identity="alice@example.com",
                role=None,
                method="POST",
                route="/v1/go-for-launch",
                status=401,
                reason=f"bad signature on {fake_jwt} with Bearer secret-bearer-val and CLIENT_SECRET=topsecret",
                extra={
                    "Authorization": f"Bearer {fake_jwt}",
                    "jwt": fake_jwt,
                    "upstream": "https://admin:hunter2@idp.example.com/jwks",
                },
                now=1_700_000_000,
            )
            self.assertEqual(entry["Authorization"], "[REDACTED]")
            self.assertEqual(entry["jwt"], "[REDACTED]")
            raw_disk = pathlib.Path(audit_file).read_text(encoding="utf-8")
            for forbidden in (fake_jwt, "secret-bearer-val", "topsecret", "hunter2"):
                self.assertNotIn(forbidden, raw_disk)
            parsed = json.loads(raw_disk.strip())
            self.assertEqual(parsed["status"], 401)
            self.assertIn("[REDACTED_JWT]", parsed["reason"])


if __name__ == "__main__":
    unittest.main()
