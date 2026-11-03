# Presentation demo: Terraform to AVM

This is **code repair, not infrastructure deployment**. All demo inputs are
synthetic: three resource groups in a selected `infra` root and another resource
group in nested `infra/production`. No Azure account, subscription, state, or
credentials are used. Real Terraform 1.9.8 downloads public modules/providers for
backend-free initialization and validation.

## Verified rehearsal: 3 November 2026

The [recorded demo run](https://github.com/ChrisSidebotham/Terraform-2-AVM/actions/runs/41655682111)
completed successfully, and all three artifacts were downloaded and inspected.
The [independent push run](https://github.com/ChrisSidebotham/Terraform-2-AVM/actions/runs/41655669615)
also passed. These runs tested branch commit
`545af64b0008887e32052cf0658284fe6b2040dc`; the PR run checked out GitHub's
synthetic merge commit `12e70adafcd6f6a68f7e014519d18587554f5c2a`.

| Scenario | Proposed conversions | Changed files | Converter exit | Verified behavior |
|---|---|---|---|---|
| Report | 3 | 0 | 0 | Source unchanged; plan and adoption requirements preserved |
| Repair | 3 | 6 | 1 | Three AVM modules; applicable patch; repeated files/patch identical |
| No-change | 0 | 0 | 1 | Previously accepted code repair generates no further patch |

All three demo jobs passed their assertions. Both repair scenarios passed real
Terraform formatting, initialization, and validation; migration TODO checks
remained failed as intended. No deployment or live publisher was exercised.
The recorded run's artifacts expire on **10 November 2026 at approximately
14:39 UTC**. See [evidence.json](evidence.json) for artifact IDs and patch hashes.

The [real Terraform integration run](https://github.com/ChrisSidebotham/Terraform-2-AVM/actions/runs/41652155241)
also passed on `main`. The
[linked manual workflow run](https://github.com/ChrisSidebotham/Terraform-2-AVM/actions/runs/41652150431)
correctly rejected the public consumer before checkout and uploaded **zero**
artifacts. A private demo repository could not be created by this session:
the repository-creation client was unavailable and the installation API token
returned HTTP 403. This is a session-access limitation, not a claim about the
maintainer's own GitHub permissions.

## What to show in five minutes

1. Open **Actions > Terraform to AVM synthetic demo** and a successful run. Its
   three jobs show report, repair, and no-change behavior.
2. Open **Synthetic report > Summary**. Three conversions are proposed, no files
   are changed, and converted-code validation is intentionally not assessed.
   Download its artifact and open `repair-manifest.json` for the plan.
3. Open **Synthetic repair** and download its artifact. Compare `source-before.tf`
   and `source-after.tf`: three AzureRM resource groups become pinned AVM modules.
   Open `repair.patch` and `changes.json` for the complete reviewable proposal.
   The job verifies patch applicability and identical output on a repeated repair.
4. Open `result.json`: real formatting, initialization, and validation passed.
   The adoption/migration TODO check deliberately fails because resource IDs
   require human review. Live plan remains `not_assessed`; deployment readiness
   remains `blocked`. The demo job passes because it verifies these exact outcomes,
   not because it hides or ignores converter failures.
5. Open **Synthetic no-change**. This job first accepts a repair into its
   disposable fixture, then runs the converter again. It proposes zero further
   conversions and creates no patch. Existing migration TODOs still block
   deployment. Every scenario verifies that conversion leaves its consumer
   checkout and nested production root untouched.

Use the phrase: **"We automate the proposed code change, preserve evidence, and
stop at human review. Green demo assertions are not a green deployment gate."**

## Rehearsal and evidence

On the default branch, use **Run workflow** with no inputs. Before merge, the
`demo/presentation-evidence` branch runs automatically when its demo code or
workflow changes; pull requests touching those files run it too.

```sh
gh workflow run terraform-to-avm-demo.yml \
  --repo ChrisSidebotham/Terraform-2-AVM --ref main
gh run list --repo ChrisSidebotham/Terraform-2-AVM \
  --workflow terraform-to-avm-demo.yml --limit 5
# Replace RUN_ID with the successful run chosen above.
gh run download RUN_ID --repo ChrisSidebotham/Terraform-2-AVM \
  --dir presentation-evidence
```

Artifacts are named `synthetic-demo-{report,repair,no-change}-RUN_ID-ATTEMPT`.
Each includes machine-readable `demo-evidence.json`, readable `demo-summary.md`,
runner results, and available synthetic diagnostics. Repair also includes its
patch and `repeat-changes.json`. Keep an offline copy before the presentation;
these artifacts expire after **seven days**, unlike normal private workflow
bundles, which expire after three days. No hidden lockfile needs to be downloaded
separately: its proposed contents are represented in `changes.json` and the patch.

Local rehearsal, from this repository with Python 3.12, Git, and Terraform 1.9.8:

```sh
python -m examples.demo --scenario report --output /tmp/avm-demo-report
python -m examples.demo --scenario repair --output /tmp/avm-demo-repair
python -m examples.demo --scenario no-change --output /tmp/avm-demo-no-change
```

Use fresh output paths. The script accepts no consumer/config/root path and
constructs its own committed synthetic repository. Never adapt this public
artifact workflow to upload real consumer source.

## The linked manual workflow and draft PRs

`terraform-to-avm-example.yml` is deliberately **not** the public demo workflow.
This factory repository is public; its manual example stops before checkout with
`Sensitive report artifacts require a private consumer repository`. That is the
expected privacy guard, not a conversion failure. Do not remove it or make the
factory private just for a demo.

For the full report > repair > draft PR demonstration, create a **private**
consumer repository with committed Terraform and config, and copy the pinned
caller from the root README. Pin the same reviewed factory commit SHA in both
`uses` and `factory_ref`. Allow Actions to create pull requests in repository
settings (organization policy can override this).

| Input | Start with | Meaning |
|---|---|---|
| Branch | `main` | Must match `base_branch` in the caller |
| `config_path` | `avm.json` | Committed, repository-relative JSON config |
| `root` | `infra` | Only immediate `.tf` files; nested roots are excluded |
| `mode` | `report` | Inspect proposals without generating a patch |
| `publish` | `false` | Keep publication explicitly off initially |

After review, use `mode=repair,publish=false` to inspect the patch, then
`mode=repair,publish=true` to request a draft PR. A draft can be reviewable while
state/adoption TODOs still block readiness. No PR is created for report/no-change
runs. Do not merge or deploy during the presentation. Bot-created PRs may not
trigger downstream CI automatically.

The public synthetic demo exercises real conversion, validation, repeatability,
and root isolation, **not the reusable workflow's private-consumer attestation or
live draft-PR publisher**. Offline CI covers publisher behavior with mocked REST.
Do not claim a live draft PR was demonstrated without actually running a private
consumer.

## Usability and troubleshooting

| Symptom | Guidance |
|---|---|
| Linked workflow immediately red | Use a private consumer, or the synthetic demo for public presentation |
| Converter exit code `1` but demo job green | Expected only when the asserted unresolved migration TODOs remain; inspect individual gates |
| `init` fails | Provider/module downloads need network or mirrors; backend-free is not offline |
| No-change setup or repeated repair fails | The job fails; seed diagnostics remain in the artifact, or repeat diagnostics under `repeat-failure/` |
| Output path already exists | Choose a fresh local output directory |
| Publication fails | Check Actions PR permissions, current base SHA, existing draft branch, and artifact availability |
| Rerun after changing `main` fails publication | Review/archive the previous draft and remove its branch before regeneration; never force-push human changes |
| "Run workflow" is missing | Merge the demo workflow to the default branch; use its already-running demo branch before merge |

Keep logs/artifacts open in advance and use downloaded evidence if the network is
unreliable. Do not run the dependency updater as presentation preparation: it
fetches unreviewed upstream candidates and is separate from this pinned demo.
