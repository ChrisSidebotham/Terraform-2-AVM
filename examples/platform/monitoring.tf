# migrated by avmigrate from azurerm_log_analytics_workspace.platform
module "log_analytics_workspace_platform" {
  source  = "Azure/avm-res-operationalinsights-workspace/azurerm"
  version = "0.5.1"

  name                                      = "demo-platform-logs"
  location                                  = module.platform.location
  resource_group_name                       = module.platform.name
  log_analytics_workspace_sku               = "PerGB2018"
  log_analytics_workspace_retention_in_days = 30
  tags                                      = local.tags
  enable_telemetry                          = false
}
