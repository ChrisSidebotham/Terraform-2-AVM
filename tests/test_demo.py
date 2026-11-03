import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.demo import demonstrate, verify_bundle
from terraform_to_avm.runner import Commands


class SyntheticDemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
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

    def execute(self, scenario):
        output = self.home / scenario
        with contextlib.redirect_stdout(io.StringIO()):
            demonstrate(scenario, output)
        return output

    def test_scenarios_use_real_engine_and_explicit_expectations(self):
        for scenario, proposals, exit_code in (
                ("report", 3, 0), ("repair", 3, 1), ("no-change", 0, 1)):
            with self.subTest(scenario=scenario):
                output = self.execute(scenario)
                evidence = json.loads((output / "demo-evidence.json").read_text())
                self.assertEqual(evidence["proposed_conversions"], proposals)
                self.assertEqual(evidence["conversion_exit_code"], exit_code)
                self.assertTrue(evidence["synthetic_only"])
                self.assertTrue(evidence["consumer_unchanged"])
                self.assertTrue(evidence["nested_root_unchanged"])
                self.assertEqual(evidence["deployment_readiness"], "blocked")
                if scenario == "repair":
                    self.assertTrue(evidence["repeat_identical"])
                    self.assertTrue(evidence["patch_applies"])

    def test_unexpected_static_failure_is_not_a_successful_demo(self):
        output = self.execute("repair")
        path = output / "result.json"
        result = json.loads(path.read_text())
        result["static_validation"]["gates"]["init"]["status"] = "failed"
        path.write_text(json.dumps(result))
        with self.assertRaisesRegex(ValueError, "candidate init did not pass"):
            verify_bundle(output, "repair", 1)

    def test_unexpected_green_adoption_gate_is_rejected(self):
        output = self.execute("repair")
        path = output / "result.json"
        result = json.loads(path.read_text())
        result["static_validation"]["gates"]["avmigrate_check"]["status"] = "passed"
        path.write_text(json.dumps(result))
        with self.assertRaisesRegex(ValueError, "Expected unresolved adoption TODOs"):
            verify_bundle(output, "repair", 1)

    def test_existing_output_is_not_overwritten(self):
        output = self.execute("report")
        before = (output / "result.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "Use a new output directory"):
            demonstrate("report", output)
        self.assertEqual((output / "result.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
