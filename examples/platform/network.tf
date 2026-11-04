# migrated by avmigrate from azurerm_virtual_network.platform
module "virtual_network_platform" {
  source  = "Azure/avm-res-network-virtualnetwork/azurerm"
  version = "0.22.2"

  name          = "demo-platform-vnet"
  location      = module.platform.location
  parent_id     = module.platform.resource_id
  address_space = ["10.42.0.0/16"]
  tags          = local.tags
  subnets = {
    application = {
      name             = "application"
      address_prefixes = ["10.42.1.0/24"]
    }
  }
  enable_telemetry = false
}

# migrated by avmigrate from azurerm_network_security_group.application
module "application" {
  source  = "Azure/avm-res-network-networksecuritygroup/azurerm"
  version = "0.5.1"

  name                = "demo-application-nsg"
  location            = module.platform.location
  resource_group_name = module.platform.name
  tags                = local.tags
  enable_telemetry    = false
}

resource "azurerm_network_security_rule" "https" {
  name                        = "AllowHttpsWithinVnet"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefix       = "VirtualNetwork"
  destination_address_prefix  = "VirtualNetwork"
  resource_group_name         = module.platform.name
  network_security_group_name = module.application.name
}
