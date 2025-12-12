# Alerting flow (OnCall)

- Routing: Prometheus Alertmanager → Grafana OnCall (two integrations: critical/warning) → Discord webhooks (`#alerts-critical`, `#alerts-warning`).
- OnCall stack lives at `monitoring-oncall` (`oncall` HelmRelease) with a dedicated Grafana UI: `https://${ENV_PREFIX}oncall.${BASE_DOMAIN}/grafana`.
- Webhook URLs for Alertmanager live in `terraform/k8s/vars/prod/secrets/alertmanager-oncall.yaml`; OnCall bootstrap job aligns integration tokens to these URLs and wires outgoing webhooks using the Discord secrets (`alertmanager-discord`).
- OnCall storage: Postgres/RabbitMQ/Redis as subcharts (passwords in `oncall-*` SOPS secrets), Grafana state on a local PV (`/mnt/data/oncall-grafana`).

## How the bootstrap works
- Job `oncall-bootstrap` (namespace `monitoring-oncall`) runs on reconcile:
  - reads `alertmanager-oncall` + `alertmanager-discord` secrets,
  - creates/updates two Alertmanager integrations with fixed tokens (`alertmanager-critical`, `alertmanager-warning`),
  - attaches outgoing webhooks filtered per integration to the matching Discord webhook, trigger type `status change` (fires on both firing/resolved).
- If you rotate tokens/webhooks, update the SOPS secrets and rerun the job: `k3s kubectl delete job/oncall-bootstrap -n monitoring-oncall`.

## Test FIRING & RESOLVED (no spam)
Use Alertmanager’s API so grouping/dedupe stays intact.

```
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
k3s kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-alertmanager 9093:9093 >/tmp/am-fw.log 2>&1 &
FW_PID=$!
sleep 2

curl -XPOST -H "Content-Type: application/json" \
  -d '[{"labels":{"alertname":"OnCallTest","severity":"warning","job":"manual"},"annotations":{"summary":"oncall test alert"}}]' \
  http://127.0.0.1:9093/api/v2/alerts

# Resolve the same alert group (reuses alertname+severity to avoid duplicates)
curl -XPOST -H "Content-Type: application/json" \
  -d '[{"labels":{"alertname":"OnCallTest","severity":"warning","job":"manual"},"annotations":{"summary":"oncall test alert"},"endsAt":"'"$(date -Iseconds)"'"}]' \
  http://127.0.0.1:9093/api/v2/alerts

kill $FW_PID
```

Expected:
- Exactly one Discord message per firing and one per resolved in the proper channel by severity.
- Alert visible in OnCall UI (`Alert Groups`) with state transitioning to resolved.

## Operations cheatsheet
- OnCall UI: `https://${ENV_PREFIX}oncall.${BASE_DOMAIN}` (Grafana admin creds in `monitoring-oncall-grafana` secret).
- Outgoing webhooks live on the OnCall side (names `discord-critical`/`discord-warning`) and are filtered by integration; routing changes are handled by the bootstrap job script.
- To rotate secrets/tokens: update SOPS secrets (`oncall-*`, `alertmanager-oncall`, `alertmanager-discord`), reconcile Flux, rerun `oncall-bootstrap`.
