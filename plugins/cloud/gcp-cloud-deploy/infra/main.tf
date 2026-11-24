terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  apis = toset([
    "run.googleapis.com",
    "clouddeploy.googleapis.com",
    "artifactregistry.googleapis.com",
    "iamcredentials.googleapis.com",
    "billingbudgets.googleapis.com",
    "monitoring.googleapis.com",
  ])
}

resource "google_project_service" "enabled" {
  for_each           = local.apis
  service            = each.value
  disable_on_destroy = false
}

resource "google_billing_budget" "monthly_guard" {
  billing_account = var.billing_account
  display_name    = "${var.service_name}-lights-out-budget"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.monthly_budget_amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }

  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "runtime" {
  account_id   = "${var.service_name}-runtime"
  display_name = "lights-out Cloud Run runtime identity"
}

resource "google_service_account" "deployer" {
  account_id   = "${var.service_name}-deployer"
  display_name = "lights-out keyless OIDC deployer"
}

resource "google_project_iam_member" "deployer_roles" {
  for_each = toset([
    "roles/clouddeploy.releaser",
    "roles/run.developer",
    "roles/artifactregistry.writer",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_act_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_iam_workload_identity_pool" "ci" {
  workload_identity_pool_id = "${var.service_name}-ci-pool"
  display_name              = "lights-out CI OIDC Pool"
  depends_on                = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "github_oidc" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.ci.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub Actions OIDC"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  attribute_condition = "assertion.repository == '${var.github_repository}'"
}

resource "google_service_account_iam_member" "github_wif_binding" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.ci.name}/attribute.repository/${var.github_repository}"
}

resource "google_cloud_run_v2_service" "app" {
  name       = var.service_name
  location   = var.region
  depends_on = [google_billing_budget.monthly_guard]

  template {
    service_account = google_service_account.runtime.email
    containers {
      image = var.initial_image
      env {
        name  = "APP_VERSION"
        value = "0.1.0"
      }
    }
  }
}

resource "google_clouddeploy_target" "prod" {
  name     = var.target_name
  location = var.region

  run {
    location = "projects/${var.project_id}/locations/${var.region}"
  }
}

resource "google_clouddeploy_delivery_pipeline" "pipeline" {
  name     = var.pipeline_name
  location = var.region

  serial_pipeline {
    stages {
      target_id = google_clouddeploy_target.prod.name
      strategy {
        canary {
          runtime_config {
            cloud_run {
              automatic_traffic_control = true
            }
          }
          custom_canary_deployment {
            phase_configs {
              phase_id   = "canary-5"
              percentage = 5
            }
            phase_configs {
              phase_id   = "canary-25"
              percentage = 25
            }
          }
        }
      }
    }
  }
}
