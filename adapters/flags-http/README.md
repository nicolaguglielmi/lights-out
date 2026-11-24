# Generic OFREP HTTP Flags Adapter (`FLAGS=flags-http`)

Generic OpenFeature Remote Evaluation Protocol (OFREP) HTTP client for remote feature-flag services.

> Tested locally in CI against a Python HTTP OFREP test server (`ci/tests/test_oci_registry_and_flags.py`) and verified by `CONFORMANCE=1 FLAGS=flags-http sh gates/flags-conformance.sh`; live remote OFREP services require your own `FLAGS_URL` and credentials.

## Required configuration

- `FLAGS_URL`: base `http://` or `https://` URL of the OFREP service (no embedded userinfo in URL; calls `POST $FLAGS_URL/ofrep/v1/evaluate/flags/<flag>`).

## Optional configuration

- `FLAGS_AUTH_HEADER`: single HTTP header line (for example `Authorization: Bearer <token>`), passed via a `0600` temporary header file (`curl -H @...`) and deleted immediately — never placed in `curl` `argv` or printed.
- `FLAGS_SET_URL`: optional HTTP control endpoint accepting `POST` `{"flag": ..., "variant": ..., "defaultVariant": ...}` when mutations are supported over HTTP.
- `READY_TIMEOUT`: readiness poll ceiling in seconds (default `60`).

## Fail-closed guarantees

- `evaluate.sh` and `ready.sh` exit `1` with empty stdout on HTTP errors, malformed JSON, or missing/`null` `.value`.
- `set.sh` fails closed when `FLAGS_SET_URL` is unset (unless `FLAGS_HTTP_WRITE_FILE=1` is explicitly enabled for a file-backed local server).
