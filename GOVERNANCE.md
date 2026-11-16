# Governance

## Who decides today

lights-out has one maintainer, Nicola ([MAINTAINERS.md](MAINTAINERS.md)),
and today Nicola decides: what merges, what is released, which ADRs are
accepted, and who becomes a reviewer or a maintainer. This is how a
one-person project works, and it is written here so that nobody has to
guess.

What does not depend on one person's judgment is already checked by a
machine. Every change passes `./go ci` and the project's own gates, and a
pull request that touches the platform never merges on its own: a human
reviews it ([CONTRIBUTING.md](CONTRIBUTING.md)). A structural choice is an
ADR in [docs/adr/](docs/adr/), with the alternatives rejected and why.

## With two or more maintainers

From the day [MAINTAINERS.md](MAINTAINERS.md) lists a second maintainer:

- **A pull request** merges with the approval of a maintainer who is not
  its author, on top of the checks. A platform change needs that approval
  even when its author is a maintainer.
- **Anything else** (an ADR, a release, a change of direction) is
  proposed in an issue, a discussion or a pull request, and is decided by
  lazy consensus: it is accepted if no maintainer objects within seven
  days.
- **An objection** is discussed where it was raised. If the maintainers
  still disagree after the discussion, they vote there: a simple majority
  of the maintainers decides, and a tie keeps things as they are. The vote
  and its outcome stay on the issue or pull request.
- **Adding or removing a maintainer or a reviewer** is a vote of the
  maintainers, majority of those listed, without the person concerned.

## No model vendor directs the project

The direction of lights-out does not depend on any model vendor. Every
model provider and coding harness sits behind the same port and is judged
by the same gates ([ADR 0040](docs/adr/0040-the-model-is-a-choice.md),
[ADR 0041](docs/adr/0041-claude-code-and-codex-harnesses.md)), and the demo
runs with no key from anyone. Support for a provider is added or removed
on the same technical grounds for every provider. No vendor holds a seat,
a vote or a veto here.

## Changing this document

By a pull request to this file. Today the maintainer decides, as for any
other change. With two or more maintainers, a change needs the approval
of two thirds of them and stays open for at least fourteen days.
