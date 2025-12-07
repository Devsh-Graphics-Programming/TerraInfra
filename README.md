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
SCW_DEFAULT_PROJECT_ID=...
KIMAI_DOMAIN=kimai2.devsh.eu
MONITORING_DOMAIN=monitoring.devsh.eu
ACME_EMAIL=you@example.com
GITHUB_PERSISTENT_TERRA_INFRA_RO_PAT=ghp_...   # read-only PAT stored in cluster for repo access
GITHUB_BOOTSTRAP_TERRA_INFRA_WEBHOOK_PAT=ghp_...   # required to auto-create GitHub webhook at bootstrap (Webhooks RW)
FLUX_HOOK_DOMAIN=flux-hook.prod.devsh.eu
```
Load envs: `.\env.ps1`

## Deploy / Recreate
From `terraform/`:
```
terraform apply `
  -var "project_id=$env:SCW_DEFAULT_PROJECT_ID" `
  -var "env_name=prod" `
  -var "kimai_domain=$env:KIMAI_DOMAIN" `
  -var "monitoring_domain=$env:MONITORING_DOMAIN" `
  -var "acme_email=$env:ACME_EMAIL" `
  -var "config_repo_url=https://github.com/Devsh-Graphics-Programming/TerraInfra.git" `
  -var "config_repo_branch=main" `
  -var "config_repo_path=terraform/k8s" `
  -var "github_persistent_terra_infra_ro_pat=$env:GITHUB_PERSISTENT_TERRA_INFRA_RO_PAT" `
  -var "github_bootstrap_terra_infra_webhook_pat=$env:GITHUB_BOOTSTRAP_TERRA_INFRA_WEBHOOK_PAT" `
  -var "flux_hook_domain=$env:FLUX_HOOK_DOMAIN"
```
To force rebuild: `terraform taint module.k3s_node_prod.scaleway_instance_server.k3s_node_1` then apply.

## DNS
Set A records to the cluster IP (e.g. `212.47.251.150`):
- `kimai2.devsh.eu`
- `monitoring.devsh.eu`
- `flux-hook.prod.devsh.eu`

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
cloud-init status --long
tail -f /var/log/cloud-init-output.log   # wait until you see all flux components ready and "Cloud-init ... finished ..."
tail -f /var/log/bootstrap.log           # live Python bootstrap output
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

## Troubleshooting
- No flux pods yet: watch `/var/log/cloud-init-output.log` until install finished.
- Kustomization not Ready: `kubectl -n flux-system describe kustomization apps`.
- Webhook: check GitHub deliveries and `kubectl -n flux-system logs deploy/notification-controller`.
- Disk pressure/evictions: clean images or move DB to dedicated persistent storage.
