output "resource_group_id" {
  value = module.platform.resource_id
}

output "virtual_network_id" {
  value = module.virtual_network_platform.resource_id
}

output "storage_account_id" {
  value = module.storage_account_application.resource_id
}

output "log_analytics_workspace_id" {
  value = module.log_analytics_workspace_platform.resource_id
}
