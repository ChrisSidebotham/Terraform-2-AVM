# AVM Migration Changelog

- **Source commit:** 634e4641acc3b02ddddb31d34bbd4eb11c312bed
- **Mode:** APPLIED
- **Selected root:** `examples/platform`
- **avmigrate version:** 1.10.0
- **AVM catalog snapshot:** 2026-09-07T08:53:47Z

## Summary

| Metric                              | Count |
|-------------------------------------|-------|
| Resources inspected                 | 7     |
| Converted to AVM modules            | 5     |
| Absorbed into a parent module       | 1     |
| Left unchanged                      | 1     |
| Files changed                       | 5     |
| Cross-resource references rewritten | 16    |
| Distinct AVM modules used           | 5     |

## Converted resources

| From                                         | To                                        | AVM module                              | Version | File          |
|----------------------------------------------|-------------------------------------------|-----------------------------------------|---------|---------------|
| `azurerm_log_analytics_workspace.platform`   | `module.log_analytics_workspace_platform` | `avm-res-operationalinsights-workspace` | 0.5.1   | monitoring.tf |
| `azurerm_network_security_group.application` | `module.application`                      | `avm-res-network-networksecuritygroup`  | 0.5.1   | network.tf    |
| `azurerm_resource_group.platform`            | `module.platform`                         | `avm-res-resources-resourcegroup`       | 0.4.0   | main.tf       |
| `azurerm_storage_account.application`        | `module.storage_account_application`      | `avm-res-storage-storageaccount`        | 0.10.0  | storage.tf    |
| `azurerm_virtual_network.platform`           | `module.virtual_network_platform`         | `avm-res-network-virtualnetwork`        | 0.22.2  | network.tf    |

## Absorbed sub-resources

These resources were removed as standalone blocks and folded into the parent module's typed input, which is how AVM models them.

| Resource                     | Absorbed into                      | Input     | File       |
|------------------------------|------------------------------------|-----------|------------|
| `azurerm_subnet.application` | `azurerm_virtual_network.platform` | `subnets` | network.tf |

## Left unchanged

| Resource                              | Reason code | Detail                                     |
|---------------------------------------|-------------|--------------------------------------------|
| `azurerm_network_security_rule.https` | none        | no AVM module found for this resource type |

## AVM modules referenced

| Module                                  | Source                                                | Version | Registry                                                                                         |
|-----------------------------------------|-------------------------------------------------------|---------|--------------------------------------------------------------------------------------------------|
| `avm-res-network-networksecuritygroup`  | `Azure/avm-res-network-networksecuritygroup/azurerm`  | 0.5.1   | https://registry.terraform.io/modules/Azure/avm-res-network-networksecuritygroup/azurerm/latest  |
| `avm-res-network-virtualnetwork`        | `Azure/avm-res-network-virtualnetwork/azurerm`        | 0.22.2  | https://registry.terraform.io/modules/Azure/avm-res-network-virtualnetwork/azurerm/latest        |
| `avm-res-operationalinsights-workspace` | `Azure/avm-res-operationalinsights-workspace/azurerm` | 0.5.1   | https://registry.terraform.io/modules/Azure/avm-res-operationalinsights-workspace/azurerm/latest |
| `avm-res-resources-resourcegroup`       | `Azure/avm-res-resources-resourcegroup/azurerm`       | 0.4.0   | https://registry.terraform.io/modules/Azure/avm-res-resources-resourcegroup/azurerm/latest       |
| `avm-res-storage-storageaccount`        | `Azure/avm-res-storage-storageaccount/azurerm`        | 0.10.0  | https://registry.terraform.io/modules/Azure/avm-res-storage-storageaccount/azurerm/latest        |

## Findings

### Informational (4)

- **`azurerm_network_security_rule.https`** - no AVM module available
  no AVM module found for this resource type
- **`azurerm_storage_account.application`** - `resource_group_name` converted to `parent_id`
  module.platform.name -> module.platform.resource_id
- **`azurerm_subnet.application`** - absorbed into `azurerm_virtual_network.platform` as `subnets["application"]`
  Subnets are declared inline on the virtual network module.
- **`azurerm_virtual_network.platform`** - `resource_group_name` converted to `parent_id`
  module.platform.name -> module.platform.resource_id

## State migration

Converting a resource into a module changes its Terraform address. `terraform state mv` only works when the **resource type is identical** on both sides, and roughly half the AVM modules are built on AzAPI - they store the resource as `azapi_resource`, so a move from `azurerm_*` is rejected with "resource types don't match". Those must be re-imported.

| Current address                              | New address                                                                       | Method               |
|----------------------------------------------|-----------------------------------------------------------------------------------|----------------------|
| `azurerm_log_analytics_workspace.platform`   | `module.log_analytics_workspace_platform.azurerm_log_analytics_workspace.this[0]` | `terraform state mv` |
| `azurerm_network_security_group.application` | `module.application.azurerm_network_security_group.this[0]`                       | `terraform state mv` |
| `azurerm_resource_group.platform`            | `module.platform.azapi_resource.this[0]`                                          | import (AzAPI)       |
| `azurerm_storage_account.application`        | `module.storage_account_application.azapi_resource.this[0]`                       | import (AzAPI)       |
| `azurerm_virtual_network.platform`           | `module.virtual_network_platform.azapi_resource.vnet`                             | import (AzAPI)       |

- **2** can be moved directly - see `avm-state-migration.sh`.
- **3** must be imported because the module is AzAPI-backed.

  For the imports, prefer Microsoft's [Terraform State Importer](https://github.com/Azure/terraform-state-importer). It queries Azure Resource Graph, runs `terraform plan` against the module, maps resource IDs to the planned addresses (including `azapi_resource`) and writes `imports.tf` for you - which removes the error-prone step of finding and pasting the right resource ID for every address.

  A starter config scoped to the migrated resource types has been generated as `terraform-state-importer.config.yaml`:

  ```bash
  terraform-state-importer run \
    --config ./terraform-state-importer.config.yaml \
    --terraformModulePath .
  ```

  It needs Azure CLI authentication and Resource Graph read access. If that is not available, `avm-state-import.tf.disabled` contains hand-fillable `import` blocks as a fallback.

## Next steps

1. Review every `# TODO(avmigrate):` comment in the changed `.tf` files, or run
   `avmigrate check .` to list them. The tool never invents values - anything it
   could not map with confidence is left as a TODO rather than silently dropped.
2. Run `terraform init` to download the AVM modules, then `terraform validate`.
3. Back up state before touching it:
   `terraform state pull > terraform.tfstate.backup.$(date +%s)`
4. Migrate state using the table above: run `avm-state-migration.sh` for the
   resources that can be moved, and import the AzAPI-backed modules.
5. Run `terraform plan` and confirm it reports no changes (or only intended
   ones) before applying. A plan that still wants to destroy and recreate means
   an address has not been migrated yet.
