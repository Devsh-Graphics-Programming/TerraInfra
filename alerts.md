## Alerting flow

- Alertmanager (monitoring) routes webhooks by `severity`: `critical` → Grafana OnCall receiver `alertmanager-critical`, `warning` → `alertmanager-warning`.
- Grafana OnCall delivers notifications to Discord webhooks: `#alerts-critical` and `#alerts-warning`.
- Dedicated OnCall stack lives in namespace `monitoring-oncall` (separate Grafana + engine + Redis + Postgres).

## How to test

1. Upewnij się, że flux jest wznowiony (`flux resume kustomization apps -n flux-system`) i pody są `Running` w `monitoring-oncall`.
2. Wyślij testowe ALERTY:

   ```sh
   kubectl exec -n monitoring-oncall toolbox -- sh -c '
     cat > /tmp/alert-test.json <<EOF
     [
       {"labels":{"alertname":"oncall-test-critical","severity":"critical","instance":"manual"},"annotations":{"summary":"Test critical via OnCall"}},
       {"labels":{"alertname":"oncall-test-warning","severity":"warning","instance":"manual"},"annotations":{"summary":"Test warning via OnCall"}}
     ]
     EOF
     apk add --no-cache curl >/dev/null
     curl -s -XPOST -H "Content-Type: application/json" --data @/tmp/alert-test.json \
       http://monitoring-kube-prometheus-alertmanager.monitoring.svc:9093/api/v2/alerts
   '
   ```

3. Zweryfikuj w OnCall UI, że powstał jeden incident z dwoma alertami i że Discord otrzymał powiadomienia na właściwe kanały.
4. Wyślij RESOLVED dla obu:

   ```sh
   kubectl exec -n monitoring-oncall toolbox -- sh -c '
     cat > /tmp/alert-test-resolve.json <<EOF
     [
       {"status":"resolved","labels":{"alertname":"oncall-test-critical","severity":"critical","instance":"manual"}},
       {"status":"resolved","labels":{"alertname":"oncall-test-warning","severity":"warning","instance":"manual"}}
     ]
     EOF
     curl -s -XPOST -H "Content-Type: application/json" --data @/tmp/alert-test-resolve.json \
       http://monitoring-kube-prometheus-alertmanager.monitoring.svc:9093/api/v2/alerts
   '
   ```

5. Sprawdź w logach Alertmanagera, że webhooki dostają `Notify success` oraz że Discord pokazał zdarzenia `FIRING` i `RESOLVED` bez spamu.

