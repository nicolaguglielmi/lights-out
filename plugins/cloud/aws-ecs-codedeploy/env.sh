#!/bin/sh
# shellcheck disable=SC2034 # variables here are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# aws-ecs-codedeploy env.sh: validates AWS ECS/CodeDeploy configuration and
# refuses static IAM keys or credential files in $WORKLOAD.
set -eu

AWS_REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-}}
ECS_CLUSTER=${ECS_CLUSTER:-}
ECS_SERVICE=${ECS_SERVICE:-}
CODEDEPLOY_APP=${CODEDEPLOY_APP:-}
CODEDEPLOY_GROUP=${CODEDEPLOY_GROUP:-}
ECS_CONTAINER_NAME=${ECS_CONTAINER_NAME:-backend}
READY_TIMEOUT=${READY_TIMEOUT:-180}
AWS_POLL_INTERVAL=${AWS_POLL_INTERVAL:-1}

if [ -z "$AWS_REGION" ]; then
  printf 'aws-ecs-codedeploy: AWS_REGION (or AWS_DEFAULT_REGION) is empty\n' >&2
  exit 1
fi
if [ -z "$ECS_CLUSTER" ]; then
  printf 'aws-ecs-codedeploy: ECS_CLUSTER is empty\n' >&2
  exit 1
fi
if [ -z "$ECS_SERVICE" ]; then
  printf 'aws-ecs-codedeploy: ECS_SERVICE is empty\n' >&2
  exit 1
fi
if [ -z "$CODEDEPLOY_APP" ]; then
  printf 'aws-ecs-codedeploy: CODEDEPLOY_APP is empty\n' >&2
  exit 1
fi
if [ -z "$CODEDEPLOY_GROUP" ]; then
  printf 'aws-ecs-codedeploy: CODEDEPLOY_GROUP is empty\n' >&2
  exit 1
fi

if [ -n "${AWS_ACCESS_KEY_ID:-}${AWS_SECRET_ACCESS_KEY:-}" ] \
   && [ -z "${AWS_WEB_IDENTITY_TOKEN_FILE:-}" ] && [ -z "${AWS_SESSION_TOKEN:-}" ]; then
  printf 'aws-ecs-codedeploy: static AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY refused; use OIDC STS web identity\n' >&2
  exit 1
fi

check_no_static_creds() {
  _wl=${WORKLOAD:-}
  [ -n "$_wl" ] && [ -d "$_wl" ] || return 0
  _found=$(find "$_wl" -maxdepth 4 \
    \( -name .git -o -name node_modules -o -name .venv -o -name __pycache__ \) -prune -o \
    -type f \( \
      -path '*/.aws/credentials' -o -path '*/.aws/config' -o \
      -name '*credentials*.json' -o -name '*service-account*.json' -o -name '*service_account*.json' -o \
      -name '*-key.json' -o -name '*.pem' -o -name '*.p12' -o -name '*.pfx' -o \
      -name 'hcloud-token*' -o -name '*kubeconfig*' \
    \) -print 2>/dev/null | head -n 1 || true)
  if [ -n "$_found" ]; then
    printf 'aws-ecs-codedeploy: static credential file refused in WORKLOAD: %s\n' "$_found" >&2
    exit 1
  fi
  _json_hit=$(find "$_wl" -maxdepth 4 \
    \( -name .git -o -name node_modules -o -name .venv -o -name __pycache__ \) -prune -o \
    -type f -name '*.json' ! -name 'package-lock.json' -print 2>/dev/null | while IFS= read -r _jf; do
      if grep -Eq '"type"[[:space:]]*:[[:space:]]*"service_account"|"private_key_id"[[:space:]]*:|"client_secret"[[:space:]]*:|"clientSecret"[[:space:]]*:|"aws_secret_access_key"[[:space:]]*:' "$_jf" 2>/dev/null; then
        printf '%s\n' "$_jf"
        break
      fi
    done || true)
  if [ -n "$_json_hit" ]; then
    printf 'aws-ecs-codedeploy: static credential JSON file refused in WORKLOAD: %s\n' "$_json_hit" >&2
    exit 1
  fi
}

check_no_static_creds

require_cli() {
  for _tool in "$@"; do
    command -v "$_tool" >/dev/null 2>&1 || {
      printf 'aws-ecs-codedeploy: %s is not on PATH\n' "$_tool" >&2
      exit 1
    }
  done
}

resolve_td_version() {
  _td=$1
  _td_json=$(aws ecs describe-task-definition --region "$AWS_REGION" \
    --task-definition "$_td" --output json) || return 1
  printf '%s' "$_td_json" | jq -er --arg c "$ECS_CONTAINER_NAME" '
    (.taskDefinition.containerDefinitions | map(select(.name == $c)) | first // .taskDefinition.containerDefinitions[0]) as $cd
    | ($cd.dockerLabels["lights-out.version"]
       // ([($cd.environment // [])[] | select(.name == "APP_VERSION") | .value] | first)
       // ($cd.image | split("@")[1] // (split(":") | last)))
  '
}

read_alb_routing() {
  _dg=$(aws deploy get-deployment-group --region "$AWS_REGION" \
    --application-name "$CODEDEPLOY_APP" --deployment-group-name "$CODEDEPLOY_GROUP" --output json) || return 1
  _listener=$(printf '%s' "$_dg" | jq -r '.deploymentGroupInfo.loadBalancerInfo.targetGroupPairInfoList[0].prodTrafficRoute.listenerArns[0] // empty')
  _tg1=$(printf '%s' "$_dg" | jq -r '.deploymentGroupInfo.loadBalancerInfo.targetGroupPairInfoList[0].targetGroups[0].name // empty')
  _tg2=$(printf '%s' "$_dg" | jq -r '.deploymentGroupInfo.loadBalancerInfo.targetGroupPairInfoList[0].targetGroups[1].name // empty')
  [ -n "$_listener" ] && [ -n "$_tg1" ] && [ -n "$_tg2" ] || return 1
  _tgs=$(aws elbv2 describe-target-groups --region "$AWS_REGION" --names "$_tg1" "$_tg2" --output json) || return 1
  _stable_arn=$(printf '%s' "$_tgs" | jq -r --arg n "$_tg1" '.TargetGroups[] | select(.TargetGroupName == $n) | .TargetGroupArn')
  _canary_arn=$(printf '%s' "$_tgs" | jq -r --arg n "$_tg2" '.TargetGroups[] | select(.TargetGroupName == $n) | .TargetGroupArn')
  _rules=$(aws elbv2 describe-rules --region "$AWS_REGION" --listener-arn "$_listener" --output json) || return 1
  _rule_arn=$(printf '%s' "$_rules" | jq -r '.Rules[] | select((.Actions[]?.Type == "forward") and ((.Actions[]?.ForwardConfig.TargetGroups // []) | length > 0)) | .RuleArn' | head -n 1)
  _weight=$(printf '%s' "$_rules" | jq -r --arg arn "$_canary_arn" --arg r "$_rule_arn" '
    [.Rules[] | select(.RuleArn == $r) | .Actions[]?.ForwardConfig.TargetGroups[]? | select(.TargetGroupArn == $arn) | .Weight] | first // 0
  ')
  printf '%s\t%s\t%s\t%s\n' "$_rule_arn" "$_stable_arn" "$_canary_arn" "$_weight"
}
