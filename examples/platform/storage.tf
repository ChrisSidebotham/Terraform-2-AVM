# migrated by avmigrate from azurerm_storage_account.application
module "storage_account_application" {
  source  = "Azure/avm-res-storage-storageaccount/azurerm"
  version = "0.10.0"

  name                            = "avmpresentationdemo"
  location                        = module.platform.location
  parent_id                       = module.platform.resource_id
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  public_network_access_enabled   = false
  allow_nested_items_to_be_public = false
  tags                            = local.tags
  enable_telemetry                = false
}
