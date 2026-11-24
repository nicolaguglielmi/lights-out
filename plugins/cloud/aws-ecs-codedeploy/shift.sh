#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# shift.sh (aws-ecs-codedeploy): updates ALB target group weights for canary
# percentages 5, 25, or 100 and continues CodeDeploy on promotion (100).
set -eu
percent=${1:?usage: shift.sh <canary-percent>}
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli aws jq

case "$percent" in
  5|25|100) ;;
  *)
    printf 'shift-aws-ecs-codedeploy: invalid canary percent %s (allowed: 5, 25, 100)\n' "$percent" >&2
    exit 1
    ;;
esac

routing=$(read_alb_routing) || {
  printf 'shift-aws-ecs-codedeploy: could not read ALB listener rule for %s/%s\n' "$CODEDEPLOY_APP" "$CODEDEPLOY_GROUP" >&2
  exit 1
}
rule_arn=$(printf '%s' "$routing" | cut -f1)
stable_arn=$(printf '%s' "$routing" | cut -f2)
canary_arn=$(printf '%s' "$routing" | cut -f3)
stable_w=$((100 - percent))

actions=$(jq -cn --arg sa "$stable_arn" --arg ca "$canary_arn" --argjson sw "$stable_w" --argjson cw "$percent" '[{
  Type: "forward",
  ForwardConfig: {
    TargetGroups: [
      {TargetGroupArn: $sa, Weight: $sw},
      {TargetGroupArn: $ca, Weight: $cw}
    ]
  }
}]')

aws elbv2 modify-rule --region "$AWS_REGION" --rule-arn "$rule_arn" --actions "$actions" --output json >/dev/null

if [ "$percent" = "100" ]; then
  dep_id=$(aws deploy list-deployments --region "$AWS_REGION" \
    --application-name "$CODEDEPLOY_APP" --deployment-group-name "$CODEDEPLOY_GROUP" \
    --include-only-statuses InProgress Queued Ready --output json | jq -r '.deployments[0] // empty')
  if [ -n "$dep_id" ]; then
    aws deploy continue-deployment --region "$AWS_REGION" \
      --deployment-id "$dep_id" --deployment-wait-type TERMINATION_WAIT --output json >/dev/null
  fi
fi

got=$(read_alb_routing | cut -f4)
[ "$got" = "$percent" ] || {
  printf 'shift-aws-ecs-codedeploy: ALB rule reports canary weight %s, want %s\n' "$got" "$percent" >&2
  exit 1
}
