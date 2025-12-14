## Snapshots (prod -> test restore)

Goal: keep prod data, test against a snapshot without touching prod.

## Managed daily snapshot (rotating)
Terraform keeps exactly one managed "daily" snapshot in prod (`latest_snapshot_id`). It is maintained by a dedicated Terraform root in `terraform/snapshots/` and applied by GitHub Actions (`.github/workflows/terraform-snapshots.yml`):
- `schedule` (daily, 03:00): creates a new **auto** snapshot (replacing the previous one; max 1 auto kept)
- `workflow_dispatch` (manual): creates a **manual** snapshot (default TTL 24h; does not replace the auto snapshot and does not delete other manual snapshots)
- `schedule` (hourly): cleanup of expired **manual** snapshots created by the workflow (no auto snapshot creation)

### CI setup (once)
Workflow expects a dedicated Object Storage bucket for Terraform state (separate from the LUKS bucket) and a Scaleway IAM key scoped to the minimum required permissions (Block snapshots + read volume, and Object Storage access to the state bucket only).

Configure GitHub repository secrets (or Environment `prod` secrets):
- `SNAPSHOTS_SCW_ACCESS_KEY`
- `SNAPSHOTS_SCW_SECRET_KEY`
- `SNAPSHOTS_DISCORD_WEBHOOK_URL` (optional) - Discord webhook URL for snapshot success/failure notifications

Snapshot configuration (project ID, volume name, tfstate bucket/key/region/endpoint) is defined in `.github/workflows/terraform-snapshots.yml` and can be overridden when running the workflow manually (`workflow_dispatch` inputs).
Manual snapshots support `manual_ttl_hours` (default `24`) and `manual_snapshot_name` (optional).

When creating the IAM API key used by GitHub Actions, set its `default_project_id` to the project that owns the Object Storage buckets (otherwise S3 requests fail with `403 Forbidden` during `HeadObject` / Terraform backend init).

### IAM + bucket policies as code (manual apply)
IAM resources and Object Storage bucket policies for snapshots live in `terraform/iam/`. This is not applied by CI. Run it manually with your full-privilege Scaleway key when you want to reconcile:

Prepare your local env (recommended):
- Load `terraform/.env` (SCW creds, `TF_VAR_project_id`, `TF_VAR_luks_key_access_key`, etc.): `cd terraform; .\env.ps1`
- Owner identity is resolved automatically from `SCW_ACCESS_KEY` (as `TF_VAR_owner_access_key`). You can override with `TF_VAR_owner_user_id` or `TF_VAR_owner_user_email` (email lookup may require `TF_VAR_organization_id`).

Create a local (not committed) vars file, e.g. `terraform/iam/local.auto.tfvars.json` (see `terraform/iam/local.auto.tfvars.json.example`). `project_id` is expected via `TF_VAR_project_id` (from `terraform/.env`) unless you explicitly set it in the file.
```json
{
  "owner_user_email": "you@example.com",
  "snapshots_state_bucket_name": "terra-snapshots-state",
  "snapshots_state_object_prefix": "terraform/snapshots/"
}
```

Apply:
```
cd terraform/iam
terraform init
terraform plan
terraform apply
```

If resources already exist (created in UI), import once and then apply:
```
cd terraform/iam
terraform init
terraform import scaleway_iam_application.snapshots <application_id>
terraform import scaleway_iam_policy.snapshots_terraform_state <policy_id>
terraform import scaleway_iam_policy.snapshots_block_storage <policy_id>
terraform import scaleway_object_bucket_policy.snapshots_state fr-par/terra-snapshots-state@<project_id>
terraform import scaleway_object_bucket_policy.luks_keys[0] fr-par/terra-luks-keys@<project_id>
terraform apply
```

### Migration
If you used the previous in-module managed daily snapshot, remove it from the `terraform/` state before your next prod apply so it is not destroyed:
```
cd terraform
terraform workspace select prod
terraform state rm module.k3s_node.scaleway_block_snapshot.data_volume[0]
```

## Manual snapshots (separate retention)
Manual snapshots are separate from the rotating daily snapshot. They do not replace it and do not delete each other.

### Create a manual snapshot (GitHub Actions, recommended)
Use the `terraform-snapshots` workflow (`workflow_dispatch`) on branch `env/prod`.

Inputs:
- `manual_ttl_hours` (default `24`)
- `manual_snapshot_name` (optional) - keep it short and unique (e.g. `incident-2025-12-14`)

The run output contains the created snapshot name and ID. If `SNAPSHOTS_DISCORD_WEBHOOK_URL` is set, a Discord notification is sent on success and failure.

### List snapshot IDs (from Terraform state)
From the dedicated snapshots root:
```
cd terraform/snapshots
terraform output -raw latest_snapshot_id
terraform output -json manual_snapshot_ids
```

### Legacy local/manual snapshots (not used by CI)
There is an older local helper `terraform/manual-snapshot.ps1` that manages manual snapshots via the main `terraform/` root.
Prefer the GitHub Actions workflow above to keep snapshot state centralized and avoid conflicts.

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
