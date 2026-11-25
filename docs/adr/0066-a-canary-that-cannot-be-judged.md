# ADR 0066 — A canary that cannot be judged says so: the canary deploy leaves its dependencies alone, `slo.promql` collapses `target_info` before the join, a failed Metrics query is `cannot judge` (rolled back, never a verdict), `load.sh` fails loudly, and the demo accepts only the kill switch's error-rate verdict

*Numbered 0063 on its branch, `claude/sharp-khayyam-dcb1de`; renumbered 0066 at the merge, because the console's ADR on another branch took 0063 too.*

- Status: accepted (amends ADR 0013 §2 [query text; `metric-shape` skips `job`, `instance` in `by (...)`], §4 [`deploy.sh` with `--no-deps`; `slo.sh`'s query errors; `demo-rollback`'s assertion]; ADR 0044 §8 [a Metrics query that errors is `cannot judge`, for `./go verify` too], §9 [`ci/metrics.sh` gains the error cases]; ADR 0030's Workload contract [`load.sh` exits non-zero when it cannot set up])
- Date: 2026-11-09
- Milestone: M34 follow-up

## Context

ADR 0062 §6: the stranger job's first attempt on 877e7a2 ended `NO-GO: e2e
— ticket 0001 ended at held`. Two instrumented runs on GitHub Actions
(ubuntu-latest, podman 4.9.3) found why. repro-canary, run 42596034173: ten
runners, each `./go demo` then `./go e2e` twice; of its 40 releases, 30
rolled back on `traffic: ... 422`, 6 on the real error-rate verdict, and 4
were held (r4, r5, r7, r9, each the first `e2e` after `demo`).
repro-compose, run 42600310185: a four-service toy project, compose 5.5.1.

- **Root.** On podman 4.9.3 compose recreates every container on an
  unchanged `up -d`, and `--profile canary up -d canary` recreates its
  dependencies too; `up -d --no-deps canary` touches only the canary. The
  compat API reports `NetworkID` as the network's name, not its id;
  config-hash and image are unchanged. Podman 6.1.4 (macOS machine) and
  docker do not recreate. Ubuntu 24.04 packages podman 4.9.3: strangers on
  the README's Linux path have it.
- **The 422.** Prometheus's TSDB survives the recreate (an anonymous
  volume, carried over). The old backend and the old Prometheus stop in
  the same second, so no staleness marker is written; the old
  `target_info{instance="backend:8000"}` (another `service_instance_id`)
  stays visible for the 5 min lookback beside the new one. Every query of
  `slo.promql` then fails with "found duplicate series" (HTTP 422), and
  `slo.sh` reports it as `NO-GO: slo — traffic: adapters/prometheus/query.sh
  exited 22: ...`, which `demo-rollback` (`"NO-GO: slo — "*`) and `e2e`
  (ticket state only) accept as a rollback.
- **The hold.** The canary deploy recreates postgres, flagd, geocoder under
  the running stable, which answers 500 for about 30 s. `app/load.sh`
  starts in that second; in the holds read (r4, r5, r7) its `POST
  /api/meetups` got a 500, `id=$(curl -fsS ... | jq -r .id)` took jq's
  status, `id` was empty, and the load looped `POST
  /api/meetups//registrations`: 405 from both versions for the whole
  release. No 5xx → `error_rate canary=no data`, read as 0 → GO at 5 % and
  25 % → held. flagd answered `on` for the canary in every sample of the
  held windows: not the cause.

ADR 0013's rule stands: a canary that cannot be judged does not go forward.

## Decision

1. **The canary deploy is `docker-compose --profile canary up -d --no-deps
   backend-canary`** in `adapters/envoy-canary/deploy.sh`. The plane is
   `./go up`'s job; deploy touches the canary only. A dependency that is not
   running: postgres down fails `/healthz` (it runs `SELECT 1`), the
   readiness wait times out, `NO-GO: launchpad — staging: deploy did not
   become healthy → rolled back`; flagd down is refused before staging by
   the freeze check (`cannot read release-freeze from the Flags port`).
2. **`slo.promql` collapses `target_info` before the join** (a): in all three
   queries `group_left (service_version) target_info` becomes
   `group_left (service_version) max by (job, instance, service_version)
   (target_info)`. It holds against any Prometheus-compatible endpoint (ADR
   0044's `METRICS_URL`), not only the L0 plane; two `service_version`s on
   one target stay a "duplicate series" error, now `cannot judge` (§3).
   `verify/metric-shape.sh` skips `job` and `instance` inside `by (...)`:
   target labels Prometheus stamps at scrape, never in `/metrics`, the
   reason ADR 0013 §2 already skips `on (...)`. **`./go up` recreating every
   container on podman 4.9.3 stays**: an engine bug, reported in the README's
   troubleshooting, not worked around here.
3. **A failed Metrics query is `cannot judge`, not a verdict.** In
   `verify/slo.sh` all three queries run before any comparison (as today);
   the first that fails ends the gate with
   `NO-GO: slo — cannot judge: <query>: <cause>`, exit 1, where `<cause>` is
   `<query.sh> exited <n>: <its stderr>` (curl failure, HTTP error under
   `curl -f`), `answer is not JSON`, or `status <status>: <errorType>:
   <error>`. Example: `NO-GO: slo — cannot judge: traffic:
   adapters/prometheus/query.sh exited 22: curl: (22) The requested URL
   returned error: 422`. `core/launchpad.sh` is unchanged: it rolls back and
   prints `NO-GO: launchpad — 5%: cannot judge: traffic: ... → rolled back`.
   The record: `verdict: rolled-back`, `stages[-1].verdict` the slo line,
   `numbers` empty; the tower's ticket ends `rolled-back`, true of the plane.
   No retry: the 422 lasts the 5 min lookback (Prometheus default, external
   constraint), longer than the 60 s stage, and no other failure has a
   measured duration. **"No traffic from canary" stays a verdict**: the query
   answered, and at L0 a canary that cannot serve emits no app metrics, so
   absence is the only evidence of it.
4. **`load.sh` fails loudly.** Each setup call keeps its body and status:
   `body=$(curl -fsS ...)` then `jq -er`; any failure prints one stderr line
   `app-load: setup failed at <METHOD> <path>: <cause>; generating no load`
   and exits 1. No retry: the 500 window came from §1's recreate, now gone.
   The release then shows that line, and at 5 % `NO-GO: launchpad — 5%: no
   traffic from canary(<v>) in the last window → rolled back`, which §5
   turns into a red demo. `workloads/minimal`'s `load.sh` has no setup.
5. **The demo accepts only the kill switch's error-rate verdict.**
   `demo-rollback` accepts `"NO-GO: slo — error_rate "*` (as `demo-seam`
   already does) and otherwise prints `NO-GO: demo-rollback — the canary
   was rolled back for [<verdict>], want the kill switch's error_rate
   verdict`. `e2e`, after the ticket state, reads `stages[-1].verdict` from
   `RELEASE_RECORD` and prints `NO-GO: e2e — ticket <id> rolled back for
   [<verdict>], want the kill switch's error_rate verdict`. ADR 0030's
   contract already makes a kill switch answer 5xx; `workloads/minimal`'s
   `outage` does (run 42588292173: `NO-GO: launchpad — 5%: error_rate
   canary(0.2.0-outage)=1.000 ...`).

## Alternatives rejected

- **§1, a deploy that waits for dependencies after recreating them**: the
  stable still serves 500s meanwhile. **Requiring a newer podman**: the
  README's Linux path is a distribution package.
- **§2 (b), Prometheus on tmpfs or without the anonymous volume**: fixes
  the L0 plane only; any endpoint keeping a stale series breaks the judge.
- **§2 (c), stop compose recreating on podman 4.9.3**: an engine bug
  outside this repo; every workaround is per engine version.
- **§2, `max without (service_instance_id)`**: names one OTel attribute;
  any other per-process resource attribute brings the duplicate back.
- **§3, a distinct exit code or a new outcome (`unjudged`)**: the gate
  contract is exit 0/1, and a new outcome ripples into the tower's states,
  `promote` and the logbook for something the line already says.
- **§3, `curl --fail-with-body` to quote Prometheus's error**: a second
  failure shape for every Metrics adapter; `./go verify` reproduces it.
- **§3, "no traffic" as `cannot judge`**: hides a dead canary behind the
  wording of a metrics outage.
- **§4, the Makefile watching `load.sh` and stopping the release**: saves
  one 60 s stage, adds a process supervisor to a Makefile target.
- **§5, accepting any `NO-GO: slo — ` but `cannot judge`**: a 405 load or a
  missing series would still pass as the demo's proof.

## Consequences

- **The one that will hurt:** a Prometheus hiccup now rolls back a good
  canary with no retry. Safe, but a flaky metrics backend is a flaky
  release, and only a measured outage duration can justify a retry.
- On podman 4.9.3 every `./go up` still recreates the plane: 11 to 19 s
  of recreating per `up` in run 42596034173 (r2), and Prometheus's WAL
  grows per recreate. `up` waits for every adapter to be ready, so no
  release starts inside it; the old `target_info` it leaves is what §2
  collapses.
- A workload whose kill switch fails by latency fails `demo-rollback`, as
  it already failed `demo-seam`.
- `metric-shape` gains a second hard-coded label exception, and its parser
  and `slo.sh`'s must still agree on the new query text.
- Docs: `docs/reference.md` "Release controller" (a `cannot judge` also
  rolls back; `demo-rollback` expects the `error_rate` NO-GO at 5 %) and
  `docs/ARCHITECTURE.md` ("automatic rollback on verify NO-GO" → "on a
  verify NO-GO or a canary that cannot be judged"); the README's podman
  troubleshooting names 4.9.3's recreate. `ci/metrics.sh` gains canned
  cases: exit 22, a non-JSON body, `status: error`, each `cannot judge`.

## Golden-rule check

No image, dependency, key or account. A stranger on Ubuntu's podman 4.9.3
gets the rollback for the right reason, or a red line that names why,
instead of a green `e2e` on a 422 or a held canary. `./go demo` must stay GO
under 900 s on both Actions jobs; measured at close, written here.
