## Getting Started

This repo is GitOps-driven (Flux). You change manifests, push to the right branch, Flux syncs clusters (prod=test code on different branches). Terraform builds the k3s node and bootstraps Flux; day‑2 is via Git.

### Git branches (quick reminder)
- Prod: `env/prod`
- Test: `env/test`
- Workflow: commit to `env/test` → verify in test → merge to `env/prod`.
- WARNING: pushing to these branches triggers Flux to reconcile the cluster that tracks them (prod watches `env/prod`, test watches `env/test`).

### Prerequisites
- Windows PowerShell
- Terraform
- `kubectl`
- `sops` (install: `winget install Mozilla.SOPS`)
- SSH key (for k3s node)

### Age key
- Private key file (e.g., `terraform/terra.agekey`) – keep it securely (password manager/secure storage), never commit.
- Set per session:  
  `cd terraform; $env:SOPS_AGE_KEY = Get-Content terra.agekey -Raw`

### Do not do this
- Never commit private keys, PATs, webhook secrets, state files, `.terraform/`, or `*.agekey`.
- Keep `.env` local only; rotate creds if it ever leaks.
- SOPS-encrypted YAMLs are fine in git; only the public age key sits in `.sops.yaml`.
- Provider test fixtures under `provider/` use dummy keys; scanners may flag them—review before whitelisting.

### Data volume unlock (LUKS)
- Systemd service `ensure-data-mount.service` unlocks and mounts `/mnt/data` on every boot using `LUKS_KEY_URL` or bucket creds from `/etc/default/terra-data`.
- Formatting (LUKS init) is allowed only when `ALLOW_LUKS_FORMAT=true` (controlled by `allow_fresh_bootstrap` in Terraform). Set it to true only when you intentionally bootstrap a new/empty volume; keep it false for existing data/snapshots.
- Key material lives on the node in `/etc/default/terra-data` (0600) and is not committed to git.
- Secrets encryption config for k3s is generated/restored before k3s starts and backed up to `/mnt/data/backup/k3s/encryption-config.json`.

### .env template (prod default)
`terraform/.env` (not committed):
```
# Scaleway provider auth
SCW_ACCESS_KEY=...                     # Scaleway access key
SCW_SECRET_KEY=...                     # Scaleway secret key

# Terraform inputs
TF_VAR_project_id=...                  # Scaleway project id
TF_VAR_acme_email=notification@devsh.eu# Email for ACME/Let’s Encrypt
TF_VAR_config_repo_url=https://github.com/Devsh-Graphics-Programming/TerraInfra # Git repo for manifests
TF_VAR_config_repo_branch=env/prod     # Git branch (env/prod or env/test)
TF_VAR_config_repo_path=terraform/k8s  # Path in repo with k8s manifests
TF_VAR_github_persistent_terra_infra_ro_pat=...      # PAT for Flux source auth (read-only)
TF_VAR_github_bootstrap_terra_infra_webhook_pat=...  # PAT for GitHub webhook token
TF_VAR_env_name=prod                   # Logical env name (prod/test)
TF_VAR_luks_key_access_key=...         # Object Storage access key for LUKS key
TF_VAR_luks_key_secret_key=...         # Object Storage secret key for LUKS key

# Optional overrides
# TF_VAR_luks_key_url=                 # Presigned URL for LUKS key (overrides access/secret)
# TF_VAR_prevent_destroy_data_volume=true  # Set true to block data volume destroy
# TF_VAR_data_volume_snapshot_id=      # Snapshot id to restore data volume
# TF_VAR_sops_age_key=                 # Age private key (set in session, not in file)
# TF_VAR_allow_fresh_bootstrap=true    # Allow formatting LUKS on a brand-new volume only
```
Reload per session: `cd terraform; . .\env.ps1`

### First bootstrap (per environment)
1) Set `.env` (prod) or override env vars (test).
2) Set age key in session:  
   `$env:SOPS_AGE_KEY = Get-Content terra.agekey -Raw`
3) Select workspace (`terraform workspace select prod|test`).
4) `terraform apply`
5) Wait until cloud-init finishes:
   - `ssh-keygen -R <ip>`; `ssh root@<ip> 'cloud-init status --wait'`
   - Live logs on the node: `tail -f /var/log/cloud-init-output.log`, `tail -f /var/log/bootstrap.log`
   - When k3s is up: `k3s kubectl get pods -A`

### Fresh bootstrap after destroying the data volume (empty disk)
- Applies when you intentionally wiped the block storage (no snapshot/data). Do this only when you are fine losing everything on the volume.
- In the session set:
  - `$env:TF_VAR_allow_fresh_bootstrap='true'` (permits LUKS format + new k3s encryption config).
  - `$env:TF_VAR_prevent_destroy_data_volume='false'` (if set in `.env`, override to allow creating a new volume).
  - Clear any snapshot override: `$env:TF_VAR_data_volume_snapshot_id=''` unless you explicitly restore from a snapshot.
  - `$env:TF_VAR_sops_age_key = Get-Content terra.agekey -Raw` (required; bootstrap fails without SOPS key).
- Then `terraform apply`, wait for `cloud-init status --wait`.

### Certificates (Let’s Encrypt)
- Check status: `k3s kubectl get certificate -A` and `k3s kubectl get orders.acme.cert-manager.io -A`.
- Let’s Encrypt rate limits: if you see `order ... errored ... too many certificates ... retry after ...`, wait until the indicated time; cert-manager will retry automatically.
- TLS per host (all subject to ACME rate limits if recreated frequently):
  - `website/devsh-blog-tls` → blog
  - `website/devsh-website-tls` → www
  - `apps-tools/kimai-cert` → kimai2
  - `monitoring/grafana-cert` → grafana
  - `flux-system/flux-hook-cert` → flux webhook

### Quick commands
- Prod apply:  
  `cd terraform; . .\env.ps1; $env:TF_VAR_sops_age_key = Get-Content terra.agekey -Raw; terraform workspace select prod; terraform apply`
- Test apply (example, branch set in env var):  
  `cd terraform; . .\env.ps1; $env:TF_VAR_sops_age_key = Get-Content terra.agekey -Raw; $env:TF_VAR_env_name='test'; $env:TF_VAR_config_repo_branch='env/test'; terraform workspace select test; terraform apply`
