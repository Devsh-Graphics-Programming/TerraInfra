# StoatChat

StoatChat runs on the dedicated `prod-chat-01` instance and stores persistent data on the encrypted `/mnt/data` volume.

The instance bootstrap keeps registration invite-only by enforcing this section in `Revolt.toml`:

```toml
[api.registration]
invite_only = true
```

The bootstrap does not create a default application user or admin password. Create the first user through an invite code and keep the code out of the repository.

Operational notes:

- `stoatchat.devsh.eu` is served by the Caddy container in the self-hosted stack.
- The app currently runs as a standalone Docker Compose stack, not through Flux.
- Do not rely on live-only config edits as durable configuration. Land durable behavior in this repository and use an explicit reconcile step for the chat host until the app is moved under GitOps.
- Do not commit invite codes, tokens, generated secrets, or database dumps.

