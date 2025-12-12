## Getting Started

GitOps-first: manifests live in this repo, Flux syncs per branch (`env/prod`, `env/test`). Terraform only builds the node and bootstraps Flux; day‑2 is Git-only. (Branch details: `docs/environments.md`. Fast-forward rules: `docs/how-to-commit.md`.)
> Pushing to `env/prod` updates live prod. Pushing to `env/test` updates the test cluster.

### Prerequisites
- Windows PowerShell
- Terraform
- `sops` (install: `winget install Mozilla.SOPS`)
- SSH key (for k3s node)

### Age key
- Private key file (e.g., `terraform/terra.agekey`) – keep it securely, never commit.
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
4) For a brand-new data disk (no snapshot/previous data), set `TF_VAR_allow_fresh_bootstrap=true` in the session for that apply. Otherwise leave it unset/false.
5) `terraform apply`
6) Wait until cloud-init finishes:
   - `ssh-keygen -R <ip>`; `ssh root@<ip> 'cloud-init status --wait'`
   - Live logs on the node: `tail -f /var/log/cloud-init-output.log`, `tail -f /var/log/bootstrap.log`
   - When k3s is up: `k3s kubectl get pods -A`

### Switching to a new Flexible IP (no rebuild)
- Create a new Flexible IP in Scaleway (PAR1). Keep the old IP attached until you switch DNS (prevents reuse).
- In the session for the target workspace set one of:
  - `$env:TF_VAR_public_ip_address='<new_ip>'` (preferred)
  - or `$env:TF_VAR_public_ip_id='<ip_uuid>'`
- Keep data volume protected (prod): `TF_VAR_prevent_destroy_data_volume=true`.
- `terraform apply` – the server stays up; Terraform detaches the old IP and attaches the new one in place.
- Update DNS to the new IP (manual for now; see `docs/dns.md`), wait for propagation; cert-manager will renew automatically (respect LE rate limits). If you want to force re-issue after DNS cutover: `k3s kubectl -n <ns> delete order,challenge -l acme.cert-manager.io/certificate-name=<cert_name>`.
- After confirming traffic on the new IP, delete the old Flexible IP in Scaleway.

### Certificates (Let’s Encrypt)
- Check status: `k3s kubectl get certificate -A` and `k3s kubectl get orders.acme.cert-manager.io -A`.
- LE rate limits apply to all hosts; if you see `order ... errored ... too many certificates ... retry after ...`, wait until the indicated time; cert-manager will retry automatically.
- Force renew all certs after DNS/IP change (from the node):  
  `(k3s kubectl get order.acme.cert-manager.io -A -o name; k3s kubectl get challenge.acme.cert-manager.io -A -o name) | xargs -r k3s kubectl delete`
- TLS per host (prod/test): www, blog, kimai2, monitoring, flux-hook (namespaces: website, apps-tools, monitoring-grafana, flux-system).

### Quick commands
- Prod apply:  
  `cd terraform; . .\env.ps1; $env:TF_VAR_sops_age_key = Get-Content terra.agekey -Raw; terraform workspace select prod; terraform apply`
- Test apply (example, branch set in env var):  
  `cd terraform; . .\env.ps1; $env:TF_VAR_sops_age_key = Get-Content terra.agekey -Raw; $env:TF_VAR_env_name='test'; $env:TF_VAR_config_repo_branch='env/test'; terraform workspace select test; terraform apply`

### Flux status & logs
- Kustomizations: `k3s kubectl get kustomizations -A`
- Receiver/webhook: `k3s kubectl -n flux-system describe receiver github-receiver`
- Last applied revision: `k3s kubectl -n flux-system get gitrepository terralinfra -o jsonpath='{.status.artifact.revision}'`
- Logs (webhook + events): `k3s kubectl -n flux-system logs deploy/notification-controller --tail=50`
- Force reconcile (from node): `k3s kubectl -n flux-system annotate gitrepository terralinfra reconcile.fluxcd.io/requestedAt="$(date --utc +%FT%TZ)" --overwrite`
