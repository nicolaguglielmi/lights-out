output "deployer_client_id" {
  value       = azurerm_user_assigned_identity.deployer.client_id
  description = "User-assigned managed identity client ID for OIDC login"
}

output "container_app_fqdn" {
  value       = azurerm_container_app.app.ingress[0].fqdn
  description = "Container App ingress FQDN"
}

output "resource_group_name" {
  value       = azurerm_resource_group.main.name
  description = "Azure resource group name"
}
