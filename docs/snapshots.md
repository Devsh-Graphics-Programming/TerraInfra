## Snapshots (prod -> test restore)

Goal: keep prod data, test against a snapshot without touching prod.

## Managed daily snapshot (rotating)
Terraform keeps exactly one managed "daily" snapshot in prod (`latest_snapshot_id`). It is maintained by a dedicated Terraform root in `terraform/snapshots/` and applied by GitHub Actions (`.github/workflows/terraform-snapshots.yml`):
- `schedule` (daily): creates a new snapshot once per day (replacing the previous one)
- `workflow_dispatch` (manual): forces a fresh snapshot immediately (also replaces the previous one)

### CI setup (once)
Workflow expects a dedicated Object Storage bucket for Terraform state (separate from the LUKS bucket) and a Scaleway IAM key scoped to the minimum required permissions (Block snapshots + read volume, and Object Storage access to the state bucket only).

Configure GitHub Environment `prod`:

Variables:
- `SNAPSHOTS_PROJECT_ID`
- `SNAPSHOTS_VOLUME_NAME` (default: `devsh-k3s-prod-data-node1`)
- `SNAPSHOTS_TFSTATE_BUCKET`
- `SNAPSHOTS_TFSTATE_KEY` (example: `terraform/snapshots/terraform.tfstate`)
- `SNAPSHOTS_TFSTATE_REGION` (example: `fr-par`)
- `SNAPSHOTS_TFSTATE_ENDPOINT` (example: `https://s3.fr-par.scw.cloud`)

Secrets:
- `SNAPSHOTS_SCW_ACCESS_KEY`
- `SNAPSHOTS_SCW_SECRET_KEY`

### Migration
If you used the previous in-module managed daily snapshot, remove it from the `terraform/` state before your next prod apply so it is not destroyed:
```
cd terraform
terraform workspace select prod
terraform state rm module.k3s_node.scaleway_block_snapshot.data_volume[0]
```

## Manual snapshots (separate retention)
Manual snapshots are separate from the rotating daily snapshot. They do not replace it and do not delete each other.

Manual snapshots are defined locally (not committed) in `terraform/manual-snapshots.auto.tfvars.json` and are destroyed after their TTL on the next `terraform apply` run.

Create a manual snapshot (default TTL 24h, auto name):
```
cd terraform
.\manual-snapshot.ps1
terraform workspace select prod
terraform apply -auto-approve
```

Create a manual snapshot with a custom TTL (auto name):
```
cd terraform
.\manual-snapshot.ps1 -TtlHours 72
terraform workspace select prod
terraform apply -auto-approve
```

Create a manual snapshot with a custom TTL and custom name:
```
cd terraform
.\manual-snapshot.ps1 -Name incident-2025-12-14 -TtlHours 72
terraform workspace select prod
terraform apply -auto-approve
```

You can list snapshot IDs from Terraform:
```
cd terraform/snapshots
terraform output -raw latest_snapshot_id

cd ..
terraform output -json manual_snapshot_ids
```

### Restore snapshot into test
1) In test session set:
```
$env:TF_VAR_env_name='test'
$env:TF_VAR_config_repo_branch='env/test'
$env:TF_VAR_data_volume_snapshot_id='<latest_snapshot_id>'
```
2) Apply:
```
terraform workspace select test
terraform apply -auto-approve
```
3) Update test DNS A records to the new test IP (from outputs).

### Verify data
- Log into test apps (Kimai, etc.) and confirm data from prod snapshot (users, etc.).

### Notes
- Prod volume is never destroyed (`prevent_destroy_data_volume=true` by default).
- Terraform keeps only the latest managed daily snapshot (replaces the previous one after the new snapshot is created).
- Manual snapshots do not affect the daily snapshot and do not delete each other. Expired manual snapshots are removed on the next `terraform apply` in prod.
- Test infra can be destroyed/recreated freely with a chosen snapshot ID.
