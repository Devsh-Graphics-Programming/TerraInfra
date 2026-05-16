# VPN control plane

DevSH uses a self-hosted Headscale tailnet with the official Tailscale client. Headscale is the coordination server. Authentik is the identity provider used for human login and group membership.

## Infrastructure

- Node: `prod-vpn-control-01`
- Terraform: `terraform/vpn-control.tf`
- Flux tree: `terraform/vpn-control-k8s/`
- Public endpoints:
  - `https://auth.devsh.eu` for Authentik
  - `https://headscale.devsh.eu` for Headscale
- Instance type: `DEV1-M`
- Root volume: 40 GiB `l_ssd`
- Data volume: 20 GiB `l_ssd`, mounted encrypted at `/mnt/data`

The node follows the standard production model: cloud-init bootstraps k3s and Flux, then Flux reconciles `vars`, `infra`, and `apps` from the repository. Do not manage Authentik or Headscale with live-only Docker Compose or host-local config files.

## User access

Human users are managed in Authentik, not in Git.

1. Open `https://auth.devsh.eu`.
2. Create or invite the user.
3. Add the user to the `vpn-users` group.
4. The user connects with the Tailscale client using the custom login server `https://headscale.devsh.eu`.

Headscale accepts OIDC logins only for members of the Authentik `vpn-users` group. User accounts and group membership are operational state in Authentik. The repository contains the OIDC application wiring and Headscale policy, not the user list.

Operators can send a one-time DevSH VPN access invite from the Flux-managed `authentik-ops` pod:

```
k3s kubectl -n authentik exec deploy/authentik-ops -- vpn-invite user@example.com --profile member --ttl 24h
```

Supported profiles:

- `member`: adds the user to `vpn-users`.
- `admin`: adds the user to `vpn-users` and `authentik Admins`.

The invite email contains a single-use account setup link, the Tailscale download link, the official Tailscale custom control server guide, and the Headscale login server URL. Invite links and user email addresses are runtime state and must not be committed to Git.

Operators can send a one-time DevSH VPN password reset email for an existing Authentik user:

```
k3s kubectl -n authentik exec deploy/authentik-ops -- vpn-password-reset user@example.com --ttl 1h
```

The reset email contains a single-use password reset link and the Headscale login server URL. Password reset links are runtime state and must not be committed to Git.

## Service access model

Private service URLs keep normal HTTPS names. `https://rocketchat.devsh.eu` is served over the tailnet for VPN clients.

For VPN users, Headscale DNS publishes private `A` records in `terraform/vpn-control-k8s/apps/headscale-config.yaml` so the same public hostname resolves to the service node's tailnet IP. Public DNS stays usable for certificate automation and external discovery, but service ports are closed at the cloud security group when a service is VPN-only.

TLS stays enabled at the service ingress. The browser still sees the normal public hostname and a normal Let's Encrypt certificate.

## Policy

Headscale configuration and tailnet ACL policy live in `terraform/vpn-control-k8s/apps/headscale-config.yaml`.

Current policy allows authenticated VPN members to reach tailnet devices. Tighten this file when per-service or per-device network segmentation is needed. Keep user lifecycle in Authentik unless a specific ACL needs a static user or device identity in Git.

## Backups

Persistent state is on the encrypted data volume:

- Authentik PostgreSQL uses `local-path` storage under `/mnt/data/local-path`.
- Headscale stores SQLite, Noise, and DERP keys under the `headscale-data` PVC in `/mnt/data/local-path`.

The `vpn-control` data volume is a managed snapshot target in `terraform/snapshots/` and the snapshot restore drill checks that the restored local-path data root exists.

## Monitoring

`prod-vpn-control-01` runs the same node-exporter DaemonSet as the other dedicated nodes. Terraform exposes port `9100` only to the observability node public IP.

Headscale exposes internal metrics on port `9090` inside the cluster. Add central scraping for that endpoint when application-level VPN metrics are needed.

## Secrets

Secrets are SOPS-encrypted in the repo:

- Authentik bootstrap credentials
- Authentik chart secret values
- Headscale OIDC client secret
- SMTP credentials reused from the shared notification secret

Never commit plaintext exports, pre-auth keys, device keys, database files, or kubeconfig files.
