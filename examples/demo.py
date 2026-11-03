"""Rehearse conversion using only a disposable, synthetic consumer repository."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from terraform_to_avm.runner import run


FACTORY = Path(__file__).resolve().parents[1]
SCENARIOS = ("report", "repair", "no-change")


def git(repository, *args):
    return subprocess.check_output(
        ["git", "-C", str(repository), *args], stderr=subprocess.PIPE)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_bundle(output, scenario, exit_code):
    result = json.loads((output / "result.json").read_text())
    manifest = json.loads((output / "repair-manifest.json").read_text())
    changes = json.loads((output / "changes.json").read_text())
    require(not (output / "private/failure.txt").exists(),
            "Conversion failed; inspect the synthetic diagnostic bundle")
    require(result["terraform_version"] == "1.9.8", "Use Terraform 1.9.8 for this demo")
    for name in ("intake", "plan_repair"):
        require(result["stages"][name]["status"] == "passed", f"{name} did not pass")
    require(result["live_plan"]["status"] == "not_assessed",
            "The demo must never claim a live plan")
    require(result["deployment_readiness"] == "blocked",
            "The demo must never authorize deployment")
    for phase in ("baseline", "candidate"):
        if phase == "candidate" and scenario == "report":
            continue
        gates = (result["baseline"]["static_validation"] if phase == "baseline"
                 else result["static_validation"]["gates"])
        for name in ("format", "init", "validate"):
            require(gates[name]["status"] == "passed",
                    f"{phase} {name} did not pass; inspect synthetic diagnostics")
    expected = 0 if scenario == "no-change" else 3
    require(len(manifest["proposed_changes"]) == expected,
            f"Expected exactly {expected} conversions")
    require(result["stages"]["intake"]["resources_inspected"] == expected,
            "Nested production root was unexpectedly inventoried")
    if scenario == "repair":
        require(bool(changes["files"]), "Repair must produce a reviewable diff")
        require((output / "repair.patch").is_file(), "Repair patch is missing")
        main = next(item for item in changes["files"] if item["path"] == "infra/main.tf")
        require(main["content"].count('"Azure/avm-res-resources-resourcegroup/azurerm"') == 3,
                "Expected three AVM resource-group module sources")
        require('resource "azurerm_resource_group"' not in main["content"],
                "Selected legacy resources were not converted")
        require(len(result["state_migration_actions"]) == 3,
                "Expected three explicit state-adoption reviews")
        require(all(item["status"] == "blocked" and item["requires_resource_id"]
                    for item in result["state_migration_actions"]),
                "State-adoption requirements must remain blocked")
    else:
        require(changes["files"] == [] and result["changed_files"] == [],
                "Report/no-change runs must not produce repairs")
        require(not (output / "repair.patch").exists(),
                "Report/no-change runs must not produce a patch")
    require(all(Path(item["path"]).parent.as_posix() == "infra"
                for item in changes["files"]), "Repair escaped the selected root")
    if scenario == "report":
        require(exit_code == 0, "Report command unexpectedly failed")
        require(result["stages"]["apply_fixes"]["status"] == "not_assessed",
                "Report mode unexpectedly applied fixes")
        require(result["stages"]["validate"]["status"] == "not_assessed",
                "Report mode unexpectedly validated converted code")
    else:
        require(result["stages"]["apply_fixes"]["status"] == "passed",
                "Code rewriting did not pass")
        require(result["static_validation"]["gates"]["format_repair"]["status"] == "passed",
                "Repair formatting did not pass")
        require(result["static_validation"]["gates"]["avmigrate_check"]["status"] == "failed",
                "Expected unresolved adoption TODOs, not an unexplained green gate")
        require(result["stages"]["validate"]["status"] == "failed" and exit_code == 1,
                "Expected an honest nonzero conversion status for unresolved TODOs")
    return result, manifest, changes


def demonstrate(scenario, output):
    require(scenario in SCENARIOS, "Unknown demo scenario")
    output = Path(output).resolve()
    require(not output.exists(), "Use a new output directory")
    with tempfile.TemporaryDirectory(prefix="avm-synthetic-demo-") as directory:
        home = Path(directory)
        consumer = home / "consumer"
        root = consumer / "infra"
        nested = root / "production"
        nested.mkdir(parents=True)
        shutil.copyfile(FACTORY / "examples/demo/main.tf", root / "main.tf")
        shutil.copyfile(FACTORY / "examples/legacy/main.tf", nested / "main.tf")
        shutil.copyfile(FACTORY / "examples/config.json", consumer / "avm.json")
        git(consumer, "init", "-q")
        git(consumer, "config", "user.name", "Synthetic AVM demo")
        git(consumer, "config", "user.email", "demo@example.invalid")
        git(consumer, "add", "infra/main.tf", "infra/production/main.tf", "avm.json")
        git(consumer, "commit", "-qm", "Synthetic legacy resource groups")
        if scenario == "no-change":
            seed = home / "seed"
            code = run(consumer, consumer / "avm.json", "infra", "repair", seed)
            verify_bundle(seed, "repair", code)
            git(consumer, "apply", "--check", str(seed / "repair.patch"))
            git(consumer, "apply", str(seed / "repair.patch"))
            seed_changes = json.loads((seed / "changes.json").read_text())
            git(consumer, "add", "--", *(item["path"] for item in seed_changes["files"]))
            git(consumer, "commit", "-qm", "Accept synthetic code repair, not state adoption")
        before = (root / "main.tf").read_bytes()
        nested_before = (nested / "main.tf").read_bytes()
        mode = "report" if scenario == "report" else "repair"
        code = run(consumer, consumer / "avm.json", "infra", mode, output)
        result, manifest, changes = verify_bundle(output, scenario, code)
        require(git(consumer, "status", "--porcelain") == b"",
                "Conversion modified the consuming checkout")
        require((root / "main.tf").read_bytes() == before,
                "Conversion edited source in place")
        require((nested / "main.tf").read_bytes() == nested_before,
                "Conversion modified nested production")
        if scenario == "repair":
            git(consumer, "apply", "--check", str(output / "repair.patch"))
            repeat = home / "repeat"
            repeat_code = run(consumer, consumer / "avm.json", "infra", mode, repeat)
            _, _, repeated_changes = verify_bundle(repeat, scenario, repeat_code)
            require(changes == repeated_changes, "Repeated repair changed the proposed files")
            require((output / "repair.patch").read_bytes() == (repeat / "repair.patch").read_bytes(),
                    "Repeated repair changed the patch")
            shutil.copyfile(repeat / "changes.json", output / "repeat-changes.json")
        (output / "source-before.tf").write_bytes(before)
        after = next((item["content"] for item in changes["files"]
                      if item["path"] == "infra/main.tf"), before.decode())
        (output / "source-after.tf").write_text(after)
        evidence = {
            "schema_version": 1,
            "scenario": scenario,
            "synthetic_only": True,
            "factory_commit": git(FACTORY, "rev-parse", "HEAD").decode().strip(),
            "conversion_exit_code": code,
            "proposed_conversions": len(manifest["proposed_changes"]),
            "changed_files": len(changes["files"]),
            "consumer_unchanged": True,
            "nested_root_unchanged": True,
            "patch_applies": True if scenario == "repair" else None,
            "repeat_identical": True if scenario == "repair" else None,
            "live_plan": result["live_plan"]["status"],
            "deployment_readiness": result["deployment_readiness"],
        }
        (output / "demo-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        summary = (
            f"## Synthetic demo: {scenario}\n\n"
            f"**Demo assertions passed.** Proposed conversions: {len(manifest['proposed_changes'])}; "
            f"changed files: {len(changes['files'])}; converter exit code: {code}.\n\n"
            "Real Terraform 1.9.8 formatting, backend-free initialization, and validation passed. "
            "The consuming checkout and nested production root are unchanged.\n\n"
            + ("Repeated repairs produced identical files and patches; patch applicability passed.\n\n"
               if scenario == "repair" else "")
            + "A successful demo job means its assertions passed, **not deployment approval**. "
            "Missing resource IDs and migration TODOs remain explicit blockers. "
            "Live plan: **not assessed**. Deployment readiness: **blocked**. "
            "No Azure credentials, deployment, import, state changes, or PR publication.\n\n"
            "Artifacts contain only the disposable synthetic fixture, not a consumer checkout. "
            "Do not use this public demo path for real infrastructure.\n"
        )
        (output / "demo-summary.md").write_text(summary)
        print(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    demonstrate(args.scenario, args.output)


if __name__ == "__main__":
    main()
