# Security baseline

Current hardening state per service/namespace:

| Service / Namespace          | PSA         | runAsNonRoot | RO rootfs | Storage (via PVC)                     | Egress                                          |
| ---------------------------- | ----------- | ------------ | --------- | ------------------------------------- | ----------------------------------------------- |
| Website / Blog (website)     | restricted  | ✓            | ✓         | ephemeral (tmpfs/emptyDir, no PV)     | DNS only                                        |
| Kimai app (apps-tools)       | restricted  | ✓            | ✗         | PVC → local PV `/mnt/data/kimai-var`  | DNS, MariaDB 3306, SMTP mail.devsh.eu:587       |
| MariaDB (apps-tools)         | restricted  | ✓            | ✗         | PVC → local PV `/mnt/data/mariadb`    | DNS only                                        |
| Grafana (monitoring-grafana) | restricted  | ✓            | ✓         | PVC → local PV `/mnt/data/grafana`    | DNS, kube-apiserver service IP 10.43.0.1:443 (dashboard sidecar), SMTP mail.devsh.eu:587 |
| Prom stack (monitoring)      | privileged* | chart defaults | chart defaults | chart-provisioned PVs            | DNS, SMTP mail.devsh.eu:587                     |
| Flux/infra/cert-manager      | baseline    | n/a          | n/a       | n/a                                   | controller defaults                             |

\*Prometheus node-exporter needs privileged host access; the namespace PSA is set to `privileged` for that DaemonSet.

## Adding a new service (follow this checklist)
- Namespace: add to `k8s/vars/namespaces.yaml` with PSA `restricted` (unless a stronger reason exists).
- Workload security:
  - `securityContext` with `runAsNonRoot: true`, `runAsUser`/`runAsGroup` non-root.
  - `readOnlyRootFilesystem: true` when the image supports it.
  - Drop capabilities/disable privilege escalation if chart allows.
- Storage:
  - Prefer PVC with local path and node affinity (pattern used in Kimai/Grafana PVs).
  - Keep reclaimPolicy `Retain` to preserve data across node recreations/snapshots.
- Network:
  - Ingress only from `infra` namespace (see existing NetPolicies).
  - Egress: whitelist DNS + only required hosts/ports. If SMTP is needed, lock to `mail.devsh.eu:587`; otherwise keep DNS-only. Allow kube-apiserver service IP (10.43.0.1:443) only when a sidecar needs to read ConfigMaps/Secrets (e.g., Grafana dashboard sidecar).
- Secrets:
  - Store as SOPS-encrypted YAML under `k8s/vars/secrets/`.
  - Reference via `envFromSecret` or `secretKeyRef`; no plaintext in manifests.
- GitOps:
  - Commit to `env/test`, fast-forward merge to `env/prod` (see `docs/how-to-commit.md`).

## How to test changes
- Reconcile and watch:
  - `flux reconcile kustomization apps -n flux-system --with-source`
  - `k3s kubectl get pods -A` to ensure workloads are Running.
- Network policies:
  - DNS reachability: `k3s kubectl exec -n <ns> <pod> -- nslookup google.com`
- SMTP (if allowed): `k3s kubectl exec -n monitoring-grafana deploy/grafana -- nc -zv mail.devsh.eu 587`
  - Confirm blocked paths by expecting failure (no nc) in namespaces without egress to the target.
- Services from outside:
  - `curl -Ik https://www.devsh.eu`, `https://blog.devsh.eu`, `https://kimai2.devsh.eu`, `https://monitoring.devsh.eu`.
- Certificates:
  - `k3s kubectl get certificate -A` should show `Ready=True` for all certs.

If a service needs broader permissions (privileged/host access), document the exception in this file and narrow it to the minimal scope (specific DS/SA, dedicated namespace).

> Note: k3s defaults to service CIDR 10.43.0.0/16, so the kube-apiserver service VIP is 10.43.0.1. The Grafana sidecar egress allowlist uses that IP to pull ConfigMaps via the Kubernetes API.
