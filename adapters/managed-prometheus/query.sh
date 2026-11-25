#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# query-managed-prometheus: executes one instant PromQL query against Google
# Cloud Managed Service for Prometheus (/api/v1/query) with stdin -H @- auth
# and prints raw /api/v1/query JSON on stdout (matching adapters/promql/query.sh).
set -eu
query=${1:?usage: query.sh '<promql>'}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/managed-prometheus/lib.sh
. "$here/lib.sh"

mp_resolve_auth || exit 1
mp_query_raw "$query"
