# Cloudflare Access (Zero Trust) Recipe for `lights-out` Console

When placing the `lights-out` console behind Cloudflare Access and a private `cloudflared` tunnel, Cloudflare authenticates the user via your IdP and injects `Cf-Access-Authenticated-User-Email` into requests forwarded over the tunnel to `http://127.0.0.1:8098`.

## 1. Console Configuration

Start the console bound strictly to loopback so no traffic can bypass `cloudflared`:

```sh
CONSOLE_AUTH=proxy \
CONSOLE_IDENTITY_HEADER=Cf-Access-Authenticated-User-Email \
CONSOLE_LAUNCHERS=alice@example.com,bob@example.com \
CONSOLE_HOSTS=console.example.com \
CONSOLE_PUBLISH=127.0.0.1 \
CONSOLE_PORT=8098 \
./go console
```

## 2. Tunnel & Access Policy Checklist

- Bind `console/serve.py` to `127.0.0.1:8098` (do not expose port `8098` on public firewall rules).
- Configure Cloudflare Zero Trust Access application for `console.example.com` allowing only your team emails or OIDC groups.
- In `cloudflared` ingress rules, route `console.example.com` directly to `http://127.0.0.1:8098` without altering `Host` or `Origin` headers (or ensure `CONSOLE_HOSTS` lists `console.example.com`).
- Only users whose email matches `CONSOLE_LAUNCHERS` verbatim can approve `POST /v1/go-for-launch`; other authenticated Access users can read screens, check specs, answer open questions, and submit ticket/intake requests.

## 3. Production Readiness Verification

```sh
CONSOLE_AUTH=proxy \
CONSOLE_IDENTITY_HEADER=Cf-Access-Authenticated-User-Email \
CONSOLE_LAUNCHERS=alice@example.com \
./go production-readiness
```
