# Rocket.Chat

Rocket.Chat runs on the dedicated `prod-rocket-01` instance and stores persistent data on the encrypted `/mnt/data` volume.

The node follows the standard production model:

1. cloud-init bootstraps k3s, SOPS, and Flux.
2. Flux reconciles `terraform/rocket-k8s`.
3. Rocket.Chat, MongoDB, Tailscale, node-exporter, and operator tooling run from Kubernetes manifests committed to this repository.

## Access

`https://rocketchat.devsh.eu` is a VPN-only service URL. Public DNS remains present for ACME and operational clarity, but HTTPS access is expected through the Headscale tailnet DNS record in `terraform/vpn-control-k8s/apps/headscale-config.yaml`.

TLS stays enabled at the Rocket.Chat ingress. Users still open the normal hostname and see a normal Let's Encrypt certificate.

## Accounts

Public registration is disabled. Operators create users from the Flux-managed ops pod:

```bash
k3s kubectl -n rocket exec deploy/rocket-ops -- rocket-invite user@example.com --role member
k3s kubectl -n rocket exec deploy/rocket-ops -- rocket-invite admin@example.com --role admin
```

The command creates or updates the Rocket.Chat user, assigns the requested role, and sends a custom DevSH access email through the shared notification SMTP credentials. Temporary passwords are not printed to stdout.

`rocket-ops` authenticates to Rocket.Chat with a dedicated technical bootstrap admin account. Human admins are separate users created through `rocket-invite`; do not reuse a human account as the bootstrap account.

Two-factor authentication is available for users to enable themselves, but email 2FA auto opt-in is disabled. A newly invited user must be able to sign in with the bootstrap password from the access email without an automatic 2FA prompt.

Rocket.Chat SMTP is configured through the shared `notification-smtp` secret and explicit `SMTP_*` application settings. Do not rely on `MAIL_URL` alone because Rocket.Chat rebuilds that environment variable from its SMTP settings at runtime.

## Attachments

Rocket.Chat file access is handled by the application. The deployment keeps these settings enabled:

- `FileUpload_ProtectFiles=true`
- `FileUpload_Restrict_to_room_members=true`
- `FileUpload_Restrict_to_users_who_can_access_room=false`
- `FileUpload_Enable_json_web_token_for_files=false`

The expected behavior is that a copied attachment URL cannot be opened from a browser session that is not authenticated and authorized for the room. Do not add an infra media gateway unless this application-level behavior regresses.

## Privacy

Rocket.Chat is operated as a self-hosted DevSH service. Chat messages, users, rooms, and file uploads stay in the local MongoDB deployment on `prod-rocket-01`.

The application runs from the official Rocket.Chat image:

```text
registry.rocket.chat/rocketchat/rocket.chat:8.4.1
```

Rocket.Chat source code is not patched for production. Version changes happen by changing the official image tag in the HelmRelease and letting Flux reconcile the deployment.

The deployment sends Rocket.Chat metadata and usage statistics to the official collector so the workspace follows the supported self-managed Starter path instead of the air-gapped read-only path. The committed metadata is:

- `Organization_Name=Devsh Graphics Programming`
- `Industry=technologyServices`
- `Size=0` (1-10 people)
- `Country=poland`
- `Website=https://www.devsh.eu`

The deployment keeps Rocket.Chat push gateway integration disabled:

- `Push_enable=false`
- `Push_enable_gateway=false`

The `rocket-settings-guard` CronJob enforces local policy settings in MongoDB, including setup wizard completion, file protection, account policy, metadata, and deployment fingerprint verification. It must not clear `Cloud_Workspace_*`, collector tokens, cloud URLs, registration data, or license data.

Deployment fingerprint changes are auto-accepted as regular configuration updates with `AUTO_ACCEPT_FINGERPRINT=true`. This prevents admin-only workspace identity prompts after expected Flux, URL, or MongoDB connection changes.

## Data

MongoDB is deployed as a single-member replica set through MongoDB Community Operator. Rocket.Chat uses authenticated MongoDB credentials and GridFS-backed file storage by default.

Persistent data lives under `/mnt/data/local-path` through the local-path provisioner. The `rocket` data volume is a managed snapshot target in `terraform/snapshots/`.

## Monitoring

`prod-rocket-01` runs the same node-exporter DaemonSet as the other dedicated nodes. Terraform exposes port `9100` only to the observability node public IP.

Deployment checks:

```bash
k3s kubectl -n rocket rollout status deploy/rocketchat-rocketchat
k3s kubectl -n rocket get pods
```

## Secrets

Secrets are SOPS-encrypted in the repo:

- Rocket.Chat bootstrap admin service account
- Rocket.Chat MongoDB connection strings
- MongoDB application user password
- Rocket.Chat SMTP URL
- Tailscale preauth key

Never commit plaintext exports, invite passwords, preauth keys, database files, kubeconfig files, or Terraform state.
