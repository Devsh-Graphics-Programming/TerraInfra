#!/usr/bin/env python3
import base64
import json
import os
import secrets
import string
import subprocess
import sys
import time
from typing import Optional


def run(cmd: str, check: bool = True, input_str: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run shell command with text mode and echo output for live logging."""
    print(f"$ {cmd}")
    sys.stdout.flush()
    result = subprocess.run(
        cmd,
        shell=True,
        text=True,
        input=input_str,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {cmd}\nstdout: {result.stdout}\nstderr: {result.stderr}")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    sys.stdout.flush()
    return result


def wait_for_k8s():
    for _ in range(60):
        if subprocess.run("kubectl get nodes", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return
        time.sleep(5)
    raise RuntimeError("k8s not ready")


def wait_for_deploy(ns: str, name: str):
    for _ in range(60):
        cp = run(f"kubectl -n {ns} rollout status deploy/{name} --timeout=30s", check=False)
        if cp.returncode == 0:
            return
        time.sleep(5)
    raise RuntimeError(f"Deployment {name} in {ns} not ready")


def wait_for_crd(crd: str):
    for _ in range(60):
        if subprocess.run(f"kubectl get crd {crd}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return
        time.sleep(5)
    raise RuntimeError(f"CRD {crd} not ready")


def random_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ensure_namespace(ns: str):
    run(f"kubectl create namespace {ns} --dry-run=client -o yaml | kubectl apply -f -")


def upsert_secret(ns: str, name: str, data: dict):
    yaml_lines = [
        "apiVersion: v1",
        "kind: Secret",
        "type: Opaque",
        "metadata:",
        f"  name: {name}",
        f"  namespace: {ns}",
        "stringData:",
    ]
    for key, value in data.items():
        yaml_lines.append(f"  {key}: {value}")
    apply_yaml("\n".join(yaml_lines))


def ensure_helm():
    if subprocess.run("command -v helm", shell=True, stdout=subprocess.DEVNULL).returncode != 0:
        run("curl -fsSL https://get.helm.sh/helm-v3.15.3-linux-amd64.tar.gz -o /tmp/helm.tar.gz")
        run("tar -xzf /tmp/helm.tar.gz -C /tmp")
        run("mv /tmp/linux-amd64/helm /usr/local/bin/helm")
        run("chmod +x /usr/local/bin/helm")


def ensure_flux():
    if subprocess.run("command -v flux", shell=True, stdout=subprocess.DEVNULL).returncode != 0:
        run("curl -s https://fluxcd.io/install.sh | bash")
    run("flux install --components-extra=image-reflector-controller,image-automation-controller")


def apply_yaml(yaml_str: str):
    run("kubectl apply -f -", input_str=yaml_str)


def main():
    required_env = [
        "ACME_EMAIL",
        "KIMAI_DOMAIN",
        "MONITORING_DOMAIN",
        "FLUX_HOOK_DOMAIN",
        "CONFIG_REPO_URL",
        "CONFIG_REPO_BRANCH",
        "CONFIG_REPO_PATH",
        "GITHUB_PERSISTENT_PAT",
    ]
    for name in required_env:
        if not os.environ.get(name):
            raise SystemExit(f"Missing env: {name}")

    acme_email = os.environ["ACME_EMAIL"]
    kimai_domain = os.environ["KIMAI_DOMAIN"]
    monitoring_domain = os.environ["MONITORING_DOMAIN"]
    flux_hook_domain = os.environ["FLUX_HOOK_DOMAIN"]
    config_repo_url = os.environ["CONFIG_REPO_URL"]
    config_repo_branch = os.environ["CONFIG_REPO_BRANCH"]
    config_repo_path = os.environ["CONFIG_REPO_PATH"]
    github_pat = os.environ["GITHUB_PERSISTENT_PAT"]
    github_bootstrap_pat = os.environ.get("GITHUB_BOOTSTRAP_PAT", "")

    print("== bootstrap start ==")
    os.environ["KUBECONFIG"] = "/etc/rancher/k3s/k3s.yaml"

    grafana_admin_user = "admin"
    grafana_admin_password = random_password()
    kimai_admin_user = f"admin@{kimai_domain}"
    kimai_admin_password = random_password()
    kimai_db_root_password = random_password()
    kimai_db_user_password = random_password()

    wait_for_k8s()
    print("k8s ready")
    ensure_namespace("monitoring")
    ensure_namespace("apps-tools")
    upsert_secret(
        "apps-tools",
        "kimai-db-credentials",
        {
            "mysql-root-password": kimai_db_root_password,
            "mysql-user-password": kimai_db_user_password,
            "database-url": f"mysql://kimai:{kimai_db_user_password}@kimai-mariadb:3306/kimai",
        },
    )
    upsert_secret(
        "apps-tools",
        "kimai-admin-credentials",
        {
            "username": kimai_admin_user,
            "password": kimai_admin_password,
        },
    )
    print("Generated credentials (stored in cluster secrets):")
    print(f"  [monitoring] grafana admin: {grafana_admin_user} / {grafana_admin_password}")
    print(f"  [apps-tools] kimai MariaDB root password: {kimai_db_root_password}")
    print(f"  [apps-tools] kimai MariaDB user password: {kimai_db_user_password}")
    print(f"  [apps-tools] kimai admin: {kimai_admin_user} / {kimai_admin_password}")

    ensure_helm()

    run("helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx || true")
    run("helm repo add jetstack https://charts.jetstack.io || true")
    run("helm repo add prometheus-community https://prometheus-community.github.io/helm-charts || true")
    run("helm repo update")

    run(
        "helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx "
        "--namespace infra --create-namespace --set controller.publishService.enabled=true"
    )
    run(
        "helm upgrade --install cert-manager jetstack/cert-manager "
        "--namespace cert-manager --create-namespace --set installCRDs=true"
    )

    wait_for_deploy("infra", "ingress-nginx-controller")
    wait_for_deploy("cert-manager", "cert-manager")
    wait_for_deploy("cert-manager", "cert-manager-webhook")
    wait_for_crd("certificates.cert-manager.io")
    wait_for_crd("clusterissuers.cert-manager.io")

    cluster_issuer = f"""apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-http
spec:
  acme:
    email: {acme_email}
    server: https://acme-v02.api.letsencrypt.org/directory
    privateKeySecretRef:
      name: letsencrypt-http-private-key
    solvers:
    - http01:
        ingress:
          class: nginx
"""
    apply_yaml(cluster_issuer)

    run(
        "helm upgrade --install monitoring prometheus-community/kube-prometheus-stack "
        f"--namespace monitoring --create-namespace --set grafana.adminPassword='{grafana_admin_password}'"
    )
    wait_for_deploy("monitoring", "monitoring-grafana")
    wait_for_deploy("monitoring", "monitoring-kube-prometheus-operator")

    ensure_flux()

    run("kubectl -n flux-system delete secret git-credentials || true")
    run(
        "kubectl -n flux-system create secret generic git-credentials "
        f"--from-literal=username=git --from-literal=password='{github_pat}'"
    )

    webhook_secret = (
        subprocess.run(
            "head -c 32 /dev/urandom | base64 | tr -d '='",
            shell=True,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
    )

    run("kubectl -n flux-system delete secret github-webhook-token || true")
    run(
        "kubectl -n flux-system create secret generic github-webhook-token "
        f"--from-literal=token='{webhook_secret}'"
    )

    flux_source = f"""apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: terralinfra
  namespace: flux-system
spec:
  interval: 2m
  url: {config_repo_url}
  ref:
    branch: {config_repo_branch}
  secretRef:
    name: git-credentials
"""
    flux_kustomization = f"""apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: apps
  namespace: flux-system
spec:
  interval: 2m
  prune: true
  path: ./{config_repo_path}
  sourceRef:
    kind: GitRepository
    name: terralinfra
    namespace: flux-system
  timeout: 2m
  postBuild:
    substitute:
      KIMAI_DOMAIN: {kimai_domain}
      MONITORING_DOMAIN: {monitoring_domain}
      FLUX_HOOK_DOMAIN: {flux_hook_domain}
      GITHUB_WEBHOOK_SECRET: {webhook_secret}
"""
    flux_receiver = f"""apiVersion: notification.toolkit.fluxcd.io/v1
kind: Receiver
metadata:
  name: github-receiver
  namespace: flux-system
spec:
  type: github
  events:
    - ping
    - push
  secretRef:
    name: github-webhook-token
  resources:
    - apiVersion: source.toolkit.fluxcd.io/v1
      kind: GitRepository
      name: terralinfra
      namespace: flux-system
  suspend: false
---
apiVersion: v1
kind: Secret
metadata:
  name: github-webhook-token
  namespace: flux-system
type: Opaque
stringData:
  token: "{webhook_secret}"
---
apiVersion: v1
kind: Service
metadata:
  name: flux-receiver
  namespace: flux-system
spec:
  type: ClusterIP
  ports:
    - port: 80
      targetPort: 9292
      protocol: TCP
      name: http
  selector:
    app: notification-controller
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: flux-hook-cert
  namespace: flux-system
spec:
  secretName: flux-hook-tls
  dnsNames:
    - {flux_hook_domain}
  issuerRef:
    name: letsencrypt-http
    kind: ClusterIssuer
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: flux-receiver
  namespace: flux-system
  annotations:
    kubernetes.io/ingress.class: nginx
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - {flux_hook_domain}
      secretName: flux-hook-tls
  rules:
    - host: {flux_hook_domain}
      http:
        paths:
          - path: /hook
            pathType: Prefix
            backend:
              service:
                name: flux-receiver
                port:
                  number: 80
"""

    apply_yaml(flux_source)
    apply_yaml(flux_kustomization)
    apply_yaml(flux_receiver)
    run("kubectl -n flux-system wait --for=condition=ready kustomization/apps --timeout=300s", check=False)
    wait_for_deploy("apps-tools", "kimai-mariadb")
    wait_for_deploy("apps-tools", "kimai")
    create_admin = run(
        "kubectl -n apps-tools exec deploy/kimai -- "
        f"php bin/console kimai:create-user {kimai_admin_user} '{kimai_admin_password}' --admin",
        check=False,
    )
    if create_admin.returncode != 0:
        print("Kimai admin user may already exist; creation command failed.")

    # Webhook sync
    if github_bootstrap_pat:
        webhook_path = ""
        for _ in range(30):
            cp = run(
                "kubectl -n flux-system get receiver github-receiver -o jsonpath='{.status.webhookPath}'",
                check=False,
            )
            webhook_path = cp.stdout.strip().strip("'\"")
            if webhook_path:
                break
            time.sleep(5)
        if not webhook_path:
            return

        hook_url = f"https://{flux_hook_domain}{webhook_path}"
        token_b64 = run(
            "kubectl -n flux-system get secret github-webhook-token -o jsonpath='{.data.token}'",
            check=True,
        ).stdout.strip().strip("'\"")
        hook_secret = base64.b64decode(token_b64).decode()

        owner_repo = config_repo_url.rstrip("/").split("/")[-2:]
        if len(owner_repo) != 2:
            return
        api_base = f"https://api.github.com/repos/{owner_repo[0]}/{owner_repo[1].replace('.git','')}/hooks"
        headers = {
            "Authorization": f"Bearer {github_bootstrap_pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        import urllib.request

        def http(method: str, url: str, data: Optional[dict] = None):
            body = None
            if data is not None:
                body = json.dumps(data).encode()
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode(), resp.getcode()

        hooks_body, _ = http("GET", api_base)
        hooks = json.loads(hooks_body or "[]")
        for h in hooks:
            cfg = h.get("config") or {}
            url = cfg.get("url") or ""
            if flux_hook_domain in url and h.get("id"):
                http("DELETE", f"{api_base}/{h['id']}")

        payload = {
            "name": "web",
            "active": True,
            "events": ["push", "ping"],
            "config": {
                "url": hook_url,
                "content_type": "json",
                "insecure_ssl": "0",
                "secret": hook_secret,
            },
        }
        http("POST", api_base, payload)


if __name__ == "__main__":
    main()
