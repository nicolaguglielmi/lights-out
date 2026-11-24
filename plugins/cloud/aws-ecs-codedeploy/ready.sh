#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready.sh (aws-ecs-codedeploy): checks ECS service ACTIVE/running, CodeDeploy
# deployment group present, and HTTP health probe.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli aws jq curl

probe=${WORKLOAD_HEALTH_PROBE:-http:/healthz}
case "$probe" in
  http:/*) probe_path=${probe#http:} ;;
  *) probe_path=/healthz ;;
esac

start=$(date +%s)
svc_status="absent"
check_url=${APP_URL:-}
while :; do
  if svc_json=$(aws ecs describe-services --region "$AWS_REGION" --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" --output json 2>/dev/null) \
     && dg_json=$(aws deploy get-deployment-group --region "$AWS_REGION" --application-name "$CODEDEPLOY_APP" --deployment-group-name "$CODEDEPLOY_GROUP" --output json 2>/dev/null); then
    svc_status=$(printf '%s' "$svc_json" | jq -r '.services[0].status // "MISSING"')
    running=$(printf '%s' "$svc_json" | jq -r '.services[0].runningCount // 0')
    if [ -z "$check_url" ]; then
      l_arn=$(printf '%s' "$dg_json" | jq -r '.deploymentGroupInfo.loadBalancerInfo.targetGroupPairInfoList[0].prodTrafficRoute.listenerArns[0] // empty')
      if [ -n "$l_arn" ]; then
        lb_arn=$(aws elbv2 describe-listeners --region "$AWS_REGION" --listener-arns "$l_arn" --output json 2>/dev/null | jq -r '.Listeners[0].LoadBalancerArn // empty')
        dns=$(aws elbv2 describe-load-balancers --region "$AWS_REGION" --load-balancer-arns "$lb_arn" --output json 2>/dev/null | jq -r '.LoadBalancers[0].DNSName // empty')
        [ -z "$dns" ] || check_url="https://$dns"
      fi
    fi
    if [ "$svc_status" = "ACTIVE" ] && [ "$running" -ge 1 ] && [ -n "$check_url" ] \
       && curl -fsS --connect-timeout 2 --max-time 5 "$check_url$probe_path" >/dev/null 2>&1; then
      exit 0
    fi
  fi
  [ $(( $(date +%s) - start )) -lt "$READY_TIMEOUT" ] || break
  sleep "$AWS_POLL_INTERVAL"
done
printf 'ready: aws-ecs-codedeploy not ready after %ss (service %s status=%s url=%s)\n' \
  "$READY_TIMEOUT" "$ECS_SERVICE" "$svc_status" "${check_url:-none}" >&2
exit 1
