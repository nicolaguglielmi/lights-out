# Brief for the dev-worker: the minimal-ts repository

What this repository tells the lights-out dev-worker (docs/adr/0037 §6,
docs/adr/0102): where its code is and how its tests run.

## Scope

Your entire task is inside `src/` and `test/`. Read only those paths, plus
the ticket. `.lights-out/` is the platform's contract: leave it alone.

## Tests

From the repository root, `npm ci && npm test`. Use exactly this command.
The suite runs natively on Node 24's test runner with no build step.
