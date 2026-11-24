#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# deploy.sh (aws-ecs-codedeploy): registers an ECS task definition with <image>
# and <version>, then starts a CodeDeploy deployment at weight 0.
set -eu
version=${1:?usage: deploy.sh <version> <image>}
image=${2:?usage: deploy.sh <version> <image>}
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli aws jq

cur_td=$(aws ecs describe-services --region "$AWS_REGION" --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" --output json \
  | jq -er '.services[0].taskDefinition')

td_payload=$(aws ecs describe-task-definition --region "$AWS_REGION" --task-definition "$cur_td" --output json \
  | jq -c --arg img "$image" --arg ver "$version" --arg cname "$ECS_CONTAINER_NAME" '
    .taskDefinition
    | {
        family: .family,
        networkMode: (.networkMode // "awsvpc"),
        requiresCompatibilities: (.requiresCompatibilities // ["FARGATE"]),
        cpu: (.cpu // "256"),
        memory: (.memory // "512"),
        executionRoleArn: .executionRoleArn,
        taskRoleArn: .taskRoleArn,
        containerDefinitions: (
          .containerDefinitions | map(
            if .name == $cname then
              .image = $img
              | .dockerLabels = ((.dockerLabels // {}) + {"lights-out.version": $ver})
              | .environment = (((.environment // []) | map(select(.name != "APP_VERSION"))) + [{name: "APP_VERSION", value: $ver}])
            else .
            end
          )
        )
      }
    | with_entries(select(.value != null))
  ')

new_td=$(aws ecs register-task-definition --region "$AWS_REGION" \
  --cli-input-json "$td_payload" --output json \
  | jq -er '.taskDefinition.taskDefinitionArn')

appspec=$(jq -cn --arg td "$new_td" --arg c "$ECS_CONTAINER_NAME" '{
  version: 1,
  Resources: [{
    TargetService: {
      Type: "AWS::ECS::Service",
      Properties: {
        TaskDefinition: $td,
        LoadBalancerInfo: {ContainerName: $c, ContainerPort: 8000}
      }
    }
  }]
}' | jq -Rs '{revisionType: "AppSpecContent", appSpecContent: {content: .}}')

dep_id=$(aws deploy create-deployment --region "$AWS_REGION" \
  --application-name "$CODEDEPLOY_APP" \
  --deployment-group-name "$CODEDEPLOY_GROUP" \
  --revision "$appspec" \
  --output json | jq -er '.deploymentId')

start=$(date +%s)
while :; do
  dep_status=$(aws deploy get-deployment --region "$AWS_REGION" --deployment-id "$dep_id" --output json \
    | jq -r '.deploymentInfo.status // "Unknown"')
  case "$dep_status" in
    Ready|Succeeded|InProgress)
      [ "$dep_status" != "InProgress" ] && exit 0
      # InProgress is healthy once replacement taskSet exists
      if aws ecs describe-services --region "$AWS_REGION" --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" --output json \
         | jq -e '.services[0].taskSets | length >= 2' >/dev/null 2>&1; then
        exit 0
      fi ;;
    Failed|Stopped)
      printf 'deploy-aws-ecs-codedeploy: deployment %s %s\n' "$dep_id" "$dep_status" >&2
      exit 1 ;;
  esac
  [ $(( $(date +%s) - start )) -lt "$READY_TIMEOUT" ] || break
  sleep "$AWS_POLL_INTERVAL"
done
printf 'deploy-aws-ecs-codedeploy: deployment %s did not become ready within %ss\n' "$dep_id" "$READY_TIMEOUT" >&2
exit 1
