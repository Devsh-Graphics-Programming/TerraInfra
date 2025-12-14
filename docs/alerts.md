# Alerting flow (OnCall)

- Routing: Prometheus Alertmanager → Grafana OnCall (two integrations: `critical`/`warning`) → OnCall outgoing webhook → `oncall-discord-proxy` → Discord webhooks (`#alerts-critical`, `#alerts-warning`).
- Discord: one message per alert group, updated in-place on resolve; only `critical` includes the `@OnCall` role mention (warnings do not mention).
- OnCall stack lives in `monitoring-oncall` (HelmRelease `oncall`) with a dedicated Grafana UI: `https://${ENV_PREFIX}oncall.${BASE_DOMAIN}/grafana`.
- Storage: Postgres + Redis subcharts (RabbitMQ is disabled); Grafana state on a local PV (`/mnt/data/oncall-grafana`).

## Bootstrap and wiring
- Job `oncall-bootstrap` (namespace `monitoring-oncall`) runs on reconcile:
  - reads `alertmanager-oncall` and `alertmanager-discord-proxy` secrets,
  - creates/updates two Alertmanager integrations with fixed tokens (`alertmanager-critical`, `alertmanager-warning`),
  - configures outgoing webhooks (trigger: status change) to call the in-cluster Discord proxy.
- If you rotate tokens/webhooks, update SOPS secrets and rerun the job:
  `k3s kubectl delete job/oncall-bootstrap -n monitoring-oncall`.

## Smoke test (FIRING → RESOLVED, 15s, no spam)
This hits the OnCall Alertmanager integration endpoint directly (deterministic; good for formatting and “edit-on-resolve”).

1) Get integration URLs (from the node):
```
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
critical=$(k3s kubectl -n monitoring get secret alertmanager-oncall -o jsonpath='{.data.critical_url}' | base64 -d)
warning=$(k3s kubectl -n monitoring get secret alertmanager-oncall -o jsonpath='{.data.warning_url}' | base64 -d)
```

2) Fire and resolve:
```
k3s kubectl -n monitoring-oncall run --rm -i alert-smoke --restart=Never --image=curlimages/curl -- sh -c '
set -euo pipefail
cat >/tmp/firing.json <<EOF
{
  "receiver": "oncall-smoke",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "oncall-smoke-delay15",
        "severity": "warning",
        "cluster": "prod",
        "namespace": "monitoring",
        "instance": "51.158.67.237:9100"
      },
      "annotations": {
        "summary": "OnCall Discord smoke test",
        "description": "Expect one Discord message that updates on resolve"
      },
      "startsAt": "2025-01-01T00:00:00Z",
      "endsAt": "0001-01-01T00:00:00Z"
    }
  ],
  "commonLabels": {
    "alertname": "oncall-smoke-delay15",
    "severity": "warning",
    "cluster": "prod",
    "namespace": "monitoring",
    "instance": "51.158.67.237:9100"
  },
  "commonAnnotations": {
    "summary": "OnCall Discord smoke test",
    "description": "Expect one Discord message that updates on resolve"
  },
  "version": "4"
}
EOF
cat >/tmp/resolved.json <<EOF
{
  "receiver": "oncall-smoke",
  "status": "resolved",
  "alerts": [
    {
      "status": "resolved",
      "labels": {
        "alertname": "oncall-smoke-delay15",
        "severity": "warning",
        "cluster": "prod",
        "namespace": "monitoring",
        "instance": "51.158.67.237:9100"
      },
      "annotations": {
        "summary": "OnCall Discord smoke test",
        "description": "Expect one Discord message that updates on resolve"
      },
      "startsAt": "2025-01-01T00:00:00Z",
      "endsAt": "2025-01-01T00:00:15Z"
    }
  ],
  "commonLabels": {
    "alertname": "oncall-smoke-delay15",
    "severity": "warning",
    "cluster": "prod",
    "namespace": "monitoring",
    "instance": "51.158.67.237:9100"
  },
  "commonAnnotations": {
    "summary": "OnCall Discord smoke test",
    "description": "Expect one Discord message that updates on resolve"
  },
  "version": "4"
}
EOF
curl -sS -XPOST -H "Content-Type: application/json" -d @/tmp/firing.json "$warning"
sleep 15
curl -sS -XPOST -H "Content-Type: application/json" -d @/tmp/resolved.json "$warning"
'
```

Expected:
- Exactly one Discord message in `#alerts-warning` without the `@OnCall` mention.
- The message is edited in-place on resolve (no second message).
- The title links to the OnCall alert group.

## Logs
- Discord proxy: `k3s kubectl -n monitoring-oncall logs deploy/oncall-discord-proxy --tail=200`
- OnCall engine: `k3s kubectl -n monitoring-oncall logs deploy/oncall-engine --tail=200`
- Alertmanager: `k3s kubectl -n monitoring logs sts/alertmanager-monitoring-kube-prometheus-alertmanager --tail=200`
