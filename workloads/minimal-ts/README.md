# workloads/minimal-ts

The TypeScript gates-only example (docs/adr/0102): a Node 24 TypeScript
repository laid out as a standalone project would be, with typed code in
`src/`, tests run natively by `node --test` in `test/`, and the gates half
only in `.lights-out/` (`workload.env`, `worker.md`, `policy.env`, `runners/`).
No compose fragment, no flag file, nothing to release.
`.lights-out/runners/` is byte for byte `stacks/typescript/runners/`.
