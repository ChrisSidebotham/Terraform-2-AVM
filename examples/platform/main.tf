terraform {
  required_version = ">= 1.13.0, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
}

locals {
  tags = {
    environment = "presentation"
    workload    = "synthetic-platform"
  }
}

# migrated by avmigrate from azurerm_resource_group.platform
module "platform" {
  source  = "Azure/avm-res-resources-resourcegroup/azurerm"
  version = "0.4.0"

  name             = "demo-platform-rg"
  location         = "uksouth"
  tags             = local.tags
  enable_telemetry = false
}
