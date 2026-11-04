output "resource_group_id" {
  value = azurerm_resource_group.platform.id
}

output "virtual_network_id" {
  value = azurerm_virtual_network.platform.id
}

output "storage_account_id" {
  value = azurerm_storage_account.application.id
}

output "log_analytics_workspace_id" {
  value = azurerm_log_analytics_workspace.platform.id
}
