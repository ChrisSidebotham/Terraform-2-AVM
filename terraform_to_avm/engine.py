"""Small adapter to the unchanged, checksum-verified avmigrate distribution."""

import hashlib
import json
from pathlib import Path
import runpy
import sys

VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "avmigrate"
ARTIFACTS = {
    "AVM-MIGRATION-CHANGELOG.md",
    "avm-state-migration.sh",
    "avm-state-import.tf.disabled",
    "terraform-state-importer.config.yaml",
}


class Avmigrate:
    name = "avmigrate"

    def __init__(self):
        self.origin = json.loads((VENDOR / "provenance.json").read_text())
        self.path = VENDOR / "avmigrate.py"
        actual = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if actual != self.origin["sha256"]:
            raise ValueError("Vendored avmigrate checksum mismatch")
        runpy.run_path(str(self.path))
        from avmigrate.catalog import Catalog
        from avmigrate import hcl
        self.catalog = Catalog.load()
        self.hcl = hcl

    def command(self, operation, root, report, config):
        args = [sys.executable, str(self.path), operation, str(root),
                "--json", str(report)]
        if operation != "check":
            args += ["--quiet"]
            if operation == "apply":
                args += ["--no-backup"]
            for key, flag in (("allow", "--only"), ("exclude", "--exclude")):
                for resource in config[key]:
                    args += [flag, resource]
            if config["include_heuristic"]:
                args += ["--include-heuristic"]
            if config["bump_providers"]:
                args += ["--bump-providers"]
        return args

    def inventory(self, root):
        resources, providers = [], []
        for path in sorted(root.glob("*.tf")):
            body = self.hcl.parse(path.read_text())
            for block in body.blocks("resource"):
                if len(block.labels) == 2:
                    resources.append({"type": block.labels[0],
                                      "address": ".".join(block.labels),
                                      "file": path.name})
            for block in body.blocks("terraform"):
                for required in block.body.blocks("required_providers"):
                    providers.append({"file": path.name, "hcl": required.render()})
        return resources, providers

    def state_actions(self, plan):
        actions = []
        for item in plan.get("converted", []):
            module = (self.catalog.get(item["module"]) or
                      self.catalog.get_submodule(item["module"]))
            address = module.internal_address if module else ""
            # A same-provider hint alone is insufficient: the resource TYPE must match.
            same_type = bool(address and
                             address.split(".")[0] == item["from"].split(".")[0])
            kind = ("compatible_address_move" if module and module.state_movable
                    and same_type else "cross_type_adoption" if address
                    else "unknown_destination")
            actions.append({
                "source": item["from"],
                "destination": f'{item["to"]}.{address}' if address else None,
                "kind": kind,
                "status": "blocked",
                "requires_resource_id": kind == "cross_type_adoption",
                "resource_id_status": "missing" if kind == "cross_type_adoption"
                else "not_assessed",
                "reason": "Manual state-aware review required; no state commands executed.",
            })
        for item in plan.get("absorbed", []):
            actions.append({"source": item["from"], "destination": None,
                            "kind": "ambiguous_absorbed_destination", "status": "blocked",
                            "reason": "Resolve keyed internal address and transition manually."})
        return actions
