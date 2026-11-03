terraform {
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

resource "azurerm_resource_group" "application" {
  name     = "demo-application-rg"
  location = "uksouth"
}

resource "azurerm_resource_group" "network" {
  name     = "demo-network-rg"
  location = "uksouth"
}

resource "azurerm_resource_group" "observability" {
  name     = "demo-observability-rg"
  location = "westeurope"
}
