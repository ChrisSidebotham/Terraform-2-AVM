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

resource "azurerm_resource_group" "platform" {
  name     = "demo-platform-rg"
  location = "uksouth"
  tags     = local.tags
}
