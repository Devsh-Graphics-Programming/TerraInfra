# TerraInfra

Infra for Devsh (k3s on Scaleway) with GitOps via Flux. Branches:
- `env/prod` → production cluster
- `env/test` → test cluster

Docs live in `docs/`:
- `docs/getting-started.md` – prerequisites, tooling, `.env` template, age key, GitOps flow
- `docs/environments.md` – prod/test branches & workspaces
- `docs/secrets.md` – SOPS/age secrets: create/encrypt/decrypt
- `docs/snapshots.md` – prod snapshot workflow & restore to test
- `docs/dns.md` – DNS (manual for now)
