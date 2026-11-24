# Production OIDC Proxy & Cryptographic JWT Recipes (`CONSOLE_AUTH=proxy`)

These templates place `console/serve.py` behind an authenticating reverse proxy and enable defence-in-depth cryptographic OIDC/JWT verification with 4-tier Console RBAC via `core/oidc_rbac.py` (see [ADR 0047](../../docs/adr/0047-console-reads-one-versioned-logbook-and-writes-only-go-for-launch.md) §5, [ADR 0111](../../docs/adr/0111-hive-guided-onboarding-ui-and-proxy-recipes.md), [ADR 0127](../../docs/adr/0127-cryptographic-oidc-jwt-and-console-rbac.md), and [docs/production.md](../../docs/production.md)).

## Trust Boundary (Mandatory)

1. **Loopback binding & cryptographic verification.** Never expose the console directly on a public network interface when running with `CONSOLE_AUTH=proxy`. Bind the console exclusively to loopback (`127.0.0.1:8098`) or a private pod-local network reachable only from the proxy, and set `CONSOLE_VERIFY_JWT=on` so every request's signed JWT (`RS256` or `ES256`) is verified against your OIDC provider's JWKS endpoint (`CONSOLE_JWKS_URL`).
2. **Strip spoofed identity and authorization headers at the proxy edge.** The proxy must delete any client-supplied `X-Auth-Request-Email`, `Cf-Access-Authenticated-User-Email`, `Cf-Access-Jwt-Assertion`, `X-Goog-IAP-JWT-Assertion`, or `Authorization` header before injecting verified headers from your OIDC provider.
3. **Zero committed secrets.** Inject OIDC client secrets and cookie encryption keys via environment variables or a secret manager (`OAUTH2_PROXY_CLIENT_SECRET`, `OAUTH2_PROXY_COOKIE_SECRET`) — never commit credentials into this repository or `.env` files tracked in git.

## 4-Tier Console RBAC (`CONSOLE_RBAC=strict`)

`core/oidc_rbac.py` enforces a hierarchical 4-tier role model (`viewer` < `developer` < `operator` < `launcher`) resolved from explicit allowlists (`CONSOLE_VIEWERS`, `CONSOLE_DEVELOPERS`, `CONSOLE_OPERATORS`, `CONSOLE_LAUNCHERS`) and/or verified JWT group/role claims (`CONSOLE_ROLE_CLAIM` + optional JSON `CONSOLE_ROLE_MAP`):

| Tier | Allowed Routes |
|---|---|
| `viewer` | `GET /`, `GET /index.html`, `GET /v1/logbook.json`, `GET /v1/console.json`, `GET /v1/events`, `GET /v1/charts.json`, `POST /v1/spec-check` |
| `developer` | All `viewer` routes + `POST /v1/answer`, `POST /v1/request`, `POST /v1/intake`, `POST /v1/intake-confirm` |
| `operator` | All `developer` routes + `POST /v1/assign`, `POST /v1/resume`, `POST /v1/watch-once`, `POST /v1/freeze` |
| `launcher` | All `operator` routes + `POST /v1/go-for-launch`, `POST /v1/rollback` |

When `CONSOLE_RBAC` is unset or `legacy` and only `CONSOLE_LAUNCHERS` is configured, authenticated proxy users outside `CONSOLE_LAUNCHERS` default to `developer` (ADR 0047 compatibility). Setting `CONSOLE_RBAC=strict` denies any identity not explicitly granted at least `viewer`.

## Recipes

- [`oauth2-proxy.cfg`](oauth2-proxy.cfg): Standalone `oauth2-proxy` configuration terminating OIDC and forwarding `X-Auth-Request-Email` plus signed OIDC ID tokens (`Authorization: Bearer <id_token>`) to `http://127.0.0.1:8098`.
- [`Caddyfile`](Caddyfile): TLS reverse proxy stripping client identity/token headers and delegating `forward_auth` to `oauth2-proxy` before proxying verified headers to `127.0.0.1:8098`.
- [`cloudflare-access.md`](cloudflare-access.md): Cloudflare Zero Trust Access + Tunnel recipe verifying `Cf-Access-Jwt-Assertion` (`RS256`) alongside `Cf-Access-Authenticated-User-Email` (and Google Cloud IAP `X-Goog-IAP-JWT-Assertion` `ES256` guidance).

## Starting the Console with Signed JWT Verification & 4-Tier RBAC

```sh
CONSOLE_AUTH=proxy \
CONSOLE_IDENTITY_HEADER=X-Auth-Request-Email \
CONSOLE_VERIFY_JWT=on \
CONSOLE_JWT_HEADER=Authorization \
CONSOLE_JWKS_URL=https://idp.example.com/realms/lights-out/protocol/openid-connect/certs \
CONSOLE_JWT_ISSUER=https://idp.example.com/realms/lights-out \
CONSOLE_JWT_AUDIENCE=lights-out-console \
CONSOLE_RBAC=strict \
CONSOLE_VIEWERS=auditor@example.com \
CONSOLE_DEVELOPERS=dev@example.com \
CONSOLE_OPERATORS=sre@example.com \
CONSOLE_LAUNCHERS=alice@example.com,bob@example.com \
CONSOLE_ROLE_CLAIM=groups \
CONSOLE_ROLE_MAP='{"eng":"developer","sre":"operator","release-leads":"launcher"}' \
CONSOLE_HOSTS=console.example.com \
CONSOLE_PUBLISH=127.0.0.1 \
CONSOLE_PORT=8098 \
./go console
```

With the standalone `lights-out` wrapper:

```sh
CONSOLE_AUTH=proxy \
CONSOLE_IDENTITY_HEADER=X-Auth-Request-Email \
CONSOLE_VERIFY_JWT=on \
CONSOLE_JWT_HEADER=Authorization \
CONSOLE_JWKS_URL=https://idp.example.com/realms/lights-out/protocol/openid-connect/certs \
CONSOLE_JWT_ISSUER=https://idp.example.com/realms/lights-out \
CONSOLE_JWT_AUDIENCE=lights-out-console \
CONSOLE_RBAC=strict \
CONSOLE_LAUNCHERS=alice@example.com,bob@example.com \
CONSOLE_HOSTS=console.example.com \
lights-out console
```

Verify production readiness checks (including OIDC issuer, audience, key source, and SSRF-safe JWKS URL) before enabling live traffic:

```sh
CONSOLE_AUTH=proxy \
CONSOLE_IDENTITY_HEADER=X-Auth-Request-Email \
CONSOLE_VERIFY_JWT=on \
CONSOLE_JWKS_URL=https://idp.example.com/realms/lights-out/protocol/openid-connect/certs \
CONSOLE_JWT_ISSUER=https://idp.example.com/realms/lights-out \
CONSOLE_JWT_AUDIENCE=lights-out-console \
CONSOLE_RBAC=strict \
CONSOLE_LAUNCHERS=alice@example.com \
./go production-readiness
```
