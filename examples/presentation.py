"""Generate a PR-ready report/repair bundle from a committed folder in this repo."""

import argparse
import json
from pathlib import Path
import sys

from terraform_to_avm.runner import digest, git, run, selected_path


REPORT = "AVM-MIGRATION-REPORT.md"


def prepare(repository, config, root, mode, output):
    repository, output = Path(repository).resolve(), Path(output).resolve()
    root = selected_path(root)
    config = Path(config).resolve()
    selected_path(config.relative_to(repository).as_posix())
    code = run(repository, config, root, mode, output)
    result = json.loads((output / "result.json").read_text())
    if ((output / "private/failure.txt").exists()
            or result["stages"]["intake"]["status"] != "passed"
            or result["stages"]["plan_repair"]["status"] != "passed"
            or (mode == "repair" and result["stages"]["apply_fixes"]["status"] != "passed")):
        raise ValueError("Conversion could not produce a reviewable bundle; inspect its diagnostics")
    changes = json.loads((output / "changes.json").read_text())
    manifest = json.loads((output / "repair-manifest.json").read_text())
    report_path = REPORT if root == "." else f"{root}/{REPORT}"
    before = repository / report_path
    source = result["source_commit"]
    content = (
        f"# Terraform to AVM {mode} report\n\n"
        f"Source commit: `{source}`. Selected folder: `{root}`.\n\n"
        f"Converter exit code: **{code}**. Nonzero status and unresolved blockers are "
        "preserved below; draft publication is not validation or deployment approval.\n\n"
        + (output / "summary.md").read_text()
        + "\n## Conversion manifest\n\n```json\n"
        + json.dumps(manifest, indent=2)
        + "\n```\n"
        + "\n## Converter result before publication\n\n```json\n"
        + json.dumps(result, indent=2)
        + "\n```\n"
    )
    changes["files"].append({
        "path": report_path,
        "before_sha256": digest(before.read_bytes()) if before.exists() else None,
        "content": content,
    })
    result["changed_files"] = [item["path"] for item in changes["files"]]
    result["stages"]["promote"] = {
        "status": "not_assessed",
        "reason": "Presentation report prepared for the separate draft publisher.",
    }
    if git(repository, "rev-parse", "HEAD").decode().strip() != source:
        raise ValueError("Source changed during conversion; rerun from a clean checkout")
    for name, value in (("changes.json", changes), ("result.json", result)):
        (output / name).write_text(json.dumps(value, indent=2) + "\n")
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--mode", choices=("report", "repair"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        code = prepare(args.repository, args.config, args.root, args.mode, args.output)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(f"Reviewable presentation bundle prepared; converter exit code: {code}. "
          "Deployment remains blocked. Inspect result.json for individual gates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
