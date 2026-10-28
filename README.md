# Terraform to AVM

PR-based **code repair**, not infrastructure deployment. Converts supported
`azurerm_*` resources into pinned Azure Verified Modules using ChrisSidebotham’s
existing **avmigrate 1.10.0** engine. Destination modules can use AzureRM or AzAPI.
Arbitrary `azapi_resource` / `azapi_update_resource` **source** conversion is not
supported; unsupported resources are reported and left unchanged.

Python 3.12 and Git are sufficient for conversion and the offline test suite.
Terraform 1.9.8 is pinned in Actions for static gates. There are no pip dependencies.

## Safety contract

- One explicitly selected root per run; only its immediate `.tf` files are
  converted. Nested roots/environments are never discovered for conversion.
- `report` leaves the entire consuming checkout unchanged. `repair` also leaves
  it unchanged: code rewriting happens in a disposable committed snapshot and
  produces a patch/bundle for review.
- **`avmigrate apply` rewrites code. `terraform apply` never runs.** Neither do
  imports, state commands, migration scripts, authenticated live plans, deployment
  or automatic merge. Generated instructions are preserved, not executed.
- Heuristic matching and provider-major constraint widening are off by default.
  Module dependencies can still require different providers; review the diff,
  requirements, lockfile and any initialization failures.
- Passing static gates is **not proof of infrastructure safety**. Live-plan status
  is always `not_assessed`; deployment readiness remains `blocked` in this MVP.
  All advisory findings—including lifecycle settings and unmapped arguments—need
  explicit human review before deployment.
- Same-resource-type address moves differ from AzureRM-to-AzAPI adoption.
  `state mv` / `moved` cannot generally cross resource types. Missing resource IDs,
  unknown internal addresses and absorbed-resource destinations are explicit
  blockers. Do not bypass these with `--no-state`; the wrapper never uses it.

## Engine provenance and licensing

[`vendor/avmigrate/provenance.json`](vendor/avmigrate/provenance.json) records the
[original gist](https://gist.github.com/ChrisSidebotham/0a3b2e9809a9831692c3bb2bff4c0a54),
author, distribution version, embedded catalogue (schema 2, September 7, 2026),
and exact artifact revision:

```text
sha256:82fe2166af1c5d7c91c4211896b03f866e8854d77d77f5b94e71a10274265911
```

The original **bundled Python artifact** is vendored unchanged and verified before
execution. Its embedded modules/catalogue are reused, not reconstructed from the
gist README. The original source tree and upstream tests are **not** included.
Standalone offline usage still works:

```sh
python /absolute/factory/vendor/avmigrate/avmigrate.py info
python /absolute/factory/vendor/avmigrate/avmigrate.py plan /absolute/infra
```

**Provenance limitation:** upstream history was inaccessible during import, so the
distribution is content-pinned, but an upstream **Git commit revision has not been
verified**. No mutable gist is downloaded at runtime. Confirm and record the
upstream Git revision before calling this a Git-revision-pinned release.

**Licensing limitation:** no license notice was found in the accessible gist
README/artifact; attempted conventional license filenames were unavailable.
Confirm permission to redistribute with the author before distributing a release.
The repository’s existing MIT license and attribution are preserved; they are not
asserted to relicense the vendored engine.

## Local usage and configuration

Run from the factory directory, using absolute filesystem paths. The consumer must
be a clean, committed Git checkout. Uncommitted/untracked files, symlinks,
submodules and selected `.tf.json` configurations are rejected rather than
partially processed. Root and file names use plain letters, digits, `_`, `-`, `.`.
The root is a repository-relative POSIX path (`.` is allowed).

```sh
cd /absolute/factory
python -m terraform_to_avm run \
  --repository /absolute/consumer \
  --config /absolute/consumer/avm.json \
  --root infrastructure/dev --mode report \
  --output /absolute/new-private-output
```

Change `--mode report` to `--mode repair` to generate a patch. Output must be a
new/empty directory **outside** the consumer. No token or Azure login is needed.
Exit status is nonzero on failed/blocked stages; available reports survive.
A blocked deployment review is separate from subprocess success.

Configuration is a JSON object, at most 64 KiB, with these optional keys only:

| Key | Default | Validation |
|---|---|---|
| `schema_version` | `1` | integer `1` |
| `allow` | `[]` | list of `azurerm_*` resource types; empty means engine-supported types |
| `exclude` | `[]` | list of resource types; must not overlap `allow` |
| `include_heuristic` | `false` | boolean; explicit opt-in, requires review |
| `bump_providers` | `false` | boolean; explicit provider constraint widening opt-in |
| `timeout_seconds` | `300` | integer `1..1800`, per subprocess |

Use [`examples/config.json`](examples/config.json). No `no_state`, arbitrary CLI
arguments, dynamic plugin loading or schema URL overrides are accepted.
avmigrate is the first named fixer behind a small adapter, not a plugin framework.

### Five stages and outputs

1. **Intake:** records commit, engine/catalogue and Terraform versions; inventories
   resources, provider requirements and existing TODOs. Baseline formatting,
   backend-free initialization, validation and TODO results are recorded separately.
2. **Plan repair:** runs `avmigrate plan --json`; writes versioned
   `repair-manifest.json` with proposed conversions, affected files, findings,
   blockers and required state transitions.
3. **Apply targeted fixes:** runs `avmigrate apply` in isolation, using allow/exclude
   filters. Only selected-root `.tf`, `.terraform.lock.hcl`, and the four named
   migration instruction files can enter the bundle. Unexpected changes stop it.
   If new conversions encounter existing migration instructions, repair stops
   rather than overwrite reviewed IDs/history. Reconcile/archive those artifacts
   manually first; originals and the new plan remain in private diagnostics.
4. **Validate:** formats only the selected root, runs `terraform fmt -check`,
   `terraform init -backend=false -input=false`, `terraform validate` and
   `avmigrate check`. Reports distinguish baseline from introduced failures and
   `passed`, `blocked`, `failed`, `not_assessed` gates. Initialization still needs
   provider/module downloads or configured mirrors; backend-free is **not offline**.
   Explicit local module dependencies may be loaded for validation, not converted.
5. **Promote:** a separate publisher validates the untrusted bundle against source
   blob hashes and publishes one draft PR on a deterministic root-hash branch.
   Identical reruns add no commits. Human edits, unrelated branches, non-draft PRs
   or stale bases stop publication with an explanation; no force pushes occur.
   Review/archive the existing PR and remove its branch before regenerating against
   a changed base. A no-change/report-only run creates no PR.

`result.json` schema version 1 includes `run_id`, `source_commit`, `selected_root`,
engine provenance, `stages`, `changed_files`, severity-tagged `findings`,
`state_migration_actions`, `static_validation`, and honest `live_plan` status.
`summary.md` is readable. `repair.patch` and `changes.json` contain the proposed
code; raw diagnostics and full engine reports are under `private/`.
Publication updates the local result’s promotion stage.

**Treat the whole bundle as sensitive.** Terraform source, provider requirements,
patches and diagnostics may contain literal secrets. Never paste them into public
logs/issues. Public-facing summaries and PR bodies use generic diagnostics and
counts, not raw values. No state or plan contents are generated or uploaded.
Commits use an explicit file list—never `git add .`; variable files, backups,
credentials, state, plans and `.terraform` caches cannot be published by the
publisher. Review source diffs for pre-existing literal secrets before sharing.

## GitHub Actions

The reusable workflow is
`.github/workflows/terraform-to-avm.yml`; a minimal manual caller is
`.github/workflows/terraform-to-avm-example.yml`. **Actions report bundles require a
private consumer repository**, enforced before checkout: artifacts in a public
repository are not private just because their name contains “private”.
Local use and the offline example below work in public repositories too.

For another repository, copy this caller to its default branch, substitute the
**same reviewed, immutable factory commit SHA in both places**, and commit
`avm.json`. GitHub does not allow an expression in a reusable workflow’s `uses`.

```yaml
name: Terraform to AVM
on:
  workflow_dispatch:
    inputs:
      root:
        description: Terraform root, relative to this repository
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
      factory_ref: REPLACE_WITH_THE_SAME_FACTORY_SHA
      config_path: avm.json
      root: ${{ inputs.root }}
      base_branch: ${{ github.ref_name }}
      mode: ${{ inputs.mode }}
      publish: ${{ inputs.publish }}
```

The factory workflow checks out **both** the manual dispatch’s immutable consumer
commit and the factory SHA into different directories. `base_branch` must match
the dispatched branch. Read-only Actions run metadata attests that `factory_ref`
matches the invoked reusable workflow’s revision; missing/mismatched metadata
fails closed. The engine is bound to that factory release, not to the consumer
checkout or a mutable download. `github.workflow_sha` identifies the caller, so is
not used to infer external factory identity. For a private factory, configure
repository/organization Actions sharing/access as well.

Default permissions are read-only; conversion also needs `actions: read` to verify
the release binding. Only the publication job receives contents/PR
write permissions. It checks out only the trusted factory, validates artifact
paths/content hashes, and never executes consumer code. Conversion has no write
credentials, Azure credentials or OIDC access. Publication is restricted to manual
repair runs. Root-based concurrency prevents overlapping updates. All actions use
full revision pins. No `pull_request_target` or fork-code write job is used.
The normal PR CI job has read-only permissions and does not receive secrets.

Enable the repository/organization setting allowing GitHub Actions to create pull
requests; organization policy may require approval. `GITHUB_TOKEN`-created PRs
**usually do not trigger downstream workflows**. Do not assume checks start:
an authorized maintainer may need to approve/run `workflow_dispatch` checks or use
an approved GitHub App/fine-grained token in a separately reviewed publisher setup.
Local publishing supports such a token via `GITHUB_TOKEN`; the reusable workflow
uses only its own job-scoped token and does not inherit caller secrets.
Artifacts are retained for three days; download/review them before expiry.

## Tests

```sh
cd /absolute/factory
python -m unittest discover -s tests -v
```

Fixture Git repositories exercise the real bundled converter; Terraform
subprocesses and GitHub REST are mocked in normal CI. No Azure credentials or
network are required. Separate, manual network integration downloads public
providers/modules (or uses your configured mirrors), without live state:

```sh
TERRAFORM_TO_AVM_INTEGRATION=1 \
  python -m unittest discover -s tests -p 'test_integration*.py' -v
```

The matching Actions workflow is `terraform-integration.yml`. These static tests
are not cloud validation.

## End-to-end example

This uses the committed [`examples/legacy/main.tf`](examples/legacy/main.tf).
Replace `/absolute/factory` with this repository’s path. Use fresh output
directories for subsequent runs.

```sh
FACTORY=/absolute/factory
DEMO="$(mktemp -d)"
mkdir -p "$DEMO/consumer/infra"
cp "$FACTORY/examples/legacy/main.tf" "$DEMO/consumer/infra/main.tf"
cp "$FACTORY/examples/config.json" "$DEMO/consumer/avm.json"
git -C "$DEMO/consumer" init -q
git -C "$DEMO/consumer" add infra/main.tf avm.json
git -C "$DEMO/consumer" -c user.name=Example -c user.email=example@example.invalid \
  commit -qm 'Existing AzureRM configuration'
cd "$FACTORY"
python -m terraform_to_avm run --repository "$DEMO/consumer" \
  --config "$DEMO/consumer/avm.json" --root infra --mode repair \
  --output "$DEMO/reports"
# Nonzero is expected if Terraform is absent, downloads fail or import IDs are missing.
cat "$DEMO/reports/summary.md"
less "$DEMO/reports/repair.patch"
git -C "$DEMO/consumer" apply --check "$DEMO/reports/repair.patch"
```

This produces an AVM resource-group module and reviewable state-adoption
instructions without editing the consumer or touching Azure. This module is
AzAPI-backed: missing adoption IDs intentionally block deployment readiness.

To **create a draft PR**, run the same repair command against a clean clone of
your actual private consumer repository, using its current target-branch commit
(not the temporary fixture above), then:

```sh
# Supply a token securely in the environment; do not put it in config or source.
python -m terraform_to_avm.publish --bundle /absolute/private-reports \
  --repository YOUR_ORG/YOUR_REPO --base-branch main
```

Manual configuration still required: factory SHA/caller, private Actions artifact
access, GitHub token/policy, Terraform executable/network or mirrors, review of all
findings, exact resource IDs and migration destinations. An authorized operator
must separately arrange state backups, state-aware authenticated assessment and
cloud validation. Neither the patch nor the draft PR authorizes deployment.
