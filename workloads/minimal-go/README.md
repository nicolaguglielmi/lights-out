# workloads/minimal-go

The Go example service workload (docs/adr/0102): a standard-library Go 1.24
module with a `Containerfile`, library helpers in `pkg/calc/`, HTTP service in
`cmd/server/`, `go.mod` and `go.sum`, and `.lights-out/` (`workload.env`,
`compose.yaml`, `flags/flags.json`, `smoke.sh`, `load.sh`, `worker.md`,
`policy.env`, `runners/`). `.lights-out/runners/` is byte for byte
`stacks/go/runners/`.
