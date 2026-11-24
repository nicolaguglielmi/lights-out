#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
: "${PLANE_HOST:=host.containers.internal}"
: "${APP_URL:=http://$PLANE_HOST:8080}"

while true; do
  curl -s -o /dev/null --connect-timeout 1 --max-time 3 "$APP_URL/" || true
  sleep 0.2
done
