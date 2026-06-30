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

The `user` role has `api-bypass-rate-limit` so authenticated UI actions are not blocked by Rocket.Chat REST route limits that key only by client IP. Unauthenticated endpoints such as login still use Rocket.Chat's API rate limiter.

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
registry.rocket.chat/rocketchat/rocket.chat:8.4.4
```

Rocket.Chat source code is not patched for production. Version changes happen by changing the official image tag in the HelmRelease and letting Flux reconcile the deployment.

The deployment sends Rocket.Chat metadata and usage statistics to the official collector so the workspace follows the supported self-managed Starter path instead of the air-gapped read-only path. The committed metadata is:

- `Organization_Name=Devsh Graphics Programming`
- `Industry=technologyServices`
- `Size=0` (1-10 people)
- `Country=poland`
- `Website=https://www.devsh.eu`

Rocket.Chat Cloud registration traffic uses the official Cloud URL:

- `Cloud_Url=https://cloud.rocket.chat`

The deployment keeps Rocket.Chat push gateway integration disabled:

- `Push_enable=false`
- `Push_enable_gateway=false`

The deployment keeps server-side link previews disabled:

- `API_Embed=false`

The `rocket-settings-guard` CronJob enforces local policy settings in MongoDB, including setup wizard completion, file protection, account policy, metadata, Cloud URL, and deployment fingerprint verification. It must not clear workspace credentials, collector tokens, real registration data, or license data.
The guard only removes legacy `http://127.0.0.1:9` Cloud URL leftovers when that exact old offline-mode value is present.

Deployment fingerprint changes are auto-accepted as regular configuration updates with `AUTO_ACCEPT_FINGERPRINT=true`. This prevents admin-only workspace identity prompts after expected Flux, URL, or MongoDB connection changes.

## Data

MongoDB is deployed as a single-member replica set through MongoDB Community Operator. Rocket.Chat uses authenticated MongoDB credentials and GridFS-backed file storage by default.

Persistent data lives under `/mnt/data/local-path` through the local-path provisioner. The `rocket` data volume is a managed snapshot target in `terraform/snapshots/`.
The bootstrap script links the k3s default local-path storage directory to `/mnt/data/local-path` before k3s starts, so early PVC provisioning still lands on the encrypted data volume even before Flux patches the local-path provisioner config.

## Monitoring

`prod-rocket-01` runs the same node-exporter DaemonSet as the other dedicated nodes. Terraform exposes port `9100` only to the observability node public IP.

Rocket.Chat application health is exported through `rocket-healthcheck`, a small in-cluster exporter exposed on NodePort `30101`. Terraform exposes that port only to the observability node public IP.

The exporter logs in through the Rocket.Chat API, checks workspace read-only risk, verifies the statistics token, verifies the supported free self-managed path is not blocked, creates and deletes a synthetic message, uploads a temporary attachment, verifies unauthenticated attachment access is denied, and verifies authenticated attachment access works. Expensive checks are cached for 5 minutes.

The key metrics are:

- `rocket_workspace_health_success`
- `rocket_workspace_stats_token_present`
- `rocket_workspace_airgapped_remaining_days`
- `rocket_workspace_read_only_risk`
- `rocket_synthetic_success`
- `rocket_synthetic_attachment_unauth_denied`

Alert rules live in `terraform/k8s/monitoring-alerts/monitoring-alerts.tpl.yaml`. They page on Rocket.Chat read-only risk, missing stats token, failed or stale health checks, and attachment protection regression.

Deployment checks:

```bash
k3s kubectl -n rocket rollout status deploy/rocketchat-rocketchat
k3s kubectl -n rocket get pods
```

Manual healthcheck run:

```bash
k3s kubectl -n rocket rollout status deploy/rocket-healthcheck
k3s kubectl -n rocket port-forward svc/rocket-healthcheck 9101:9101
curl -s http://127.0.0.1:9101/metrics | grep '^rocket_'
```

NodePort check from the Rocket node:

```bash
curl -s http://127.0.0.1:30101/metrics | grep '^rocket_'
```

The synthetic check uses the dedicated `rocket.ops` bootstrap admin account. It does not use a human admin account and does not print secrets.

## Restore Drill

The `rocket` snapshot target is included in the managed snapshot and restore-drill workflows. The restore drill creates a temporary verifier from a selected snapshot and does not touch the production Rocket.Chat node, DNS, ingress, or live MongoDB.

Use `.github/workflows/snapshot-restore-drill.yml` with:

```text
target_names=rocket
snapshot_source=auto
```

For a fresh manual snapshot, run the snapshots workflow first for `target_names=rocket`, then run the restore drill with `snapshot_source=manual` and the manual snapshot name.
When validating a specific chat message, set `expected_rocket_message` to that exact `#general` message text. The workflow only reports whether it was found and does not print the message content in the sanitized result.

## Secrets

Secrets are SOPS-encrypted in the repo:

- Rocket.Chat bootstrap admin service account
- Rocket.Chat MongoDB connection strings
- MongoDB application user password
- Rocket.Chat SMTP URL
- Tailscale preauth key

Never commit plaintext exports, invite passwords, preauth keys, database files, kubeconfig files, or Terraform state.
