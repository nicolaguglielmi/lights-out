#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# deploy.sh (gcp-cloud-deploy): creates a Cloud Deploy release staging <image>
# with <version> at canary weight 0.
set -eu
version=${1:?usage: deploy.sh <version> <image>}
image=${2:?usage: deploy.sh <version> <image>}
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli gcloud jq

case "$image" in
  *.*/*|localhost:*/*) full_image=$image ;;
  *) full_image="$GCP_AR_HOST/$GCP_PROJECT/$image" ;;
esac

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

cat > "$tmp/skaffold.yaml" <<EOF
apiVersion: skaffold/v4beta7
kind: Config
metadata:
  name: $CLOUD_RUN_SERVICE
manifests:
  rawYaml:
    - service.yaml
deploy:
  cloudrun: {}
EOF

cat > "$tmp/service.yaml" <<EOF
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: $CLOUD_RUN_SERVICE
  annotations:
    lights-out.version: "$version"
spec:
  template:
    metadata:
      labels:
        lights-out.version: "$version"
    spec:
      containers:
        - image: $full_image
          env:
            - name: APP_VERSION
              value: "$version"
EOF

rel_slug=$(printf '%s' "$version" | tr -cd 'a-zA-Z0-9-' | tr '[:upper:]' '[:lower:]' | cut -c1-16)
[ -n "$rel_slug" ] || rel_slug=canary
rel_name="rel-$(date -u +%Y%m%d%H%M%S)-$rel_slug"

gcloud deploy releases create "$rel_name" \
  --delivery-pipeline="$CLOUD_DEPLOY_PIPELINE" \
  --region="$GCP_REGION" \
  --project="$GCP_PROJECT" \
  --source="$tmp" \
  --images="app=$full_image" \
  --deploy-parameters="lights-out-version=$version" \
  --annotations="lights-out.version=$version" \
  --quiet --format=json >/dev/null

rollouts=$(gcloud deploy rollouts list \
  --delivery-pipeline="$CLOUD_DEPLOY_PIPELINE" \
  --release="$rel_name" \
  --region="$GCP_REGION" \
  --project="$GCP_PROJECT" \
  --format=json)
[ "$(printf '%s' "$rollouts" | jq -r 'length')" -gt 0 ] || {
  printf 'deploy-gcp-cloud-deploy: release %s created no rollout\n' "$rel_name" >&2
  exit 1
}
