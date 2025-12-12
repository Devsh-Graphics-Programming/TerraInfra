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
