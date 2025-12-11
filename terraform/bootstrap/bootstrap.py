#!/usr/bin/env python3
import base64
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import sys
import time
from datetime import datetime
from typing import Optional, Sequence


SENSITIVE: list[str] = []
start_ts = time.time()
K3S_ENCRYPTION_CONFIG = "/var/lib/rancher/k3s/server/cred/encryption-config.json"
K3S_ENCRYPTION_CONFIG_BACKUP = "/mnt/data/backup/k3s/encryption-config.json"
BOOTSTRAP_VOLUME_MARKER = "/mnt/data/.bootstrap-initialized"


def encryption_config_has_keys(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    resources = data.get("resources", [])
    for resource in resources:
        providers = resource.get("providers", [])
        for provider in providers:
            keys = provider.get("aescbc", {}).get("keys", [])
            if isinstance(keys, list) and len(keys) > 0:
                return True
    return False


def add_sensitive(value: Optional[str]) -> None:
    if value:
        SENSITIVE.append(value)


def sanitize(text: str) -> str:
    masked = text
    for secret in SENSITIVE:
        if secret:
            masked = masked.replace(secret, "*****")
    return masked


def log(msg: str, level: str = "INFO") -> None:
    colors = {
        "INFO": "\033[36m",   # cyan
        "WARN": "\033[33m",   # yellow
        "ERROR": "\033[31m",  # red
        "CMD": "\033[35m",    # magenta
        "OK": "\033[32m",     # green
    }
    reset = "\033[0m"
    ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    prefix = f"[{ts}][{level}]"
    safe_msg = sanitize(msg)
    if level in colors:
        print(f"{colors[level]}{prefix}{reset}: {safe_msg}")
    else:
        print(f"{prefix}: {safe_msg}")
    sys.stdout.flush()


def log_status(subject: str, status: str, detail: str = "") -> None:
    level = "OK" if status.upper() == "OK" else "ERROR"
    msg = "done" if status.upper() == "OK" else "failure!"
    if detail:
        msg = f"{msg} {detail}"
    log(msg, level=level)


def run(
    cmd: str,
    check: bool = True,
    input_str: Optional[str] = None,
    quiet: bool = False,
    log_ok: bool = True,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """Run shell command with text mode and echo output for live logging."""
    log(f"$ {sanitize(cmd)}", level="CMD")
    result = subprocess.run(
        cmd,
        shell=True,
        text=True,
        input=input_str,
        capture_output=True,
        env=env,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {cmd}\nstdout: {result.stdout}\nstderr: {result.stderr}")
    if result.stdout and not quiet:
        out = sanitize(result.stdout)
        print(out, end="")
        if not out.endswith("\n"):
            print()
    if result.stderr and not quiet:
        err = sanitize(result.stderr)
        print(err, file=sys.stderr, end="")
        if not err.endswith("\n"):
            print(file=sys.stderr)
    if check and result.returncode == 0 and not quiet and log_ok:
        log_status("", "OK")
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
            log_status(f"deploy {ns}/{name}", "OK")
            return
        time.sleep(5)
    raise RuntimeError(f"Deployment {name} in {ns} not ready")


def wait_for_crd(crd: str):
    for _ in range(60):
        if subprocess.run(f"kubectl get crd {crd}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            log_status(f"crd {crd}", "OK")
            return
        time.sleep(5)
    raise RuntimeError(f"CRD {crd} not ready")


def _directory_is_clean(path: str, allowed: Optional[Sequence[str]] = None) -> bool:
    """Return True if *path* contains only entries from *allowed* (or is missing)."""
    try:
        entries = os.listdir(path)
    except FileNotFoundError:
        return True
    allowed = set(allowed or ())
    for entry in entries:
        if entry not in allowed:
            return False
    return True


def assert_clean_for_fresh(allow_fresh_env: bool, wants_fresh: bool) -> None:
    """Ensure a clean data volume when we need to treat the run as a fresh bootstrap."""
    if not wants_fresh:
        return
    if os.path.exists(BOOTSTRAP_VOLUME_MARKER):
        raise RuntimeError(
            "ALLOW_FRESH_BOOTSTRAP is enabled but /mnt/data already contains bootstrap artifacts. "
            "Wipe the volume (or remove the marker) before retrying a fresh bootstrap."
        )

    reason = "ALLOW_FRESH_BOOTSTRAP is enabled" if allow_fresh_env else "Fresh bootstrap assumed (no backups detected)"
    allowed_root = {"lost+found", "mariadb", "kimai-var"}
    if not _directory_is_clean("/mnt/data", allowed_root):
        raise RuntimeError(
            f"{reason}, but /mnt/data already holds data; remove or mount an empty disk before retrying."
        )
    if not _directory_is_clean("/mnt/data/mariadb", {"lost+found"}):
        raise RuntimeError(
            f"{reason}, but /mnt/data/mariadb contains leftover files; wipe the volume before rerunning."
        )
    if not _directory_is_clean("/mnt/data/kimai-var", {"lost+found"}):
        raise RuntimeError(
            f"{reason}, but /mnt/data/kimai-var contains leftover files; wipe the volume before rerunning."
        )


def restore_k3s_encryption_config(backup_path: str) -> bool:
    """Restore the k3s secrets encryption config from the data volume if available."""
    if encryption_config_has_keys(K3S_ENCRYPTION_CONFIG):
        return False
    if not encryption_config_has_keys(backup_path):
        return False
    os.makedirs(os.path.dirname(K3S_ENCRYPTION_CONFIG), exist_ok=True)
    log(f"[k3s] Restoring encryption config from {backup_path}", level="INFO")
    shutil.copy2(backup_path, K3S_ENCRYPTION_CONFIG)
    run(f"chmod 600 {K3S_ENCRYPTION_CONFIG}")
    return True


def backup_k3s_encryption_config(backup_path: str) -> None:
    """Persist the active k3s encryption config onto the data volume."""
    if not encryption_config_has_keys(K3S_ENCRYPTION_CONFIG):
        log("[k3s] Encryption config missing or empty; skipping backup", level="WARN")
        return
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    shutil.copy2(K3S_ENCRYPTION_CONFIG, backup_path)
    log(f"[k3s] Stored encryption config backup to {backup_path}", level="INFO")


def mark_volume_initialized(marker_path: str = BOOTSTRAP_VOLUME_MARKER) -> None:
    """Write a marker onto the data volume so we can detect existing state."""
    try:
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as fh:
            fh.write(datetime.utcnow().isoformat() + "\n")
    except Exception as exc:  # noqa: BLE001
        log(f"Failed to write volume marker: {exc}", level="WARN")


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


def load_secret(ns: str, name: str) -> dict:
    """
    Return decoded secret data if it exists, otherwise {}.
    """
    cp = subprocess.run(
        f"kubectl -n {ns} get secret {name} -o json",
        shell=True,
        text=True,
        capture_output=True,
    )
    if cp.returncode != 0 or not cp.stdout:
        return {}
    try:
        data = json.loads(cp.stdout).get("data") or {}
    except json.JSONDecodeError:
        return {}
    decoded = {}
    for key, value in data.items():
        try:
            decoded[key] = base64.b64decode(value).decode()
        except Exception:
            continue
    return decoded


def secret_exists(ns: str, name: str) -> bool:
    return (
        subprocess.run(
            f"kubectl -n {ns} get secret {name}",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


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


def run_with_retries(cmd: str, attempts: int = 10, delay: int = 5) -> subprocess.CompletedProcess:
    """Retry a command to tolerate transient readiness issues."""
    last = None
    for i in range(attempts):
        log(f"Attempt {i + 1}/{attempts}: {sanitize(cmd)}", level="INFO")
        quiet = i < attempts - 1
        last = run(cmd, check=False, quiet=quiet, log_ok=False)
        if last.returncode == 0:
            log_status("", "OK")
            return last
        if i < attempts - 1:
            log(f"Attempt {i + 1}/{attempts} failed (rc={last.returncode}); retrying in {delay}s", level="WARN")
            time.sleep(delay)
    log(f"All {attempts} attempts failed for: {cmd}", level="ERROR")
    if last and last.stdout:
        print(last.stdout, end="")
    if last and last.stderr:
        print(last.stderr, file=sys.stderr, end="")
    log_status("", "ERROR")
    return last


def ensure_secrets_encryption():
    """
    Make sure K3s secrets encryption is healthy; fail fast on any error.
    """
    status = run_with_retries("k3s secrets-encrypt status", attempts=15, delay=5)
    if status.returncode != 0:
        raise RuntimeError("K3s secrets encryption status failed; check k3s server logs.")
    if "disabled" in status.stdout.lower():
        run("k3s secrets-encrypt enable", check=True)
        time.sleep(5)


def ensure_data_mount(env_name: str, luks_key_url: str, luks_key_access: str, luks_key_secret: str) -> None:
    """Ensure /mnt/data is mounted via LUKS (if present)."""
    if os.path.ismount("/mnt/data"):
        log("[mnt] /mnt/data already mounted; skipping LUKS setup", level="INFO")
        return

    root_dev_cp = subprocess.run("findmnt -n -o SOURCE /", shell=True, text=True, capture_output=True)
    root_dev = (root_dev_cp.stdout or "").strip()
    root_pkname_cp = subprocess.run(f"lsblk -no PKNAME {root_dev}", shell=True, text=True, capture_output=True)
    root_disk = (root_pkname_cp.stdout or "").strip()
    if not root_disk:
        root_disk = os.path.basename(root_dev).rstrip("0123456789")

    data_dev = ""
    lsblk = subprocess.run("lsblk -ndo NAME,TYPE", shell=True, text=True, capture_output=True)
    if lsblk.stdout:
        for line in lsblk.stdout.splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            name, typ = parts
            if typ == "disk" and name != root_disk:
                data_dev = f"/dev/{name}"
                break
    if not data_dev:
        log("[mnt] No data device found; skipping /mnt/data setup", level="WARN")
        return

    fstype_cp = subprocess.run(f"lsblk -no FSTYPE {data_dev}", shell=True, text=True, capture_output=True)
    fstype = (fstype_cp.stdout or "").strip()
    os.makedirs("/mnt/data", exist_ok=True)

    # Plain filesystem already present (non-LUKS): just mount it
    if fstype and fstype != "crypto_LUKS":
        log(f"[mnt] Existing non-LUKS filesystem on {data_dev}: {fstype}; mounting directly", level="INFO")
        uuid_cp = run(f"blkid -s UUID -o value {data_dev}", quiet=True)
        uuid_val = (uuid_cp.stdout or "").strip()
        if uuid_val and "UUID" not in uuid_val:
            fstab_line = f"UUID={uuid_val} /mnt/data {fstype} defaults,nofail 0 2\n"
            with open("/etc/fstab", "r+", encoding="utf-8") as f:
                content = f.read()
                if fstab_line not in content:
                    f.write(fstab_line)
        run("mount -a")
        os.makedirs("/mnt/data/mariadb", exist_ok=True)
        os.makedirs("/mnt/data/kimai-var", exist_ok=True)
        return

    # LUKS path (new or existing crypto_LUKS)
    key_path = None
    try:
        shm_dir = "/dev/shm"
        os.makedirs(shm_dir, exist_ok=True)
        key_path = tempfile.NamedTemporaryFile(delete=False, dir=shm_dir).name
        if luks_key_url:
            import urllib.request

            log("[mnt] Fetching LUKS key from URL", level="INFO")
            with urllib.request.urlopen(luks_key_url, timeout=30) as resp, open(key_path, "wb") as fh:
                fh.write(resp.read())
        elif luks_key_access and luks_key_secret:
            log("[mnt] Fetching LUKS key from bucket with read-only creds", level="INFO")
            env = dict(os.environ)
            env.update(
                {
                    "AWS_ACCESS_KEY_ID": luks_key_access,
                    "AWS_SECRET_ACCESS_KEY": luks_key_secret,
                    "AWS_DEFAULT_REGION": "fr-par",
                }
            )
            run(
                f"aws --endpoint-url=https://s3.fr-par.scw.cloud s3 cp s3://terra-luks-keys/luks.key {key_path}",
                env=env,
            )
        else:
            raise RuntimeError("No LUKS key source provided (url or access/secret)")

        if not fstype:
            run(f"cryptsetup luksFormat --type luks2 --batch-mode --key-file {key_path} {data_dev}")
        else:
            log(f"[mnt] LUKS detected on {data_dev}, skipping format", level="INFO")

        run(f"cryptsetup luksOpen {data_dev} data_crypt --key-file {key_path}")

        mapper_fstype_cp = subprocess.run(
            "lsblk -no FSTYPE /dev/mapper/data_crypt", shell=True, text=True, capture_output=True
        )
        mapper_fstype = (mapper_fstype_cp.stdout or "").strip()
        if not mapper_fstype:
            run("mkfs.ext4 -F /dev/mapper/data_crypt")

        uuid_cp = run("blkid -s UUID -o value /dev/mapper/data_crypt", quiet=True)
        uuid_val = (uuid_cp.stdout or "").strip()
        if uuid_val and "UUID" not in uuid_val:
            fstab_line = f"UUID={uuid_val} /mnt/data ext4 defaults,nofail 0 2\n"
            with open("/etc/fstab", "r+", encoding="utf-8") as f:
                content = f.read()
                if fstab_line not in content:
                    f.write(fstab_line)
        run("mount -a")
        os.makedirs("/mnt/data/mariadb", exist_ok=True)
        os.makedirs("/mnt/data/kimai-var", exist_ok=True)
    finally:
        if key_path and os.path.exists(key_path):
            run(f"shred -u {key_path}", check=False, quiet=True, log_ok=False)


def main():
    global start_ts
    start_ts = time.time()
    required_env = [
        "ACME_EMAIL",
        "CONFIG_REPO_URL",
        "CONFIG_REPO_BRANCH",
        "CONFIG_REPO_PATH",
        "GITHUB_PERSISTENT_PAT",
        "ENV_NAME",
  ]
    for name in required_env:
        if not os.environ.get(name):
            raise SystemExit(f"Missing env: {name}")

    acme_email = os.environ["ACME_EMAIL"]
    env_name = os.environ["ENV_NAME"]
    env_prefix = os.environ.get(
        "ENV_PREFIX", "" if env_name == "prod" else f"{env_name}."
    )
    config_repo_url = os.environ["CONFIG_REPO_URL"]
    config_repo_branch = os.environ["CONFIG_REPO_BRANCH"]
    config_repo_path = os.environ["CONFIG_REPO_PATH"]
    github_pat = os.environ["GITHUB_PERSISTENT_PAT"]
    luks_key_access = os.environ.get("LUKS_KEY_ACCESS_KEY", "")
    luks_key_secret = os.environ.get("LUKS_KEY_SECRET_KEY", "")
    luks_key_url = os.environ.get("LUKS_KEY_URL", "")
    github_bootstrap_pat = os.environ.get("GITHUB_BOOTSTRAP_PAT", "")
    add_sensitive(github_pat)
    add_sensitive(github_bootstrap_pat)

    print("== bootstrap start ==")
    os.environ["KUBECONFIG"] = "/etc/rancher/k3s/k3s.yaml"
    allow_fresh_env = os.environ.get("ALLOW_FRESH_BOOTSTRAP", "").lower() in ("1", "true", "yes")
    ensure_data_mount(env_name, luks_key_url, luks_key_access, luks_key_secret)

    restore_k3s_encryption_config(K3S_ENCRYPTION_CONFIG_BACKUP)
    wait_for_k8s()
    ensure_secrets_encryption()
    ensure_namespace("monitoring")
    ensure_namespace("apps-tools")
    ensure_namespace("website")
    print("k8s ready")

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
    add_sensitive(webhook_secret)

    run("kubectl -n flux-system delete secret github-webhook-token || true")
    run(
        "kubectl -n flux-system create secret generic github-webhook-token "
        f"--from-literal=token='{webhook_secret}'"
    )

    sops_age_key_raw = os.environ.get("SOPS_AGE_KEY", "")
    sops_age_key_b64 = os.environ.get("SOPS_AGE_KEY_B64", "")
    sops_age_key = ""
    if sops_age_key_b64:
        try:
            sops_age_key = base64.b64decode(sops_age_key_b64).decode()
        except Exception as exc:  # noqa: BLE001
            log(f"Failed to decode SOPS_AGE_KEY_B64: {exc}", level="WARN")
    elif sops_age_key_raw:
        sops_age_key = sops_age_key_raw

    if not sops_age_key:
        raise RuntimeError("SOPS_AGE_KEY (or SOPS_AGE_KEY_B64) must be set to decrypt cluster secrets")

    add_sensitive(sops_age_key)
    with tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="utf-8") as keyf:
        keyf.write(sops_age_key)
        key_path = keyf.name
    try:
        run("kubectl -n flux-system delete secret sops-age || true")
        run(
            "kubectl -n flux-system create secret generic sops-age "
            f"--from-file=age.agekey={key_path}"
        )
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass

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
    decryption_section = (
        "  decryption:\n"
        "    provider: sops\n"
        "    secretRef:\n"
        "      name: sops-age\n"
    )
    vars_path = f"{config_repo_path}/vars/{env_name}"
    apps_path = f"{config_repo_path}/apps"
    flux_kustomization_vars = f"""apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: vars
  namespace: flux-system
spec:
  interval: 2m
  prune: true
  path: ./{vars_path}
  sourceRef:
    kind: GitRepository
    name: terralinfra
    namespace: flux-system
{decryption_section}  timeout: 1m
"""
    flux_kustomization_apps = (
        "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
        "kind: Kustomization\n"
        "metadata:\n"
        "  name: apps\n"
        "  namespace: flux-system\n"
        "spec:\n"
        "  interval: 2m\n"
        "  prune: true\n"
        f"  path: ./{apps_path}\n"
        "  sourceRef:\n"
        "    kind: GitRepository\n"
        "    name: terralinfra\n"
        "    namespace: flux-system\n"
        "  dependsOn:\n"
        "    - name: vars\n"
        f"{decryption_section}"
        "  timeout: 2m\n"
        "  postBuild:\n"
        "    substitute:\n"
        f"      GITHUB_WEBHOOK_SECRET: {webhook_secret}\n"
        "    substituteFrom:\n"
        "      - kind: ConfigMap\n"
        "        name: cluster-vars\n"
    )

    apply_yaml(flux_source)
    apply_yaml(flux_kustomization_vars)
    apply_yaml(flux_kustomization_apps)
    run("kubectl -n flux-system wait --for=condition=ready kustomization/vars --timeout=120s")
    run("kubectl -n flux-system wait --for=condition=ready kustomization/apps --timeout=300s")
    base_domain_cfg = ""
    env_prefix_cfg = ""
    try:
        cfg_raw = (
            run(
                "kubectl -n flux-system get configmap cluster-vars -o jsonpath='{.data.BASE_DOMAIN}|{.data.ENV_PREFIX}'",
                quiet=True,
            )
            .stdout.strip()
            .strip("'\"")
        )
        parts = cfg_raw.split("|")
        if len(parts) == 2:
            base_domain_cfg = parts[0]
            env_prefix_cfg = parts[1]
    except Exception as exc:  # noqa: BLE001
        log(f"Failed to read cluster-vars ConfigMap: {exc}", level="WARN")
    flux_hook_domain = f"{env_prefix_cfg}flux-hook.{base_domain_cfg}".replace("..", ".")
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

        if not flux_hook_domain:
            log("Skipping GitHub webhook: flux_hook_domain missing", level="WARN")
            return
        hook_url = f"https://{flux_hook_domain}{webhook_path}"
        token_b64 = run(
            "kubectl -n flux-system get secret github-webhook-token -o jsonpath='{.data.token}'",
            check=True,
            quiet=True,
        ).stdout.strip().strip("'\"")
        hook_secret = base64.b64decode(token_b64).decode()
        add_sensitive(hook_secret)

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
            with urllib.request.urlopen(req, timeout=10) as resp:
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

    backup_k3s_encryption_config(K3S_ENCRYPTION_CONFIG_BACKUP)
    mark_volume_initialized()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        elapsed = time.time() - globals().get("start_ts", time.time())
        log_status("bootstrap", "ERROR", detail=f"after {elapsed:.1f}s: {exc}")
        sys.exit(1)
    else:
        elapsed = time.time() - start_ts
        print()
        log(f"bootstrap completed (took {elapsed:.1f}s)", level="OK")
