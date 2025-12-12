# UI access (Kubernetes Dashboard)

We ship Kubernetes Dashboard (read-only) via Helm:
- Host: `${ENV_PREFIX}k8s.${BASE_DOMAIN}` (e.g., `k8s.devsh.eu` for prod). Update DNS A record to the node IP before use.
- TLS: cert-manager issues `dashboard-cert` (check: `k3s kubectl -n kubernetes-dashboard get certificate`).

Login (token):
```
# short-lived (default, ~1h)
k3s kubectl -n kubernetes-dashboard create token dashboard-sa

# longer (example: 24h)
k3s kubectl -n kubernetes-dashboard create token dashboard-sa --duration=24h
```
Use the token in the Dashboard login screen.

Ingress/egress:
- Ingress allowed via nginx ingress only.
- Egress restricted to DNS (kube-dns).

Notes:
- Dashboard is read-only (ClusterRoleBinding to `view`).
- If cert is Pending, ensure DNS is pointing to the current node IP and wait/retry a few minutes.
