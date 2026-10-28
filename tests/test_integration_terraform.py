"""Opt-in public provider/module downloads; no Azure credentials or live state."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from terraform_to_avm.runner import run


@unittest.skipUnless(os.environ.get("TERRAFORM_TO_AVM_INTEGRATION") == "1",
                     "Network-dependent Terraform integration is opt-in")
class TerraformIntegration(unittest.TestCase):
    def test_real_backend_free_validation(self):
        self.assertIsNotNone(shutil.which("terraform"), "Install Terraform 1.9.8 first")
        factory = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = home / "repo"
            root = repo / "infra"
            root.mkdir(parents=True)
            shutil.copyfile(factory / "examples/legacy/main.tf", root / "main.tf")
            shutil.copyfile(factory / "examples/config.json", repo / "config.json")
            for args in (["init", "-q"], ["config", "user.name", "Integration"],
                         ["config", "user.email", "integration@example.invalid"],
                         ["add", "."], ["commit", "-qm", "fixture"]):
                subprocess.run(["git", "-C", str(repo), *args], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            output = home / "reports"
            run(repo, repo / "config.json", "infra", "repair", output)
            result = json.loads((output / "result.json").read_text())
            self.assertTrue(result["changed_files"])
            gates = result["static_validation"]["gates"]
            for name in ("format", "init", "validate"):
                self.assertEqual(gates[name]["status"], "passed",
                                 f"{name} failed: inspect private integration diagnostics")
            # Missing import IDs deliberately keep deployment readiness blocked.
            self.assertEqual(result["live_plan"]["status"], "not_assessed")
            self.assertEqual(result["deployment_readiness"], "blocked")
            self.assertEqual(subprocess.check_output(
                ["git", "-C", str(repo), "status", "--porcelain"]), b"")


if __name__ == "__main__":
    unittest.main()
