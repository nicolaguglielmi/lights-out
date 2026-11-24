# Brief for the dev-worker: the minimal-go repository

What this repository tells the lights-out dev-worker (docs/adr/0037 §6,
docs/adr/0102): where its code is and how its tests run.

## Scope

Your entire task is inside `pkg/` and `cmd/`. Read only those paths, plus
the ticket. `.lights-out/` is the platform's contract: leave it alone.

## Tests

From the repository root, `go test ./...`. Use exactly this command.
The suite uses Go's standard testing package and needs no external service.
