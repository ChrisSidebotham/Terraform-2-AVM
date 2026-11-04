# Presentation demo: folder to draft PR

Run the converter against a **committed folder in this repository**, then see its
output in a draft pull request. The default folder, `examples/platform`, contains
seven synthetic resources across networking, storage and monitoring. No Azure
account, state, or credentials are needed. Terraform 1.13.5 downloads public
modules/providers for backend-free initialization and validation.

| Area | Source resources | Expected proposal |
|---|---|---|
| Foundation | Resource group | AVM resource-group module |
| Networking | Virtual network and subnet | AVM virtual-network module with the subnet folded into its `subnets` input |
| Network security | Network security group and HTTPS rule | AVM security-group module; standalone rule remains for manual review |
| Storage | Storage account | AVM storage-account module |
| Monitoring | Log Analytics workspace | AVM workspace module |

The configuration is split across five Terraform files, including outputs that
reference resource IDs. The repair demonstrates cross-file reference rewriting,
five module conversions, one absorbed resource, and one unconverted resource.
Shared tags and settings such as address prefixes, TLS and retention make the
diff meaningful. Names and addresses are synthetic; this is not a deployable
reference architecture (for example, no subnet-to-NSG association is included).

**This proposes code, not infrastructure deployment.** No apply, import,
state-modification commands, generated migration scripts, or automatic merge run.

## Run it in GitHub Actions

1. Commit the Terraform folder and configuration to the branch you want to use.
   The existing `examples/platform` and `examples/platform/config.json` work out
   of the box. The narrower config in `examples/config.json` only selects resource
   groups; do not use it for the full platform demo.
2. Enable **Settings > Actions > General > Allow GitHub Actions to create and
   approve pull requests**. Organization policy can restrict this setting.
3. Open **Actions > Terraform to AVM presentation demo > Run workflow** and
   select that branch.
4. Choose `root=examples/platform`, `config_path=examples/platform/config.json`, and either
   `mode=report` or `mode=repair`. In this public repository, tick
   `acknowledge_public_output` after confirming the source and generated reports
   are safe to publish.
5. Open **Open report or repair draft PR > Summary** for the resulting PR link.
   Inspect the PR's **Files changed** tab and the conversion artifact.

| Mode | Draft PR contents |
|---|---|
| `report` | `AVM-MIGRATION-REPORT.md` in the selected folder, containing the summary, conversion manifest and full gate results; Terraform is unchanged |
| `repair` | The report plus proposed AVM modules, lockfile changes and migration instructions when generated |

Every successful manual conversion goes on to publication. There is no separate
`publish` switch. Even a repair with no Terraform changes produces a report PR,
so the result is visible. Report and repair use separate branches; identical
reruns of the same source revision update the same draft without extra commits.
A new source revision gets a new draft rather than overwriting old evidence.

Only the selected folder's immediate `.tf` files are converted; nested
environments are not scanned. `root=.` selects the repository root.
Use your own folder and config instead of the defaults if desired. Config must
be inside the repository. The checkout is never edited in place.

```sh
# Report-only draft PR against main.
gh workflow run terraform-to-avm-demo.yml \
  --repo ChrisSidebotham/Terraform-2-AVM --ref main \
  -f root=examples/platform -f config_path=examples/platform/config.json \
  -f mode=report -f acknowledge_public_output=true

# Repair draft PR with the full report and proposed fixes.
gh workflow run terraform-to-avm-demo.yml \
  --repo ChrisSidebotham/Terraform-2-AVM --ref main \
  -f root=examples/platform -f config_path=examples/platform/config.json \
  -f mode=repair -f acknowledge_public_output=true
```

Artifacts are named `presentation-MODE-RUN_ID-ATTEMPT` and
`presentation-publication-RUN_ID-ATTEMPT`, and expire after **three days**.
The first contains the report bundle, patch when available, and diagnostics.
The second records the final promotion result. The full report is also
committed in the PR and is not dependent on artifact retention.

## What to show

Start with a report PR: inspect its conversion manifest, affected files,
findings, and state-adoption requirements. Then run repair against the same
source branch and compare the five proposed AVM modules to the legacy resources.
Inspect `network.tf` for subnet absorption and the retained security rule, and
`outputs.tf` for rewritten module references. State review includes compatible
address moves, cross-type adoption, and an ambiguous absorbed-subnet destination.
Open the report to see which validation gates passed and which remain blocked.

Resource IDs and migration TODOs need human review. The converter may return
`1` while still producing a useful repair; the presentation records that exit
code and preserves failed gates in the report rather than discarding the draft.
Conversion/staging failures that prevent a reviewable bundle fail the workflow
and preserve diagnostics without publishing a PR.

**Publication success is not validation success.** Live plan remains
`not_assessed`; deployment readiness remains `blocked`. Bot-created PRs may not
trigger downstream CI automatically. Do not merge or deploy during the demo.

## Run against this checkout locally

Use Python 3.12, Git, Terraform 1.13.5, a **clean committed checkout**, and a fresh
output directory outside this repository:

```sh
python -m examples.presentation \
  --repository "$PWD" --config "$PWD/examples/platform/config.json" \
  --root examples/platform --mode report --output /tmp/avm-folder-report

python -m examples.presentation \
  --repository "$PWD" --config "$PWD/examples/platform/config.json" \
  --root examples/platform --mode repair --output /tmp/avm-folder-repair
```

Inspect `summary.md`, `repair-manifest.json`, `result.json`, and `changes.json`.
The generated PR report is represented in `changes.json`; `repair.patch`
contains only the converter's proposed code changes, not the presentation report.
The command succeeds when a reviewable bundle is prepared, and prints the
underlying converter exit code. It does not publish by itself.

To publish the reviewed bundle locally, set `GITHUB_TOKEN` to a token with
contents and pull-request write access, then run:

```sh
python -m terraform_to_avm.publish \
  --bundle /tmp/avm-folder-repair \
  --repository ChrisSidebotham/Terraform-2-AVM --base-branch main \
  --include-report
```

The base branch must still match the bundle's source commit. Review the report
and source for literal secrets before sharing. Public artifacts and PRs are
public; the acknowledgement is not secret detection.

## Keep the disposable rehearsal

Automatic push/PR runs still exercise the original synthetic report, repair,
and no-change scenarios without publishing anything. These use a disposable
consumer, verify root isolation and repeatability, and assert known migration
TODO failures. They do not operate on your selected folder.
They retain the smaller `examples/demo` three-resource-group fixture and
Terraform 1.9.8; the expanded manual presentation uses Terraform 1.13.5.
The storage module requires Terraform 1.10 or later, and newer logical-expression
evaluation avoids null-value errors in the pinned modules' validation expressions.

```sh
python -m examples.demo --scenario report --output /tmp/avm-demo-report
python -m examples.demo --scenario repair --output /tmp/avm-demo-repair
python -m examples.demo --scenario no-change --output /tmp/avm-demo-no-change
```

Historical synthetic rehearsal results remain in [evidence.json](evidence.json).
They predate folder-to-PR publication and do not demonstrate the new manual flow.

The reusable `terraform-to-avm.yml` workflow and its linked example retain their
private-consumer privacy guard. Use the root README's pinned caller for another
private repository; this presentation path is a separate, explicitly opted-in
same-repository demonstration.

## Troubleshooting

| Symptom | Guidance |
|---|---|
| Run stops at public-output consent | Confirm the selected source is safe and tick `acknowledge_public_output` |
| Draft branch exists but no draft PR; publisher returns HTTP 403 | In **Settings > Actions > General**, enable **Allow GitHub Actions to create and approve pull requests**; confirm the publisher job has `pull-requests: write` and no organization policy blocks it. The publisher preserves its branch; rerun the same workflow after enabling the setting. |
| Publisher says base advanced | Dispatch again from the current branch; do not reuse an old bundle |
| Existing draft is human-edited or closed | Review/archive it and remove its presentation branch before regenerating; human changes are never force-pushed |
| `init` fails | Provider/module downloads need network or mirrors; no Azure credentials are needed |
| Converter exit `1`, publication succeeds | Inspect failed gates and adoption blockers; the PR is reviewable, not deployable |
| Local run rejects checkout | Commit all source/config changes first and use an output directory outside the repo |
| Workflow missing from Actions | Merge the workflow to the default branch before dispatching it |
