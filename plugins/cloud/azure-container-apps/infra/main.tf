terraform {
  required_version = ">= 1.6.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_consumption_budget_resource_group" "monthly_guard" {
  name              = "${var.app_name}-lights-out-budget"
  resource_group_id = azurerm_resource_group.main.id
  amount            = var.monthly_budget_amount
  time_grain        = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThan"
    contact_emails = var.budget_alert_emails
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    contact_emails = var.budget_alert_emails
  }
}

resource "azurerm_user_assigned_identity" "deployer" {
  name                = "${var.app_name}-oidc-deployer"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
}

resource "azurerm_federated_identity_credential" "github" {
  name                = "${var.app_name}-github-oidc"
  resource_group_name = azurerm_resource_group.main.name
  audience            = ["api://AzureADTokenExchange"]
  issuer              = "https://token.actions.githubusercontent.com"
  parent_id           = azurerm_user_assigned_identity.deployer.id
  subject             = "repo:${var.github_repository}:ref:refs/heads/${var.default_branch}"
}

resource "azurerm_role_assignment" "deployer_rg" {
  scope                = azurerm_resource_group.main.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.deployer.principal_id
}

resource "azurerm_container_app_environment" "env" {
  name                = var.environment_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  depends_on          = [azurerm_consumption_budget_resource_group.monthly_guard]
}

resource "azurerm_container_app" "app" {
  name                         = var.app_name
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Multiple"

  template {
    container {
      name   = "backend"
      image  = var.initial_image
      cpu    = 0.25
      memory = "0.5Gi"
      env {
        name  = "APP_VERSION"
        value = "0.1.0"
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    traffic_weight {
      label           = "stable"
      latest_revision = true
      percentage      = 100
    }
  }
}
