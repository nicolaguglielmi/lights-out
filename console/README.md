# console

The business client's view of lights-out ([ADR 0047](../docs/adr/0047-console-reads-one-versioned-logbook-and-writes-only-go-for-launch.md); every field, rule and route in [docs/plans/console.md](../docs/plans/console.md)). Five screens:

1. **This week's tickets**: each with GO/NO-GO, the judge that decided, and its line.
2. **One ticket's chain**, from the ticket to the rollback: spec, task (harness, provider, model, tokens), PR, gates, review, merge, release stages with their `slo:` numbers, verify, go-for-launch.
3. **Incidents and post mortems**, with their fix tickets and those tickets' states.
4. **Go-for-launch**: the release record, what the plane runs now, the brief, and the one button.
5. **Thresholds**: every number that can stop a change, where it is set, and its provenance ("undeclared" when it has none).

It reads **one document, the logbook** (`lights-out/logbook/1.0`, [schema](../core/logbook/1.0.schema.json)), which `./go logbook` writes. It never reads the tower, the plane or a file behind it. Its **one write is go-for-launch**, through the same `core/promote.sh` and the same checks as `./go promote`; the record then says who pressed and `via: console`. `ci/logbook.sh` checks both.

## Run it

```bash
./go up
```

```bash
./go console
```

Open http://localhost:8098. After `./go demo` it shows ticket 0001's chain to the rollback. To press the button, hold a canary first: `./go kill-switch`, then `./go release`, which holds the canary at 25% when verify passes (`git checkout app/flags/flags.json` undoes the kill switch afterwards). `CONSOLE_PORT` moves the port.

Without the plane, serve a logbook file instead; nothing can be pressed:

```bash
CONSOLE_SNAPSHOT=console/snapshot/logbook.json ./go console
```

`./go demo-console` is the acceptance: a held canary promoted by an HTTP POST to a running console, checked at 100% through Envoy, then un-promoted. It is not part of `./go demo`.

## Who may press (`CONSOLE_AUTH`)

The console has no login, no session and no password. It does not start unless the mode is declared:

| `CONSOLE_AUTH` | When | Who presses |
|---|---|---|
| `loopback` (default) | published on 127.0.0.1, ::1 or localhost only | anyone at that machine; recorded as `unauthenticated` |
| `proxy` | behind a proxy that authenticates, with `CONSOLE_IDENTITY_HEADER` naming the header it sets | the identities listed in `CONSOLE_LAUNCHERS` (comma-separated, compared verbatim); a request without the header gets 401 |
| `public` | only with `CONSOLE_SNAPSHOT=console/snapshot/logbook.json` | nobody |

The POST also needs `Content-Type: application/json`, an `Origin` and `Host` that are the console's own (`CONSOLE_HOSTS` adds host names a proxy uses), and a body that matches the record and the brief as they are now.

**`proxy` trusts a header.** The console cannot tell whether a request came through the proxy. Make it reachable from the proxy only (bind it to a private interface, a firewall rule, or the same pod), or anyone can claim any identity. Behind Google's Identity-Aware Proxy the header is `X-Goog-Authenticated-User-Email` and its value carries the `accounts.google.com:` prefix, which `CONSOLE_LAUNCHERS` must repeat. The first version does not verify IAP's signed `X-Goog-IAP-JWT-Assertion` (ADR 0047, provisional decision 8). oauth2-proxy's header names were not verified when this was written; check your proxy's documentation.

## What is real at L0

- **The record is real**: the tower's documents, the release log (`.crew/releases.jsonl`), launchpad's record and the policy files, as `./go logbook` reads them.
- **The tower is in memory at L0**: tickets from before the last `./go down` are not shown, and screen 1 says so. The mirror that fixes this (ADR 0031 §6) is not built yet.
- **The workers replayed**: at L0 a ticket's task shows the provider, model and tokens of the recorded run, marked "replayed"; no tokens were spent.
- **The brief at L0 is rendered from the record** by `core/promote.sh`, labelled as such; from L1 the release role writes it (ADR 0020 §4).
- **The button is real**: it runs `core/promote.sh`, which shifts the canary to 100% or rolls it back.

## Who built it

This first version was written by a Claude Code session on 2026-11-04, by hand, as plain HTML and a standard-library Python server, without Stitch and without a frontend worker: no key could be spent where it was built, and Stitch's export format is still unverified (ADR 0047, provisional decision 6). The plan is for Stitch to design the five screens into `design/` and a frontend worker to rebuild `index.html` from it, against the same logbook and the same ci check.
