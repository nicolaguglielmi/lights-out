#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# canary-url.sh (aws-ecs-codedeploy): prints the ALB test-listener URL for canary.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli aws jq

url=${CANARY_URL:-}
if [ -z "$url" ]; then
  dg=$(aws deploy get-deployment-group --region "$AWS_REGION" \
    --application-name "$CODEDEPLOY_APP" --deployment-group-name "$CODEDEPLOY_GROUP" --output json)
  test_l=$(printf '%s' "$dg" | jq -r '.deploymentGroupInfo.loadBalancerInfo.targetGroupPairInfoList[0].testTrafficRoute.listenerArns[0] // empty')
  [ -n "$test_l" ] || {
    printf 'canary-url-aws-ecs-codedeploy: no test listener found on deployment group %s\n' "$CODEDEPLOY_GROUP" >&2
    exit 1
  }
  l_info=$(aws elbv2 describe-listeners --region "$AWS_REGION" --listener-arns "$test_l" --output json)
  port=$(printf '%s' "$l_info" | jq -r '.Listeners[0].Port // 8443')
  proto=$(printf '%s' "$l_info" | jq -r '.Listeners[0].Protocol // "HTTPS"' | tr '[:upper:]' '[:lower:]')
  lb_arn=$(printf '%s' "$l_info" | jq -r '.Listeners[0].LoadBalancerArn // empty')
  dns=$(aws elbv2 describe-load-balancers --region "$AWS_REGION" --load-balancer-arns "$lb_arn" --output json \
    | jq -r '.LoadBalancers[0].DNSName // empty')
  [ -n "$dns" ] || {
    printf 'canary-url-aws-ecs-codedeploy: ALB DNSName is empty\n' >&2
    exit 1
  }
  url="${proto}://${dns}:${port}"
fi
printf '%s\n' "$url"
