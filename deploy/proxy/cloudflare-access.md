# Cloudflare Access & Google Cloud IAP Recipes for `lights-out` Console

When placing the `lights-out` console behind Cloudflare Access and a private `cloudflared` tunnel, Cloudflare authenticates the user via your IdP and injects both `Cf-Access-Authenticated-User-Email` and a cryptographically signed `RS256` JWT in `Cf-Access-Jwt-Assertion` into requests forwarded over the tunnel to `http://127.0.0.1:8098` (see [ADR 0111](../../docs/adr/0111-hive-guided-onboarding-ui-and-proxy-recipes.md) and [ADR 0127](../../docs/adr/0127-cryptographic-oidc-jwt-and-console-rbac.md)).

## 1. Console Configuration (Cloudflare Access Signed JWT + 4-Tier RBAC)

Start the console bound strictly to loopback with `CONSOLE_VERIFY_JWT=on` so `core/oidc_rbac.py` verifies every `Cf-Access-Jwt-Assertion` signature against your Cloudflare Access team JWKS endpoint:

```sh
CONSOLE_AUTH=proxy \
CONSOLE_IDENTITY_HEADER=Cf-Access-Authenticated-User-Email \
CONSOLE_VERIFY_JWT=on \
CONSOLE_JWT_HEADER=Cf-Access-Jwt-Assertion \
CONSOLE_JWKS_URL=https://example-team.cloudflareaccess.com/cdn-cgi/access/certs \
CONSOLE_JWT_ISSUER=https://example-team.cloudflareaccess.com \
CONSOLE_JWT_AUDIENCE=0000000000000000000000000000000000000000000000000000000000000000 \
CONSOLE_RBAC=strict \
CONSOLE_VIEWERS=auditor@example.com \
CONSOLE_DEVELOPERS=dev@example.com \
CONSOLE_OPERATORS=sre@example.com \
CONSOLE_LAUNCHERS=alice@example.com,bob@example.com \
CONSOLE_HOSTS=console.example.com \
CONSOLE_PUBLISH=127.0.0.1 \
CONSOLE_PORT=8098 \
./go console
```

## 2. Google Cloud Identity-Aware Proxy (`ES256`) Variant

Google Cloud IAP signs assertions with NIST P-256 `ES256` in `X-Goog-IAP-JWT-Assertion`:

```sh
CONSOLE_AUTH=proxy \
CONSOLE_IDENTITY_HEADER=X-Goog-Authenticated-User-Email \
CONSOLE_VERIFY_JWT=on \
CONSOLE_JWT_HEADER=X-Goog-IAP-JWT-Assertion \
CONSOLE_JWKS_URL=https://www.gstatic.com/iap/verify/public_key-jwk \
CONSOLE_JWT_ISSUER=https://cloud.google.com/iap \
CONSOLE_JWT_AUDIENCE=/projects/1234567890/global/backendServices/9876543210 \
CONSOLE_RBAC=strict \
CONSOLE_VIEWERS=auditor@example.com \
CONSOLE_DEVELOPERS=dev@example.com \
CONSOLE_OPERATORS=sre@example.com \
CONSOLE_LAUNCHERS=alice@example.com \
CONSOLE_HOSTS=console.example.com \
CONSOLE_PUBLISH=127.0.0.1 \
CONSOLE_PORT=8098 \
./go console
```

## 3. Tunnel, JWT & 4-Tier RBAC Checklist

- Bind `console/serve.py` to `127.0.0.1:8098` (do not expose port `8098` on public firewall rules).
- Configure Cloudflare Zero Trust Access (or Google Cloud IAP) for `console.example.com` allowing only your team emails or OIDC groups, and copy the Application Audience (`AUD`) tag into `CONSOLE_JWT_AUDIENCE`.
- In `cloudflared` ingress rules, route `console.example.com` directly to `http://127.0.0.1:8098` without altering `Host` or `Origin` headers (ensure `CONSOLE_HOSTS` lists `console.example.com`).
- Assign least-privilege tiers across `viewer` (read + `POST /v1/spec-check`), `developer` (answers, ticket requests, intakes), `operator` (`assign`, `resume`, `watch-once`, `freeze`), and `launcher` (`go-for-launch`, `rollback`) via `CONSOLE_VIEWERS`, `CONSOLE_DEVELOPERS`, `CONSOLE_OPERATORS`, `CONSOLE_LAUNCHERS`, or verified JWT claims (`CONSOLE_ROLE_CLAIM` + `CONSOLE_ROLE_MAP`).

## 4. Production Readiness Verification

```sh
CONSOLE_AUTH=proxy \
CONSOLE_IDENTITY_HEADER=Cf-Access-Authenticated-User-Email \
CONSOLE_VERIFY_JWT=on \
CONSOLE_JWT_HEADER=Cf-Access-Jwt-Assertion \
CONSOLE_JWKS_URL=https://example-team.cloudflareaccess.com/cdn-cgi/access/certs \
CONSOLE_JWT_ISSUER=https://example-team.cloudflareaccess.com \
CONSOLE_JWT_AUDIENCE=0000000000000000000000000000000000000000000000000000000000000000 \
CONSOLE_RBAC=strict \
CONSOLE_LAUNCHERS=alice@example.com \
./go production-readiness
```
