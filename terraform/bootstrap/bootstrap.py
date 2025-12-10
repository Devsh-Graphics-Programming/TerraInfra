#!/usr/bin/env python3
import base64
import json
import os
import secrets
import shlex
import shutil
import string
import subprocess
import tempfile
import textwrap
import sys
import time
from datetime import datetime
from typing import Optional, Sequence


SENSITIVE: list[str] = []
start_ts = time.time()
K3S_ENCRYPTION_KEY = "/var/lib/rancher/k3s/server/aescbc.keys"
K3S_ENCRYPTION_KEY_BACKUP = "/mnt/data/backup/k3s/aescbc.keys"
BOOTSTRAP_VOLUME_MARKER = "/mnt/data/.bootstrap-initialized"


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


def random_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def backup_files_exist(paths: Sequence[str]) -> bool:
    """Return True if any of the predefined backup files already exist."""
    for path in paths:
        if os.path.exists(path):
            return True
    return False


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


def restore_k3s_encryption_keys(backup_path: str) -> bool:
    """Restore the k3s secrets encryption key file from the data volume if available."""
    if os.path.exists(K3S_ENCRYPTION_KEY):
        return False
    if not os.path.exists(backup_path):
        return False
    os.makedirs(os.path.dirname(K3S_ENCRYPTION_KEY), exist_ok=True)
    log(f"[k3s] Restoring encryption key from {backup_path}", level="INFO")
    shutil.copy2(backup_path, K3S_ENCRYPTION_KEY)
    run(f"chmod 600 {K3S_ENCRYPTION_KEY}")
    run("systemctl restart k3s")
    return True


def backup_k3s_encryption_keys(backup_path: str) -> None:
    """Persist the active k3s encryption keys onto the data volume."""
    if not os.path.exists(K3S_ENCRYPTION_KEY):
        log("[k3s] Encryption key file missing; skipping backup", level="WARN")
        return
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    shutil.copy2(K3S_ENCRYPTION_KEY, backup_path)
    log(f"[k3s] Stored encryption key backup to {backup_path}", level="INFO")


def mark_volume_initialized(marker_path: str = BOOTSTRAP_VOLUME_MARKER) -> None:
    """Write a marker onto the data volume so we can detect existing state."""
    try:
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as fh:
            fh.write(datetime.utcnow().isoformat() + "\n")
    except Exception as exc:  # noqa: BLE001
        log(f"Failed to write volume marker: {exc}", level="WARN")


def ensure_mariadb_credentials(root_password: str, user_password: str, namespace: str = "apps-tools", deployment: str = "kimai-mariadb") -> None:
    """Make sure MariaDB root + kimai user/password match the secrets we expect."""
    sql = textwrap.dedent(
        f"""\
        CREATE DATABASE IF NOT EXISTS kimai;
        CREATE USER IF NOT EXISTS 'kimai'@'%' IDENTIFIED BY '{user_password}';
        GRANT ALL PRIVILEGES ON kimai.* TO 'kimai'@'%';
        ALTER USER 'kimai'@'%' IDENTIFIED BY '{user_password}';
        ALTER USER 'root'@'localhost' IDENTIFIED BY '{root_password}';
        FLUSH PRIVILEGES;
        """
    )
    script = textwrap.dedent(
        f"""\
        CLIENT=$(command -v mariadb || command -v mysql)
        if [ -z "$CLIENT" ]; then
          echo "mysql/mariadb client not installed" >&2
          exit 1
        fi
        cat <<'SQL' | "$CLIENT" -uroot -p'{root_password}'
        {sql}SQL
        """
    )
    cmd = (
        f"kubectl -n {namespace} exec deploy/{deployment} -- "
        f"bash -c {shlex.quote(script)}"
    )
    result = run_with_retries(cmd, attempts=10, delay=10)
    if result.returncode != 0:
        raise RuntimeError("Failed to sync MariaDB credentials; check mariadb logs.")


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


def restore_secret_from_backup(ns: str, name: str, backup_path: str) -> None:
    ensure_namespace(ns)
    if secret_exists(ns, name):
        log(f"Secret {ns}/{name} exists; skipping restore", level="INFO")
        return
    if not os.path.exists(backup_path):
        log(f"Backup for {ns}/{name} not found at {backup_path}; skipping restore", level="WARN")
        return
    log(f"Restoring secret {ns}/{name} from {backup_path}", level="INFO")
    run(f"kubectl apply -f {backup_path}")


def backup_secret(ns: str, name: str, backup_path: str) -> None:
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    cp = subprocess.run(
        f"kubectl -n {ns} get secret {name} -o yaml",
        shell=True,
        text=True,
        capture_output=True,
    )
    if cp.returncode != 0 or not cp.stdout:
        log(f"Cannot backup {ns}/{name} (not found yet); skipping", level="WARN")
        return
    with open(backup_path, "w", encoding="utf-8") as f:
        f.write(cp.stdout)
    log(f"Backed up secret {ns}/{name} to {backup_path}", level="INFO")


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
    reenc = run_with_retries("k3s secrets-encrypt reencrypt --force", attempts=5, delay=10)
    if reenc.returncode != 0:
        raise RuntimeError("K3s secrets-encrypt reencrypt failed; check k3s server logs.")


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
        "KIMAI_DOMAIN",
        "MONITORING_DOMAIN",
        "FLUX_HOOK_DOMAIN",
        "CONFIG_REPO_URL",
        "CONFIG_REPO_BRANCH",
    "CONFIG_REPO_PATH",
    "GITHUB_PERSISTENT_PAT",
    "ENV_NAME",
    "WEBSITE_DOMAIN",
    "BLOG_DOMAIN",
  ]
    for name in required_env:
        if not os.environ.get(name):
            raise SystemExit(f"Missing env: {name}")

    acme_email = os.environ["ACME_EMAIL"]
    kimai_domain = os.environ["KIMAI_DOMAIN"]
    monitoring_domain = os.environ["MONITORING_DOMAIN"]
    flux_hook_domain = os.environ["FLUX_HOOK_DOMAIN"]
    website_domain = os.environ["WEBSITE_DOMAIN"]
    blog_domain = os.environ["BLOG_DOMAIN"]
    config_repo_url = os.environ["CONFIG_REPO_URL"]
    config_repo_branch = os.environ["CONFIG_REPO_BRANCH"]
    config_repo_path = os.environ["CONFIG_REPO_PATH"]
    github_pat = os.environ["GITHUB_PERSISTENT_PAT"]
    env_name = os.environ["ENV_NAME"]
    luks_key_access = os.environ.get("LUKS_KEY_ACCESS_KEY", "")
    luks_key_secret = os.environ.get("LUKS_KEY_SECRET_KEY", "")
    luks_key_url = os.environ.get("LUKS_KEY_URL", "")
    github_bootstrap_pat = os.environ.get("GITHUB_BOOTSTRAP_PAT", "")
    add_sensitive(github_pat)
    add_sensitive(github_bootstrap_pat)

    print("== bootstrap start ==")
    os.environ["KUBECONFIG"] = "/etc/rancher/k3s/k3s.yaml"
    secrets_backup_base = "/mnt/data/backup/secrets"
    kimai_db_backup = f"{secrets_backup_base}/apps-tools/kimai-db-credentials.yaml"
    kimai_admin_backup = f"{secrets_backup_base}/apps-tools/kimai-admin-credentials.yaml"
    grafana_admin_backup = f"{secrets_backup_base}/monitoring/monitoring-grafana.yaml"
    allow_fresh_env = os.environ.get("ALLOW_FRESH_BOOTSTRAP", "").lower() in ("1", "true", "yes")
    ensure_data_mount(env_name, luks_key_url, luks_key_access, luks_key_secret)

    backup_paths = [
        kimai_db_backup,
        kimai_admin_backup,
        grafana_admin_backup,
    ]
    has_backups = backup_files_exist(backup_paths)
    if allow_fresh_env and has_backups:
        raise RuntimeError(
            "ALLOW_FRESH_BOOTSTRAP is enabled but backup secrets already exist on /mnt/data. "
            "Disable the flag or clear /mnt/data if you really intend to recreate everything."
        )
    allow_fresh = allow_fresh_env or not has_backups
    if not allow_fresh_env and not has_backups:
        log("[bootstrap] No secret backups detected on /mnt/data; assuming fresh bootstrap", level="INFO")
    if allow_fresh_env and not has_backups:
        log("[bootstrap] ALLOW_FRESH_BOOTSTRAP requested and volume is clean; generating new secrets", level="INFO")

    assert_clean_for_fresh(allow_fresh_env, allow_fresh)

    # Prepare attached data volume (non-root) for stateful data
    run(
        r"""bash -euxo pipefail
ROOT_DEV=$(findmnt -n -o SOURCE / | sed 's/[0-9]*$//')
if findmnt -n /mnt/data >/dev/null 2>&1; then
  echo "[INFO] /mnt/data already mounted (likely LUKS handled by cloud-init); skipping format/mount"
  exit 0
fi
DATA_DEV=$(lsblk -ndo NAME,TYPE | awk -v root="${ROOT_DEV##*/}" '$2=="disk" && $1!=root {print "/dev/"$1; exit}')
if [ -z "${DATA_DEV}" ]; then
  echo "[WARN] No data device found for mount, skipping /mnt/data setup"
  exit 0
fi
FSTYPE=$(lsblk -no FSTYPE "${DATA_DEV}" || true)
if [ -z "${FSTYPE}" ]; then
  mkfs.ext4 -F "${DATA_DEV}"
elif [ "${FSTYPE}" = "crypto_LUKS" ]; then
  echo "[INFO] Detected LUKS on ${DATA_DEV}, skipping format"
  exit 0
fi
mkdir -p /mnt/data
if ! grep -q "${DATA_DEV} /mnt/data" /etc/fstab; then
  echo "${DATA_DEV} /mnt/data ext4 defaults,nofail 0 2" >> /etc/fstab
fi
mount -a
mkdir -p /mnt/data/mariadb /mnt/data/kimai-var
""",
        quiet=True,
    )

    restore_k3s_encryption_keys(K3S_ENCRYPTION_KEY_BACKUP)
    wait_for_k8s()
    ensure_secrets_encryption()
    ensure_namespace("monitoring")
    ensure_namespace("apps-tools")
    print("k8s ready")

    grafana_admin_user = "admin"
    existing_grafana_secret = load_secret("monitoring", "monitoring-grafana")
    if not existing_grafana_secret and os.path.exists(grafana_admin_backup):
        restore_secret_from_backup("monitoring", "monitoring-grafana", grafana_admin_backup)
        existing_grafana_secret = load_secret("monitoring", "monitoring-grafana")
    if not existing_grafana_secret and not allow_fresh:
        raise RuntimeError("monitoring/monitoring-grafana secret missing and fresh init disabled (set ALLOW_FRESH_BOOTSTRAP=1 to allow).")
    grafana_admin_password = existing_grafana_secret.get("admin-password") or random_password()

    kimai_admin_user_default = f"admin@{kimai_domain}"
    existing_kimai_admin_secret = load_secret("apps-tools", "kimai-admin-credentials")
    if not existing_kimai_admin_secret and os.path.exists(kimai_admin_backup):
        restore_secret_from_backup("apps-tools", "kimai-admin-credentials", kimai_admin_backup)
        existing_kimai_admin_secret = load_secret("apps-tools", "kimai-admin-credentials")
    if not existing_kimai_admin_secret and not allow_fresh:
        raise RuntimeError("apps-tools/kimai-admin-credentials missing and fresh init disabled (set ALLOW_FRESH_BOOTSTRAP=1 to allow).")
    kimai_admin_user = existing_kimai_admin_secret.get("username", kimai_admin_user_default)
    kimai_admin_password = existing_kimai_admin_secret.get("password") or random_password()

    existing_kimai_db_secret = load_secret("apps-tools", "kimai-db-credentials")
    if not existing_kimai_db_secret and os.path.exists(kimai_db_backup):
        restore_secret_from_backup("apps-tools", "kimai-db-credentials", kimai_db_backup)
        existing_kimai_db_secret = load_secret("apps-tools", "kimai-db-credentials")
    if not existing_kimai_db_secret and not allow_fresh:
        raise RuntimeError("apps-tools/kimai-db-credentials missing and fresh init disabled (set ALLOW_FRESH_BOOTSTRAP=1 to allow).")
    fresh_kimai_creds = not bool(existing_kimai_db_secret)
    kimai_db_root_password = existing_kimai_db_secret.get("mysql-root-password") or random_password()
    kimai_db_user_password = existing_kimai_db_secret.get("mysql-user-password") or random_password()

    add_sensitive(grafana_admin_password)
    add_sensitive(kimai_admin_password)
    add_sensitive(kimai_db_root_password)
    add_sensitive(kimai_db_user_password)
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
    print("Generated credentials stored in cluster secrets. Retrieve with kubectl:")
    print("  [monitoring] grafana admin user: admin")
    print("    kubectl -n monitoring get secret monitoring-grafana -o jsonpath='{.data.admin-password}' | base64 -d && echo")
    print("  [apps-tools] kimai MariaDB root password:")
    print("    kubectl -n apps-tools get secret kimai-db-credentials -o jsonpath='{.data.mysql-root-password}' | base64 -d && echo")
    print("  [apps-tools] kimai MariaDB user password:")
    print("    kubectl -n apps-tools get secret kimai-db-credentials -o jsonpath='{.data.mysql-user-password}' | base64 -d && echo")
    print(f"  [apps-tools] kimai admin user: {kimai_admin_user}")
    print("    kubectl -n apps-tools get secret kimai-admin-credentials -o jsonpath='{.data.password}' | base64 -d && echo")

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
    add_sensitive(webhook_secret)

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
      WEBSITE_DOMAIN: {website_domain}
      BLOG_DOMAIN: {blog_domain}
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
    run("kubectl -n flux-system wait --for=condition=ready kustomization/apps --timeout=300s")
    wait_for_deploy("apps-tools", "kimai-mariadb")
    ensure_mariadb_credentials(kimai_db_root_password, kimai_db_user_password)
    wait_for_deploy("apps-tools", "kimai")
    create_admin_cmd = (
        "kubectl -n apps-tools exec deploy/kimai -- "
        f"bash -lc \"cd /opt/kimai && php bin/console kimai:user:create "
        f"{kimai_admin_user.split('@')[0]} {kimai_admin_user} ROLE_SUPER_ADMIN '{kimai_admin_password}'\""
    )
    should_create_admin = fresh_kimai_creds or not existing_kimai_admin_secret
    if should_create_admin:
        create_admin = run_with_retries(create_admin_cmd, attempts=10, delay=10)
        if create_admin.returncode != 0:
            combined = f"{create_admin.stdout} {create_admin.stderr}".lower()
            if "already exists" in combined:
                log("Kimai admin already exists; keeping existing credentials/roles", level="WARN")
            else:
                raise RuntimeError("Kimai admin user creation failed; see logs above for details.")
    else:
        log("Skipping Kimai admin creation (existing creds restored)", level="INFO")

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

    backup_secret("monitoring", "monitoring-grafana", grafana_admin_backup)
    backup_secret("apps-tools", "kimai-db-credentials", kimai_db_backup)
    backup_secret("apps-tools", "kimai-admin-credentials", kimai_admin_backup)
    backup_k3s_encryption_keys(K3S_ENCRYPTION_KEY_BACKUP)
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
