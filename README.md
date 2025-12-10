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
TF_VAR_website_domain=www.devsh.eu
TF_VAR_blog_domain=blog.devsh.eu
TF_VAR_acme_email=you@example.com
TF_VAR_config_repo_url=https://github.com/Devsh-Graphics-Programming/TerraInfra
TF_VAR_config_repo_branch=master
TF_VAR_config_repo_path=terraform/k8s
TF_VAR_github_persistent_terra_infra_ro_pat=ghp_...   # read-only PAT stored in cluster for repo access
TF_VAR_github_bootstrap_terra_infra_webhook_pat=ghp_...   # required to auto-create GitHub webhook at bootstrap (Webhooks RW)
TF_VAR_flux_hook_domain=flux-hook.prod.devsh.eu
TF_VAR_snapshot_rotation_hours=24   # rolling snapshots only run on prod (default 24h)
TF_VAR_data_volume_snapshot_id=     # optional snapshot ID to seed /mnt/data (set before apply)
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
To force rebuild: `terraform taint module.k3s_node.scaleway_instance_server.k3s_node_1` then apply.

### Destroy/recreate while keeping the data volume
The data block volume is marked `prevent_destroy`; destroy only the node/public IP/SG and leave the volume:
```
.\env.ps1
terraform destroy -auto-approve `
  -target="module.k3s_node.scaleway_instance_server.k3s_node_1" `
  -target="module.k3s_node.scaleway_instance_ip.public_ip" `
  -target="module.k3s_node.scaleway_instance_security_group.web_sg"
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

### Static website services

Two additional Caddy-backed services host the main site (`TF_VAR_website_domain`, default `www.devsh.eu`) and the blog (`TF_VAR_blog_domain`, default `blog.devsh.eu`). Both containers run from prebuilt images, mount their root filesystem read-only, and expose writable tmpfs folders (`/tmp`, `/config`, `/data`) so caches remain ephemeral. They are defined in `terraform/k8s/www-sites.tpl.yaml` and picked up by the same Flux kustomization that boots Kimai and Grafana.

These services do not require cloud-init changes; updating the domains just means editing your `.env` entries and rerunning `terraform apply` (Flux handles TLS via `cert-manager` ingresses defined in the template). Because the containers are immutable, nothing writes to `/opt`—everything mutable lives on temporary memory-backed volumes exposed in the manifest.

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

After renaming `module.k3s_node_prod` to `module.k3s_node`, re-select the workspace you're operating in (`default` or `test`) and run `terraform state mv module.k3s_node_prod module.k3s_node` once per workspace so the existing resources stay managed under the new module name.

Whenever you switch between stacks, run `terraform workspace select default` (or `test`) and reset `$env:TF_VAR_env_name` (reload `.env` for prod). `test` keeps its own state file so it won't mutate prod. To tear down the test stack, select `test` and run `terraform destroy -auto-approve` (the shared `terra-luks-keys` bucket still has `prevent_destroy`, so nothing accidentally deletes it). The test workspace is meant for short-lived experiments only—destroy all `test` resources once you finish validating changes so the shared snapshot/volume workflow stays clean.

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
- When you copy a `/mnt/data` snapshot between envs (e.g., promo  test), the shared key lets you unlock it without rotating keys; just ensure the new node has the same read-only creds or presigned URL and run bootstrap so it pulls `terra-luks-keys/luks.key` before mounting.
- TLS secrets (Kimai, Flux and cert-manager) are intentionally not written to `/mnt/data` so that every workspace boots up fresh certs for the domains it owns; copying `/mnt/data` between prod and test never transfers certificates across environments.
- Production now keeps a single rotating snapshot of the `/mnt/data` block volume: Terraform creates `scaleway_block_snapshot` inside the prod workspace whose name includes the `time_rotating` trigger. The default rotation window is 24 hours (`TF_VAR_snapshot_rotation_hours=24`), so rerunning `terraform apply` in the default workspace once a day automatically replaces the prior snapshot; the incremental storage cost for one snapshot is small, so the higher cadence gives fresher restore points without materially affecting billing.
- **Starting from scratch:** if there is no snapshot yet, Terraform can’t build the first automanaged snapshot in a single apply because `time_rotating.snapshot_trigger[0].rotation_rfc3339` does not exist while the snapshot is being created. In that case, run one apply to provision the node/volume without the snapshot (so the block volume exists and `time_rotating` is present) and then run a second `terraform apply` (or target the snapshot resource) to let the rotation resource compute a valid `name`. Once the automated snapshot exists you can follow the usual restore flow.
- To force a new snapshot immediately via Terraform, taint the trigger, then apply just the snapshot resource so it recreates with the current timestamp:

  ```
  .\env.ps1
  terraform workspace select default
  terraform taint module.k3s_node.time_rotating.snapshot_trigger[0]
  terraform apply -auto-approve -target="module.k3s_node.scaleway_block_snapshot.data_volume[0]"
  ```

  When you need a named manual copy, use the Scaleway CLI. Grab the prod data volume and organization IDs from the default workspace state (for example, `terraform state show module.k3s_node.scaleway_block_volume.data_volume`), then:

  ```powershell
  $ts = Get-Date -Format 'yyyy-MM-dd-HH-mm-ss'
  scw block snapshot create `
    name="devsh-k3s-prod-data-snapshot-manual-$ts" `
    volume-id=<data-volume-id> `
    project-id=$env:TF_VAR_project_id `
    organization-id=<organization-id>
  ```

  Manual snapshots always include a `-manual-<timestamp>` suffix so they stand out in the console, while the Terraform-managed snapshot stays as a single rotating resource.

### Restoring from snapshots

1. Capture the snapshot ID you want to restore (the automated copy lives in `module.k3s_node.scaleway_block_snapshot.data_volume[0]`, or use the Console/`scw block snapshot list`).
2. Switch to the workspace you are rebuilding (`terraform workspace select test` for the test stack, or `default` for prod) and tear down the compute resources as shown in “Destroy/recreate while keeping the data volume”; the disk stays intact because `prevent_destroy` blocks its deletion.
3. Remove the stale volume from Terraform state so a new disk can be created: `terraform state rm module.k3s_node.scaleway_block_volume.data_volume`.
4. Set `$env:TF_VAR_data_volume_snapshot_id = '<snapshot-id>'` (or add `TF_VAR_data_volume_snapshot_id=<snapshot-id>` to your `.env`) and re-run `terraform apply`. Terraform will now create the block volume from the referenced snapshot before attaching it to the node.
5. After the restore, reset `TF_VAR_data_volume_snapshot_id` to an empty string if you want subsequent applies to start from a blank, fresh disk.

The same workflow also works in the default workspace when you need to roll production back to a previous snapshot; just be mindful of `prevent_destroy` when you remove the disk from state so Terraform can rebuild it cleanly.

### Rebuilding the test workspace from scratch

When you want to bootstrap a new test environment directly from a prod snapshot (for example to validate `cloud-init`/credentials without touching prod), follow this guard-raised sequence so Terraform never tries to attach the same block volume twice:

1. **Produce a usable snapshot in `default`**  
   - Select the prod workspace, confirm the node is healthy and `cloud-init` finished.  
   - Because the first apply after a fresh setup may not yet expose `time_rotating.snapshot_trigger[0].rotation_rfc3339`, you may need two applies: one to create the instance/volume/trigger and a second (or `terraform taint` + `-target=`) to create the snapshot with a valid `name`.  
   - Copy its `snapshot_id` (`terraform state show module.k3s_node.scaleway_block_snapshot.data_volume[0]`).

2. **Tear down the stale test resources**  
   - Switch to `terraform workspace select test` and `terraform destroy -auto-approve -target=module.k3s_node.scaleway_instance_server.k3s_node_1 -target=module.k3s_node.scaleway_instance_ip.public_ip` so the volume is detached (avoids “still in use” errors).  
   - Remove the volume entry from state: `terraform state rm module.k3s_node.scaleway_block_volume.data_volume`.  

3. **Apply the snapshot**  
   - Set env vars for the test run: `$env:TF_VAR_env_name='test'` and `$env:TF_VAR_data_volume_snapshot_id='<snapshot-id>'` (use the UUID shown in the snapshot output, *not* the zoned `fr-par-1/...`).  
   - Run `terraform apply -auto-approve`. Terraform will create a fresh test volume from that snapshot and attach it to a new test node.  
   - After apply finishes, clear the `TF_VAR_data_volume_snapshot_id` variable (set back to empty) so future applies no longer try to reuse the same snapshot.

4. **Verify the test stack**  
   - Watch `cloud-init status --wait --long` and tail `/var/log/cloud-init-output.log`/`/var/log/bootstrap.log` on the test node; the bootstrap will generate new secrets and sync the same GitHub webhook as prod but under the test domains (`test.kimai2.devsh.eu`, etc.).  
   - Confirm secrets/credentials via `kubectl -n apps-tools get secret kimai-admin-credentials` (the values should match what prod generated if you restored `kimai-db-credentials`, `grafana` and friends first).  

5. **Cleanup**  
   - Leave the kube resources running for verification or destroy them via `terraform destroy` in the test workspace when you are done. The shared bucket (`terra-luks-keys`) remains intact thanks to `prevent_destroy`.
## Troubleshooting
- No flux pods yet: watch `/var/log/cloud-init-output.log` until install finished.
- Kustomization not Ready: `kubectl -n flux-system describe kustomization apps`.
- Webhook: check GitHub deliveries and `kubectl -n flux-system logs deploy/notification-controller`.
- Disk pressure/evictions: clean images or move DB to dedicated persistent storage.
