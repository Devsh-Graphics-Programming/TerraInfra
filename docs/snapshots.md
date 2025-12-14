## Snapshots (prod -> test restore)

Goal: keep prod data, test against a snapshot without touching prod.

Snapshots are managed by Terraform. A new snapshot is created when you run `terraform apply` (manually or via automation) and Terraform decides the rotation window has advanced. Only one managed snapshot is kept at a time (Terraform replaces the previous snapshot when creating a new one).

## Manual snapshots (separate retention)
Manual snapshots are separate from the rotating daily snapshot. They do not replace it and do not delete each other.

Manual snapshots are defined locally (not committed) in `terraform/manual-snapshots.auto.tfvars.json` and are destroyed after their TTL on the next `terraform apply` run.

Create a manual snapshot (default TTL 24h):
```
cd terraform
.\manual-snapshot.ps1
terraform workspace select prod
terraform apply -auto-approve
```

Create a manual snapshot with a custom TTL:
```
cd terraform
.\manual-snapshot.ps1 -Name incident-2025-12-14 -TtlHours 72
terraform workspace select prod
terraform apply -auto-approve
```

### Create prod snapshot
If you want a snapshot immediately, taint the snapshot resource before applying.
```
cd terraform
. .\env.ps1
$env:TF_VAR_sops_age_key = Get-Content terra.agekey -Raw
terraform workspace select prod
terraform taint module.k3s_node.scaleway_block_snapshot.data_volume[0]
terraform apply -auto-approve
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
- Prod volume is never destroyed (prevent_destroy). Snapshots run every `snapshot_rotation_hours` (default 24h) on prod; manual snapshot via taint when needed.
- Terraform keeps only the latest managed snapshot (replaces the previous one after the new snapshot is created).
- Test infra can be destroyed/recreated freely with a chosen snapshot ID.
