variable "project_id" {
  type        = string
  description = "GCP project ID for Cloud Run and Cloud Deploy"
}

variable "region" {
  type        = string
  default     = "europe-west1"
  description = "GCP region"
}

variable "billing_account" {
  type        = string
  description = "GCP billing account ID (required to enforce billing budget)"
}

variable "monthly_budget_amount" {
  type        = number
  default     = 50
  description = "Monthly budget alert threshold in var.budget_currency"
}

variable "budget_currency" {
  type        = string
  default     = "USD"
  description = "ISO currency code for billing budget"
}

variable "service_name" {
  type        = string
  default     = "backend"
  description = "Cloud Run service name"
}

variable "pipeline_name" {
  type        = string
  default     = "backend-pipeline"
  description = "Cloud Deploy delivery pipeline name"
}

variable "target_name" {
  type        = string
  default     = "prod"
  description = "Cloud Deploy target name"
}

variable "github_repository" {
  type        = string
  description = "GitHub repository allowed via OIDC (owner/repo)"
}

variable "initial_image" {
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
  description = "Initial Cloud Run container image before first lights-out deploy"
}
