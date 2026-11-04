# Terraform to AVM

Turn supported `azurerm_*` Terraform resources into **Azure Verified Module
(AVM) calls**, with a report, a reviewable patch, and optionally a draft pull
request. This repository packages the vendored **avmigrate** converter with a
Python CLI and a reusable GitHub Actions workflow.

For example, an `azurerm_resource_group` can become a pinned AVM resource-group
module. The report also identifies state-adoption work that an operator must
review separately. Unsupported resources are reported and left unchanged;
arbitrary `azapi_resource` / `azapi_update_resource` inputs are not supported.

**This changes proposed code, not deployed infrastructure.** It does not run
`terraform apply`, imports, or state commands, and it does not merge PRs.

## Choose how to use it

| Your goal | Use |
|---|---|
| Run on a folder in this repo and open a report or repair draft PR | [Presentation demo](examples/demo/README.md) |
| Add report/repair runs to another repository | [Reusable workflow quick start](#use-from-another-github-repository) |
| Run locally, or integrate with a different CI system | [Python CLI](#use-locally-or-in-another-ci-system) |
| Understand validation, provenance, licensing, or engine updates | [Operational reference](docs/reference.md) |

Both entry points select **one Terraform root per run** and process only its
immediate `.tf` files, not nested environments. Neither modifies the original
consumer checkout: repairs are generated in a disposable snapshot.

| Mode | What you get |
|---|---|
| `report` | Inventory, proposed conversions, findings, and required state transitions; no repair patch |
| `repair` | The report plus a patch and static validation of the proposed code |
| `repair` with `publish: true` in Actions | A draft PR when there are reviewable changes |

The [presentation workflow](examples/demo/README.md) is a separate,
same-repository flow: select a committed folder (default `examples/platform`) and
`report` or `repair`. Both modes automatically open a draft containing a generated
report; repair also includes proposed fixes. Public output requires explicit
acknowledgement. The private-consumer reusable workflow below is unchanged.
The default platform has seven resources spanning networking, storage and
monitoring, with cross-file references, subnet absorption and manual-review
findings. This expanded manual demo uses Terraform 1.13.5.

## Use from another GitHub repository

### 1. Prepare the consumer repository

The **consumer** is the repository containing the Terraform you want to convert.
The reusable workflow currently requires a **private consumer repository** and
a **manual `workflow_dispatch` run on a branch**. It is not a general-purpose
push/PR-triggered action. For public repositories, use the local CLI or the
synthetic demo instead.

Commit your Terraform and an `avm.json` file to the consumer. Start with a narrow
scope, for example:

```json
{
  "schema_version": 1,
  "allow": ["azurerm_resource_group"]
}
```

This converts only supported resource groups. Use `"allow": []` to consider all
engine-supported types. See [configuration](#configuration) for other options.

### 2. Choose the factory SHA

**Factory** means this repository, `ChrisSidebotham/Terraform-2-AVM`: the tooling
that produces the proposed repair. **Factory SHA** means the full, 40-character
Git commit ID of the version of this repository you have reviewed and want to
run. It is **not** your Terraform repository's commit, an Azure identifier, the
upstream gist revision, or the engine's SHA256 checksum.

To find a candidate commit on this repository's default branch:

```sh
gh api repos/ChrisSidebotham/Terraform-2-AVM/commits/main --jq .sha
```

Review that commit and its checks before adopting it. If you have cloned this
repository and checked out the version you want, `git rev-parse HEAD` gives its
full SHA instead.

Paste the **same literal SHA in two places** in the caller below:

| Place | Purpose |
|---|---|
| `uses: ...terraform-to-avm.yml@<SHA>` | Selects the reusable workflow version |
| `factory_ref: <SHA>` | Selects the matching converter, publisher, and bundled engine |

The workflow verifies these match using GitHub Actions run metadata. Do not use
`main`, a tag, a short SHA, or the consumer's `${{ github.sha }}` as
`factory_ref`. GitHub also does not allow expressions in a reusable workflow's
`uses`, so a variable cannot replace that literal pin.

The engine is bundled in the factory commit; migration runs do not download the
latest gist. To upgrade later, review a newer factory commit and update **both**
pins together.

### 3. Add a caller workflow

Save this as `.github/workflows/terraform-to-avm.yml` in the **consumer**.
Replace both `REPLACE_WITH_40_CHARACTER_FACTORY_SHA` placeholders with the SHA
chosen above, then commit the workflow to the consumer's default branch.

```yaml
name: Terraform to AVM
on:
  workflow_dispatch:
    inputs:
      root:
        description: Terraform root relative to this repository, e.g. infra/dev
        required: true
        type: string
      mode:
        type: choice
        default: report
        options: [report, repair]
      publish:
        type: boolean
        default: false
permissions:
  contents: read
jobs:
  migrate:
    permissions:
      contents: write
      pull-requests: write
      actions: read
    uses: ChrisSidebotham/Terraform-2-AVM/.github/workflows/terraform-to-avm.yml@REPLACE_WITH_40_CHARACTER_FACTORY_SHA
    with:
      factory_ref: REPLACE_WITH_40_CHARACTER_FACTORY_SHA
      config_path: avm.json
      root: ${{ inputs.root }}
      base_branch: ${{ github.ref_name }}
      mode: ${{ inputs.mode }}
      publish: ${{ inputs.publish }}
```

This is a **job-level reusable workflow call**, not an action to put under
`steps`. Add the `migrate` job to an existing manually dispatched workflow if
you prefer; preserve its inputs and permissions. If your organization restricts
Actions, allow this reusable workflow and its dependencies. A private factory
also requires Actions sharing/access configuration.

The caller permissions are upper bounds: conversion is read-only;
contents/PR writes are used only by the separate publishing job. No Azure
credentials, OIDC, or inherited caller secrets are needed. To publish PRs,
enable **Settings > Actions > General > Allow GitHub Actions to create and
approve pull requests**, subject to organization policy.

### 4. Run report, then repair

In the consumer, open **Actions > Terraform to AVM > Run workflow**. Choose the
branch containing your committed Terraform and config, set `root` to its
repository-relative directory (for example `infra/dev`, or `.` for the repository
root), and start with `mode=report`, `publish=false`.

You can also dispatch it from an existing automation or terminal:

```sh
gh workflow run terraform-to-avm.yml --repo YOUR_ORG/YOUR_REPO --ref main \
  -f root=infra/dev -f mode=report -f publish=false
```

Use the same branch for the dispatch and `base_branch`; the example derives the
latter automatically. Only the selected root is converted. For multiple roots,
dispatch a separate run for each.

Download the run's `terraform-to-avm-private-RUN_ID-ATTEMPT` artifact and read
`summary.md` and `repair-manifest.json`. After reviewing the proposals, run again
with `mode=repair`, `publish=false` to inspect `repair.patch`. Set
`mode=repair`, `publish=true` when you want a draft PR. Report-only and no-change
runs do not create PRs.

## Use locally or in another CI system

Use **Python 3.12 and Git**; there are no pip dependencies. Install Terraform
**1.9.8** to match Actions' static checks. Initialization needs access to
provider/module downloads or configured mirrors; backend-free is not offline.
No Azure login is required.

Clone this repository separately from your consumer, check out a reviewed
factory commit, then run from the factory directory:

```sh
git clone https://github.com/ChrisSidebotham/Terraform-2-AVM.git
cd Terraform-2-AVM
git checkout YOUR_REVIEWED_FACTORY_SHA
python -m terraform_to_avm run \
  --repository /absolute/consumer \
  --config /absolute/consumer/avm.json \
  --root infra/dev \
  --mode report \
  --output /absolute/new-private-report
```

The consumer must be a **clean, committed Git checkout**, including `avm.json`.
Use absolute filesystem paths and a new/empty output directory **outside** the
consumer. Root/file names use plain letters, digits, `_`, `-`, and `.`; symlinks,
submodules, selected `.tf.json` configurations, and uncommitted/untracked files
are rejected.

Change to `--mode repair` and choose a fresh output directory to generate a
patch. Review it before applying it yourself or publishing it through the
[local publisher](docs/reference.md#end-to-end-example).

Other CI systems can invoke this same CLI and consume `result.json`; they must
provide their own checkout, dependencies, and restricted artifact storage.
The CLI does not publish a PR automatically.

## Configuration

`avm.json` is a JSON object; all keys are optional and unknown keys are rejected.
The [full example](examples/config.json) uses these defaults except for its
narrow resource-group allow list:

| Key | Default | Meaning |
|---|---|---|
| `schema_version` | `1` | Configuration schema; must be integer `1` |
| `allow` | `[]` | Resource types to consider; empty means all engine-supported types |
| `exclude` | `[]` | Resource types to skip; must not overlap `allow` |
| `include_heuristic` | `false` | Opt into heuristic matching; requires extra review |
| `bump_providers` | `false` | Opt into provider-major constraint widening |
| `timeout_seconds` | `300` | Per-subprocess timeout, integer `1..1800` |

## Read the results

| File | What to use it for |
|---|---|
| `summary.md` | Human-readable findings and gate results |
| `repair-manifest.json` | Proposed conversions, affected files, blockers, and state transitions |
| `result.json` | Machine-readable stages, changed files, provenance, and validation status |
| `repair.patch`, `changes.json` | Proposed code changes from a repair with changes |
| `private/` | Raw diagnostics and engine reports |

A nonzero exit can still leave useful reports and a repair patch. Static
validation failures and unresolved migration TODOs need investigation; a failed
run is not necessarily a failure to generate code. Read the individual gates.
A reviewable draft PR can exist while deployment remains blocked.

**Neither a patch nor passing static checks means the infrastructure is safe
to deploy.** Live-plan status remains `not_assessed` and deployment readiness
remains `blocked`. Review state-adoption instructions, exact resource IDs,
provider/lockfile changes, and all findings before separately arranging
state-aware assessment and deployment.

## Common setup problems

| Symptom | What to check |
|---|---|
| Public-consumer rejection | Use a private consumer for the reusable workflow; use the CLI/demo for public repositories |
| Factory release cannot be verified | Both full SHA pins must match; retain `actions: read` and use a manual branch dispatch |
| Terraform init fails | Check provider/module network access or mirrors; Azure credentials are not required |
| No draft PR | Use repair + publish, check there are changes, and enable Actions PR creation |
| PR exists but CI did not start | `GITHUB_TOKEN`-created PRs usually do not trigger downstream workflows; an authorized maintainer must arrange checks |
| Publishing after the base branch changed fails | Review/archive the previous draft and remove its branch before regenerating; human changes are not force-pushed |

**Keep bundles private:** they may contain Terraform source and literal secrets.
Actions artifacts expire after **three days**. Do not copy raw reports into public
logs or issues.

## Further reading and licensing

The [operational reference](docs/reference.md) covers the exact safety contract,
validation stages, automated engine-update review, local publishing, and tests.
The [demo guide](examples/demo/README.md) provides a complete synthetic rehearsal.

This repository retains its [MIT license](LICENSE). The vendored engine's
[provenance](vendor/avmigrate/provenance.json) records a separate, unresolved
upstream licensing limitation: confirm redistribution permission before a
release. The repository license is not asserted to relicense that engine; see
the [provenance and licensing details](docs/reference.md#engine-provenance-and-licensing).
