# Brief for the dev-worker: minimal-java

Drafted by ./go init on 2026-11-24 (docs/adr/0042 §6) from what the repository
declares; edit it before committing. What this repository tells the
lights-out dev-worker (docs/adr/0037 §6): where its code is and how its
tests run.

## Scope

Your entire task is inside `src/main/` and `src/test/`. Read only those paths, plus the
ticket. `.lights-out/` is the platform's contract: leave it alone.

## Tests

From the repository root, `mvn test`. Use exactly this command.
