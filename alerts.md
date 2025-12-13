# Alerts

- Flow: Alertmanager -> OnCall (Alertmanager integrations for critical and warning) -> OnCall outgoing webhook -> Discord proxy -> #alerts-critical / #alerts-warning with `@OnCall` mention; one message per alert group is patched on resolve.
- Routing: `severity` label picks the Discord channel; `cluster`, `instance`, `namespace`, and `alertname` show up in the card, and the title links to the OnCall alert group.
- Stack: dedicated OnCall namespace `monitoring-oncall` (Grafana + OnCall engine + Redis + Postgres + Discord proxy).

## Smoke test (prod)
1) Get integration URLs:
```
critical=$(k3s kubectl -n monitoring get secret alertmanager-oncall -o jsonpath='{.data.critical_url}' | base64 -d)
warning=$(k3s kubectl -n monitoring get secret alertmanager-oncall -o jsonpath='{.data.warning_url}' | base64 -d)
```
2) Fire and resolve (warning example):
```
k3s kubectl -n monitoring-oncall run --rm -i alert-smoke --restart=Never --image=curlimages/curl -- sh -c '
set -e
cat >/tmp/firing.json <<EOF
{
  "receiver": "oncall-smoke",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "oncall-smoke",
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
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "http://prometheus/graph"
    }
  ],
  "commonLabels": {
    "alertname": "oncall-smoke",
    "severity": "warning",
    "cluster": "prod",
    "namespace": "monitoring",
    "instance": "51.158.67.237:9100"
  },
  "commonAnnotations": {
    "summary": "OnCall Discord smoke test",
    "description": "Expect one Discord message that updates on resolve"
  },
  "externalURL": "http://alertmanager",
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
        "alertname": "oncall-smoke",
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
      "endsAt": "2025-01-01T00:10:00Z",
      "generatorURL": "http://prometheus/graph"
    }
  ],
  "commonLabels": {
    "alertname": "oncall-smoke",
    "severity": "warning",
    "cluster": "prod",
    "namespace": "monitoring",
    "instance": "51.158.67.237:9100"
  },
  "commonAnnotations": {
    "summary": "OnCall Discord smoke test",
    "description": "Expect one Discord message that updates on resolve"
  },
  "externalURL": "http://alertmanager",
  "version": "4"
}
EOF
curl -XPOST -H "Content-Type: application/json" -d @/tmp/firing.json "$warning"
sleep 15
curl -XPOST -H "Content-Type: application/json" -d @/tmp/resolved.json "$warning"
'
```
3) Expected: a single message in #alerts-warning with `@OnCall` mention; status flips to RESOLVED without creating a new message; the title links to the alert group in OnCall.

## Logs and checks
- Proxy Discord: `k3s kubectl -n monitoring-oncall logs deploy/oncall-discord-proxy`
- OnCall webhook responses: `k3s kubectl -n monitoring-oncall logs deploy/oncall-engine | grep webhook`
- Flux apply state: `k3s kubectl -n flux-system get kustomizations,helmreleases` and `k3s kubectl -n flux-system logs deploy/helm-controller`
