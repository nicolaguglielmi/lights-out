variable "cluster_name" {
  type        = string
  default     = "lights-out-k3s"
  description = "Hetzner Cloud k3s cluster name prefix"
}

variable "location" {
  type        = string
  default     = "fsn1"
  description = "Hetzner Cloud datacenter location (fsn1, nbg1, hel1, ash, hil)"
}

variable "server_type" {
  type        = string
  default     = "cx22"
  description = "Cost-capped Hetzner Cloud server type for the single-node k3s control plane"

  validation {
    condition     = contains(["cx22", "cx32", "cpx11", "cpx21", "cax11", "cax21"], var.server_type)
    error_message = "Choose a budget-capped server_type (cx22, cx32, cpx11, cpx21, cax11, cax21) to stay within monthly budget."
  }
}

variable "monthly_budget_eur" {
  type        = number
  default     = 20
  description = "Target monthly Hetzner Cloud spend ceiling in EUR (pair with Hetzner Console billing alert; Hetzner exposes no native Terraform budget API)"

  validation {
    condition     = var.monthly_budget_eur > 0 && var.monthly_budget_eur <= 100
    error_message = "monthly_budget_eur must be between 1 and 100 EUR."
  }
}

variable "namespace" {
  type        = string
  default     = "lights-out"
  description = "Kubernetes namespace hosting the Argo Rollout and canary resources"
}

variable "allowed_api_cidrs" {
  type        = list(string)
  description = "CIDR allowlist permitted to reach k3s kube-apiserver port 6443"
}

variable "oidc_issuer_url" {
  type        = string
  default     = "https://token.actions.githubusercontent.com"
  description = "OIDC issuer URL configured on k3s kube-apiserver"
}

variable "oidc_client_id" {
  type        = string
  description = "OIDC audience / client ID expected in CI identity tokens"
}

variable "oidc_subject" {
  type        = string
  description = "Exact OIDC subject claim bound to the namespace deployer Role (e.g. repo:owner/repo:ref:refs/heads/main)"
}
