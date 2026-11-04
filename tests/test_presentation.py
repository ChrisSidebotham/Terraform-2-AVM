import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from examples.presentation import REPORT, prepare
from terraform_to_avm.runner import Commands, digest


FACTORY = Path(__file__).resolve().parents[1]


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.repo = self.home / "repo"
        self.root = self.repo / "examples/demo"
        self.root.mkdir(parents=True)
        shutil.copyfile(FACTORY / "examples/demo/main.tf", self.root / "main.tf")
        shutil.copyfile(FACTORY / "examples/config.json", self.repo / "avm.json")
        nested = self.root / "production"
        nested.mkdir()
        shutil.copyfile(FACTORY / "examples/legacy/main.tf", nested / "main.tf")
        self.git("init", "-q")
        self.git("config", "user.name", "Presentation test")
        self.git("config", "user.email", "test@example.invalid")
        self.commit()
        original = Commands.run

        def commands(instance, args, cwd, label):
            if args[0] == "terraform":
                if args[1] == "version":
                    version = instance.private / "mock-version.json"
                    version.write_text('{"terraform_version":"1.9.8"}')
                    return {"status": "passed", "diagnostic": "private/mock-version.json"}
                return {"status": "passed", "exit_code": 0}
            return original(instance, args, cwd, label)

        mock = patch.object(Commands, "run", commands)
        mock.start()
        self.addCleanup(mock.stop)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args],
                                       stderr=subprocess.PIPE)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "Committed presentation folder")

    def execute(self, mode, name=None):
        output = self.home / (name or mode)
        code = prepare(self.repo, self.repo / "avm.json", "examples/demo", mode, output)
        return code, output, json.loads((output / "changes.json").read_text())

    def test_report_contains_plan_but_never_changes_terraform_or_checkout(self):
        code, output, changes = self.execute("report")
        self.assertEqual(code, 0)
        self.assertEqual([item["path"] for item in changes["files"]],
                         ["examples/demo/" + REPORT])
        report = changes["files"][0]
        self.assertIsNone(report["before_sha256"])
        self.assertIn("azurerm_resource_group", report["content"])
        self.assertIn("Conversion manifest", report["content"])
        self.assertIn("Converter result before publication", report["content"])
        self.assertIn("Deployment readiness: **blocked**", report["content"])
        self.assertFalse((output / "repair.patch").exists())
        self.assertEqual(self.git("status", "--porcelain"), b"")
        self.assertEqual(json.loads((output / "result.json").read_text())["changed_files"],
                         ["examples/demo/" + REPORT])

    def test_repair_keeps_failed_adoption_gate_and_proposes_modules_with_report(self):
        code, output, changes = self.execute("repair")
        self.assertEqual(code, 1)
        main = next(item for item in changes["files"] if item["path"] == "examples/demo/main.tf")
        self.assertEqual(main["content"].count('"Azure/avm-res-resources-resourcegroup/azurerm"'), 3)
        self.assertEqual(changes["files"][-1]["path"], "examples/demo/" + REPORT)
        self.assertIn("Converter exit code: **1**", changes["files"][-1]["content"])
        self.assertIn('"avmigrate_check"', changes["files"][-1]["content"])
        self.assertTrue((output / "repair.patch").is_file())
        self.assertFalse(any("/production/" in item["path"] for item in changes["files"]))
        result = json.loads((output / "result.json").read_text())
        self.assertEqual(result["stages"]["validate"]["status"], "failed")
        self.assertEqual(result["deployment_readiness"], "blocked")
        self.assertEqual(self.git("status", "--porcelain"), b"")

    def test_no_change_repair_still_has_report_for_a_pr(self):
        _, output, _ = self.execute("repair")
        self.git("apply", str(output / "repair.patch"))
        self.commit()
        code, _, changes = self.execute("repair", "repeat")
        self.assertEqual(code, 1)
        self.assertEqual([item["path"] for item in changes["files"]],
                         ["examples/demo/" + REPORT])

    def test_existing_committed_report_has_verified_before_hash(self):
        before = b"# Previous report\n"
        (self.root / REPORT).write_bytes(before)
        self.commit()
        _, _, changes = self.execute("report")
        self.assertEqual(changes["files"][0]["before_sha256"], digest(before))

    def test_failure_retains_diagnostics_without_preparing_publication(self):
        (self.repo / "avm.json").write_text('{"unknown":true}')
        self.commit()
        with self.assertRaisesRegex(ValueError, "reviewable bundle"):
            self.execute("repair")
        output = self.home / "repair"
        self.assertTrue((output / "private/failure.txt").exists())
        self.assertEqual(json.loads((output / "changes.json").read_text())["files"], [])

    def test_config_cannot_escape_repository(self):
        outside = self.home / "outside.json"
        outside.write_text("{}")
        with self.assertRaises(ValueError):
            prepare(self.repo, outside, "examples/demo", "report", self.home / "outside")
        self.assertFalse((self.home / "outside").exists())

    def test_selected_repository_root_is_supported(self):
        self.git("mv", "examples/demo/main.tf", "main.tf")
        self.commit()
        output = self.home / "dot"
        prepare(self.repo, self.repo / "avm.json", ".", "report", output)
        changes = json.loads((output / "changes.json").read_text())
        self.assertEqual([item["path"] for item in changes["files"]], [REPORT])

    def test_platform_demo_has_cross_file_conversions_absorption_and_manual_review(self):
        root = self.repo / "examples/platform"
        shutil.copytree(FACTORY / "examples/platform", root)
        self.commit()
        for mode in ("report", "repair"):
            with self.subTest(mode=mode):
                output = self.home / ("platform-" + mode)
                code = prepare(self.repo, root / "config.json", "examples/platform", mode, output)
                result = json.loads((output / "result.json").read_text())
                manifest = json.loads((output / "repair-manifest.json").read_text())
                changes = json.loads((output / "changes.json").read_text())
                self.assertEqual(result["stages"]["intake"]["resources_inspected"], 7)
                proposals = manifest["proposed_changes"]
                self.assertEqual(len(proposals), 6)
                self.assertEqual(len({item["module"] for item in proposals if "module" in item}), 5)
                subnet = next(item for item in proposals if item["from"] == "azurerm_subnet.application")
                self.assertEqual(subnet["input"], "subnets")
                self.assertTrue(any(f["code"] == "resource_not_converted" for f in result["findings"]))
                self.assertEqual({action["kind"] for action in result["state_migration_actions"]},
                                 {"compatible_address_move", "cross_type_adoption",
                                  "ambiguous_absorbed_destination"})
                self.assertEqual(changes["files"][-1]["path"], "examples/platform/" + REPORT)
                if mode == "report":
                    self.assertEqual(code, 0)
                    self.assertEqual(len(changes["files"]), 1)
                    self.assertFalse((output / "repair.patch").exists())
                else:
                    self.assertEqual(code, 1)
                    converted = {item["path"]: item["content"] for item in changes["files"]}
                    self.assertIn('resource "azurerm_network_security_rule" "https"',
                                  converted["examples/platform/network.tf"])
                    self.assertNotIn('resource "azurerm_subnet"',
                                     converted["examples/platform/network.tf"])
                    self.assertIn("module.virtual_network_platform.resource_id",
                                  converted["examples/platform/outputs.tf"])
                    self.assertNotIn("azurerm_resource_group.platform",
                                     converted["examples/platform/storage.tf"])
                    self.assertIn('source  = "Azure/avm-res-storage-storageaccount/azurerm"',
                                  converted["examples/platform/storage.tf"])
                    self.git("apply", "--check", str(output / "repair.patch"))
                self.assertEqual(self.git("status", "--porcelain"), b"")
                self.assertEqual(result["deployment_readiness"], "blocked")


if __name__ == "__main__":
    unittest.main()
