#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# status.sh (aws-ecs-codedeploy): emits exact 3-key JSON {stable, canary, weight}.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli aws jq

svc_json=$(aws ecs describe-services --region "$AWS_REGION" --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" --output json)
stable_td=$(printf '%s' "$svc_json" | jq -r '([.services[0].taskSets[]? | select(.status == "PRIMARY") | .taskDefinition] | first) // .services[0].taskDefinition // empty')
[ -n "$stable_td" ] || {
  printf 'status-aws-ecs-codedeploy: no stable task definition found\n' >&2
  exit 1
}
stable_ver=$(resolve_td_version "$stable_td") || {
  printf 'status-aws-ecs-codedeploy: could not resolve version for %s\n' "$stable_td" >&2
  exit 1
}

canary_td=$(printf '%s' "$svc_json" | jq -r '[.services[0].taskSets[]? | select(.status != "PRIMARY") | .taskDefinition] | first // empty')
if [ -n "$canary_td" ]; then
  canary_ver=$(resolve_td_version "$canary_td") || {
    printf 'status-aws-ecs-codedeploy: could not resolve version for canary %s\n' "$canary_td" >&2
    exit 1
  }
  canary_json=$(printf '%s' "$canary_ver" | jq -R .)
else
  canary_json=null
fi

weight=$(read_alb_routing | cut -f4)
jq -cn --arg s "$stable_ver" --argjson c "$canary_json" --argjson w "${weight:-0}" \
  '{stable: $s, canary: $c, weight: $w}'
