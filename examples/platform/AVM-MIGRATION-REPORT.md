# Terraform to AVM report report

Source commit: `634e4641acc3b02ddddb31d34bbd4eb11c312bed`. Selected folder: `examples/platform`.

Converter exit code: **0**. Nonzero status and unresolved blockers are preserved below; draft publication is not validation or deployment approval.

# Terraform to AVM

Mode: report. Changed files: 0.

- intake: **passed**
- plan_repair: **passed**
- apply_fixes: **not_assessed**
- validate: **not_assessed**
- promote: **not_assessed**

Static validation: not_assessed.
Live plan: **not assessed**. Deployment readiness: **blocked**.

Review all advisory findings, lifecycle changes, unmapped arguments and
state-migration instructions. Static validation is not infrastructure safety.
No terraform apply, import or state-modification commands were executed.

## Conversion manifest

```json
{
  "schema_version": 1,
  "fixer": "avmigrate",
  "proposed_changes": [
    {
      "confidence": "curated",
      "file": "main.tf",
      "from": "azurerm_resource_group.platform",
      "module": "avm-res-resources-resourcegroup",
      "to": "module.platform"
    },
    {
      "confidence": "curated",
      "file": "monitoring.tf",
      "from": "azurerm_log_analytics_workspace.platform",
      "module": "avm-res-operationalinsights-workspace",
      "to": "module.log_analytics_workspace_platform"
    },
    {
      "confidence": "curated",
      "file": "network.tf",
      "from": "azurerm_virtual_network.platform",
      "module": "avm-res-network-virtualnetwork",
      "to": "module.virtual_network_platform"
    },
    {
      "confidence": "curated",
      "file": "network.tf",
      "from": "azurerm_network_security_group.application",
      "module": "avm-res-network-networksecuritygroup",
      "to": "module.application"
    },
    {
      "confidence": "curated",
      "file": "storage.tf",
      "from": "azurerm_storage_account.application",
      "module": "avm-res-storage-storageaccount",
      "to": "module.storage_account_application"
    },
    {
      "file": "network.tf",
      "from": "azurerm_subnet.application",
      "input": "subnets",
      "into": "azurerm_virtual_network.platform"
    }
  ],
  "affected_files": [
    "main.tf",
    "monitoring.tf",
    "network.tf",
    "storage.tf"
  ],
  "findings": [
    {
      "code": "upstream_license_unverified",
      "severity": "warning",
      "message": "Confirm upstream artifact distribution permission; no license notice was available.",
      "review_required": true
    },
    {
      "code": "resource_not_converted",
      "severity": "warning",
      "message": "A resource is unsupported, excluded or requires manual conversion.",
      "review_required": true
    },
    {
      "code": "engine_finding",
      "severity": "info",
      "message": "Review the private avmigrate report and generated migration instructions.",
      "review_required": false
    },
    {
      "code": "engine_finding",
      "severity": "info",
      "message": "Review the private avmigrate report and generated migration instructions.",
      "review_required": false
    },
    {
      "code": "engine_finding",
      "severity": "info",
      "message": "Review the private avmigrate report and generated migration instructions.",
      "review_required": false
    },
    {
      "code": "engine_finding",
      "severity": "info",
      "message": "Review the private avmigrate report and generated migration instructions.",
      "review_required": false
    },
    {
      "code": "state_review_required",
      "severity": "error",
      "message": "State transitions are unresolved; review IDs, types and destinations manually.",
      "review_required": true
    }
  ],
  "blockers": [
    {
      "code": "state_review_required",
      "severity": "error",
      "message": "State transitions are unresolved; review IDs, types and destinations manually.",
      "review_required": true
    }
  ],
  "state_migration_actions": [
    {
      "source": "azurerm_resource_group.platform",
      "destination": "module.platform.azapi_resource.this[0]",
      "kind": "cross_type_adoption",
      "status": "blocked",
      "requires_resource_id": true,
      "resource_id_status": "missing",
      "reason": "Manual state-aware review required; no state commands executed."
    },
    {
      "source": "azurerm_log_analytics_workspace.platform",
      "destination": "module.log_analytics_workspace_platform.azurerm_log_analytics_workspace.this[0]",
      "kind": "compatible_address_move",
      "status": "blocked",
      "requires_resource_id": false,
      "resource_id_status": "not_assessed",
      "reason": "Manual state-aware review required; no state commands executed."
    },
    {
      "source": "azurerm_virtual_network.platform",
      "destination": "module.virtual_network_platform.azapi_resource.vnet",
      "kind": "cross_type_adoption",
      "status": "blocked",
      "requires_resource_id": true,
      "resource_id_status": "missing",
      "reason": "Manual state-aware review required; no state commands executed."
    },
    {
      "source": "azurerm_network_security_group.application",
      "destination": "module.application.azurerm_network_security_group.this[0]",
      "kind": "compatible_address_move",
      "status": "blocked",
      "requires_resource_id": false,
      "resource_id_status": "not_assessed",
      "reason": "Manual state-aware review required; no state commands executed."
    },
    {
      "source": "azurerm_storage_account.application",
      "destination": "module.storage_account_application.azapi_resource.this[0]",
      "kind": "cross_type_adoption",
      "status": "blocked",
      "requires_resource_id": true,
      "resource_id_status": "missing",
      "reason": "Manual state-aware review required; no state commands executed."
    },
    {
      "source": "azurerm_subnet.application",
      "destination": null,
      "kind": "ambiguous_absorbed_destination",
      "status": "blocked",
      "reason": "Resolve keyed internal address and transition manually."
    }
  ],
  "source_commit": "634e4641acc3b02ddddb31d34bbd4eb11c312bed",
  "selected_root": "examples/platform",
  "engine": {
    "name": "avmigrate",
    "version": "1.10.0",
    "origin": "https://gist.github.com/ChrisSidebotham/0a3b2e9809a9831692c3bb2bff4c0a54",
    "artifact_origin": "https://gist.githubusercontent.com/ChrisSidebotham/0a3b2e9809a9831692c3bb2bff4c0a54/raw/avmigrate.py",
    "revision": "sha256:82fe2166af1c5d7c91c4211896b03f866e8854d77d77f5b94e71a10274265911",
    "git_revision": null,
    "git_revision_status": "Upstream history was unavailable during import; content-pinned distribution, not a verified upstream Git commit.",
    "sha256": "82fe2166af1c5d7c91c4211896b03f866e8854d77d77f5b94e71a10274265911",
    "catalogue_version": "2",
    "catalogue_generated": "2026-09-07T08:53:47Z",
    "built": "2026-09-07 09:16:17 UTC",
    "author": "ChrisSidebotham",
    "distribution": "Original single-file bundled Python artifact; original source tree and upstream tests are not included.",
    "license_status": "No license notice found in the accessible gist README or artifact. Confirm upstream distribution permission; repository MIT license is not asserted over this artifact."
  }
}
```

## Converter result before publication

```json
{
  "schema_version": 1,
  "run_id": "b4c07196-9393-457d-8c9a-8e8a0826fd4f",
  "factory_version": "0.1.0",
  "source_commit": "634e4641acc3b02ddddb31d34bbd4eb11c312bed",
  "selected_root": "examples/platform",
  "mode": "report",
  "engine": {
    "name": "avmigrate",
    "version": "1.10.0",
    "origin": "https://gist.github.com/ChrisSidebotham/0a3b2e9809a9831692c3bb2bff4c0a54",
    "artifact_origin": "https://gist.githubusercontent.com/ChrisSidebotham/0a3b2e9809a9831692c3bb2bff4c0a54/raw/avmigrate.py",
    "revision": "sha256:82fe2166af1c5d7c91c4211896b03f866e8854d77d77f5b94e71a10274265911",
    "git_revision": null,
    "git_revision_status": "Upstream history was unavailable during import; content-pinned distribution, not a verified upstream Git commit.",
    "sha256": "82fe2166af1c5d7c91c4211896b03f866e8854d77d77f5b94e71a10274265911",
    "catalogue_version": "2",
    "catalogue_generated": "2026-09-07T08:53:47Z",
    "built": "2026-09-07 09:16:17 UTC",
    "author": "ChrisSidebotham",
    "distribution": "Original single-file bundled Python artifact; original source tree and upstream tests are not included.",
    "license_status": "No license notice found in the accessible gist README or artifact. Confirm upstream distribution permission; repository MIT license is not asserted over this artifact."
  },
  "stages": {
    "intake": {
      "status": "passed",
      "resources_inspected": 7,
      "provider_requirement_blocks": 1
    },
    "plan_repair": {
      "status": "passed",
      "exit_code": 0,
      "diagnostic": "private/06-avmigrate-plan.log"
    },
    "apply_fixes": {
      "status": "not_assessed",
      "reason": "Report-only mode"
    },
    "validate": {
      "status": "not_assessed",
      "reason": "Converted code was not generated"
    },
    "promote": {
      "status": "not_assessed",
      "reason": "No meaningful repair diff; no PR"
    }
  },
  "changed_files": [],
  "findings": [
    {
      "code": "upstream_license_unverified",
      "severity": "warning",
      "message": "Confirm upstream artifact distribution permission; no license notice was available.",
      "review_required": true
    },
    {
      "code": "resource_not_converted",
      "severity": "warning",
      "message": "A resource is unsupported, excluded or requires manual conversion.",
      "review_required": true
    },
    {
      "code": "engine_finding",
      "severity": "info",
      "message": "Review the private avmigrate report and generated migration instructions.",
      "review_required": false
    },
    {
      "code": "engine_finding",
      "severity": "info",
      "message": "Review the private avmigrate report and generated migration instructions.",
      "review_required": false
    },
    {
      "code": "engine_finding",
      "severity": "info",
      "message": "Review the private avmigrate report and generated migration instructions.",
      "review_required": false
    },
    {
      "code": "engine_finding",
      "severity": "info",
      "message": "Review the private avmigrate report and generated migration instructions.",
      "review_required": false
    },
    {
      "code": "state_review_required",
      "severity": "error",
      "message": "State transitions are unresolved; review IDs, types and destinations manually.",
      "review_required": true
    }
  ],
  "state_migration_actions": [
    {
      "source": "azurerm_resource_group.platform",
      "destination": "module.platform.azapi_resource.this[0]",
      "kind": "cross_type_adoption",
      "status": "blocked",
      "requires_resource_id": true,
      "resource_id_status": "missing",
      "reason": "Manual state-aware review required; no state commands executed."
    },
    {
      "source": "azurerm_log_analytics_workspace.platform",
      "destination": "module.log_analytics_workspace_platform.azurerm_log_analytics_workspace.this[0]",
      "kind": "compatible_address_move",
      "status": "blocked",
      "requires_resource_id": false,
      "resource_id_status": "not_assessed",
      "reason": "Manual state-aware review required; no state commands executed."
    },
    {
      "source": "azurerm_virtual_network.platform",
      "destination": "module.virtual_network_platform.azapi_resource.vnet",
      "kind": "cross_type_adoption",
      "status": "blocked",
      "requires_resource_id": true,
      "resource_id_status": "missing",
      "reason": "Manual state-aware review required; no state commands executed."
    },
    {
      "source": "azurerm_network_security_group.application",
      "destination": "module.application.azurerm_network_security_group.this[0]",
      "kind": "compatible_address_move",
      "status": "blocked",
      "requires_resource_id": false,
      "resource_id_status": "not_assessed",
      "reason": "Manual state-aware review required; no state commands executed."
    },
    {
      "source": "azurerm_storage_account.application",
      "destination": "module.storage_account_application.azapi_resource.this[0]",
      "kind": "cross_type_adoption",
      "status": "blocked",
      "requires_resource_id": true,
      "resource_id_status": "missing",
      "reason": "Manual state-aware review required; no state commands executed."
    },
    {
      "source": "azurerm_subnet.application",
      "destination": null,
      "kind": "ambiguous_absorbed_destination",
      "status": "blocked",
      "reason": "Resolve keyed internal address and transition manually."
    }
  ],
  "static_validation": {
    "status": "not_assessed"
  },
  "live_plan": {
    "status": "not_assessed",
    "reason": "No authenticated, state-aware assessment was performed."
  },
  "deployment_readiness": "blocked",
  "terraform_version": "1.13.5",
  "terraform_version_gate": {
    "status": "passed",
    "exit_code": 0,
    "diagnostic": "private/01-terraform-version.log"
  },
  "baseline": {
    "static_validation": {
      "format": {
        "status": "passed",
        "exit_code": 0,
        "diagnostic": "private/03-baseline-fmt.log"
      },
      "init": {
        "status": "passed",
        "exit_code": 0,
        "diagnostic": "private/04-baseline-init.log"
      },
      "validate": {
        "status": "passed",
        "exit_code": 0,
        "diagnostic": "private/05-baseline-validate.log"
      }
    },
    "avmigrate_check": {
      "status": "passed",
      "exit_code": 0,
      "diagnostic": "private/02-baseline-check.log"
    },
    "blocking_todos": 0
  }
}
```
