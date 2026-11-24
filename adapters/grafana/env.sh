#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# grafana env.sh: resolves endpoint URL (CHARTS_URL > GRAFANA_URL > METRICS_URL)
# and optional authentication header (GRAFANA_TOKEN or METRICS_AUTH_HEADER),
# passing credentials to curl exclusively via stdin (-H @-), never in argv.
set -eu

GRAFANA_BASE_URL=${CHARTS_URL:-${GRAFANA_URL:-${METRICS_URL:-}}}
GRAFANA_TOKEN=${GRAFANA_TOKEN:-}
METRICS_AUTH_HEADER=${METRICS_AUTH_HEADER:-}
READY_TIMEOUT=${READY_TIMEOUT:-60}

case "$GRAFANA_BASE_URL" in
  ''|replace-me)
    printf 'grafana: GRAFANA_URL (or METRICS_URL) is empty or placeholder\n' >&2
    exit 1 ;;
  http://*|https://*) ;;
  *)
    printf 'grafana: URL=%s must start with http:// or https://\n' "$GRAFANA_BASE_URL" >&2
    exit 1 ;;
esac
case "$GRAFANA_BASE_URL" in
  *://*@*)
    printf 'grafana: URL must not embed credentials\n' >&2
    exit 1 ;;
esac
GRAFANA_BASE_URL=${GRAFANA_BASE_URL%/}

if [ -n "$GRAFANA_TOKEN" ] && [ "$GRAFANA_TOKEN" != replace-me ]; then
  GRAFANA_AUTH_HEADER="Authorization: Bearer $GRAFANA_TOKEN"
elif [ -n "$METRICS_AUTH_HEADER" ]; then
  GRAFANA_AUTH_HEADER=$METRICS_AUTH_HEADER
else
  GRAFANA_AUTH_HEADER=""
fi

grafana_curl_get() {
  if [ -n "$GRAFANA_AUTH_HEADER" ]; then
    printf '%s\n' "$GRAFANA_AUTH_HEADER" | curl -fsS --connect-timeout 2 --max-time 5 -H @- "$@"
  else
    curl -fsS --connect-timeout 2 --max-time 5 "$@"
  fi
}
