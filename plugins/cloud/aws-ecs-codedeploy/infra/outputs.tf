output "deployer_role_arn" {
  value       = aws_iam_role.deployer.arn
  description = "IAM role ARN assumed via OIDC federation"
}

output "ecs_cluster_name" {
  value       = aws_ecs_cluster.main.name
  description = "ECS cluster name"
}

output "codedeploy_app_name" {
  value       = aws_codedeploy_app.app.name
  description = "CodeDeploy application name"
}

output "codedeploy_group_name" {
  value       = aws_codedeploy_deployment_group.dg.deployment_group_name
  description = "CodeDeploy deployment group name"
}
