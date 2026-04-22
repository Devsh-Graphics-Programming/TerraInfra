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

Operational notes:

- `stoatchat.devsh.eu` is served by the chat k3s ingress.
- Do not rely on live-only config edits as durable configuration. Land durable behavior in this repository and let Flux reconcile it.
- Do not commit invite codes, tokens, generated secrets, or database dumps.
