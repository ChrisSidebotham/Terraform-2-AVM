resource "azurerm_virtual_network" "platform" {
  name                = "demo-platform-vnet"
  location            = azurerm_resource_group.platform.location
  resource_group_name = azurerm_resource_group.platform.name
  address_space       = ["10.42.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "application" {
  name                 = "application"
  resource_group_name  = azurerm_resource_group.platform.name
  virtual_network_name = azurerm_virtual_network.platform.name
  address_prefixes     = ["10.42.1.0/24"]
}

resource "azurerm_network_security_group" "application" {
  name                = "demo-application-nsg"
  location            = azurerm_resource_group.platform.location
  resource_group_name = azurerm_resource_group.platform.name
  tags                = local.tags
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
  resource_group_name         = azurerm_resource_group.platform.name
  network_security_group_name = azurerm_network_security_group.application.name
}
