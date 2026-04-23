# StoatChat

StoatChat runs on the dedicated `prod-chat-01` instance and stores persistent data on the encrypted `/mnt/data` volume.

The node follows the standard production model:

1. cloud-init bootstraps k3s, SOPS, and Flux.
2. Flux reconciles `terraform/chat-k8s`.
3. StoatChat workloads run from Kubernetes manifests committed to this repository.

The instance bootstrap keeps registration invite-only by enforcing this section in `Revolt.toml`:

```toml
[api.registration]
invite_only = true
```

The bootstrap does not create a default application user or admin password. Create the first user through an invite code and keep the code out of the repository.

Registration invites are created from the Flux-managed ops pod:

```bash
kubectl -n chat exec deploy/stoat-ops -- stoat-invite invite user@example.com
```

The command creates a single-use registration invite in MongoDB and sends the invite email through the shared notification SMTP credentials. The invite code is not printed to stdout.

Operational notes:

- `stoatchat.devsh.eu` is served by the chat k3s ingress.
- Message attachments under `/autumn/attachments` are served through the Flux-managed media gateway. Anonymous bearer URLs are blocked. A logged-in browser receives a short-lived `HttpOnly` media cookie from normal `/api` traffic, and the gateway verifies the backing message/channel access in MongoDB before proxying to Autumn.
- Other Autumn media classes such as avatars and icons remain public because they are profile or server presentation assets.
- Do not rely on live-only config edits as durable configuration. Land durable behavior in this repository and let Flux reconcile it.
- Do not commit invite codes, tokens, generated secrets, or database dumps.
