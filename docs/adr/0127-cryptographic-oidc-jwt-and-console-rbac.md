# ADR 0127 — Cryptographic OIDC/JWT Verification and 4-Tier Console RBAC

- Status: proposed
- Date: 2026-11-24
- Milestone: M87
- Amends: ADR 0047 §5 (`CONSOLE_AUTH=proxy` gains opt-in cryptographic JWT verification and 4-tier RBAC while preserving legacy header-only behavior when unset); ADR 0057 §10 (`gates/production-readiness.sh` validates issuer, audience, key source, and SSRF-safe JWKS URL when JWT/RBAC settings are active); ADR 0111 §5 (`deploy/proxy/` recipes forward signed JWT assertions alongside identity headers)

## Context

`console/serve.py` (`CONSOLE_AUTH=proxy`, ADR 0047 §5) currently trusts a plain-text HTTP identity header (`CONSOLE_IDENTITY_HEADER`, such as `X-Auth-Request-Email` or `Cf-Access-Authenticated-User-Email`) injected by an upstream reverse proxy and enforces a two-level distinction: identities listed in `CONSOLE_LAUNCHERS` may press `POST /v1/go-for-launch`, while every other authenticated proxy user implicitly receives write access to `POST /v1/answer`, `POST /v1/request`, `POST /v1/intake`, and `POST /v1/intake-confirm`.

Enterprise multi-team deployments expose three security and governance gaps:

1. **No cryptographic token verification inside the Console boundary.** If a misconfigured ingress, mesh sidecar, or shared pod network allows traffic to reach port `8098` with a forged `X-Auth-Request-Email` header, plain header trust cannot detect the spoof. Identity-aware proxies such as Cloudflare Access (`Cf-Access-Jwt-Assertion`, `RS256`), Google Cloud Identity-Aware Proxy (`X-Goog-IAP-JWT-Assertion`, `ES256` P-256), and OIDC `oauth2-proxy` (`Authorization: Bearer <id_token>`) already emit signed RFC 7519 JSON Web Tokens verifiable against standard RFC 7517 JWKS endpoints.
2. **Coarse two-tier authorization across read, developer, operator, and launch actions.** As the Console workstation grows to `17` governed routes (`6` `GET` endpoints and `11` `POST` endpoints across ADR 0047, ADR 0055, ADR 0059, ADR 0090, ADR 0124, and ADR 0125), enterprises need least-privilege separation between read-only auditors (`viewer`), ticket/spec contributors (`developer`), day-to-day queue/freeze operators (`operator`), and final canary release signers (`launcher`), mapped either from explicit environment allowlists or verified OIDC group/role claims.
3. **Zero external dependency constraint in `core/`.** All verification logic in `core/` must remain 100% pure Python standard library with zero third-party packages and zero subprocesses, preserving cold offline execution (`./go demo`) and vendor/app/model neutrality.

## Decision

### 1. Pure-Stdlib OIDC/JWT Verifier (`core/oidc_rbac.py`)

`core/oidc_rbac.py` implements RFC 7515 (JWS), RFC 7517 (JWK/JWKS), RFC 7518 (JWA), and RFC 7519 (JWT) verification using only Python's standard library (`base64`, `hashlib`, `hmac`, `ipaddress`, `json`, `math`, `os`, `re`, `threading`, `time`, `urllib.parse`, `urllib.request`):

- **Supported JWS algorithms (`SUPPORTED_ALGS = ("RS256", "ES256", "HS256")`)**:
  - **`RS256` (RSASSA-PKCS1-v1_5 using SHA-256, RFC 8017 §8.2.2 & §9.2)**: requires JWK `kty == "RSA"`, odd public exponent `e >= 3`, and RSA modulus `n` of at least `2048` bits (`256` octets) because NIST SP 800-57 and RFC 7518 §3.3 mandate `>= 2048`-bit RSA keys. Computes `pow(sig_int, e, n)` over exact `k`-byte signature inputs and compares the resulting encoded message via `hmac.compare_digest` against the exact full-width block `0x00 || 0x01 || (0xff * (k - 54)) || 0x00 || ASN1_SHA256_DIGEST_INFO || sha256(signing_input)` (`51`-byte DER `DigestInfo`), eliminating ASN.1 parser laxity and Bleichenbacher padding-oracle or low-exponent trailing-garbage flaws.
  - **`ES256` (ECDSA over NIST P-256 / `secp256r1` using SHA-256, FIPS 186-4 & RFC 7518 §3.4)**: requires JWK `kty == "EC"` and `crv == "P-256"` with `32`-octet `x` and `y` coordinates, verifies `0 <= x < p`, `0 <= y < p`, and curve membership `y^2 == x^3 - 3x + b (mod p)` (not point at infinity), parses `64`-byte raw JWS `r || s` signatures (plus strict canonical ASN.1 DER `SEQUENCE { r, s }` normalization when needed), enforces `1 <= r < n` and `1 <= s < n` (blocking `r = 0, s = 0` psychic signatures), computes `R = (z * s^-1 mod n) * G + (r * s^-1 mod n) * Q`, rejects `R == infinity`, and checks `R.x mod n == r`. Compatible with OIDC IdPs and Google Cloud IAP (`X-Goog-IAP-JWT-Assertion`).
  - **`HS256` (HMAC-SHA256)**: verified via `hmac.compare_digest` strictly when `policy.jwt_secret` (`CONSOLE_JWT_SECRET`) is explicitly configured AND the candidate JWK has symmetric `kty == "oct"`. Asymmetric JWKS (`RSA` / `EC`) never fall back to `HS256` or treat public key material as an HMAC secret, providing structural immunity to algorithm/key-confusion attacks.
- **Hard claim and framing rejections**:
  - Tokens exceeding `MAX_TOKEN_BYTES = 16384` bytes (`16` KiB), malformed Base64URL segments, non-canonical Base64URL padding bits, `alg == "none"` (case-insensitive), missing/unsupported `alg`, unsupported `crit` headers, or mismatched `alg`/`kty`/`crv`/`use`.
  - Missing or non-numeric `exp` (rejecting booleans), expired tokens where `exp + leeway < now` (`CONSOLE_JWT_LEEWAY_S`, default `60` s, capped at `300` s), future `nbf` (`nbf - leeway > now`), future `iat` (`iat - leeway > now`), `iss` mismatch when `issuer` (`CONSOLE_JWT_ISSUER`) is configured, and `aud` mismatch (accepting a `str` or non-empty `list[str]`) when `audience` (`CONSOLE_JWT_AUDIENCE`) is configured.

### 2. SSRF-Safe `JwksCache` with TTL and `kid`-Miss Cooldown

`JwksCache` loads JWKS documents from `CONSOLE_JWKS_FILE` or fetches `CONSOLE_JWKS_URL` via an isolated `urllib.request` opener (`HTTPHandler` + `HTTPSHandler` only, capped at `MAX_JWKS_BYTES = 262144` bytes and `5` s timeout):

- **Strict SSRF URL validation (`validate_jwks_url`)**: allows `https://` endpoints (excluding link-local/cloud metadata ranges such as `169.254.169.254` / `169.254.0.0/16`, `fe80::/10`, `fd00:ec2::254`, `100.100.100.200`, `metadata.google.internal`, unspecified/multicast addresses, IPv4-mapped metadata addresses, and URL userinfo credentials) or explicit loopback `http://127.0.0.1`, `http://localhost`, and `http://[::1]` for local integration/testing. Non-loopback `http://` URLs, `file://` URLs, and unsafe HTTP redirects are rejected before opening or following connections.
- **Caching & Rotation**: caches parsed JWKS keys in memory for `CONSOLE_JWKS_TTL_S` (default `300` s). When an incoming token's `kid` is absent from the cached key set, `JwksCache` triggers an on-demand refresh subject to a mandatory cooldown floor of `MIN_JWKS_REFRESH_COOLDOWN_S = 10` s since the previous fetch so IdP key rotations cut over seamlessly without exposing the JWKS endpoint to random-`kid` request floods.

### 3. Token Extraction, 4-Tier RBAC, and Redacted Security Audit Log

- **Token Extraction (`extract_jwt`)**: reads `CONSOLE_JWT_HEADER` (default `Authorization`, requiring the `Bearer <token>` scheme; or raw/Bearer compact tokens from assertion headers such as `Cf-Access-Jwt-Assertion` and `X-Goog-IAP-JWT-Assertion`), returning explicit refusal reasons when missing or malformed.
- **4-Tier Role Hierarchy (`ROLES = ("viewer", "developer", "operator", "launcher")`)**:
  higher tiers inherit all permissions of lower tiers across the `17` Console routes (`authorize(role, method, route) -> (bool, str)`), failing closed on unknown methods or routes:
  - **`viewer`** (`0`): `GET /`, `GET /index.html`, `GET /v1/logbook.json`, `GET /v1/console.json`, `GET /v1/events`, `GET /v1/charts.json`, and `POST /v1/spec-check` (read-only / dry-run check).
  - **`developer`** (`1`): `viewer` routes plus `POST /v1/answer`, `POST /v1/request`, `POST /v1/intake`, and `POST /v1/intake-confirm`.
  - **`operator`** (`2`): `developer` routes plus `POST /v1/assign`, `POST /v1/resume`, `POST /v1/watch-once`, and `POST /v1/freeze`.
  - **`launcher`** (`3`): `operator` routes plus `POST /v1/go-for-launch` and `POST /v1/rollback`.
- **Role Resolution (`resolve_role(identity, claims, env)`)**:
  evaluates comma-separated identity lists (`CONSOLE_VIEWERS`, `CONSOLE_DEVELOPERS`, `CONSOLE_OPERATORS`, `CONSOLE_LAUNCHERS`) together with verified JWT claims named by `CONSOLE_ROLE_CLAIM` (for example `roles`, `groups`, or `realm_access.roles`, optionally translated via JSON `CONSOLE_ROLE_MAP`), selecting the **highest** granted tier. When `CONSOLE_RBAC` is unset or `legacy` and only `CONSOLE_LAUNCHERS` is configured, unmatched authenticated identities continue to resolve to `developer` (`CONSOLE_LAUNCHERS` -> `launcher`), preserving 100% backward compatibility with ADR 0047. When `CONSOLE_RBAC=strict`, unmatched identities resolve to `CONSOLE_DEFAULT_ROLE` if explicitly set, or `None` (denied across all routes) by default.
- **Security Audit Trail (`append_audit_event`)**: appends structured JSONL authentication/authorization events to `CONSOLE_AUDIT_LOG` (default `.crew/console-audit.jsonl`), recursively scrubbing compact JWT strings, `Bearer`/`Basic` headers, secret-named fields (`token`, `jwt`, `secret`, `password`, `authorization`, `cookie`, `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`), and URL credentials before writing.

### 4. Production Readiness Gate & Proxy Recipes

- **`core/gates/readiness.py` (`human_gate`)**: remains 100% unchanged when `CONSOLE_VERIFY_JWT`, `CONSOLE_JWKS_URL`, `CONSOLE_JWKS_FILE`, and `CONSOLE_RBAC` are unset (`ci/readiness.sh` compatibility). Whenever OIDC/RBAC hardening is active (`CONSOLE_VERIFY_JWT=on`, `CONSOLE_JWKS_URL`, `CONSOLE_JWKS_FILE`, or `CONSOLE_RBAC=strict`), `human_gate` requires `CONSOLE_JWT_ISSUER`, `CONSOLE_JWT_AUDIENCE`, at least one non-placeholder key source (`CONSOLE_JWKS_URL`, `CONSOLE_JWKS_FILE`, or `CONSOLE_JWT_SECRET`), and an SSRF-safe `CONSOLE_JWKS_URL` scheme/host when `CONSOLE_JWKS_URL` is set.
- **`deploy/proxy/*`**: `README.md`, `Caddyfile`, `oauth2-proxy.cfg`, and `cloudflare-access.md` document signed JWT header forwarding, `CONSOLE_VERIFY_JWT=on`, `CONSOLE_JWKS_URL`, `CONSOLE_JWT_ISSUER`, `CONSOLE_JWT_AUDIENCE`, `CONSOLE_RBAC=strict`, and the 4 RBAC tiers while keeping legacy proxy-header recipes intact and zero committed secrets.

## Alternatives rejected

- **Adding `PyJWT` and `cryptography` to `core/` runtime dependencies:** Violates the zero-third-party stdlib-only boundary of `core/` and breaks standalone host/minimal-Python invocations. Pure-Python modular exponentiation (`pow(s, e, n)`) and P-256 curve arithmetic verify `RS256` and `ES256` in `< 1` ms using stdlib alone while test suites cross-verify signatures generated by `cryptography`.
- **Parsing PKCS#1 v1.5 `DigestInfo` with a permissive ASN.1 decoder:** Historical RSA signature-forgery vulnerabilities (`e = 3` Bleichenbacher attacks) stem from accepting trailing bytes or alternate ASN.1 encodings. Constructing the single exact `k`-byte RFC 8017 §9.2 SHA-256 `DigestInfo` block and comparing with `hmac.compare_digest` admits zero structural variation.
- **Allowing `HS256` fallback when a JWKS is configured:** Classic JWT key-confusion attacks sign forged `HS256` tokens using a server's public RSA/EC key. Requiring both an explicit `CONSOLE_JWT_SECRET` and symmetric `kty == "oct"` (never falling back from asymmetric JWKS) makes key confusion impossible by construction.
- **Fetching arbitrary `http://` or header-supplied `jku` URLs:** Trusting token-supplied `jku`/`jwk` headers or plain `http://` / link-local JWKS URLs enables SSRF and credential theft against cloud instance metadata (`169.254.169.254`). Pinning key sources to operator configuration and enforcing `validate_jwks_url` closes both vectors.

## Consequences

- Operators running `console/serve.py` behind Cloudflare Access, Google Cloud IAP, or `oauth2-proxy` + `Caddy` gain defence-in-depth cryptographic JWT verification and least-privilege 4-tier RBAC without adding external Python packages.
- Existing L0/loopback/snapshot flows and legacy `CONSOLE_AUTH=proxy` setups without `CONSOLE_VERIFY_JWT` or `CONSOLE_RBAC=strict` behave identically to ADR 0047, keeping `./go demo` and `ci/readiness.sh` green.
- The integration commit wires `core.oidc_rbac` directly into `console/serve.py` without touching adapter or workload boundaries.

## Provenance

| Threshold / Constant | Value | Provenance |
|---|---|---|
| `MAX_TOKEN_BYTES` | `16384` bytes (`16` KiB) | Derived from `DRAFT_MAX = 16384` in `console/serve.py` (ADR 0059 §4) and standard `16` KiB HTTP header buffer limits in reverse proxies, bounding untrusted token size before Base64URL/bignum parsing. |
| `MIN_RSA_BITS` / `MIN_RSA_BYTES` | `2048` bits / `256` bytes | External standard: NIST SP 800-57 Part 1 and RFC 7518 §3.3 minimum RSA modulus size for `RS256`; RSA public exponent `e >= 3` odd per RFC 8017 §3.1. |
| `PKCS1_SHA256_DIGEST_INFO` | `51` bytes (`19` prefix + `32` digest, `k - 54 >= 8` `0xff` pad) | External standard: RFC 8017 §9.2 EMSA-PKCS1-v1_5 DER `DigestInfo` for SHA-256 (`32`-byte FIPS 180-4 digest). |
| `P256_COORD_BYTES` / `ES256_RAW_SIG_BYTES` | `32` bytes / `64` bytes (`256` bits, `1 <= r, s < n`) | External standard: FIPS 186-4 / SEC 2 `secp256r1` and RFC 7518 §3.4 & §6.2.1 (`32`-octet `x`/`y` coordinates and `64`-octet `r \|\| s` JWS signature). |
| `DEFAULT_LEEWAY_S` / `MAX_LEEWAY_S` | `60` s / `300` s | Derived from RFC 7519 §4.1.4 clock-skew leeway recommendation ("usually no more than a few minutes"): `60` s default tolerates minor NTP skew; `300` s (`5` min) ceiling prevents long-lived expired token reuse. |
| `DEFAULT_JWKS_TTL_S` | `300` s (`5` min) | Decision aligned with standard OIDC provider JWKS cache windows (`300` s), refreshing rotated keys periodically without per-request IdP latency. |
| `MIN_JWKS_REFRESH_COOLDOWN_S` | `10` s | Decision: `10` s floor between `kid`-miss JWKS fetches caps upstream IdP traffic to at most `6` refreshes/minute under random-`kid` token floods while matching `CHECK_TIMEOUT_S = 10` s in `console/serve.py`. |
| `JWKS_FETCH_TIMEOUT_S` / `MAX_JWKS_BYTES` | `5` s / `262144` bytes (`256` KiB) | Decision: `5` s socket timeout stays well under `10` s Console check budgets, and `262144` bytes (`256` KiB) accommodates multi-key JWKS rotations while preventing memory exhaustion. |
| `ROLES` / governed Console routes | `4` tiers (`0`..`3`) / `17` routes (`6` `GET`, `11` `POST`) | Derived from the Console route inventory across ADR 0047, ADR 0055, ADR 0059, ADR 0090, ADR 0124, and ADR 0125 (`viewer`, `developer`, `operator`, `launcher`). |

## Tests, from this ADR

- `core/tests/test_oidc_rbac.py` — unit tests for `RS256` (`>= 2048`-bit enforcement, PKCS#1 v1.5 padding/byte tamper rejection), `ES256` (`secp256r1` raw `64`-byte and DER signatures, off-curve public key rejection, `r=0`/`s=0` rejection), `HS256` and asymmetric-JWKS key-confusion immunity, `alg=none`/oversized/expired/future-`nbf`/future-`iat`/`iss`/`aud` rejections, `JwksCache` SSRF validation + TTL + `10` s `kid`-miss cooldown, token header extraction, 4-tier route authorization over all `17` routes, legacy vs `strict` role resolution with `CONSOLE_ROLE_CLAIM`/`CONSOLE_ROLE_MAP`, redacted JSONL audit logging, and `core.adr_gov.validate_adr` validation of ADR 0127.
- `core/tests/test_gates_readiness.py` — existing ADR 0057 readiness tests unchanged plus `human_gate` validation when `CONSOLE_VERIFY_JWT=on`, `CONSOLE_JWKS_URL`, `CONSOLE_JWKS_FILE`, or `CONSOLE_RBAC=strict` are set.
- `ci/tests/test_oidc_rbac_e2e.py` — end-to-end Podman workbench tests generating real `RSA-2048` (`RS256`) and `P-256` (`ES256`) keys and tokens with `cryptography`, serving an ephemeral loopback JWKS HTTP endpoint, verifying live key rotation, token expiry, `alg=none`, RS256-to-HS256 key-confusion rejection, SSRF rejection (`http://` non-loopback and `169.254.169.254`), and all 4 RBAC tiers.
