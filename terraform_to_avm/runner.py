"""Five-stage isolated repair runner. All subprocess diagnostics stay private."""

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import uuid

from . import __version__
from .engine import ARTIFACTS, Avmigrate

DEFAULTS = {"schema_version": 1, "allow": [], "exclude": [],
            "include_heuristic": False, "bump_providers": False, "timeout_seconds": 300}
STATUSES = ("passed", "blocked", "failed", "not_assessed")


def digest(content):
    return hashlib.sha256(content).hexdigest()


def selected_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("root must be a repository-relative POSIX path")
    if value == ".":
        return value
    parts = value.split("/")
    if any(p.lower() in ("", ".", "..", ".git", ".github", ".terraform",
                         ".terragrunt-cache", ".hg", ".svn") or
           not re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts):
        raise ValueError("root contains an unsafe path component")
    return value


def load_config(path):
    if Path(path).stat().st_size > 64 * 1024:
        raise ValueError("Configuration exceeds 64 KiB")
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValueError("Configuration contains unknown keys")
    config = {**DEFAULTS, **data}
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("Unsupported configuration schema_version")
    for key in ("include_heuristic", "bump_providers"):
        if type(config[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    for key in ("allow", "exclude"):
        if not isinstance(config[key], list) or any(
                not isinstance(x, str) or not re.fullmatch(r"azurerm_[a-z0-9_]+", x)
                for x in config[key]):
            raise ValueError(f"{key} must contain azurerm resource types")
    if set(config["allow"]) & set(config["exclude"]):
        raise ValueError("allow and exclude must not overlap")
    if type(config["timeout_seconds"]) is not int or not 1 <= config["timeout_seconds"] <= 1800:
        raise ValueError("timeout_seconds must be an integer from 1 to 1800")
    return config


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args],
                                   stderr=subprocess.DEVNULL)


def snapshot(repo, dest, commit):
    records = git(repo, "ls-tree", "-rz", "--full-tree", commit).split(b"\0")
    for record in filter(None, records):
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        name = raw_path.decode()
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError("Unsafe tracked path")
        if mode not in ("100644", "100755") or kind != "blob":
            raise ValueError("Symlinks and submodules are unsupported; use a plain checkout")
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git(repo, "cat-file", "blob", oid))


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def permitted(name, root):
    path = PurePosixPath(name)
    parent = "" if root == "." else root
    return (bool(re.fullmatch(r"[A-Za-z0-9_.-]+", path.name))
            and path.parent.as_posix() in (parent, "." if not parent else parent)
            and not (path.name.startswith(".") and path.name != ".terraform.lock.hcl")
            and (path.name.endswith(".tf") or path.name in ARTIFACTS
                 or path.name == ".terraform.lock.hcl"))


class Commands:
    def __init__(self, private, timeout):
        self.private, self.timeout, self.counter = private, timeout, 0

    def run(self, args, cwd, label):
        self.counter += 1
        log = self.private / f"{self.counter:02}-{label}.log"
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ARM_", "AZURE_", "AWS_", "TF_CLI_ARGS"))
               and k not in ("GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
                             "TF_DATA_DIR", "TF_LOG_PATH", "TF_LOG", "TF_LOG_CORE",
                             "TF_LOG_PROVIDER")}
        env.update(TF_IN_AUTOMATION="1", TF_INPUT="0")
        try:
            with log.open("wb") as stream:
                proc = subprocess.run(args, cwd=cwd, env=env, stdout=stream,
                                      stderr=subprocess.STDOUT, timeout=self.timeout,
                                      check=False)
            return {"status": "passed" if proc.returncode == 0 else "failed",
                    "exit_code": proc.returncode, "diagnostic": f"private/{log.name}"}
        except FileNotFoundError:
            log.write_text("Executable unavailable\n")
            return {"status": "blocked", "reason": "Executable unavailable",
                    "diagnostic": f"private/{log.name}"}
        except subprocess.TimeoutExpired:
            return {"status": "failed", "reason": "Command timed out",
                    "diagnostic": f"private/{log.name}"}


def static_gates(commands, root, phase):
    gates = {}
    gates["format"] = commands.run(["terraform", "fmt", "-check", "-diff", "-no-color"],
                                    root, f"{phase}-fmt")
    gates["init"] = commands.run(
        ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
        root, f"{phase}-init")
    gates["validate"] = (commands.run(["terraform", "validate", "-json"], root,
                                     f"{phase}-validate")
                         if gates["init"]["status"] == "passed" else
                         {"status": "blocked", "reason": "Backend-free init did not pass"})
    return gates


def outcome(gates):
    values = [g["status"] for g in gates.values()]
    return ("failed" if "failed" in values else "blocked" if "blocked" in values
            else "not_assessed" if "not_assessed" in values else "passed")


def read_report(path, expected):
    value = json.loads(path.read_text())
    if not isinstance(value, expected):
        raise ValueError("Invalid engine report")
    return value


def finding(result, code, severity, message):
    result["findings"].append({"code": code, "severity": severity, "message": message,
                               "review_required": severity in ("warning", "error")})


def run(repository, config_path, root, mode, output):
    output = Path(output).resolve()
    repo = Path(repository).resolve()
    if output == repo or repo in output.parents:
        raise ValueError("Output must be outside the consuming repository")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Output directory must be empty")
    private = output / "private"
    private.mkdir(mode=0o700)
    result = {
        "schema_version": 1, "run_id": str(uuid.uuid4()), "factory_version": __version__,
        "source_commit": None, "selected_root": root, "mode": mode, "engine": None,
        "stages": {s: {"status": "not_assessed"} for s in
                   ("intake", "plan_repair", "apply_fixes", "validate", "promote")},
        "changed_files": [], "findings": [], "state_migration_actions": [],
        "static_validation": {"status": "not_assessed"},
        "live_plan": {"status": "not_assessed",
                      "reason": "No authenticated, state-aware assessment was performed."},
        "deployment_readiness": "blocked",
    }
    manifest = {"schema_version": 1, "fixer": "avmigrate", "proposed_changes": [],
                "affected_files": [], "findings": [], "blockers": [],
                "state_migration_actions": []}
    changes = {"schema_version": 1, "source_commit": None, "selected_root": root, "files": []}
    stage = "intake"
    code = 1
    try:
        root = selected_path(root)
        if mode not in ("report", "repair"):
            raise ValueError("mode must be report or repair")
        config = load_config(config_path)
        engine = Avmigrate()
        result["engine"] = engine.origin
        finding(result, "upstream_license_unverified", "warning",
                "Confirm upstream artifact distribution permission; no license notice was available.")
        commit = git(repo, "rev-parse", "HEAD").decode().strip()
        result["source_commit"] = changes["source_commit"] = commit
        if git(repo, "status", "--porcelain", "--untracked-files=normal").strip():
            raise ValueError("Use a clean committed checkout; commit or remove local changes")
        with tempfile.TemporaryDirectory(prefix="terraform-to-avm-") as tmp:
            workspace = Path(tmp) / "checkout"
            workspace.mkdir()
            snapshot(repo, workspace, commit)
            selected = workspace / root
            if not selected.is_dir() or not list(selected.glob("*.tf")):
                raise ValueError("Selected root must contain immediate tracked .tf files")
            if list(selected.glob("*.tf.json")):
                raise ValueError(".tf.json roots are unsupported by the bundled engine")
            for path in selected.glob("*.tf"):
                if not permitted(path.relative_to(workspace).as_posix(), root):
                    raise ValueError("Terraform filenames must be plain, non-hidden path segments")
            original = files(workspace)
            isolated = Path(tmp) / "engine-root"
            isolated.mkdir()
            for path in selected.iterdir():
                if path.is_file() and (path.suffix == ".tf" or path.name in ARTIFACTS):
                    shutil.copyfile(path, isolated / path.name)
            resources, providers = engine.inventory(isolated)
            (private / "inventory.json").write_text(json.dumps(
                {"resources": resources, "provider_requirements": providers}, indent=2))
            commands = Commands(private, config["timeout_seconds"])
            version = commands.run(["terraform", "version", "-json"], selected, "terraform-version")
            result["terraform_version"] = None
            if version["status"] == "passed":
                version_data = read_report(output / version["diagnostic"], dict)
                result["terraform_version"] = version_data.get("terraform_version")
            result["terraform_version_gate"] = version
            baseline_check_path = private / "baseline-check.json"
            baseline_check = commands.run(
                engine.command("check", isolated, baseline_check_path, config),
                isolated, "baseline-check")
            baseline_items = read_report(baseline_check_path, list) if baseline_check_path.exists() else []
            baseline = static_gates(commands, selected, "baseline")
            result["baseline"] = {"static_validation": baseline, "avmigrate_check": baseline_check,
                                  "blocking_todos": sum(bool(i["blocking"]) for i in baseline_items)}
            # Baseline init can write locks and caches. Neither becomes input to repair.
            shutil.rmtree(workspace)
            workspace.mkdir()
            snapshot(repo, workspace, commit)
            selected = workspace / root
            result["stages"][stage] = {"status": "passed", "resources_inspected": len(resources),
                                       "provider_requirement_blocks": len(providers)}
            stage = "plan_repair"
            report_path = private / "plan.json"
            gate = commands.run(engine.command("plan", isolated, report_path, config),
                                isolated, "avmigrate-plan")
            if not report_path.exists():
                result["stages"][stage] = gate
                raise ValueError("avmigrate plan did not produce its machine-readable report")
            plan = read_report(report_path, dict)
            plan_artifacts = private / "plan-artifacts"
            plan_artifacts.mkdir()
            for name in ARTIFACTS:
                if (isolated / name).exists():
                    shutil.copyfile(isolated / name, plan_artifacts / name)
            result["stages"][stage] = gate
            manifest["proposed_changes"] = plan.get("converted", []) + plan.get("absorbed", [])
            manifest["affected_files"] = sorted({i["file"] for i in manifest["proposed_changes"]})
            result["state_migration_actions"] = engine.state_actions(plan)
            for item in plan.get("skipped", []):
                finding(result, "resource_not_converted", "warning",
                        "A resource is unsupported, excluded or requires manual conversion.")
            if any(not r["type"].startswith("azurerm_") for r in resources):
                finding(result, "unsupported_source", "warning",
                        "Non-AzureRM source resources are not supported and remain unchanged.")
            for item in plan.get("findings", []):
                severity = item.get("severity", "warning")
                finding(result, "engine_finding", severity,
                        "Review the private avmigrate report and generated migration instructions.")
            if baseline_items:
                finding(result, "existing_todos", "error" if any(
                    i["blocking"] for i in baseline_items) else "warning",
                    "Existing migration TODOs require explicit review.")
            if result["state_migration_actions"]:
                finding(result, "state_review_required", "error",
                        "State transitions are unresolved; review IDs, types and destinations manually.")
            if config["include_heuristic"] or config["bump_providers"]:
                finding(result, "risky_option_enabled", "warning",
                        "Heuristic matching or provider-major widening was explicitly enabled.")
            stage = "apply_fixes"
            if mode == "report":
                result["stages"][stage] = {"status": "not_assessed", "reason": "Report-only mode"}
                result["stages"]["validate"] = {
                    "status": "not_assessed", "reason": "Converted code was not generated"}
            else:
                # Remove plan-only artifacts before apply so no repeated changelog history leaks in.
                for name in ARTIFACTS:
                    (isolated / name).unlink(missing_ok=True)
                apply_path = private / "apply.json"
                applied = commands.run(engine.command("apply", isolated, apply_path, config),
                                       isolated, "avmigrate-apply")
                result["stages"][stage] = applied
                if not apply_path.exists() or applied.get("exit_code", 2) > 1:
                    raise ValueError("avmigrate apply failed without a usable conversion report")
                for path in isolated.iterdir():
                    if path.is_dir() or not (path.suffix == ".tf" or path.name in ARTIFACTS):
                        raise ValueError("Engine wrote an unexpected path")
                    if path.name == "AVM-MIGRATION-CHANGELOG.md":
                        text = path.read_text()
                        text = re.sub(r"(?m)^- \*\*Run at:\*\* .*$",
                                      f"- **Source commit:** {commit}", text)
                        text = re.sub(r"(?m)^- \*\*Working directory:\*\* .*$",
                                      f"- **Selected root:** `{root}`", text)
                        path.write_text(text)
                    shutil.copyfile(path, selected / path.name)
                # Only format this root, never -recursive.
                format_gate = commands.run(["terraform", "fmt", "-no-color"], selected, "repair-fmt")
                candidate_check_path = private / "candidate-check.json"
                check = commands.run(engine.command("check", isolated, candidate_check_path, config),
                                     isolated, "candidate-check")
                items = read_report(candidate_check_path, list) if candidate_check_path.exists() else []
                if any(i["blocking"] for i in items):
                    finding(result, "blocking_todos", "error",
                            "Blocking migration TODOs remain in generated instructions or code.")
                if any(not i["blocking"] for i in items):
                    finding(result, "advisory_review", "warning",
                            "Advisory TODOs, including lifecycle or unmapped arguments, require review.")
                candidate = static_gates(commands, selected, "candidate")
                candidate["avmigrate_check"] = check
                candidate["format_repair"] = format_gate
                result["static_validation"] = {"status": outcome(candidate), "gates": candidate}
                introduced = [key for key in baseline
                              if baseline[key]["status"] == "passed"
                              and candidate[key]["status"] == "failed"]
                result["static_validation"]["introduced_failures"] = introduced
                result["static_validation"]["baseline_failures"] = [
                    key for key in baseline if baseline[key]["status"] != "passed"]
                if introduced:
                    finding(result, "introduced_validation_failure", "error",
                            "A previously passing static gate failed after conversion.")
                result["stages"]["validate"] = {"status": outcome(candidate)}
                # init creates only disposable caches here. Lock changes are reviewable.
                cache = selected / ".terraform"
                if cache.exists():
                    shutil.rmtree(cache)
                candidate_files = files(workspace)
                changed = sorted(name for name in original.keys() | candidate_files.keys()
                                 if original.get(name) != candidate_files.get(name))
                if any(not permitted(name, root) for name in changed):
                    raise ValueError("Unexpected change outside the permitted root file list")
                meaningful = any(name.endswith(".tf") and
                                 original.get(name) != candidate_files.get(name) for name in changed)
                if meaningful:
                    patch = []
                    for name in changed:
                        before, after = original.get(name), candidate_files.get(name)
                        text = after.decode("utf-8") if after is not None else None
                        changes["files"].append({"path": name,
                                                 "before_sha256": digest(before) if before is not None else None,
                                                 "content": text})
                        patch.extend(difflib.unified_diff(
                            before.decode().splitlines(True) if before is not None else [],
                            text.splitlines(True) if text is not None else [],
                            fromfile=f"a/{name}" if before is not None else "/dev/null",
                            tofile=f"b/{name}" if after is not None else "/dev/null"))
                    (output / "repair.patch").write_text("".join(patch))
                    result["changed_files"] = changed
            result["stages"]["promote"] = {
                "status": "not_assessed",
                "reason": "Separate credential-isolated publisher required" if changes["files"]
                else "No meaningful repair diff; no PR"}
            code = int(any(s["status"] in ("failed", "blocked")
                           for s in result["stages"].values()))
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        # Full errors can contain source text or values. Do not copy them into public output.
        (private / "failure.txt").write_text(f"{type(exc).__name__}: {exc}\n")
        result["stages"][stage] = {"status": "failed",
                                  "reason": "See private/failure.txt for actionable diagnostics"}
        finding(result, "stage_failed", "error", f"The {stage} stage failed; diagnostics preserved.")
    finally:
        manifest["findings"] = result["findings"]
        manifest["blockers"] = [f for f in result["findings"] if f["severity"] == "error"]
        manifest["state_migration_actions"] = result["state_migration_actions"]
        manifest["source_commit"] = result["source_commit"]
        manifest["selected_root"] = root
        manifest["engine"] = result["engine"]
        for name, value in (("result.json", result), ("repair-manifest.json", manifest),
                            ("changes.json", changes)):
            (output / name).write_text(json.dumps(value, indent=2) + "\n")
        summary = ["# Terraform to AVM", "",
                   f"Mode: {mode}. Changed files: {len(result['changed_files'])}.", ""]
        summary += [f"- {name}: **{value['status']}**" for name, value in result["stages"].items()]
        summary += ["", f"Static validation: {result['static_validation']['status']}.",
                    "Live plan: **not assessed**. Deployment readiness: **blocked**.",
                    "", "Review all advisory findings, lifecycle changes, unmapped arguments and",
                    "state-migration instructions. Static validation is not infrastructure safety.",
                    "No terraform apply, import or state-modification commands were executed.", ""]
        (output / "summary.md").write_text("\n".join(summary))
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("run")
    command.add_argument("--repository", required=True)
    command.add_argument("--config", required=True)
    command.add_argument("--root", required=True)
    command.add_argument("--mode", choices=("report", "repair"), default="report")
    command.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        return run(args.repository, args.config, args.root, args.mode, args.output)
    except (ValueError, OSError):
        parser.exit(2, "Cannot start: use a new output directory outside the repository.\n")
