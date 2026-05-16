## Snapshots (prod -> test restore)

Goal: keep prod data, test against a snapshot without touching prod.

## Managed daily snapshots (rotating)
Terraform keeps exactly one managed "daily" snapshot per prod data volume. The snapshots are maintained by a dedicated Terraform root in `terraform/snapshots/` and applied by GitHub Actions (`.github/workflows/terraform-snapshots.yml`):
- `schedule` (daily, 03:00): creates new **auto** snapshots (replacing the previous auto snapshots; max 1 auto kept per target)
- `workflow_dispatch` (manual): creates **manual** snapshots (default TTL 24h; does not replace auto snapshots and does not delete other manual snapshots)
- each run also enforces manual retention (expired manual snapshots are deleted) and prunes manual snapshots that were deleted in Scaleway UI (so they are not recreated)
- each target runs as an independent matrix job with its own Terraform state key under `terraform/snapshots/<target>.tfstate`

Managed targets:
- `node1-main` -> `devsh-k3s-prod-data-node1` (Kimai/node1 data)
- `chat` -> `prod-chat-01-data` (StoatChat data)
- `rocket` -> `prod-rocket-01-data` (Rocket.Chat data)
- `jenkins` -> `jenkins-prod-data` (Jenkins home)
- `observability` -> `prod-observability-01-data` (Grafana/monitoring data)
- `vpn-control` -> `prod-vpn-control-01-data` (Authentik/Headscale data)

For k3s nodes that use `local-path`, bootstrap links `/var/lib/rancher/k3s/storage` to `/mnt/data/local-path` before k3s starts. This keeps PVC data on the encrypted data volume even if the default k3s local-path provisioner creates a PVC before Flux has reconciled the custom local-path config.

### CI setup (once)
Workflow expects a dedicated Object Storage bucket for Terraform state (separate from the LUKS bucket) and a Scaleway IAM key scoped to the minimum required permissions: Block Storage snapshot/volume access, temporary Instance access for restore-drill verifier machines, Object Storage access to the snapshot state bucket, and read-only access to the single LUKS key object used by the restore drill.
The LUKS bucket policy grants the snapshot application read access only to that key object. It does not grant broad bucket access.

Configure GitHub repository secrets (or Environment `prod` secrets):
- `SNAPSHOTS_SCW_ACCESS_KEY`
- `SNAPSHOTS_SCW_SECRET_KEY`
- `SNAPSHOTS_DISCORD_WEBHOOK_URL` (optional) - Discord webhook URL for snapshot and restore-drill success/failure notifications

Snapshot configuration (project ID, target names, tfstate bucket/key/region/endpoint) is defined in `.github/workflows/terraform-snapshots.yml` and can be overridden when running the workflow manually (`workflow_dispatch` inputs).
The `tfstate_key` input is treated as a legacy key or prefix; the workflow derives per-target keys from its directory/prefix.
Manual snapshots support `manual_ttl_hours` (default `24`) and `manual_snapshot_name` (optional).
When a manual run targets only a subset of volumes, the request stores that target list in state. During the next refresh pass, the workflow keeps only the target snapshots that still exist in Scaleway, so manually deleted snapshots are pruned instead of being recreated.

When creating the IAM API key used by GitHub Actions, set its `default_project_id` to the project that owns the Object Storage buckets (otherwise S3 requests fail with `403 Forbidden` during `HeadObject` / Terraform backend init).

### IAM + bucket policies as code (manual apply)
IAM resources and Object Storage bucket policies for snapshots live in `terraform/iam/`. This is not applied by CI. Run it manually with your full-privilege Scaleway key when you want to reconcile:
The state bucket policy grants the snapshot application access to the snapshots state prefix and the restore-drill state prefix, not the full bucket.

Prepare your local env (recommended):
- Load `terraform/.env` (SCW creds, `TF_VAR_project_id`, `TF_VAR_luks_key_access_key`, etc.): `cd terraform; .\env.ps1`
- Owner identity is resolved automatically from `SCW_ACCESS_KEY` (as `TF_VAR_owner_access_key`). You can override with `TF_VAR_owner_user_id` or `TF_VAR_owner_user_email` (email lookup may require `TF_VAR_organization_id`).

Create a local (not committed) vars file, e.g. `terraform/iam/local.auto.tfvars.json` (see `terraform/iam/local.auto.tfvars.json.example`). `project_id` is expected via `TF_VAR_project_id` (from `terraform/.env`) unless you explicitly set it in the file.
```json
{
  "owner_user_email": "you@example.com",
  "snapshots_state_bucket_name": "terra-snapshots-state",
  "snapshots_state_object_prefix": "terraform/snapshots/",
  "snapshot_restore_drill_state_object_prefix": "terraform/snapshot-restore-drill/"
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

The legacy combined snapshots state key (`terraform/snapshots/terraform.tfstate`) is no longer used by the matrix workflow. New runs write per-target state keys. Do not run the legacy combined key in parallel with the matrix workflow.

## Manual snapshots (separate retention)
Manual snapshots are separate from the rotating daily snapshot. They do not replace it and do not delete each other.

### Create a manual snapshot (GitHub Actions, recommended)
Use the `terraform-snapshots` workflow (`workflow_dispatch`) on branch `env/prod`.

Inputs:
- `target_names` (default `all`; comma-separated allowed, e.g. `chat,jenkins`)
- `manual_ttl_hours` (default `24`)
- `manual_snapshot_name` (optional) - keep it short and unique (e.g. `incident-2025-12-14`)

The run output prints only target keys and counts. Snapshot IDs, volume IDs, and temporary verifier resource IDs are treated as sensitive Terraform outputs and are not printed to public Actions logs. If `SNAPSHOTS_DISCORD_WEBHOOK_URL` is set, a Discord notification is sent on success and failure.

### List snapshot IDs (from Terraform state)
From the dedicated snapshots root, initialize the target state first, then read outputs:
```
cd terraform/snapshots
terraform init -reconfigure -backend-config="key=terraform/snapshots/chat.tfstate" ...
terraform output -json latest_snapshot_ids
terraform output -json manual_snapshot_ids
```

## Restore drill (non-prod temporary verifier)
The `snapshot-restore-drill` workflow (`.github/workflows/snapshot-restore-drill.yml`) verifies that the latest managed snapshots can be restored without touching production nodes or production workloads.

It runs daily after the managed snapshot job and can be started manually. Inputs:
- `target_names` (default `all`; comma-separated allowed, e.g. `chat,jenkins`)
- `project_id`
- Terraform state bucket/key settings
- `snapshot_source` (default `auto`; set to `manual` to restore a specific manual snapshot)
- `manual_snapshot_name` (required only when `snapshot_source=manual`)
- `expected_rocket_message` (optional; only for `rocket`, verifies that a specific `#general` message exists in the restored database)

Before creating any verifier, the workflow runs a janitor that first destroys any leftover resources still tracked in the dedicated restore-drill Terraform state and then deletes only stale Scaleway resources that match all restore-drill safety gates: the project, the `devsh` and `restore-drill` tags, an allowed target tag/name, a `restore-drill-*` resource name where applicable, a non-current run tag, and the minimum age window. It does not delete production nodes, production volumes, snapshots, buckets, DNS, or any untagged resource.

For each selected target the workflow runs an independent matrix job. Targets can run in parallel because each verifier reads its own snapshot state, uses its own restore-drill backend key, and creates its own temporary resources.
The matrix selection logic lives in `.github/scripts/resolve-snapshot-matrix.sh` and `.github/scripts/resolve-restore-matrix.sh`; the quality gate validates the `jenkins` subset and unknown-target rejection before Terraform runs.

For each target the workflow:
1. Reads the selected snapshot ID from the snapshots Terraform state.
2. Creates a temporary Block volume from that snapshot.
3. Starts a temporary verifier instance.
4. Allows SSH only from the current GitHub runner public IP for the duration of the job.
5. Unlocks and mounts the restored data volume with the existing LUKS key.
6. Runs local health checks on `127.0.0.1` using disposable containers.
7. Destroys the temporary instance and temporary volume.
8. Verifies that the per-target restore-drill Terraform state is empty after destroy.
9. Publishes a compact sanitized Discord result with janitor status, per-target health check counts, and cleanup status when `SNAPSHOTS_DISCORD_WEBHOOK_URL` is configured.

By default, restore drill uses the latest managed auto snapshot. Manual restore drill runs can set `snapshot_source=manual` and `manual_snapshot_name=<manual key>` to verify a fresh manual snapshot without waiting for the next daily auto snapshot.

Target checks:
- `node1-main`: restored `/mnt/data` opens, MariaDB data starts locally, Kimai var data is present. The live Kimai node is not restarted and no production pod is touched.
- `chat`: restored MongoDB, MinIO and RabbitMQ data start locally and respond to health checks.
- `rocket`: restored local-path data root is present, restored MongoDB starts from the snapshot, the Rocket.Chat `general` room exists, and the room contains restored user messages. Manual runs can also set `expected_rocket_message` to prove a specific message survived the snapshot.
- `jenkins`: restored Jenkins home includes controller config, master key, plugins, the managed smoke job, the generic runner plan job, and the configured `ci/ditt` jobs; the restored controller starts locally, `/login` responds, and `/prometheus/` is present but requires authentication.
- `observability`: restored Grafana and OnCall Grafana data start locally and `/api/health` responds; local-path data root is present.

The restore drill intentionally does not reuse production DNS, ingress, cert-manager challenges, Flux alerting, or public service endpoints. This avoids duplicate alerts and avoids any interaction with live Kimai, StoatChat, Jenkins, or monitoring workloads.
Terraform output and apply logs are redacted before they are written to public CI logs. The matrix passed between jobs contains only target keys and instance types, not snapshot IDs.
The temporary verifier uploads only sanitized status JSON (target, phase, message, check names, check statuses, check messages, and cleanup state) to the restore-drill state prefix so CI can report health checks without exposing temporary IPs or resource IDs.
The workflow treats cleanup as part of the result: `destroy` must succeed and the per-target restore-drill Terraform state must be empty after cleanup. The janitor report contains counts only. It does not expose resource IDs, IPs, snapshot IDs, or provider response bodies in public logs or Discord messages.

The current guarantee is crash-consistent Block Storage restore. For databases that need tighter RPO/RTO guarantees, add a second layer of application-aware logical backups later (for example MariaDB and MongoDB dumps) and test those in the same restore-drill pattern.

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
- Terraform keeps only the latest managed daily snapshot per target (replaces the previous one after the new snapshot is created).
- Manual snapshots do not affect the daily snapshot and do not delete each other. Expired manual snapshots are removed on the next workflow run (at latest the daily schedule).
- If you delete a manual snapshot in Scaleway UI, the next workflow run will prune it from Terraform state and it will not be recreated.
- Test infra can be destroyed/recreated freely with a chosen snapshot ID.
