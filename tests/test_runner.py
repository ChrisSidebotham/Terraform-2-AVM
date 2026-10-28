import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from terraform_to_avm.engine import Avmigrate
from terraform_to_avm.runner import Commands, load_config, run, selected_path


RESOURCE = '''resource "azurerm_resource_group" "example" {
  name = "example-rg"
  location = "uksouth"
}
'''


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.root = self.repo / "infra"
        self.root.mkdir()
        self.config = self.home / "config.json"
        self.config.write_text('{"schema_version": 1}')
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args],
                                       stderr=subprocess.DEVNULL)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def execute(self, mode="repair", root="infra", output="output"):
        dest = self.home / output
        # Terraform networking is deliberately excluded from the standard suite.
        original = Commands.run

        def commands(instance, args, cwd, label):
            if args[0] == "terraform":
                if args[1] == "version":
                    path = instance.private / "mock-version.json"
                    path.write_text('{"terraform_version":"1.9.8"}')
                    return {"status": "passed", "diagnostic": "private/mock-version.json"}
                return {"status": "passed", "exit_code": 0}
            return original(instance, args, cwd, label)

        with patch.object(Commands, "run", commands):
            code = run(self.repo, self.config, root, mode, dest)
        return code, json.loads((dest / "result.json").read_text()), dest

    def test_report_only_does_not_change_any_source(self):
        (self.root / "main.tf").write_text(RESOURCE)
        self.commit()
        before = (self.root / "main.tf").read_bytes()
        code, result, dest = self.execute(mode="report")
        self.assertEqual(code, 0)
        self.assertEqual((self.root / "main.tf").read_bytes(), before)
        self.assertEqual(self.git("status", "--porcelain"), b"")
        self.assertEqual(result["changed_files"], [])
        self.assertTrue(json.loads((dest / "repair-manifest.json").read_text())["proposed_changes"])
        self.assertTrue((dest / "private/plan-artifacts/avm-state-migration.sh").exists())

    def test_supported_conversion_and_no_live_safety_claim(self):
        (self.root / "main.tf").write_text(RESOURCE)
        self.commit()
        code, result, dest = self.execute()
        changes = json.loads((dest / "changes.json").read_text())["files"]
        main = next(f for f in changes if f["path"] == "infra/main.tf")
        self.assertIn('module "example"', main["content"])
        self.assertNotIn('resource "azurerm_resource_group"', main["content"])
        self.assertEqual(main["before_sha256"], hashlib.sha256(RESOURCE.encode()).hexdigest())
        self.assertEqual(result["live_plan"]["status"], "not_assessed")
        self.assertEqual(result["deployment_readiness"], "blocked")
        self.assertTrue(result["state_migration_actions"])
        self.assertEqual(result["state_migration_actions"][0]["kind"], "cross_type_adoption")
        self.assertTrue(result["state_migration_actions"][0]["requires_resource_id"])
        self.assertEqual((self.root / "main.tf").read_text(), RESOURCE)
        self.assertTrue((dest / "repair.patch").exists())

    def test_unsupported_azapi_and_azurerm_are_explicit(self):
        (self.root / "main.tf").write_text(
            'resource "azapi_resource" "example" { name = "untouched" }\n'
            'resource "azurerm_not_real" "example" { name = "untouched" }\n')
        self.commit()
        _, result, dest = self.execute()
        codes = {f["code"] for f in result["findings"]}
        self.assertIn("unsupported_source", codes)
        self.assertIn("resource_not_converted", codes)
        self.assertEqual(json.loads((dest / "changes.json").read_text())["files"], [])

    def test_nested_root_is_never_converted_or_scanned(self):
        (self.root / "main.tf").write_text(RESOURCE)
        nested = self.root / "production"
        nested.mkdir()
        (nested / "main.tf").write_text(RESOURCE)
        self.commit()
        _, result, _ = self.execute()
        self.assertTrue(result["changed_files"])
        self.assertFalse(any("production" in f for f in result["changed_files"]))
        self.assertEqual(result["stages"]["intake"]["resources_inspected"], 1)

    def test_baseline_blocking_todo_is_reported(self):
        (self.root / "main.tf").write_text(
            'module "existing" {\n# TODO(avmigrate):\n# name = <required>\n}\n')
        self.commit()
        _, result, _ = self.execute()
        self.assertEqual(result["baseline"]["blocking_todos"], 1)
        self.assertIn("existing_todos", [f["code"] for f in result["findings"]])
        self.assertEqual(result["stages"]["validate"]["status"], "failed")

    def test_repeated_conversion_then_no_change(self):
        (self.root / "main.tf").write_text(RESOURCE)
        self.commit()
        _, _, dest = self.execute()
        first = json.loads((dest / "changes.json").read_text())["files"]
        _, _, repeat = self.execute(output="repeat")
        second = json.loads((repeat / "changes.json").read_text())["files"]
        self.assertEqual(first, second)
        for item in first:
            path = self.repo / item["path"]
            if item["content"] is None:
                path.unlink()
            else:
                path.write_text(item["content"])
        self.commit()
        _, result, dest = self.execute(output="after")
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(json.loads((dest / "changes.json").read_text())["files"], [])
        self.assertEqual(result["static_validation"]["gates"]["avmigrate_check"]["status"],
                         "failed")

    def test_invalid_root_preserves_diagnostics(self):
        (self.root / "main.tf").write_text(RESOURCE)
        self.commit()
        for n, root in enumerate(("../other", "/tmp", "infra//nested", ".git", "infra\\other")):
            code, result, dest = self.execute(root=root, output=f"bad-{n}")
            self.assertEqual(code, 1)
            self.assertEqual(result["stages"]["intake"]["status"], "failed")
            self.assertTrue((dest / "private/failure.txt").exists())

    def test_symlink_is_rejected(self):
        (self.root / "main.tf").write_text(RESOURCE)
        (self.root / "linked.tf").symlink_to("main.tf")
        self.commit()
        _, result, _ = self.execute()
        self.assertEqual(result["stages"]["intake"]["status"], "failed")

    def test_failed_engine_subprocess_preserves_reports(self):
        (self.root / "main.tf").write_text(RESOURCE)
        self.commit()
        with patch.object(Avmigrate, "command", return_value=[
                "/definitely-missing-avmigrate-command"]):
            _, result, dest = self.execute()
        self.assertEqual(result["stages"]["plan_repair"]["status"], "failed")
        self.assertTrue((dest / "private/failure.txt").exists())
        self.assertTrue((dest / "summary.md").exists())

    def test_unexpected_engine_file_blocks_publication_bundle(self):
        (self.root / "main.tf").write_text(RESOURCE)
        self.commit()
        original = Commands.run

        def commands(instance, args, cwd, label):
            gate = original(instance, args, cwd, label)
            if label == "avmigrate-apply":
                (Path(cwd) / "unexpected.tfvars").write_text('password = "not-for-publication"')
            return gate

        with patch.object(Commands, "run", commands):
            run(self.repo, self.config, "infra", "repair", self.home / "unexpected")
        dest = self.home / "unexpected"
        result = json.loads((dest / "result.json").read_text())
        self.assertEqual(result["stages"]["apply_fixes"]["status"], "failed")
        self.assertEqual(json.loads((dest / "changes.json").read_text())["files"], [])

    def test_missing_terraform_is_blocked_not_passed(self):
        (self.root / "main.tf").write_text(RESOURCE)
        self.commit()
        original = Commands.run

        def commands(instance, args, cwd, label):
            if args[0] == "terraform":
                return {"status": "blocked", "reason": "Executable unavailable"}
            return original(instance, args, cwd, label)

        with patch.object(Commands, "run", commands):
            run(self.repo, self.config, "infra", "repair", self.home / "missing-tf")
        result = json.loads((self.home / "missing-tf/result.json").read_text())
        self.assertIsNone(result["terraform_version"])
        self.assertEqual(result["static_validation"]["gates"]["init"]["status"], "blocked")
        self.assertEqual(result["static_validation"]["gates"]["validate"]["status"], "blocked")
        self.assertEqual(result["live_plan"]["status"], "not_assessed")

    def test_formatting_alone_is_not_a_meaningful_migration(self):
        (self.root / "main.tf").write_text("locals {value=1}\n")
        self.commit()
        original = Commands.run

        def commands(instance, args, cwd, label):
            if args[0] == "terraform":
                if label == "repair-fmt":
                    (Path(cwd) / "main.tf").write_text("locals {\n  value = 1\n}\n")
                return {"status": "blocked"} if args[1] == "version" else {"status": "passed"}
            return original(instance, args, cwd, label)

        with patch.object(Commands, "run", commands):
            run(self.repo, self.config, "infra", "repair", self.home / "format-only")
        result = json.loads((self.home / "format-only/result.json").read_text())
        self.assertEqual(result["changed_files"], [])
        self.assertFalse((self.home / "format-only/repair.patch").exists())

    def test_existing_migration_instructions_are_not_overwritten(self):
        (self.root / "main.tf").write_text(RESOURCE)
        instructions = '# Previously reviewed import instructions\nid = "reviewed-resource-id"\n'
        (self.root / "avm-state-import.tf.disabled").write_text(instructions)
        self.commit()
        code, result, dest = self.execute()
        self.assertEqual(code, 1)
        self.assertEqual(result["stages"]["apply_fixes"]["status"], "blocked")
        self.assertEqual(result["changed_files"], [])
        self.assertEqual((self.root / "avm-state-import.tf.disabled").read_text(), instructions)
        self.assertEqual((dest / "private/existing-migration-artifacts/avm-state-import.tf.disabled")
                         .read_text(), instructions)
        self.assertIn("existing_migration_instructions", [f["code"] for f in result["findings"]])

    def test_introduced_failure_is_distinct_from_baseline(self):
        (self.root / "main.tf").write_text(RESOURCE)
        self.commit()
        original = Commands.run

        def commands(instance, args, cwd, label):
            if args[0] == "terraform":
                if args[1] == "version":
                    return {"status": "blocked"}
                return {"status": "failed" if label == "candidate-validate" else "passed"}
            return original(instance, args, cwd, label)

        with patch.object(Commands, "run", commands):
            run(self.repo, self.config, "infra", "repair", self.home / "validation")
        result = json.loads((self.home / "validation/result.json").read_text())
        self.assertEqual(result["static_validation"]["introduced_failures"], ["validate"])
        self.assertEqual(result["static_validation"]["baseline_failures"], [])

    def test_source_secrets_never_enter_public_summary(self):
        (self.root / "main.tf").write_text(RESOURCE.replace(
            'location = "uksouth"', 'location = "uksouth"\n  secret_unknown = "do-not-publish-this"'))
        self.commit()
        _, _, dest = self.execute()
        for filename in ("result.json", "summary.md", "repair-manifest.json"):
            self.assertNotIn("do-not-publish-this", (dest / filename).read_text())


class ConfigurationTests(unittest.TestCase):
    def test_subprocesses_do_not_inherit_ci_or_cloud_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp)
            commands = Commands(private, 30)
            with patch.dict("os.environ", {
                    "ACTIONS_RUNTIME_TOKEN": "fixture",
                    "ACTIONS_CACHE_URL": "https://example.invalid",
                    "GITHUB_TOKEN": "fixture", "GITHUB_ENV": "/tmp/not-to-write",
                    "GH_TOKEN": "fixture", "ARM_CLIENT_SECRET": "fixture",
                    "TF_DATA_DIR": "/tmp/not-to-use",
                    "TF_CLI_ARGS_init": "-backend=true"}):
                with patch("terraform_to_avm.runner.subprocess.run") as process:
                    process.return_value.returncode = 0
                    commands.run(["terraform", "version"], private, "environment")
                    env = process.call_args.kwargs["env"]
            for key in ("ACTIONS_RUNTIME_TOKEN", "ACTIONS_CACHE_URL", "GITHUB_TOKEN",
                        "GITHUB_ENV", "GH_TOKEN", "ARM_CLIENT_SECRET", "TF_DATA_DIR",
                        "TF_CLI_ARGS_init"):
                self.assertNotIn(key, env)

    def test_duplicate_config_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text('{"include_heuristic":false,"include_heuristic":true}')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_config(path)

    def test_risky_options_disabled_and_no_no_state(self):
        engine = Avmigrate()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{}")
            config = load_config(path)
        args = engine.command("apply", Path("/tmp/root"), Path("/tmp/report"), config)
        for flag in ("--include-heuristic", "--bump-providers", "--no-state"):
            self.assertNotIn(flag, args)
        self.assertIn("--no-backup", args)

    def test_config_strict_types_and_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for value in ({"no_state": True}, {"include_heuristic": "false"},
                          {"timeout_seconds": True}, {"allow": ["azapi_resource"]},
                          {"allow": ["azurerm_resource_group"],
                           "exclude": ["azurerm_resource_group"]}):
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_checksum_is_enforced(self):
        with patch("terraform_to_avm.engine.hashlib.sha256") as checksum:
            checksum.return_value.hexdigest.return_value = "bad"
            with self.assertRaisesRegex(ValueError, "checksum"):
                Avmigrate()

    def test_exact_type_needed_for_address_move(self):
        engine = Avmigrate()
        from types import SimpleNamespace
        with patch.object(engine.catalog, "get", return_value=SimpleNamespace(
                internal_address="azurerm_other.this", state_movable=True)):
            actions = engine.state_actions({"converted": [{
                "module": "test", "from": "azurerm_resource_group.rg", "to": "module.rg"}]})
        self.assertEqual(actions[0]["kind"], "cross_type_adoption")

    def test_compatible_move_and_unknown_destinations_remain_distinct(self):
        engine = Avmigrate()
        from types import SimpleNamespace
        plan = {"converted": [{"module": "test", "from": "azurerm_resource_group.rg",
                               "to": "module.rg"}]}
        with patch.object(engine.catalog, "get", return_value=SimpleNamespace(
                internal_address="azurerm_resource_group.this", state_movable=True)):
            self.assertEqual(engine.state_actions(plan)[0]["kind"], "compatible_address_move")
        with patch.object(engine.catalog, "get", return_value=None), patch.object(
                engine.catalog, "get_submodule", return_value=None):
            self.assertEqual(engine.state_actions(plan)[0]["kind"], "unknown_destination")


if __name__ == "__main__":
    unittest.main()
