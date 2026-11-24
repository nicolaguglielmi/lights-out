# SPDX-License-Identifier: Apache-2.0
"""Cryptographic OIDC/JWT verification and 4-tier Console RBAC (docs/adr/0127).

100% Python standard library, zero external packages, zero subprocesses:
- RFC 7515 / 7517 / 7518 / 7519 JWT verification for RS256, ES256 (NIST P-256),
  and explicit symmetric HS256 with structural key-confusion immunity.
- JwksCache loading CONSOLE_JWKS_FILE or fetching CONSOLE_JWKS_URL with strict
  SSRF validation, TTL caching, and 10 s kid-miss refresh cooldown.
- Token extraction from CONSOLE_JWT_HEADER (Authorization: Bearer <jwt>,
  Cf-Access-Jwt-Assertion, X-Goog-IAP-JWT-Assertion, etc.).
- 4-tier Console RBAC (viewer, developer, operator, launcher) across all 17
  Console HTTP routes, resolving roles from CONSOLE_VIEWERS, CONSOLE_DEVELOPERS,
  CONSOLE_OPERATORS, CONSOLE_LAUNCHERS, and verified JWT claims
  (CONSOLE_ROLE_CLAIM + optional CONSOLE_ROLE_MAP JSON).
- Append-only JSONL security audit trail redacting raw JWTs and header secrets.
"""

import base64
import collections
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROLES = ("viewer", "developer", "operator", "launcher")
ROLE_RANK = {name: idx for idx, name in enumerate(ROLES)}
SUPPORTED_ALGS = ("RS256", "ES256", "HS256")

# Provenance (ADR 0127): matches console/serve.py DRAFT_MAX (16 KiB).
MAX_TOKEN_BYTES = 16384
# Provenance (ADR 0127): NIST SP 800-57 / RFC 7518 §3.3 minimum RSA modulus.
MIN_RSA_BITS = 2048
MIN_RSA_BYTES = 256
# Provenance (ADR 0127): FIPS 186-4 / RFC 7518 §3.4 & §6.2.1 P-256 widths.
P256_COORD_BYTES = 32
ES256_RAW_SIG_BYTES = 64
# Provenance (ADR 0127): RFC 7519 §4.1.4 clock-skew leeway default & ceiling.
DEFAULT_LEEWAY_S = 60.0
MAX_LEEWAY_S = 300.0
# Provenance (ADR 0127): JWKS cache TTL, kid-miss refresh floor, and fetch bounds.
DEFAULT_JWKS_TTL_S = 300.0
MIN_JWKS_REFRESH_COOLDOWN_S = 10.0
JWKS_FETCH_TIMEOUT_S = 5.0
MAX_JWKS_BYTES = 262144

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
DEFAULT_AUDIT_LOG = ".crew/console-audit.jsonl"

# RFC 8017 §9.2 Notes: exact DER DigestInfo prefix for SHA-256 (19 bytes + 32-byte hash = 51 bytes).
_ASN1_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

# FIPS 186-4 / SEC 2 §2.4.2 NIST P-256 (secp256r1 / prime256v1) domain parameters.
_P256_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_P256_A = _P256_P - 3
_P256_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_P256_GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
_P256_GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
_P256_G = (_P256_GX, _P256_GY)

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_JWT_COMPACT_RE = re.compile(r"\bey[A-Za-z0-9_-]{4,}\.ey[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*")
_AUTH_SCHEME_RE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_KV_SECRET_RE = re.compile(
    r"(\b[A-Za-z0-9_]*(?:_KEY|_TOKEN|_SECRET|_PASSWORD)=)([^\s,;\"']+)",
    re.IGNORECASE,
)
_URL_USERINFO_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/\s:@]+:[^/\s@]+)@")
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "token",
        "jwt",
        "id_token",
        "access_token",
        "refresh_token",
        "raw_token",
        "secret",
        "jwt_secret",
        "client_secret",
        "cookie_secret",
        "password",
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "cf-access-jwt-assertion",
        "x-goog-iap-jwt-assertion",
        "x-auth-request-access-token",
    }
)
_SENSITIVE_KEY_SUFFIXES = ("_key", "_token", "_secret", "_password", "_assertion")

_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata",
        "instance-data",
        "169.254.169.254",
        "100.100.100.200",
    }
)
_AWS_IPV6_IMDS = ipaddress.ip_network("fd00:ec2::/32")
_ALIBABA_IMDS_IP = ipaddress.ip_address("100.100.100.200")

# 17 governed Console routes mapped to minimum required RBAC tier (ADR 0127 §3).
ROUTE_MIN_ROLE = {
    ("GET", "/"): "viewer",
    ("GET", "/index.html"): "viewer",
    ("GET", "/v1/logbook.json"): "viewer",
    ("GET", "/v1/console.json"): "viewer",
    ("GET", "/v1/events"): "viewer",
    ("GET", "/v1/charts.json"): "viewer",
    ("POST", "/v1/spec-check"): "viewer",
    ("POST", "/v1/answer"): "developer",
    ("POST", "/v1/request"): "developer",
    ("POST", "/v1/intake"): "developer",
    ("POST", "/v1/intake-confirm"): "developer",
    ("POST", "/v1/assign"): "operator",
    ("POST", "/v1/resume"): "operator",
    ("POST", "/v1/watch-once"): "operator",
    ("POST", "/v1/freeze"): "operator",
    ("POST", "/v1/go-for-launch"): "launcher",
    ("POST", "/v1/rollback"): "launcher",
}

AuthDecision = collections.namedtuple(
    "AuthDecision",
    "ok status identity role claims reason",
)

_AUDIT_LOCK = threading.Lock()


class OidcRbacError(ValueError):
    """Base error for OIDC/JWT verification, JWKS loading, and RBAC policy."""


class JwtVerificationError(OidcRbacError):
    """Raised when a JWT fails framing, cryptographic signature, or claim checks."""


class JwksError(OidcRbacError):
    """Raised when a JWKS URL/file is unsafe, unreachable, or malformed."""


class RbacConfigError(OidcRbacError):
    """Raised when JWT policy or RBAC environment configuration is invalid."""


def _b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment, *, strict_unpadded=True, label="segment"):
    if not isinstance(segment, str) or not segment:
        raise JwtVerificationError(f"empty Base64URL {label}")
    working = segment
    if not strict_unpadded:
        working = working.rstrip("=")
        if not working:
            raise JwtVerificationError(f"empty Base64URL {label}")
    elif "=" in segment:
        raise JwtVerificationError(f"Base64URL {label} must not contain '=' padding")
    if len(working) % 4 == 1 or not _B64URL_RE.match(working):
        raise JwtVerificationError(f"malformed Base64URL characters or length in {label}")
    padded = working + ("=" * ((4 - len(working) % 4) % 4))
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    if strict_unpadded and _b64url_encode(raw) != working:
        raise JwtVerificationError(f"non-canonical Base64URL trailing bits in {label}")
    return raw


def _parse_audiences(value):
    if value is None:
        return ()
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",") if part.strip()]
        return tuple(items)
    if isinstance(value, (list, tuple, set, frozenset)):
        out = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise RbacConfigError("audience entries must be non-empty strings")
            out.append(item.strip())
        return tuple(out)
    raise RbacConfigError("audience must be a string or sequence of strings")


class JwtPolicy:
    """Verification policy for RFC 7519 claims and allowed algorithms."""

    def __init__(
        self,
        *,
        issuer=None,
        audience=None,
        leeway_s=DEFAULT_LEEWAY_S,
        jwt_secret=None,
        allowed_algs=SUPPORTED_ALGS,
        max_token_bytes=MAX_TOKEN_BYTES,
    ):
        self.issuer = issuer.strip() if isinstance(issuer, str) and issuer.strip() else None
        if issuer is not None and not isinstance(issuer, str):
            raise RbacConfigError("issuer must be a string or None")
        self.audiences = _parse_audiences(audience)
        self.audience = self.audiences[0] if len(self.audiences) == 1 else (self.audiences or None)
        if isinstance(leeway_s, bool) or not isinstance(leeway_s, (int, float)):
            raise RbacConfigError("leeway_s must be a finite non-negative number")
        leeway_val = float(leeway_s)
        if math.isnan(leeway_val) or math.isinf(leeway_val) or leeway_val < 0.0 or leeway_val > MAX_LEEWAY_S:
            raise RbacConfigError(f"leeway_s must be between 0 and {int(MAX_LEEWAY_S)} seconds")
        self.leeway_s = leeway_val
        if jwt_secret is not None and not isinstance(jwt_secret, str):
            raise RbacConfigError("jwt_secret must be a string or None")
        if jwt_secret == "replace-me":
            raise RbacConfigError("CONSOLE_JWT_SECRET uses placeholder value 'replace-me'")
        self.jwt_secret = jwt_secret if jwt_secret else None
        if isinstance(allowed_algs, str):
            algs = tuple(a.strip() for a in allowed_algs.split(",") if a.strip())
        else:
            algs = tuple(allowed_algs or ())
        if not algs:
            raise RbacConfigError("allowed_algs must not be empty")
        for alg in algs:
            if alg not in SUPPORTED_ALGS:
                raise RbacConfigError(f"unsupported algorithm in allowed_algs: {alg!r}")
        self.allowed_algs = algs
        self.max_token_bytes = int(max_token_bytes)
        if self.max_token_bytes <= 0 or self.max_token_bytes > MAX_TOKEN_BYTES:
            raise RbacConfigError(f"max_token_bytes must be in 1..{MAX_TOKEN_BYTES}")

    @classmethod
    def from_env(cls, env=None):
        e = os.environ if env is None else env
        raw_leeway = (e.get("CONSOLE_JWT_LEEWAY_S") or "").strip()
        try:
            leeway_s = float(raw_leeway) if raw_leeway else DEFAULT_LEEWAY_S
        except ValueError as exc:
            raise RbacConfigError(f"invalid CONSOLE_JWT_LEEWAY_S={raw_leeway!r}") from exc
        raw_algs = (e.get("CONSOLE_JWT_ALGS") or "").strip()
        algs = tuple(a.strip() for a in raw_algs.split(",") if a.strip()) if raw_algs else SUPPORTED_ALGS
        raw_secret = e.get("CONSOLE_JWT_SECRET") or ""
        return cls(
            issuer=e.get("CONSOLE_JWT_ISSUER") or None,
            audience=e.get("CONSOLE_JWT_AUDIENCE") or None,
            leeway_s=leeway_s,
            jwt_secret=raw_secret if raw_secret else None,
            allowed_algs=algs,
        )


def _coerce_policy(policy):
    if policy is None:
        return JwtPolicy()
    if isinstance(policy, JwtPolicy):
        return policy
    if isinstance(policy, dict):
        if any(k.startswith("CONSOLE_JWT_") for k in policy):
            return JwtPolicy.from_env(policy)
        return JwtPolicy(
            issuer=policy.get("issuer"),
            audience=policy.get("audience"),
            leeway_s=policy.get("leeway_s", DEFAULT_LEEWAY_S),
            jwt_secret=policy.get("jwt_secret"),
            allowed_algs=policy.get("allowed_algs", SUPPORTED_ALGS),
            max_token_bytes=policy.get("max_token_bytes", MAX_TOKEN_BYTES),
        )
    raise RbacConfigError("policy must be a JwtPolicy or mapping")


# --- NIST P-256 (secp256r1) pure-Python curve arithmetic ---


def _p256_on_curve(point):
    if point is None:
        return False
    x, y = point
    if x < 0 or x >= _P256_P or y < 0 or y >= _P256_P:
        return False
    lhs = (y * y) % _P256_P
    rhs = (pow(x, 3, _P256_P) + _P256_A * x + _P256_B) % _P256_P
    return lhs == rhs


def _p256_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % _P256_P == 0:
            return None
        slope = ((3 * x1 * x1 + _P256_A) * pow(2 * y1, -1, _P256_P)) % _P256_P
    else:
        slope = ((y2 - y1) * pow(x2 - x1, -1, _P256_P)) % _P256_P
    x3 = (slope * slope - x1 - x2) % _P256_P
    y3 = (slope * (x1 - x3) - y1) % _P256_P
    return (x3, y3)


def _p256_mul(scalar, point):
    if scalar % _P256_N == 0 or point is None:
        return None
    k = scalar % _P256_N
    result = None
    addend = point
    while k > 0:
        if k & 1:
            result = _p256_add(result, addend)
        addend = _p256_add(addend, addend)
        k >>= 1
    return result


def _parse_der_positive_int(buf, offset):
    if offset + 2 > len(buf) or buf[offset] != 0x02:
        raise JwtVerificationError("invalid DER ECDSA INTEGER tag")
    length = buf[offset + 1]
    if length < 1 or length > 33 or offset + 2 + length > len(buf):
        raise JwtVerificationError("invalid DER ECDSA INTEGER length")
    body = buf[offset + 2 : offset + 2 + length]
    if body[0] & 0x80:
        raise JwtVerificationError("negative DER ECDSA INTEGER is invalid")
    if length > 1 and body[0] == 0x00 and (body[1] & 0x80) == 0:
        raise JwtVerificationError("non-minimal leading zero in DER ECDSA INTEGER")
    return int.from_bytes(body, "big"), offset + 2 + length


def _parse_ecdsa_der(sig_bytes):
    if len(sig_bytes) < 8 or len(sig_bytes) > 72 or sig_bytes[0] != 0x30:
        raise JwtVerificationError("ECDSA signature must be 64-byte raw r||s or canonical DER")
    seq_len = sig_bytes[1]
    if seq_len + 2 != len(sig_bytes):
        raise JwtVerificationError("DER ECDSA SEQUENCE length mismatch")
    r_int, next_off = _parse_der_positive_int(sig_bytes, 2)
    s_int, end_off = _parse_der_positive_int(sig_bytes, next_off)
    if end_off != len(sig_bytes):
        raise JwtVerificationError("trailing bytes in DER ECDSA signature")
    return r_int, s_int


def der_to_raw_p256(der_bytes):
    """Normalize a canonical ASN.1 DER P-256 signature to 64-byte JWS r||s."""
    r_int, s_int = _parse_ecdsa_der(der_bytes)
    if not (1 <= r_int < _P256_N and 1 <= s_int < _P256_N):
        raise JwtVerificationError("ECDSA r or s out of range [1, n-1]")
    return r_int.to_bytes(P256_COORD_BYTES, "big") + s_int.to_bytes(P256_COORD_BYTES, "big")


# --- JWS signature verifiers (RS256, ES256, HS256) ---


def _check_jwk_meta(jwk, expected_alg, expected_kty):
    if not isinstance(jwk, dict):
        raise JwtVerificationError("JWK entry must be a JSON object")
    kty = jwk.get("kty")
    if kty != expected_kty:
        raise JwtVerificationError(
            f"algorithm/kty mismatch: alg {expected_alg} requires kty={expected_kty!r}, got {kty!r}"
        )
    use = jwk.get("use")
    if use is not None and use != "sig":
        raise JwtVerificationError(f"JWK 'use' must be 'sig', got {use!r}")
    key_ops = jwk.get("key_ops")
    if key_ops is not None:
        if not isinstance(key_ops, list) or "verify" not in key_ops:
            raise JwtVerificationError("JWK 'key_ops' does not permit signature verification")
    jwk_alg = jwk.get("alg")
    if jwk_alg is not None and jwk_alg != expected_alg:
        raise JwtVerificationError(f"JWK 'alg' {jwk_alg!r} does not match token alg {expected_alg!r}")


def _verify_rs256(signing_input, sig_bytes, jwk):
    _check_jwk_meta(jwk, "RS256", "RSA")
    n_b64 = jwk.get("n")
    e_b64 = jwk.get("e")
    if not isinstance(n_b64, str) or not isinstance(e_b64, str):
        raise JwtVerificationError("RSA JWK missing string 'n' or 'e' parameter")
    n_bytes = _b64url_decode(n_b64, strict_unpadded=False, label="RSA modulus n")
    e_bytes = _b64url_decode(e_b64, strict_unpadded=False, label="RSA exponent e")
    if len(n_bytes) > 1 and n_bytes[0] == 0:
        raise JwtVerificationError("RSA modulus 'n' has non-minimal leading zero")
    if len(e_bytes) > 1 and e_bytes[0] == 0:
        raise JwtVerificationError("RSA exponent 'e' has non-minimal leading zero")
    n_int = int.from_bytes(n_bytes, "big")
    e_int = int.from_bytes(e_bytes, "big")
    if n_int.bit_length() < MIN_RSA_BITS:
        raise JwtVerificationError(
            f"RSA modulus is {n_int.bit_length()} bits; minimum is {MIN_RSA_BITS} bits ({MIN_RSA_BYTES} bytes)"
        )
    if n_int % 2 == 0:
        raise JwtVerificationError("RSA modulus 'n' must be odd")
    if e_int < 3 or e_int % 2 == 0 or e_int >= n_int:
        raise JwtVerificationError("RSA public exponent 'e' must be an odd integer in [3, n-1]")
    k = (n_int.bit_length() + 7) // 8
    if k < MIN_RSA_BYTES:
        raise JwtVerificationError(f"RSA modulus length {k} bytes is below {MIN_RSA_BYTES} bytes")
    if len(sig_bytes) != k:
        raise JwtVerificationError(f"RS256 signature length {len(sig_bytes)} does not match modulus {k} bytes")
    sig_int = int.from_bytes(sig_bytes, "big")
    if sig_int <= 0 or sig_int >= n_int:
        raise JwtVerificationError("RS256 signature representative out of range [1, n-1]")
    em_int = pow(sig_int, e_int, n_int)
    em = em_int.to_bytes(k, "big")
    digest = hashlib.sha256(signing_input).digest()
    digest_info = _ASN1_SHA256_PREFIX + digest
    ps_len = k - len(digest_info) - 3
    if ps_len < 8:
        raise JwtVerificationError("RSA modulus too short for EMSA-PKCS1-v1_5 padding")
    expected_em = b"\x00\x01" + (b"\xff" * ps_len) + b"\x00" + digest_info
    if not hmac.compare_digest(em, expected_em):
        raise JwtVerificationError("RS256 signature verification failed")


def _verify_es256_raw_rs(signing_input, r_int, s_int, pub_point):
    if not (1 <= r_int < _P256_N and 1 <= s_int < _P256_N):
        raise JwtVerificationError("ES256 signature r or s out of range [1, n-1]")
    z = int.from_bytes(hashlib.sha256(signing_input).digest(), "big")
    w = pow(s_int, -1, _P256_N)
    u1 = (z * w) % _P256_N
    u2 = (r_int * w) % _P256_N
    r_point = _p256_add(_p256_mul(u1, _P256_G), _p256_mul(u2, pub_point))
    if r_point is None:
        raise JwtVerificationError("ES256 verification produced point at infinity")
    if (r_point[0] % _P256_N) != r_int:
        raise JwtVerificationError("ES256 signature verification failed")


def _verify_es256(signing_input, sig_bytes, jwk):
    _check_jwk_meta(jwk, "ES256", "EC")
    crv = jwk.get("crv")
    if crv != "P-256":
        raise JwtVerificationError(f"algorithm/crv mismatch: ES256 requires crv='P-256', got {crv!r}")
    x_b64 = jwk.get("x")
    y_b64 = jwk.get("y")
    if not isinstance(x_b64, str) or not isinstance(y_b64, str):
        raise JwtVerificationError("EC JWK missing string 'x' or 'y' coordinate")
    x_bytes = _b64url_decode(x_b64, strict_unpadded=False, label="EC x coordinate")
    y_bytes = _b64url_decode(y_b64, strict_unpadded=False, label="EC y coordinate")
    if len(x_bytes) != P256_COORD_BYTES or len(y_bytes) != P256_COORD_BYTES:
        raise JwtVerificationError(f"P-256 coordinates must be {P256_COORD_BYTES} bytes each")
    pub_point = (int.from_bytes(x_bytes, "big"), int.from_bytes(y_bytes, "big"))
    if not _p256_on_curve(pub_point):
        raise JwtVerificationError("EC public key point is not on NIST P-256 curve")
    if len(sig_bytes) == ES256_RAW_SIG_BYTES:
        r_int = int.from_bytes(sig_bytes[:P256_COORD_BYTES], "big")
        s_int = int.from_bytes(sig_bytes[P256_COORD_BYTES:], "big")
        try:
            _verify_es256_raw_rs(signing_input, r_int, s_int, pub_point)
            return
        except JwtVerificationError:
            if sig_bytes[0] != 0x30:
                raise
            r_der, s_der = _parse_ecdsa_der(sig_bytes)
            _verify_es256_raw_rs(signing_input, r_der, s_der, pub_point)
            return
    r_int, s_int = _parse_ecdsa_der(sig_bytes)
    _verify_es256_raw_rs(signing_input, r_int, s_int, pub_point)


def _verify_hs256(signing_input, sig_bytes, jwk):
    _check_jwk_meta(jwk, "HS256", "oct")
    k_b64 = jwk.get("k")
    if not isinstance(k_b64, str) or not k_b64:
        raise JwtVerificationError("symmetric JWK (kty='oct') missing 'k' material")
    key_bytes = _b64url_decode(k_b64, strict_unpadded=False, label="oct key k")
    if not key_bytes:
        raise JwtVerificationError("symmetric JWK key 'k' is empty")
    expected = hmac.new(key_bytes, signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(sig_bytes, expected):
        raise JwtVerificationError("HS256 signature verification failed")


# --- SSRF-safe JWKS URL validation and JwksCache ---


def _is_forbidden_ip(ip_obj):
    mapped = getattr(ip_obj, "ipv4_mapped", None)
    target = mapped if mapped is not None else ip_obj
    if (
        ip_obj.is_link_local
        or target.is_link_local
        or ip_obj.is_unspecified
        or target.is_unspecified
        or ip_obj.is_multicast
        or target.is_multicast
    ):
        return True
    if isinstance(target, ipaddress.IPv4Address) and target == _ALIBABA_IMDS_IP:
        return True
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj in _AWS_IPV6_IMDS:
        return True
    return False


def validate_jwks_url(url):
    """Return (ok: bool, reason: str | None) enforcing SSRF-safe JWKS URL rules."""
    if not isinstance(url, str) or not url.strip():
        return False, "JWKS URL is empty"
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url):
        return False, "JWKS URL contains whitespace or control characters"
    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("https", "http"):
        return False, f"JWKS URL scheme {scheme or '<empty>'} is not allowed; use https:// or loopback http://"
    if parsed.username is not None or parsed.password is not None or "@" in (parsed.netloc or ""):
        return False, "JWKS URL must not embed userinfo credentials"
    try:
        port = parsed.port
    except ValueError:
        return False, "JWKS URL has an invalid port"
    if port is not None and not (1 <= port <= 65535):
        return False, "JWKS URL port out of range"
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return False, "JWKS URL has no hostname"
    if hostname in _METADATA_HOSTS or hostname.endswith(".metadata.google.internal"):
        return False, f"cloud metadata host {hostname} is forbidden"
    try:
        ip_obj = ipaddress.ip_address(hostname)
    except ValueError:
        ip_obj = None
    if ip_obj is not None and _is_forbidden_ip(ip_obj):
        return False, f"JWKS IP {hostname} is link-local, metadata, unspecified, or multicast"
    if scheme == "http" and hostname not in LOOPBACK_HOSTS:
        return False, "http:// JWKS URL is allowed only on loopback (127.0.0.1, localhost, [::1])"
    return True, None


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, why = validate_jwks_url(newurl)
        if not ok:
            raise urllib.error.URLError(f"blocked unsafe JWKS redirect: {why}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_fetch_jwks_bytes(url, timeout_s):
    ok, why = validate_jwks_url(url)
    if not ok:
        raise JwksError(f"unsafe JWKS URL: {why}")
    opener = urllib.request.build_opener(
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        _SafeRedirectHandler(),
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with opener.open(req, timeout=timeout_s) as resp:  # nosec B310 - scheme and host validated by validate_jwks_url
            data = resp.read(MAX_JWKS_BYTES + 1)
    except JwksError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise JwksError(f"fetching JWKS URL failed: {exc}") from exc
    if len(data) > MAX_JWKS_BYTES:
        raise JwksError(f"JWKS response exceeds {MAX_JWKS_BYTES} bytes")
    return data


def _parse_jwks_bytes(raw_bytes, source_label):
    try:
        doc = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise JwksError(f"{source_label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("keys"), list):
        raise JwksError(f"{source_label} must be a JSON object containing a 'keys' list")
    for entry in doc["keys"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("kty"), str):
            raise JwksError(f"{source_label} contains a JWK entry without string 'kty'")
    return doc


def _has_kid(jwks_doc, kid):
    if not kid or not isinstance(jwks_doc, dict):
        return False
    return any(isinstance(k, dict) and k.get("kid") == kid for k in jwks_doc.get("keys", ()))


class JwksCache:
    """Thread-safe JWKS loader with SSRF guard, TTL cache, and 10 s kid-miss cooldown."""

    def __init__(
        self,
        *,
        jwks_url=None,
        jwks_file=None,
        ttl_s=DEFAULT_JWKS_TTL_S,
        min_refresh_s=MIN_JWKS_REFRESH_COOLDOWN_S,
        timeout_s=JWKS_FETCH_TIMEOUT_S,
        clock=time.time,
        fetcher=None,
    ):
        self.jwks_url = jwks_url.strip() if isinstance(jwks_url, str) and jwks_url.strip() else None
        self.jwks_file = jwks_file.strip() if isinstance(jwks_file, str) and jwks_file.strip() else None
        if not self.jwks_url and not self.jwks_file:
            raise JwksError("JwksCache requires jwks_url or jwks_file")
        if self.jwks_url:
            ok, why = validate_jwks_url(self.jwks_url)
            if not ok:
                raise JwksError(f"unsafe CONSOLE_JWKS_URL: {why}")
        self.ttl_s = max(0.0, float(ttl_s))
        self.min_refresh_s = max(MIN_JWKS_REFRESH_COOLDOWN_S, float(min_refresh_s))
        self.timeout_s = max(0.5, float(timeout_s))
        self._clock = clock
        self._fetcher = fetcher or _default_fetch_jwks_bytes
        self._lock = threading.Lock()
        self._cached = None
        self._fetched_at = None
        self._last_refresh_at = None
        self.fetch_count = 0

    @classmethod
    def from_env(cls, env=None, **kwargs):
        e = os.environ if env is None else env
        raw_ttl = (e.get("CONSOLE_JWKS_TTL_S") or "").strip()
        raw_cooldown = (e.get("CONSOLE_JWKS_REFRESH_COOLDOWN_S") or "").strip()
        try:
            ttl_s = float(raw_ttl) if raw_ttl else DEFAULT_JWKS_TTL_S
            cooldown_s = float(raw_cooldown) if raw_cooldown else MIN_JWKS_REFRESH_COOLDOWN_S
        except ValueError as exc:
            raise RbacConfigError(f"invalid JWKS TTL/cooldown setting: {exc}") from exc
        return cls(
            jwks_url=e.get("CONSOLE_JWKS_URL") or None,
            jwks_file=e.get("CONSOLE_JWKS_FILE") or None,
            ttl_s=ttl_s,
            min_refresh_s=cooldown_s,
            **kwargs,
        )

    def _load_source(self):
        keys = []
        if self.jwks_file:
            try:
                with open(self.jwks_file, "rb") as fh:
                    raw = fh.read(MAX_JWKS_BYTES + 1)
            except OSError as exc:
                raise JwksError(f"reading CONSOLE_JWKS_FILE {self.jwks_file!r} failed: {exc}") from exc
            if len(raw) > MAX_JWKS_BYTES:
                raise JwksError(f"CONSOLE_JWKS_FILE exceeds {MAX_JWKS_BYTES} bytes")
            doc = _parse_jwks_bytes(raw, "CONSOLE_JWKS_FILE")
            keys.extend(doc["keys"])
        if self.jwks_url:
            ok, why = validate_jwks_url(self.jwks_url)
            if not ok:
                raise JwksError(f"unsafe CONSOLE_JWKS_URL: {why}")
            raw = self._fetcher(self.jwks_url, self.timeout_s)
            doc = _parse_jwks_bytes(raw, "CONSOLE_JWKS_URL")
            keys.extend(doc["keys"])
        self.fetch_count += 1
        return {"keys": keys}

    def get_jwks(self, *, kid=None, force_refresh=False, now=None):
        t = float(self._clock() if now is None else now)
        with self._lock:
            if self._cached is None or (t - self._fetched_at) >= self.ttl_s:
                self._cached = self._load_source()
                self._fetched_at = t
                self._last_refresh_at = t
                return self._cached
            need_refresh = force_refresh or (kid is not None and not _has_kid(self._cached, kid))
            if need_refresh and (t - self._last_refresh_at) >= self.min_refresh_s:
                self._last_refresh_at = t
                self._cached = self._load_source()
                self._fetched_at = t
            return self._cached


# --- RFC 7519 JWT claim validation & top-level verify_jwt ---


def _numeric_date(claims, name, *, required=False):
    if name not in claims or claims[name] is None:
        if required:
            raise JwtVerificationError(f"JWT missing required '{name}' claim")
        return None
    val = claims[name]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise JwtVerificationError(f"JWT '{name}' claim must be a JSON number")
    fval = float(val)
    if math.isnan(fval) or math.isinf(fval):
        raise JwtVerificationError(f"JWT '{name}' claim must be finite")
    return fval


def _validate_claims(claims, policy, now_ts):
    exp = _numeric_date(claims, "exp", required=True)
    if exp + policy.leeway_s < now_ts:
        raise JwtVerificationError("JWT has expired (exp)")
    nbf = _numeric_date(claims, "nbf", required=False)
    if nbf is not None and nbf - policy.leeway_s > now_ts:
        raise JwtVerificationError("JWT is not yet valid (nbf is in the future)")
    iat = _numeric_date(claims, "iat", required=False)
    if iat is not None and iat - policy.leeway_s > now_ts:
        raise JwtVerificationError("JWT issued-at (iat) is in the future")
    if policy.issuer is not None:
        iss = claims.get("iss")
        if not isinstance(iss, str) or iss != policy.issuer:
            raise JwtVerificationError(f"JWT 'iss' mismatch: expected {policy.issuer!r}")
    if policy.audiences:
        aud = claims.get("aud")
        if isinstance(aud, str):
            aud_list = [aud] if aud else []
        elif isinstance(aud, list) and aud and all(isinstance(a, str) and a for a in aud):
            aud_list = aud
        else:
            raise JwtVerificationError("JWT 'aud' claim missing or malformed")
        if not any(a in policy.audiences for a in aud_list):
            raise JwtVerificationError("JWT 'aud' does not match configured audience")


def _normalize_jwks_arg(jwks, kid, now_ts):
    if jwks is None:
        return None
    if isinstance(jwks, JwksCache):
        return jwks.get_jwks(kid=kid, now=now_ts)
    if isinstance(jwks, dict):
        if "keys" in jwks:
            if not isinstance(jwks["keys"], list):
                raise JwksError("JWKS 'keys' must be a list")
            return jwks
        if "kty" in jwks:
            return {"keys": [jwks]}
    raise JwksError("jwks must be a JwksCache, JWKS dict {'keys': [...]}, single JWK dict, or None")


def verify_jwt(raw_jwt, policy=None, jwks=None, now=None):
    """Verify a compact JWS/JWT and return its verified claims dict."""
    pol = _coerce_policy(policy)
    if not isinstance(raw_jwt, str) or not raw_jwt:
        raise JwtVerificationError("JWT must be a non-empty string")
    raw_bytes = raw_jwt.encode("utf-8")
    if len(raw_bytes) > pol.max_token_bytes:
        raise JwtVerificationError(f"JWT length {len(raw_bytes)} exceeds maximum {pol.max_token_bytes} bytes")
    if any(ch.isspace() for ch in raw_jwt):
        raise JwtVerificationError("JWT must not contain whitespace")
    parts = raw_jwt.split(".")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        raise JwtVerificationError("JWT must have three dot-separated segments (header.payload.signature)")
    header_b64, payload_b64, sig_b64 = parts

    header_raw = _b64url_decode(header_b64, strict_unpadded=True, label="header")
    try:
        header = json.loads(header_raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise JwtVerificationError("JWT header is not valid UTF-8 JSON") from exc
    if not isinstance(header, dict):
        raise JwtVerificationError("JWT header must be a JSON object")

    alg = header.get("alg")
    if not isinstance(alg, str) or not alg:
        raise JwtVerificationError("JWT header missing 'alg'")
    if alg.lower() == "none":
        raise JwtVerificationError("unsecured JWT (alg=none) is rejected")
    if alg not in SUPPORTED_ALGS:
        raise JwtVerificationError(f"unsupported JWT alg: {alg!r}")
    if alg not in pol.allowed_algs:
        raise JwtVerificationError(f"JWT alg {alg!r} is not permitted by policy")
    if "crit" in header:
        raise JwtVerificationError("unsupported critical JWT header extension ('crit')")
    if not sig_b64:
        raise JwtVerificationError("JWT signature segment is empty")

    kid = header.get("kid")
    if kid is not None and (not isinstance(kid, str) or not kid):
        raise JwtVerificationError("JWT header 'kid' must be a non-empty string when present")

    sig_bytes = _b64url_decode(sig_b64, strict_unpadded=True, label="signature")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    now_ts = float(time.time() if now is None else now)

    if alg == "HS256":
        if not pol.jwt_secret:
            raise JwtVerificationError("HS256 is disabled unless jwt_secret is explicitly set")
        if jwks is None:
            candidates = [{"kty": "oct", "alg": "HS256", "k": _b64url_encode(pol.jwt_secret.encode("utf-8"))}]
        else:
            jwks_doc = _normalize_jwks_arg(jwks, kid, now_ts)
            pool = jwks_doc.get("keys", [])
            if not pool:
                raise JwtVerificationError("JWKS contains no keys")
            candidates = [k for k in pool if k.get("kid") == kid] if kid is not None else list(pool)
            if not candidates:
                raise JwtVerificationError(f"no JWK found for kid={kid!r}")
            for cand in candidates:
                if not isinstance(cand, dict) or cand.get("kty") != "oct":
                    raise JwtVerificationError(
                        "key confusion blocked: HS256 cannot verify against non-symmetric JWK"
                    )
        last_err = None
        verified = False
        for cand in candidates:
            try:
                _verify_hs256(signing_input, sig_bytes, cand)
                verified = True
                break
            except JwtVerificationError as exc:
                last_err = exc
        if not verified:
            raise last_err or JwtVerificationError("HS256 signature verification failed")
    else:
        if jwks is None:
            raise JwtVerificationError(f"{alg} verification requires a JWK/JWKS key source")
        jwks_doc = _normalize_jwks_arg(jwks, kid, now_ts)
        pool = jwks_doc.get("keys", [])
        if not pool:
            raise JwtVerificationError("JWKS contains no keys")
        candidates = [k for k in pool if isinstance(k, dict) and k.get("kid") == kid] if kid is not None else list(pool)
        if not candidates:
            raise JwtVerificationError(f"no JWK found matching kid={kid!r}")
        verifier = _verify_rs256 if alg == "RS256" else _verify_es256
        last_err = None
        verified = False
        for cand in candidates:
            try:
                verifier(signing_input, sig_bytes, cand)
                verified = True
                break
            except JwtVerificationError as exc:
                last_err = exc
        if not verified:
            raise last_err or JwtVerificationError(f"{alg} signature verification failed")

    payload_raw = _b64url_decode(payload_b64, strict_unpadded=True, label="payload")
    try:
        claims = json.loads(payload_raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise JwtVerificationError("JWT payload is not valid UTF-8 JSON") from exc
    if not isinstance(claims, dict):
        raise JwtVerificationError("JWT payload must be a JSON object")

    _validate_claims(claims, pol, now_ts)
    return claims


# --- Token extraction, 4-tier RBAC, and request evaluation ---


def _get_header(headers, name):
    if not headers or not name:
        return None
    if hasattr(headers, "get"):
        direct = headers.get(name)
        if direct is not None:
            return direct
    lower = name.lower()
    if hasattr(headers, "items"):
        for k, v in headers.items():
            if isinstance(k, str) and k.lower() == lower:
                return v
    return None


def extract_jwt(headers, header_name=None):
    """Extract compact JWT from HEADERS; returns (jwt_str | None, error_reason | None)."""
    hname = (header_name or "Authorization").strip()
    if not hname:
        return None, "JWT header name is empty"
    raw = _get_header(headers, hname)
    if raw is None or not str(raw).strip():
        return None, f"missing {hname} header"
    val = str(raw).strip()
    if hname.lower() == "authorization":
        parts = val.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            return None, "Authorization header must use Bearer <jwt> scheme"
        extracted = parts[1].strip()
    else:
        if val[:7].lower() == "bearer ":
            extracted = val[7:].strip()
        else:
            extracted = val
    if not extracted or any(ch.isspace() for ch in extracted):
        return None, f"malformed JWT value in {hname} header"
    return extracted, None


def is_jwt_verification_enabled(env=None):
    e = os.environ if env is None else env
    flag = (e.get("CONSOLE_VERIFY_JWT") or "").strip().lower()
    if flag in ("", "off", "0", "false", "no"):
        return False
    if flag in ("on", "1", "true", "yes"):
        return True
    raise RbacConfigError(f"CONSOLE_VERIFY_JWT={e.get('CONSOLE_VERIFY_JWT')!r} must be 'on' or 'off'")


def identity_from_claims(claims, env=None):
    """Extract authenticated principal string from verified JWT claims."""
    if not isinstance(claims, dict):
        return None
    e = os.environ if env is None else env
    configured = (e.get("CONSOLE_IDENTITY_CLAIM") or "").strip()
    keys = (configured,) if configured else ("email", "preferred_username", "upn", "sub")
    for key in keys:
        val = claims.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _csv_set(raw):
    if not raw:
        return frozenset()
    return frozenset(item.strip() for item in str(raw).split(",") if item.strip())


def parse_role_map(raw_json):
    """Parse CONSOLE_ROLE_MAP JSON into {external_group_or_role: tier_name}."""
    if not raw_json or not str(raw_json).strip():
        return {}
    try:
        parsed = json.loads(raw_json)
    except ValueError as exc:
        raise RbacConfigError(f"CONSOLE_ROLE_MAP is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RbacConfigError("CONSOLE_ROLE_MAP must be a JSON object")
    mapping = {}
    for key, val in parsed.items():
        if not isinstance(key, str) or not key.strip():
            raise RbacConfigError("CONSOLE_ROLE_MAP keys must be non-empty strings")
        if isinstance(val, str):
            tier = val.strip()
            if tier not in ROLE_RANK:
                raise RbacConfigError(f"CONSOLE_ROLE_MAP maps {key!r} to unknown role {tier!r}")
            mapping[key.strip()] = tier
        elif isinstance(val, list) and key.strip() in ROLE_RANK:
            tier = key.strip()
            for ext in val:
                if not isinstance(ext, str) or not ext.strip():
                    raise RbacConfigError(f"CONSOLE_ROLE_MAP[{tier!r}] entries must be non-empty strings")
                prev = mapping.get(ext.strip())
                if prev is None or ROLE_RANK[tier] > ROLE_RANK[prev]:
                    mapping[ext.strip()] = tier
        else:
            raise RbacConfigError(
                f"CONSOLE_ROLE_MAP entry {key!r} must map to a role in {ROLES} or list group names"
            )
    return mapping


def _extract_claim_values(claims, claim_path):
    if not isinstance(claims, dict) or not claim_path:
        return ()
    if claim_path in claims:
        node = claims[claim_path]
    elif "." in claim_path:
        node = claims
        for part in claim_path.split("."):
            if not isinstance(node, dict) or part not in node:
                return ()
            node = node[part]
    else:
        return ()
    if isinstance(node, str):
        stripped = node.strip()
        return (stripped,) if stripped else ()
    if isinstance(node, (list, tuple)):
        return tuple(item.strip() for item in node if isinstance(item, str) and item.strip())
    return ()


def resolve_role(identity, claims=None, env=None):
    """Resolve the highest granted RBAC tier ('viewer'|'developer'|'operator'|'launcher'|None)."""
    e = os.environ if env is None else env
    rbac_mode = (e.get("CONSOLE_RBAC") or "legacy").strip().lower()
    if rbac_mode not in ("legacy", "strict"):
        raise RbacConfigError(f"CONSOLE_RBAC={e.get('CONSOLE_RBAC')!r} must be 'legacy' or 'strict'")

    default_role_raw = (e.get("CONSOLE_DEFAULT_ROLE") or "").strip().lower()
    if default_role_raw and default_role_raw not in ROLE_RANK:
        raise RbacConfigError(f"CONSOLE_DEFAULT_ROLE={default_role_raw!r} must be one of {ROLES}")

    who = identity.strip() if isinstance(identity, str) else ""
    if not who:
        return None

    viewers = _csv_set(e.get("CONSOLE_VIEWERS"))
    developers = _csv_set(e.get("CONSOLE_DEVELOPERS"))
    operators = _csv_set(e.get("CONSOLE_OPERATORS"))
    launchers = _csv_set(e.get("CONSOLE_LAUNCHERS"))
    role_claim = (e.get("CONSOLE_ROLE_CLAIM") or "").strip()
    raw_role_map = (e.get("CONSOLE_ROLE_MAP") or "").strip()
    role_map = parse_role_map(raw_role_map)

    granted = set()
    if who in viewers or "*" in viewers:
        granted.add("viewer")
    if who in developers:
        granted.add("developer")
    if who in operators:
        granted.add("operator")
    if who in launchers:
        granted.add("launcher")

    if role_claim and isinstance(claims, dict):
        for token_role in _extract_claim_values(claims, role_claim):
            if role_map:
                mapped = role_map.get(token_role)
                if mapped in ROLE_RANK:
                    granted.add(mapped)
            elif token_role in ROLE_RANK:
                granted.add(token_role)

    if granted:
        return max(granted, key=lambda r: ROLE_RANK[r])

    if rbac_mode == "strict":
        return default_role_raw or None

    if default_role_raw:
        return default_role_raw

    has_multi_tier_config = bool(viewers or developers or operators or role_claim or raw_role_map)
    if not has_multi_tier_config:
        return "developer"
    if viewers:
        return None
    return "viewer"


def authorize(role, method, route):
    """Return (allowed: bool, reason: str) for (role, HTTP method, route path)."""
    norm_method = (method or "").strip().upper()
    norm_route = (route or "").split("?", 1)[0].strip()
    required = ROUTE_MIN_ROLE.get((norm_method, norm_route))
    if required is None:
        return False, f"route {norm_method} {norm_route} is not in the governed Console route table"
    if not isinstance(role, str) or role not in ROLE_RANK:
        return False, f"no RBAC role granted; {norm_method} {norm_route} requires '{required}'"
    if ROLE_RANK[role] < ROLE_RANK[required]:
        return False, f"role '{role}' is insufficient for {norm_method} {norm_route} (requires '{required}')"
    return True, f"role '{role}' satisfies required tier '{required}' for {norm_method} {norm_route}"


# --- Redacted security audit trail ---


def _is_sensitive_key(key):
    if not isinstance(key, str):
        return False
    low = key.strip().lower()
    if low in _SENSITIVE_KEY_NAMES:
        return True
    return low.endswith(_SENSITIVE_KEY_SUFFIXES)


def _scrub_string(text):
    out = _JWT_COMPACT_RE.sub("[REDACTED_JWT]", text)
    out = _AUTH_SCHEME_RE.sub(lambda m: f"{m.group(1)} [REDACTED]", out)
    out = _KV_SECRET_RE.sub(r"\1[REDACTED]", out)
    out = _URL_USERINFO_RE.sub(r"\1[REDACTED]@", out)
    return out


def redact_audit_data(value, *, key=None):
    """Recursively scrub raw JWTs, Authorization/Cookie headers, and secret fields."""
    if _is_sensitive_key(key):
        return "[REDACTED]" if value is not None else None
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, dict):
        return {str(k): redact_audit_data(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_audit_data(item) for item in value]
    return value


def append_audit_event(
    audit_path=None,
    path=None,
    *,
    event="authz",
    decision="allow",
    identity=None,
    role=None,
    method=None,
    route=None,
    status=None,
    reason=None,
    claims=None,
    extra=None,
    now=None,
    env=None,
):
    """Append one redacted JSONL audit record and return the sanitized dict."""
    e = os.environ if env is None else env
    base = {}
    target_path = path
    if isinstance(audit_path, dict):
        base.update(audit_path)
    elif audit_path is not None:
        target_path = audit_path
    resolved_path = str(target_path or e.get("CONSOLE_AUDIT_LOG") or DEFAULT_AUDIT_LOG)

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else float(now)))
    record = {
        "at": base.pop("at", ts),
        "event": base.pop("event", event),
        "decision": base.pop("decision", decision),
        "identity": base.pop("identity", identity),
        "role": base.pop("role", role),
        "method": base.pop("method", method),
        "route": (base.pop("route", route) or None),
        "status": base.pop("status", status),
        "reason": base.pop("reason", reason),
    }
    if isinstance(claims, dict):
        record["token_meta"] = {
            k: claims[k] for k in ("iss", "sub", "aud", "exp", "iat", "jti") if k in claims
        }
    if base:
        record.update(base)
    if isinstance(extra, dict):
        record.update(extra)

    sanitized = redact_audit_data(record)
    line = json.dumps(sanitized, sort_keys=True) + "\n"
    parent = os.path.dirname(os.path.abspath(resolved_path))
    with _AUDIT_LOCK:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(resolved_path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
    return sanitized


def evaluate_request(headers, method, route, *, env=None, jwks=None, now=None, audit_path=None):
    """Authenticate HEADERS and authorize (METHOD, ROUTE), returning AuthDecision."""
    e = os.environ if env is None else env
    auth_mode = (e.get("CONSOLE_AUTH") or "loopback").strip()
    verify_on = is_jwt_verification_enabled(e)

    def finish(ok, status, who, role, claims, reason):
        if audit_path is not None or e.get("CONSOLE_AUDIT_LOG"):
            append_audit_event(
                audit_path,
                event="request_authz",
                decision="allow" if ok else "deny",
                identity=who,
                role=role,
                method=method,
                route=route,
                status=status,
                reason=reason,
                claims=claims,
                now=now,
                env=e,
            )
        return AuthDecision(ok, status, who, role, claims, reason)

    if auth_mode == "public":
        allowed, why = authorize("viewer", method, route)
        return finish(allowed, 200 if allowed else 403, None, "viewer", None, why)

    if auth_mode == "loopback" and not verify_on:
        rbac_set = any(
            (e.get(k) or "").strip()
            for k in (
                "CONSOLE_RBAC",
                "CONSOLE_VIEWERS",
                "CONSOLE_DEVELOPERS",
                "CONSOLE_OPERATORS",
                "CONSOLE_LAUNCHERS",
            )
        )
        role = resolve_role("loopback", None, e) if rbac_set else "launcher"
        allowed, why = authorize(role, method, route)
        return finish(allowed, 200 if allowed else 403, "loopback", role, None, why)

    if verify_on:
        jwt_header = (e.get("CONSOLE_JWT_HEADER") or "Authorization").strip()
        raw_jwt, err = extract_jwt(headers, jwt_header)
        if err:
            return finish(False, 401, None, None, None, err)
        try:
            pol = JwtPolicy.from_env(e)
            active_jwks = jwks
            if active_jwks is None and (e.get("CONSOLE_JWKS_URL") or e.get("CONSOLE_JWKS_FILE")):
                active_jwks = JwksCache.from_env(e)
            claims = verify_jwt(raw_jwt, pol, jwks=active_jwks, now=now)
        except OidcRbacError as exc:
            return finish(False, 401, None, None, None, str(exc))
        who = identity_from_claims(claims, e)
        if not who:
            return finish(False, 401, None, None, claims, "JWT contains no usable identity claim")
        role = resolve_role(who, claims, e)
        allowed, why = authorize(role, method, route)
        return finish(allowed, 200 if allowed else 403, who, role, claims, why)

    id_header = (e.get("CONSOLE_IDENTITY_HEADER") or "").strip()
    who = _get_header(headers, id_header)
    who = str(who).strip() if who is not None else ""
    if not who:
        return finish(False, 401, None, None, None, f"no {id_header} header: reach the console through its proxy")
    role = resolve_role(who, None, e)
    allowed, why = authorize(role, method, route)
    return finish(allowed, 200 if allowed else 403, who, role, None, why)
