variable "aws_region" {
  type        = string
  default     = "eu-west-1"
  description = "AWS region for ECS and CodeDeploy"
}

variable "cluster_name" {
  type        = string
  default     = "lights-out-ecs"
  description = "ECS cluster name"
}

variable "service_name" {
  type        = string
  default     = "backend"
  description = "ECS service name"
}

variable "codedeploy_app_name" {
  type        = string
  default     = "backend-cd"
  description = "CodeDeploy application name"
}

variable "codedeploy_group_name" {
  type        = string
  default     = "backend-dg"
  description = "CodeDeploy deployment group name"
}

variable "vpc_id" {
  type        = string
  description = "VPC ID hosting ECS and ALB target groups"
}

variable "prod_listener_arn" {
  type        = string
  description = "ALB production listener ARN"
}

variable "test_listener_arn" {
  type        = string
  description = "ALB test/canary listener ARN"
}

variable "github_repository" {
  type        = string
  description = "GitHub repository allowed via OIDC (owner/repo)"
}

variable "monthly_budget_amount" {
  type        = number
  default     = 50
  description = "Monthly AWS cost budget alert threshold (USD)"
}

variable "budget_alert_emails" {
  type        = list(string)
  description = "Email addresses notified on 50% and 100% budget thresholds"
}
