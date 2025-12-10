# TerraInfra Quick Ops Guide

## Prereqs
- Terraform >= 1.5
- Scaleway creds (`SCW_ACCESS_KEY`, `SCW_SECRET_KEY`, `SCW_DEFAULT_PROJECT_ID`)
- GitHub fine-grained PAT (read-only to this repo)
- Domains pointing to the cluster IP (A records)
- Python3 on the node (installed by cloud-init)

## .env (terraform/.env)
Do not commit. Example:
```
SCW_ACCESS_KEY=...
SCW_SECRET_KEY=...
TF_VAR_project_id=...
TF_VAR_env_name=prod       # set to e.g. "test"; only lowercase letters/digits/hyphen are kept (others collapse to "-") before suffix/domain prep
TF_VAR_kimai_domain=kimai2.devsh.eu
TF_VAR_monitoring_domain=monitoring.devsh.eu
TF_VAR_acme_email=you@example.com
TF_VAR_config_repo_url=https://github.com/Devsh-Graphics-Programming/TerraInfra
TF_VAR_config_repo_branch=master
TF_VAR_config_repo_path=terraform/k8s
TF_VAR_github_persistent_terra_infra_ro_pat=ghp_...   # read-only PAT stored in cluster for repo access
TF_VAR_github_bootstrap_terra_infra_webhook_pat=ghp_...   # required to auto-create GitHub webhook at bootstrap (Webhooks RW)
TF_VAR_flux_hook_domain=flux-hook.prod.devsh.eu
TF_VAR_luks_key_access_key=...
TF_VAR_luks_key_secret_key=...
# LUKS key bucket is shared for every environment and always named `terra-luks-keys`.
# optional, only if using presigned URL instead of RO creds:
# TF_VAR_luks_key_url=https://...
```
Load envs: `.\env.ps1`

## Deploy / Recreate
From `terraform/`:
```
.\env.ps1
terraform apply
```
To force rebuild: `terraform taint module.k3s_node_prod.scaleway_instance_server.k3s_node_1` then apply.

### Destroy/recreate while keeping the data volume
The data block volume is marked `prevent_destroy`; destroy only the node/public IP/SG and leave the volume:
```
.\env.ps1
terraform destroy -auto-approve `
  -target="module.k3s_node_prod.scaleway_instance_server.k3s_node_1" `
  -target="module.k3s_node_prod.scaleway_instance_ip.public_ip" `
  -target="module.k3s_node_prod.scaleway_instance_security_group.web_sg"
```
Then recreate the node (volume will be reattached automatically):
```
.\env.ps1
terraform apply
```
To delete the data volume entirely, remove `prevent_destroy` first, then run a full `terraform destroy`.

## DNS
Set A records to the cluster IP (e.g. `212.47.251.150`):
- `kimai2.devsh.eu`
- `monitoring.devsh.eu`
- `flux-hook.prod.devsh.eu`
When `TF_VAR_env_name` is not `prod`, Terraform automatically prefixes the sanitized env slug (non `[a-z0-9-]` characters collapse to `-`, empty slugs fall back to `prod`) to each domain so the stack advertises `test.kimai2.devsh.eu`, `test.monitoring.devsh.eu`, and `test.flux-hook.prod.devsh.eu`.

## Running prod and test side by side
Terraform keeps a single state per workspace (`terraform.tfstate`), so applying `TF_VAR_env_name=test` in the same workspace as prod simply rewrites the existing resources with `test` in the names/tags. To stand up both environments concurrently, keep production in the default workspace and use a dedicated workspace for `test`:

```
cd terraform
.\env.ps1                        # loads default vars (TF_VAR_env_name=prod)
terraform workspace show         # should print "default" for prod
terraform apply -auto-approve     # keeps prod resources intact

terraform workspace new test      # run once; creates terraform.tfstate.d/test
$env:TF_VAR_env_name='test'
terraform workspace select test
terraform apply -auto-approve     # creates/updates test resources
```

Whenever you switch between stacks, run `terraform workspace select default` (or `test`) and reset `$env:TF_VAR_env_name` (reload `.env` for prod). `test` keeps its own state file so it won’t mutate prod. To tear down the test stack, select `test` and run `terraform destroy -auto-approve` (the shared `terra-luks-keys` bucket still has `prevent_destroy`, so nothing accidentally deletes it).

## GitHub Webhook
Bootstrap (Python) auto-creates the webhook when `GITHUB_BOOTSTRAP_TERRA_INFRA_WEBHOOK_PAT` is set:
- Deletes any existing hooks whose URL contains `FLUX_HOOK_DOMAIN`.
- Creates a new hook pointing to the receiver path, with a fresh secret stored in `flux-system/github-webhook-token`.
If you need to inspect manually:
- Path (after receiver Ready): `kubectl -n flux-system get receiver github-receiver -o jsonpath='{.status.webhookPath}'`
- Payload URL example: `https://flux-hook.prod.devsh.eu/hook/...`
- Content type: `application/json`
- Secret: decoded `token` from `flux-system/github-webhook-token`
- Events: push (ping allowed)

## After boot
On the node:
```
cloud-init status --wait --long          # block until cloud-init finishes
tail -f /var/log/cloud-init-output.log   # watch remaining output if needed
tail -f /var/log/bootstrap.log           # live Python bootstrap output (contains generated creds)
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl get nodes
flux get kustomizations -A
kubectl get deploy -A
kubectl -n apps-tools get pods
kubectl -n monitoring get pods
kubectl get ingress -A
```

## Iterating services
- Edit `terraform/k8s/*.tpl.yaml`, commit/push to `main`.
- Flux (2m polling + webhook) applies changes and rolls out pods.
- Check: `flux get kustomizations -A` and `kubectl get deploy/ingress -A`.

## Storage & data
- Root volume: 40 GB local NVMe (ephemeral). Treat node as disposable.
- Persist/backup DB and app data externally (e.g., S3 bucket dumps or snapshots) before destroying nodes.
- LUKS key bucket is shared across all environments and always called `terra-luks-keys`, so the bucket name should stay static when reusing the storage layer.
- When you copy a `/mnt/data` snapshot between envs (e.g., promo → test), the shared key lets you unlock it without rotating keys; just ensure the new node has the same read-only creds or presigned URL and run bootstrap so it pulls `terra-luks-keys/luks.key` before mounting.
- Production now keeps a single rotating snapshot of the `/mnt/data` block volume: Terraform creates `scaleway_block_snapshot` inside the prod workspace whose name includes the `time_rotating` trigger. The default rotation window is 24 hours (`TF_VAR_snapshot_rotation_hours=24`), so rerunning `terraform apply` in the default workspace once a day automatically replaces the prior snapshot; the incremental storage cost for one snapshot is small, so the higher cadence gives fresher restore points without materially affecting billing.
- To force a new snapshot immediately via Terraform, taint the trigger, then apply just the snapshot resource so it recreates with the current timestamp:

  ```
  .\env.ps1
  terraform workspace select default
  terraform taint module.k3s_node_prod.time_rotating.snapshot_trigger[0]
  terraform apply -auto-approve -target="module.k3s_node_prod.scaleway_block_snapshot.data_volume[0]"
  ```

  You can still use the CLI example above when you need an extra manual copy with a recognizable `-manual-<timestamp>` suffix, but the Terraform-managed snapshot itself always stays as a single rotating resource.

## Troubleshooting
- No flux pods yet: watch `/var/log/cloud-init-output.log` until install finished.
- Kustomization not Ready: `kubectl -n flux-system describe kustomization apps`.
- Webhook: check GitHub deliveries and `kubectl -n flux-system logs deploy/notification-controller`.
- Disk pressure/evictions: clean images or move DB to dedicated persistent storage.
