# TerraInfra

Infra for Devsh (k3s on Scaleway) with GitOps via Flux. Branches:
- `env/prod` → production cluster (kept up, do not destroy)
- `env/test` → ephemeral/test cluster (can be recreated on demand)

> [!WARNING]
> Pushing to `env/prod` reconciles the live production cluster. Read `docs/environments.md` and `docs/getting-started.md` before changing prod.

Docs live in `docs/`:
- `docs/getting-started.md` – prerequisites, tooling, `.env` template, age key, GitOps flow
- `docs/environments.md` – prod/test branches & workspaces
- `docs/how-to-commit.md` – fast-forward workflow (test → prod)
- `docs/secrets.md` – SOPS/age secrets: create/encrypt/decrypt
- `docs/snapshots.md` – prod snapshot workflow & restore to test
- `docs/dns.md` – DNS (manual for now)
- `docs/security.md` – hardening matrix, how to add services with current security baseline
- `docs/ui.md` – Kubernetes Dashboard (read-only) access
- `docs/monitoring.md` – Grafana dashboard provisioning and updates

## Testing cheatsheet
- Alerts → Discord: exec into alertmanager and send a test alert (labels `severity=warning|critical`) to verify Discord message + RESOLVED update:  
  `k3s kubectl exec -n monitoring deploy/monitoring-kube-prometheus-alertmanager -- sh -c 'apk add --no-cache curl >/dev/null && curl -XPOST -H "Content-Type: application/json" -d '[{"\""}labels{"\""}:{"\""}alertname{"\""}:{"\""}TestAlert{"\""},"\""}severity{"\""}:{"\""}warning{"\""}},{"\""}annotations{"\""}:{"\""}summary{"\""}:{"\""}test{"\""}}]' http://localhost:9093/api/v2/alerts'`
- Image rollout (website/blog): push a new semver tag to GHCR (`ghcr.io/devsh-graphics-programming/www-website` / `www-blog`), then watch automation:  
  `flux get image policy -n flux-system`, `flux get image update -n flux-system`, confirm commit and rollout of website/blog Deployments.
- Alert noise/dedupe: Alertmanager groups by alertname/namespace/cluster/resource with `for` on rules; routes only by `severity`.
