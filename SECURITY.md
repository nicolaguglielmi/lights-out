# Security policy

## Reporting a vulnerability

There is one channel, GitHub's private vulnerability reporting: the
repository's **Security** tab, then **Report a vulnerability**. Please
do not put the details in a public issue. The project publishes no email
address.

Until that button is there (private reporting is being turned on), open
an issue titled **"security contact request"** with no details in it,
only a request for a private channel. The maintainer opens a draft
security advisory, which only you and the maintainers can read, adds you
to it, and you report there.

## Who answers, and when

The maintainers listed in [MAINTAINERS.md](MAINTAINERS.md) handle every
report; today that is one person, in their spare time.

- **First response within 14 days** of the report: that it was received,
  and whether it is in scope.
- Then an assessment and, for a confirmed vulnerability, a fix plan with
  a date. You are told before anything about it is published.
- The fix is released, and the advisory published with credit to you
  unless you prefer otherwise.

Only the latest release and the latest commit on `main` are supported.

## What to report privately

- A way for a ticket, a pull request, a fixture, an attached repository's
  `.lights-out/` or an agent's output to run code on the host outside the
  toolchain container, or to read `.env`.
- A way for content an agent or a contributor controls to merge a change,
  or send it traffic, without the gates and the policy of the base branch
  judging it.
- A secret reaching a log, a recording under `fixtures/`, or the audit
  chain.

An agent walking around a gate in a way that needs a human to help it is
still worth knowing about: open a public issue with the **gate bypass**
template.

## Known and documented, not vulnerabilities

- For plane commands, `./go` hands its toolchain container your engine's
  socket, which means full control of the engine (README, "What is real
  and what is recorded"). Scripts in an attached repository's
  `.lights-out/` run there too (docs/use-on-your-repo.md).
- The local plane is for a development machine. Its services (Gitea, Zot,
  Prometheus, Envoy, flagd and the app) use development credentials and
  publish their ports on every interface of the host (`compose.yaml`).
  Run it on a network you trust, and never expose it.
