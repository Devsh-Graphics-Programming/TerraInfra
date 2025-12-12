# Monitoring (Grafana dashboards)

Provisioning
- Dashboards are provisioned from ConfigMap `grafana-dashboards` (label `grafana_dashboard=1`) in `monitoring-grafana`.
- Files live in repo: `terraform/k8s/grafana-dashboards/*.json`. Flux apps kustomization includes this folder; Grafana mounts it via `dashboardProviders`/`dashboardsConfigMaps`.

View current dashboards
- Grafana: `https://${ENV_PREFIX}monitoring.${BASE_DOMAIN}`.
- Admin credentials (read from secret):
  ```
  k3s kubectl -n monitoring-grafana get secret monitoring-grafana -o jsonpath='{.data.admin-user}' | base64 -d
  k3s kubectl -n monitoring-grafana get secret monitoring-grafana -o jsonpath='{.data.admin-password}' | base64 -d
  ```

Update existing dashboards
- Edit the JSON in `terraform/k8s/grafana-dashboards/`.
- Commit to `env/test`, fast-forward to `env/prod`, reconcile Flux:
  ```
  export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
  flux reconcile kustomization apps -n flux-system --with-source
  ```
- Grafana auto-reloads after rollout restart if needed:
  `k3s kubectl rollout restart deploy/grafana -n monitoring-grafana`

Add a new dashboard
1) In Grafana UI import/create the dashboard.
2) Export JSON via API (example):
   ```
   G_USER=$(k3s kubectl -n monitoring-grafana get secret monitoring-grafana -o jsonpath='{.data.admin-user}' | base64 -d)
   G_PASS=$(k3s kubectl -n monitoring-grafana get secret monitoring-grafana -o jsonpath='{.data.admin-password}' | base64 -d)
   curl -ks -u "$G_USER:$G_PASS" https://${ENV_PREFIX}monitoring.${BASE_DOMAIN}/api/dashboards/uid/<uid> -o terraform/k8s/grafana-dashboards/<name>.json
   ```
3) Commit JSON (keep it under `grafana-dashboards/`), push to `env/test`, fast-forward to `env/prod`, reconcile Flux.

Notes
- Datasource is pre-provisioned to Prometheus (`monitoring-kube-prometheus-prometheus.monitoring.svc:9090`); dashboards should reference it as default.
- If you remove a dashboard JSON from the repo, Grafana will drop it on next reconcile.

## Alerting (Alertmanager → OnCall → Discord)
- Full flow lives in `monitoring-oncall` (HelmRelease `oncall`). Alertmanager webhooks go to OnCall integrations, which forward to Discord via outgoing webhooks. See `docs/alerts.md` for the flow and test steps.
- OnCall URLs/tokens: `terraform/k8s/vars/prod/secrets/alertmanager-oncall.yaml`. Discord webhooks: `alertmanager-discord` secrets (prod/test).
- Bootstrap job (`oncall-bootstrap`) keeps integrations/webhooks in sync; rerun it if you rotate tokens/secrets:
  `k3s kubectl delete job/oncall-bootstrap -n monitoring-oncall`.
- Alert rules are defined in `terraform/k8s/monitoring-alerts.tpl.yaml` (Node down, disk pressure, CoreDNS/CP down, PVC 90%, Flux failed/stalled, CrashLoop, HPA max, etc.).

## Image digest rollout (www/blog)
- Flux polls GHCR (`image.toolkit.fluxcd.io` ImageRepository/ImagePolicy), but git writes are suspended.
- CronJob `digest-rollout` (namespace `website`) runs every 2m:
  - reads latest digest for `www-website:latest` and `www-blog:latest`,
  - compares with deployment annotation,
  - if changed, patches the deployment annotation to force a restart.
- Containers use `imagePullPolicy: Always`, so new pods pull the updated digest.
- Run job manually:
  ```
  k3s kubectl create job --from=cronjob/digest-rollout digest-rollout-manual -n website
  k3s kubectl logs job/digest-rollout-manual -n website
  k3s kubectl delete job/digest-rollout-manual -n website
  ```
- To verify rollout on new image: push `latest` to GHCR, wait ≤2m, then `k3s kubectl rollout status deploy/devsh-website -n website` (same for `devsh-blog`).
