#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-oci-registry: polls the OCI Distribution API v2 root at
# $OCI_REGISTRY_URL/v2/ (handling Bearer realm auth if challenged). Silent
# on success, one stderr line and exit 1 on failure or timeout.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/oci-registry/env.sh
. "$here/env.sh"

oci_auth || exit 1

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

i=0
while [ "$i" -lt "$READY_TIMEOUT" ]; do
  code=$(oci_curl GET "$OCI_REGISTRY_URL/v2/" "$tmp/body" "$tmp/hdrs")
  if [ "$code" = "200" ]; then
    exit 0
  fi
  if [ "$code" = "401" ] && [ -z "$_OCI_BEARER_TOKEN" ] && grep -qi '^docker-distribution-api-version:' "$tmp/hdrs"; then
    exit 0
  fi
  i=$((i + 1))
  sleep 1
done

printf 'ready: oci-registry not ready after %ss: GET %s/v2/ (last HTTP %s)\n' "$READY_TIMEOUT" "$OCI_REGISTRY_URL" "${code:-000}" >&2
exit 1
