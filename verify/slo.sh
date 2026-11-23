#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# verify-slo: the verify runner (docs/adr/0013 §4, §5; amends docs/adr/0011).
# core/launchpad.sh calls this once per shifted stage. NO-GO instantly, one
# line, no network call first, if CANARY_VERSION or STABLE_VERSION is
# unset — exactly what a cold ci/contract.sh run sees, and what every real
# pre-deploy run hits before the canary has taken any traffic. Otherwise:
# run the three named queries in verify/slo.promql through METRICS_QUERY,
# and compare canary against stable, version by version:
#   - traffic first (closes the docs/adr/0011 "empty numerator" bug): a
#     version absent from the map, or present as 0, is NO-GO "no data",
#     checked before error_rate or latency_p95 is even looked at;
#   - error_rate: with both traffic values confirmed nonzero, a version
#     absent from the map has zero errors (an empty numerator selector is
#     an empty PromQL vector, not a zero — this gate supplies the zero);
#   - latency_p95: same lookups, but absent despite confirmed traffic is a
#     genuine anomaly and stays NO-GO "no data" (bucket data should exist
#     wherever traffic exists).
# Stderr always carries the numbers observed for all three queries, both
# versions, regardless of GO or NO-GO; core/launchpad.sh does not capture
# or re-print this gate's stderr, so it passes straight through.
set -eu
name=slo
. ci/lib.sh

# (a) No network call before this: it is what ci/contract.sh's cold scan
# sees, and it must stay one line, fast, and answerable with no plane.
canary_version=${CANARY_VERSION:-}
stable_version=${STABLE_VERSION:-}
if [ -z "$canary_version" ] || [ -z "$stable_version" ]; then
  nogo "CANARY_VERSION and STABLE_VERSION must both be set"
fi

: "${METRICS_QUERY:=adapters/${METRICS:-prometheus}/query.sh}"

# The two deltas, compared as plain deltas (canary - stable), not ratios:
# a ratio would divide by a stable error rate of zero. Their defaults and
# provenance are in policy/merge.env since 2026-11-03; a workload's
# policy.env may override them (docs/adr/0037 §5).
policy=$(mktemp)
trap 'rm -f "$policy"' EXIT
effective_policy "$policy"
ERROR_RATE_MAX_DELTA=$(sed -n 's/^ERROR_RATE_MAX_DELTA=//p' "$policy")
LATENCY_P95_MAX_DELTA=$(sed -n 's/^LATENCY_P95_MAX_DELTA=//p' "$policy")

if [ -n "${SLO_PROMQL:-}" ]; then
  promql=$SLO_PROMQL
elif [ -n "${DELIVERY:-}" ] && [ -f "adapters/$DELIVERY/slo.promql" ]; then
  promql="adapters/$DELIVERY/slo.promql"
else
  promql=verify/slo.promql
fi
[ -f "$promql" ] || nogo "$promql not found"

reason=$(python3 - "$promql" "$METRICS_QUERY" "$canary_version" "$stable_version" \
    "$ERROR_RATE_MAX_DELTA" "$LATENCY_P95_MAX_DELTA" <<'PY'
import json
import subprocess
import sys


def fail(message: str) -> None:
    print(message)
    sys.exit(1)


try:
    (promql_path, metrics_query, canary_version, stable_version,
     error_rate_max_delta_s, latency_p95_max_delta_s) = sys.argv[1:7]
    error_rate_max_delta = float(error_rate_max_delta_s)
    latency_p95_max_delta = float(latency_p95_max_delta_s)

    # Same three-block format verify/metric-shape.sh parses: blocks
    # separated by a blank line, each "# name: <id>" then one query line.
    # Keep the two parsers in agreement if you touch either.
    with open(promql_path) as f:
        promql_text = f.read()

    blocks = [b for b in promql_text.split("\n\n") if b.strip()]
    queries: dict[str, str] = {}
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if len(lines) != 2 or not lines[0].startswith("# name:"):
            fail(f"malformed block in {promql_path}: {block!r}")
        qname = lines[0].split("# name:", 1)[1].strip()
        qtext = lines[1].strip()
        if not qtext:
            fail(f"query {qname} has no query line")
        queries[qname] = qtext

    needed = ["traffic", "error_rate", "latency_p95"]
    missing = [n for n in needed if n not in queries]
    if missing:
        fail(f"{promql_path} is missing quer{'y' if len(missing) == 1 else 'ies'}: {missing}")

    def run_query(qname: str) -> dict[str, float]:
        proc = subprocess.run(
            ["sh", metrics_query, queries[qname]],
            capture_output=True, text=True,
        )
        # A query that cannot be answered is not a verdict on the canary:
        # `cannot judge`, then the launchpad rolls back all the same
        # (docs/adr/0066 §3; ADR 0013: a canary that cannot be judged does
        # not go forward).
        if proc.returncode != 0:
            fail(f"cannot judge: {qname}: {metrics_query} exited {proc.returncode}: {proc.stderr.strip()}")
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            fail(f"cannot judge: {qname}: answer is not JSON")
        if not isinstance(payload, dict):
            fail(f"cannot judge: {qname}: answer is not JSON")
        if payload.get("status") != "success":
            fail(
                f"cannot judge: {qname}: status {payload.get('status')}: "
                f"{payload.get('errorType')}: {payload.get('error')}"
            )
        result: dict[str, float] = {}
        for item in payload.get("data", {}).get("result", []):
            metric = item.get("metric", {})
            version = metric.get("service_version")
            if version is None and "envoy_cluster_name" in metric:
                cluster = metric["envoy_cluster_name"]
                if cluster == "canary":
                    version = sys.argv[3]
                elif cluster == "stable":
                    version = sys.argv[4]
            if version is None:
                continue
            try:
                result[version] = float(item["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        return result

    values = {qname: run_query(qname) for qname in needed}

    def fmt(v):
        return "no data" if v is None else f"{v:.3f}"

    # Numbers for all three queries, both versions, on stderr — regardless
    # of what the decision below turns out to be.
    for qname in needed:
        m = values[qname]
        print(
            f"slo: {qname} canary({canary_version})={fmt(m.get(canary_version))} "
            f"stable({stable_version})={fmt(m.get(stable_version))}",
            file=sys.stderr,
        )

    if canary_version == stable_version:
        # Both lookups would read the same series and every delta would be 0.
        fail(f"canary and stable are the same version ({canary_version}); nothing to compare")

    traffic = values["traffic"]
    if not traffic.get(canary_version):
        fail(f"no traffic from canary({canary_version}) in the last window")
    if not traffic.get(stable_version):
        fail(f"no traffic from stable({stable_version}) in the last window")

    error_rate = values["error_rate"]
    canary_err = error_rate.get(canary_version, 0.0)
    stable_err = error_rate.get(stable_version, 0.0)
    delta_err = canary_err - stable_err
    if delta_err > error_rate_max_delta:
        fail(
            f"error_rate canary({canary_version})={canary_err:.3f} "
            f"stable({stable_version})={stable_err:.3f} "
            f"delta={delta_err:.3f} max={error_rate_max_delta:.3f}"
        )

    latency = values["latency_p95"]
    canary_lat = latency.get(canary_version)
    stable_lat = latency.get(stable_version)
    if canary_lat is None:
        fail(f"no latency_p95 data for canary({canary_version}) despite nonzero traffic")
    if stable_lat is None:
        fail(f"no latency_p95 data for stable({stable_version}) despite nonzero traffic")
    delta_lat = canary_lat - stable_lat
    if delta_lat > latency_p95_max_delta:
        fail(
            f"latency_p95 canary({canary_version})={canary_lat:.3f} "
            f"stable({stable_version})={stable_lat:.3f} "
            f"delta={delta_lat:.3f} max={latency_p95_max_delta:.3f}"
        )
except SystemExit:
    raise
except Exception as exc:  # keep the contract: one stdout line, always
    fail(f"unexpected error evaluating slo.promql: {exc}")
PY
) && ok=1 || ok=0

if [ "$ok" -ne 1 ]; then
  nogo "$reason"
fi

go
