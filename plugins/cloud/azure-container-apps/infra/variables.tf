variable "subscription_id" {
  type        = string
  description = "Azure subscription ID"
}

variable "resource_group_name" {
  type        = string
  default     = "rg-lights-out"
  description = "Resource group hosting Container Apps"
}

variable "location" {
  type        = string
  default     = "westeurope"
  description = "Azure region"
}

variable "environment_name" {
  type        = string
  default     = "lights-out-cae"
  description = "Container Apps Environment name"
}

variable "app_name" {
  type        = string
  default     = "backend"
  description = "Azure Container App name"
}

variable "github_repository" {
  type        = string
  description = "GitHub repository allowed via OIDC (owner/repo)"
}

variable "default_branch" {
  type        = string
  default     = "main"
  description = "Default branch permitted for OIDC federated credential"
}

variable "initial_image" {
  type        = string
  default     = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
  description = "Initial container image before first lights-out deploy"
}

variable "monthly_budget_amount" {
  type        = number
  default     = 50
  description = "Monthly Azure consumption budget threshold"
}

variable "budget_start_date" {
  type        = string
  default     = "2026-11-01T00:00:00Z"
  description = "First day of the month (RFC3339) for consumption budget"
}

variable "budget_alert_emails" {
  type        = list(string)
  description = "Email addresses notified on 50% and 100% budget thresholds"
}
