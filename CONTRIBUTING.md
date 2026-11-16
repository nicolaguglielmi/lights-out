# Contributing to lights-out

Thanks for looking. lights-out is a harness people let agents loose
behind, so the bar for a change is that it can be checked by a machine.

Everyone in the project's spaces follows the
[Code of Conduct](CODE_OF_CONDUCT.md). Who decides, and how you become a
reviewer or a maintainer: [GOVERNANCE.md](GOVERNANCE.md) and
[MAINTAINERS.md](MAINTAINERS.md).

## Before you open a pull request

You need podman, git and a POSIX shell. Everything else runs in the
workbench container.

```sh
./go ci     # the repository's own checks
./go demo   # the golden rule: clone to automatic rollback, no account, no key
```

Both must be green. If your change touches a demo path, run that demo
too (`./go help` lists them).

## Sign your commits (DCO)

Every commit carries a `Signed-off-by:` line, which certifies the
[Developer Certificate of Origin](https://developercertificate.org/):
you wrote the change, or have the right to submit it under the
project's licence.

```sh
git commit -s
```

Use your own address, the one your GitHub account knows. A
`users.noreply.github.com` address needs its account id
(`<id>+<login>@users.noreply.github.com`): without the id it can belong to
someone else's account. `./go ci` checks every commit (`ci/authors.sh`).

There is no CLA. Contributions are licensed under Apache 2.0, like the
rest of the repository (its §5).

## The rules every change follows

- **The gate contract.** A gate prints exactly one line on stdout,
  `GO: <name>` or `NO-GO: <name> — <reason>`, and exits 0 or 1. Tool noise
  goes to stderr. No colour when stdout is not a TTY. Use `ci/lib.sh`;
  `ci/contract.sh` checks every gate.
- **Shell is the contract, Python is the logic.** Shell stays on the host
  (`./go`), in adapter verbs and in thin wrappers; a gate's wrapper keeps
  its guards and ends in `verdict core.gates.<x>`, and the rule lives in
  `core/gates/<x>.py` with `unittest` tests in `core/tests/`. A shell file
  in `core/` or `gates/` has at most 30 code lines; `ci/shell-boundary.sh`
  holds it ([ADR 0052](docs/adr/0052-where-shell-is-allowed.md),
  [ADR 0084](docs/adr/0084-gates-under-the-shell-boundary.md)).
- **No hidden mocks.** If something is simulated at a level, the README
  says so and the code fails with a clear message. Nothing pretends to work.
- **Tests come from the spec**, never from the implementation.
- **A structural choice gets an ADR** in `docs/adr/`: short, with the
  alternatives rejected and why. A numeric threshold in an ADR says where
  it comes from: a measurement, an external constraint, or a decision.
- **No secrets**, not even in examples. Only `.env.example` with fake values.
- **English** everywhere except `docs/PLAN.md`, the Italian working plan.

## Changes to the platform

A pull request that touches the platform (`gates/`, `ci/`, `verify/`,
`core/`, `policy/`, `roles/`, adapters, a workload's gate surface; the full
list is `RISK_HIGH_PLATFORM` in `policy/merge.env`) never merges on its
own. A human reviews it.

## Contributed adapters and gate packs

An adapter must pass the conformance suite for its port
(`./go delivery-conformance` for Delivery, ADR 0026). It also needs a
nightly run and a named maintainer. An adapter without both is a debt
for the project, not a gift, and will not be merged.

## Where to start

Issues labelled `good first issue` are small and come from real
findings. For anything larger, open an issue first: the plan is in
[ROADMAP.md](ROADMAP.md), and a short discussion saves you a rejected PR.
