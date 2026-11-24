# Production OIDC Proxy Recipes (`CONSOLE_AUTH=proxy`)

These templates place `console/serve.py` behind an authenticating reverse proxy (see [ADR 0047](../../docs/adr/0047-console-reads-one-versioned-logbook-and-writes-only-go-for-launch.md) §5, [ADR 0111](../../docs/adr/0111-hive-guided-onboarding-ui-and-proxy-recipes.md), and [docs/production.md](../../docs/production.md)).

## Trust Boundary (Mandatory)

1. **`console/serve.py` trusts `CONSOLE_IDENTITY_HEADER` verbatim.** Never expose the console directly on a public network interface when running with `CONSOLE_AUTH=proxy`. Bind the console exclusively to loopback (`127.0.0.1:8098`) or a private pod-local network reachable only from the proxy.
2. **Strip spoofed identity headers at the proxy edge.** The proxy must delete any client-supplied `X-Auth-Request-Email` or `Cf-Access-Authenticated-User-Email` header before forwarding the authenticated header from your OIDC provider.
3. **Zero committed secrets.** Inject OIDC client secrets and cookie encryption keys via environment variables or a secret manager (`OAUTH2_PROXY_CLIENT_SECRET`, `OAUTH2_PROXY_COOKIE_SECRET`) — never commit credentials into this repository or `.env` files tracked in git.

## Recipes

- [`oauth2-proxy.cfg`](oauth2-proxy.cfg): Standalone `oauth2-proxy` configuration terminating OIDC and forwarding `X-Auth-Request-Email` to `http://127.0.0.1:8098`.
- [`Caddyfile`](Caddyfile): TLS reverse proxy stripping client identity headers and delegating `forward_auth` to `oauth2-proxy` before proxying to `127.0.0.1:8098`.
- [`cloudflare-access.md`](cloudflare-access.md): Cloudflare Zero Trust Access + Tunnel recipe using `Cf-Access-Authenticated-User-Email`.

## Starting the Console Behind the Proxy

```sh
CONSOLE_AUTH=proxy \
CONSOLE_IDENTITY_HEADER=X-Auth-Request-Email \
CONSOLE_LAUNCHERS=alice@example.com,bob@example.com \
CONSOLE_HOSTS=console.example.com \
CONSOLE_PUBLISH=127.0.0.1 \
CONSOLE_PORT=8098 \
./go console
```

With the standalone `lights-out` wrapper:

```sh
CONSOLE_AUTH=proxy \
CONSOLE_IDENTITY_HEADER=X-Auth-Request-Email \
CONSOLE_LAUNCHERS=alice@example.com,bob@example.com \
CONSOLE_HOSTS=console.example.com \
lights-out console
```

Verify production readiness checks before enabling live traffic:

```sh
CONSOLE_AUTH=proxy \
CONSOLE_IDENTITY_HEADER=X-Auth-Request-Email \
CONSOLE_LAUNCHERS=alice@example.com \
./go production-readiness
```
