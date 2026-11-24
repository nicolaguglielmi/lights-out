#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# rollback.sh (aws-ecs-codedeploy): idempotently stops active CodeDeploy
# deployments and resets ALB target group weights to 100/0.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli aws jq

deps=$(aws deploy list-deployments --region "$AWS_REGION" \
  --application-name "$CODEDEPLOY_APP" --deployment-group-name "$CODEDEPLOY_GROUP" \
  --include-only-statuses InProgress Queued Ready --output json)
for dep_id in $(printf '%s' "$deps" | jq -r '.deployments[]?'); do
  aws deploy stop-deployment --region "$AWS_REGION" \
    --deployment-id "$dep_id" --rollback-enabled --output json >/dev/null
done

routing=$(read_alb_routing) || {
  printf 'rollback-aws-ecs-codedeploy: could not read ALB listener rule\n' >&2
  exit 1
}
rule_arn=$(printf '%s' "$routing" | cut -f1)
stable_arn=$(printf '%s' "$routing" | cut -f2)
canary_arn=$(printf '%s' "$routing" | cut -f3)
weight=$(printf '%s' "$routing" | cut -f4)

if [ "$weight" != "0" ]; then
  actions=$(jq -cn --arg sa "$stable_arn" --arg ca "$canary_arn" '[{
    Type: "forward",
    ForwardConfig: {
      TargetGroups: [
        {TargetGroupArn: $sa, Weight: 100},
        {TargetGroupArn: $ca, Weight: 0}
      ]
    }
  }]')
  aws elbv2 modify-rule --region "$AWS_REGION" --rule-arn "$rule_arn" --actions "$actions" --output json >/dev/null
fi

got=$(read_alb_routing | cut -f4)
[ "$got" = "0" ] || {
  printf 'rollback-aws-ecs-codedeploy: canary weight still %s after rollback\n' "$got" >&2
  exit 1
}
