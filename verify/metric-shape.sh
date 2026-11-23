#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# verify-metric-shape: checks the active slo.promql against one live scrape
# (ADR 0098 amends ADR 0011). On the proxy path (verify/slo.promql) it reads
# Envoy admin /stats/prometheus; on adapters with target_info (Argo Rollouts /
# Flagger) it reads $APP_URL/metrics. It does not evaluate rate() or
# histogram_quantile() — that needs Prometheus (M2/M3).
set -eu
name=metric-shape
. ci/lib.sh

: "${PLANE_HOST:=host.containers.internal}"
: "${APP_URL:=http://$PLANE_HOST:8080}"
: "${ENVOY_ADMIN_URL:=http://$PLANE_HOST:${DELIVERY_ADMIN_PORT:-9901}}"
[ -n "${WORKLOAD_SAMPLE_PATH:-}" ] || nogo "WORKLOAD_SAMPLE_PATH is empty"

if [ -n "${SLO_PROMQL:-}" ]; then
  promql=$SLO_PROMQL
elif [ -n "${DELIVERY:-}" ] && [ -f "adapters/$DELIVERY/slo.promql" ]; then
  promql="adapters/$DELIVERY/slo.promql"
else
  promql=verify/slo.promql
fi
[ -f "$promql" ] || nogo "$promql not found"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

if ! curl -fsS --connect-timeout 1 --max-time 3 "$APP_URL$WORKLOAD_SAMPLE_PATH" >/dev/null 2>"$tmp/sample.err"; then
  cat "$tmp/sample.err" >&2
  nogo "app not reachable at $APP_URL; run ./go up"
fi

if grep -qE '(^|[^[:alnum:]_])target_info([^[:alnum:]_]|$)' "$promql"; then
  scrape_url=${METRICS_SCRAPE_URL:-$APP_URL/metrics}
else
  scrape_url=${METRICS_SCRAPE_URL:-$ENVOY_ADMIN_URL/stats/prometheus}
fi

if ! curl -fsS --connect-timeout 1 --max-time 3 "$scrape_url" >"$tmp/metrics.txt" 2>"$tmp/metrics.err"; then
  cat "$tmp/metrics.err" >&2
  nogo "metrics endpoint not reachable at $scrape_url; run ./go up"
fi

reason=$(python3 - "$promql" "$tmp/metrics.txt" <<'PY'
import re
import sys


def fail(message: str) -> None:
    print(message)
    sys.exit(1)


try:
    promql_path, metrics_path = sys.argv[1], sys.argv[2]

    with open(promql_path, encoding="utf-8") as f:
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

    expected_names = {"traffic", "error_rate", "latency_p95"}
    if set(queries) != expected_names:
        fail(
            f"{promql_path} must define exactly {sorted(expected_names)}, "
            f"found {sorted(queries)}"
        )

    TARGET_INFO = "target_info"
    metric_re = re.compile(r"\b([a-zA-Z_:][a-zA-Z0-9_:]*)\s*(?=[{\[])")
    selector_re = re.compile(r"\b([a-zA-Z_:][a-zA-Z0-9_:]*)\s*\{([^}]*)\}")
    label_re = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=~?")
    by_re = re.compile(r"by\s*\(([^)]*)\)")
    target_info_re = re.compile(r"\btarget_info\b")

    with open(metrics_path, encoding="utf-8") as f:
        metrics_text = f.read()
    metrics_lines = metrics_text.splitlines()

    def family_lines(metric: str) -> list[str]:
        pat = re.compile(r"^" + re.escape(metric) + r"(\{|\s)")
        return [ln for ln in metrics_lines if pat.match(ln)]

    any_target_info = any(target_info_re.search(qt) for qt in queries.values())

    for qname, qtext in queries.items():
        q_metrics = set(metric_re.findall(qtext))
        if not q_metrics:
            fail(f"query {qname} references no metric")
        references_target_info = bool(target_info_re.search(qtext))
        if references_target_info and not any(m.startswith("http_") for m in q_metrics):
            fail(f"query {qname} references no http_-prefixed metric")

        by_labels: set[str] = set()
        for group in by_re.findall(qtext):
            by_labels |= {g.strip() for g in group.split(",") if g.strip()}
        if references_target_info:
            by_labels -= {"job", "instance"}

        if "service_version" in by_labels:
            by_labels -= {"service_version"}
            if not references_target_info:
                fail(f"query {qname} asserts service_version without referencing target_info")
            flines = family_lines(TARGET_INFO)
            if not flines:
                fail(f"metric {TARGET_INFO} (from query {qname}) not found in scrape")
            if not any('service_version="' in ln for ln in flines):
                fail(
                    f"label service_version not found on metric {TARGET_INFO} "
                    f"(from query {qname}) in scrape"
                )
        elif references_target_info and not family_lines(TARGET_INFO):
            fail(f"metric {TARGET_INFO} (from query {qname}) not found in scrape")

        sel_by_metric: dict[str, set[str]] = {}
        for m, body in selector_re.findall(qtext):
            sel_by_metric.setdefault(m, set()).update(label_re.findall(body))

        for metric in q_metrics:
            flines = family_lines(metric)
            if not flines:
                fail(f"metric {metric} (from query {qname}) not found in scrape")
            expected_labels = (
                (by_labels - (set() if metric.endswith("_bucket") else {"le"}))
                | sel_by_metric.get(metric, set())
            )
            for label in sorted(expected_labels):
                if not any(f'{label}="' in ln for ln in flines):
                    fail(
                        f"label {label} not found on metric {metric} "
                        f"(from query {qname}) in scrape"
                    )

    if any_target_info:
        for marker in ('le="0.005"', 'le="+Inf"'):
            if marker not in metrics_text:
                fail(f"missing histogram bucket boundary {marker} in scrape")
        if 'le="10"' not in metrics_text and 'le="10.0"' not in metrics_text:
            fail('missing histogram bucket boundary le="10" (or "10.0") in scrape')

        status_re = re.compile(r'http_response_status_code="([^"]*)"')
        statuses = set(status_re.findall(metrics_text))
        if not statuses:
            fail("http_response_status_code label not observed in scrape")
        bad = sorted(s for s in statuses if not re.fullmatch(r"[1-5][0-9][0-9]", s))
        if bad:
            fail(f"http_response_status_code value(s) not well-formed: {bad}")
    else:
        bucket_lines = family_lines("envoy_cluster_upstream_rq_time_bucket")
        bucket_text = "\n".join(bucket_lines)
        for marker in ('le="0.5"', 'le="3600000"', 'le="+Inf"'):
            if marker not in bucket_text:
                fail(f"missing Envoy millisecond histogram bucket boundary {marker} in scrape")
        xx_text = "\n".join(family_lines("envoy_cluster_upstream_rq_xx"))
        classes = set(re.findall(r'envoy_response_code_class="([^"]*)"', xx_text))
        if not classes:
            fail("envoy_response_code_class label not observed on envoy_cluster_upstream_rq_xx")
        bad = sorted(c for c in classes if not re.fullmatch(r"[1-5]", c))
        if bad:
            fail(f"envoy_response_code_class value(s) not well-formed: {bad}")
except SystemExit:
    raise
except Exception as exc:  # keep the contract: one stdout line, always
    fail(f"unexpected error checking {sys.argv[1]} against {sys.argv[2]}: {exc}")
PY
) && ok=1 || ok=0

if [ "$ok" -ne 1 ]; then
  nogo "$reason"
fi

if [ -z "${WORKLOAD_KILL_SWITCH:-}" ]; then
  printf 'metric-shape: WORKLOAD_KILL_SWITCH is empty — rollback returns traffic to stable only\n' >&2
fi
printf 'metric-shape: checked query/scrape agreement against one live scrape (metric and label names, histogram buckets, status/class format)\n' >&2
printf 'metric-shape: did not evaluate rate() or histogram_quantile() — needs a TSDB (Prometheus at M2, canary vs stable comparison at M3)\n' >&2
go
