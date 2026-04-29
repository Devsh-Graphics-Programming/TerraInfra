#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/etc/proxmox-runner-git-cache}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
ENV_FILE="${ENV_FILE:-${INSTALL_DIR}/proxmox-runner-git-cache.env}"
API_SERVICE_NAME="${API_SERVICE_NAME:-proxmox-runner-git-cache.service}"
GIT_SERVICE_NAME="${GIT_SERVICE_NAME:-proxmox-runner-git-daemon.service}"
API_SERVICE_FILE="${SYSTEMD_UNIT_DIR}/${API_SERVICE_NAME}"
GIT_SERVICE_FILE="${SYSTEMD_UNIT_DIR}/${GIT_SERVICE_NAME}"

CACHE_ROOT="${CACHE_ROOT:-/var/lib/proxmox-runner-git-cache}"
BLOB_CACHE_ROOT="${BLOB_CACHE_ROOT:-${CACHE_ROOT}/blobs}"
BLOB_CACHE_ALLOWED_PREFIXES="${BLOB_CACHE_ALLOWED_PREFIXES:-runner-cache/}"
BLOB_CACHE_MAX_BYTES="${BLOB_CACHE_MAX_BYTES:-536870912}"
BLOB_FETCH_ALLOWED_URL_PREFIXES="${BLOB_FETCH_ALLOWED_URL_PREFIXES:-}"
BLOB_FETCH_BASIC_AUTH_FILE="${BLOB_FETCH_BASIC_AUTH_FILE:-${INSTALL_DIR}/blob-fetch-basic-auth.json}"
BLOB_FETCH_BASIC_AUTH_SOURCE="${BLOB_FETCH_BASIC_AUTH_SOURCE:-}"
BLOB_FETCH_TIMEOUT_SECONDS="${BLOB_FETCH_TIMEOUT_SECONDS:-300}"
CONFIG_FILE="${CONFIG_FILE:-${INSTALL_DIR}/repos.json}"
CONFIG_SOURCE="${CONFIG_SOURCE:-}"
SCRIPT_SOURCE="${SCRIPT_SOURCE:-$(dirname "$0")/proxmox-runner-git-cache.py}"
LISTEN_HOST="${LISTEN_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-18082}"
GIT_PORT="${GIT_PORT:-9418}"
PUBLIC_GIT_BASE_URL="${PUBLIC_GIT_BASE_URL:-git://${LISTEN_HOST}:${GIT_PORT}}"
SCRATCH_ROOT="${SCRATCH_ROOT:-/var/lib/proxmox-runner-scratch}"
SCRATCH_SMB_SHARE="${SCRATCH_SMB_SHARE:-runner-scratch}"
SCRATCH_SMB_USER="${SCRATCH_SMB_USER:-proxmox-runner-scratch}"
SCRATCH_SMB_CREDENTIAL_FILE="${SCRATCH_SMB_CREDENTIAL_FILE:-${INSTALL_DIR}/scratch-smb-credential}"
SCRATCH_SMB_HOSTS_ALLOW="${SCRATCH_SMB_HOSTS_ALLOW:-10.254.254.0/24 127.}"
SCRATCH_UNC_ROOT="${SCRATCH_UNC_ROOT:-\\\\${LISTEN_HOST}\\${SCRATCH_SMB_SHARE}}"
ENABLE_SCRATCH_SMB="${ENABLE_SCRATCH_SMB:-true}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
GIT_BIN="${GIT_BIN:-/usr/bin/git}"
GIT_SSH_COMMAND_VALUE="${GIT_SSH_COMMAND_VALUE:-}"

if [[ ! "${API_SERVICE_NAME}" =~ ^[A-Za-z0-9_.@-]+\.service$ ]]; then
  echo "API_SERVICE_NAME must be a systemd service file name." >&2
  exit 1
fi

if [[ ! "${GIT_SERVICE_NAME}" =~ ^[A-Za-z0-9_.@-]+\.service$ ]]; then
  echo "GIT_SERVICE_NAME must be a systemd service file name." >&2
  exit 1
fi

if [[ ! "${LISTEN_HOST}" =~ ^[A-Za-z0-9_.:-]+$ ]]; then
  echo "LISTEN_HOST is invalid." >&2
  exit 1
fi

if [[ ! "${API_PORT}" =~ ^[0-9]+$ || ! "${GIT_PORT}" =~ ^[0-9]+$ ]]; then
  echo "API_PORT and GIT_PORT must be numeric." >&2
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable is missing: ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ ! -x "${GIT_BIN}" ]]; then
  echo "Git executable is missing: ${GIT_BIN}" >&2
  exit 1
fi

if [[ ! -f "${SCRIPT_SOURCE}" ]]; then
  echo "Git cache API script is missing: ${SCRIPT_SOURCE}" >&2
  exit 1
fi

install -d -m 0755 "${INSTALL_DIR}"
install -d -m 0755 "${CACHE_ROOT}"
install -d -m 0755 "${BLOB_CACHE_ROOT}"
install -m 0755 "${SCRIPT_SOURCE}" "${INSTALL_DIR}/proxmox-runner-git-cache.py"

if [[ "${ENABLE_SCRATCH_SMB}" == "true" ]]; then
  if ! id -u "${SCRATCH_SMB_USER}" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SCRATCH_SMB_USER}"
  fi
  install -d -m 2750 -o root -g "${SCRATCH_SMB_USER}" "${SCRATCH_ROOT}"
  if ! command -v smbd >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y samba
  fi
  if [[ ! -f "${SCRATCH_SMB_CREDENTIAL_FILE}" ]]; then
    old_umask="$(umask)"
    umask 077
    openssl rand -hex 32 >"${SCRATCH_SMB_CREDENTIAL_FILE}"
    umask "${old_umask}"
  fi
  chmod 0600 "${SCRATCH_SMB_CREDENTIAL_FILE}"
  scratch_smb_credential="$(cat "${SCRATCH_SMB_CREDENTIAL_FILE}")"
  printf '%s\n%s\n' "${scratch_smb_credential}" "${scratch_smb_credential}" | smbpasswd -s -a "${SCRATCH_SMB_USER}" >/dev/null
  smbpasswd -e "${SCRATCH_SMB_USER}" >/dev/null
  smb_filtered="$(mktemp)"
  smb_tmp="$(mktemp)"
  awk '
    /^# BEGIN proxmox-runner-scratch-global$/ { skip = 1; next }
    /^# END proxmox-runner-scratch-global$/ { skip = 0; next }
    /^# BEGIN proxmox-runner-scratch$/ { skip = 1; next }
    /^# END proxmox-runner-scratch$/ { skip = 0; next }
    skip != 1 { print }
  ' /etc/samba/smb.conf >"${smb_filtered}"
  cp "${smb_filtered}" "${smb_tmp}"
  cat >>"${smb_tmp}" <<EOF
# BEGIN proxmox-runner-scratch
[${SCRATCH_SMB_SHARE}]
  path = ${SCRATCH_ROOT}
  browseable = no
  read only = no
  guest ok = no
  valid users = ${SCRATCH_SMB_USER}
  force user = ${SCRATCH_SMB_USER}
  force group = ${SCRATCH_SMB_USER}
  create mask = 0660
  directory mask = 0770
  hosts allow = ${SCRATCH_SMB_HOSTS_ALLOW}
  hosts deny = 0.0.0.0/0 ::/0
# END proxmox-runner-scratch
EOF
  install -m 0644 "${smb_tmp}" /etc/samba/smb.conf
  rm -f "${smb_filtered}" "${smb_tmp}"
else
  install -d -m 0700 "${SCRATCH_ROOT}"
fi

if [[ -n "${CONFIG_SOURCE}" ]]; then
  install -m 0644 "${CONFIG_SOURCE}" "${CONFIG_FILE}"
elif [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "CONFIG_SOURCE is required for first install because ${CONFIG_FILE} does not exist." >&2
  exit 1
fi

if [[ -n "${BLOB_FETCH_BASIC_AUTH_SOURCE}" ]]; then
  install -m 0600 "${BLOB_FETCH_BASIC_AUTH_SOURCE}" "${BLOB_FETCH_BASIC_AUTH_FILE}"
fi

cat >"${ENV_FILE}" <<EOF
GIT_CACHE_CONFIG_PATH=${CONFIG_FILE}
GIT_CACHE_ROOT=${CACHE_ROOT}
BLOB_CACHE_ROOT=${BLOB_CACHE_ROOT}
BLOB_CACHE_ALLOWED_PREFIXES=${BLOB_CACHE_ALLOWED_PREFIXES}
BLOB_CACHE_MAX_BYTES=${BLOB_CACHE_MAX_BYTES}
BLOB_FETCH_ALLOWED_URL_PREFIXES=${BLOB_FETCH_ALLOWED_URL_PREFIXES}
BLOB_FETCH_TIMEOUT_SECONDS=${BLOB_FETCH_TIMEOUT_SECONDS}
SCRATCH_ROOT=${SCRATCH_ROOT}
SCRATCH_UNC_ROOT='${SCRATCH_UNC_ROOT}'
SCRATCH_SMB_USERNAME=${SCRATCH_SMB_USER}
SCRATCH_SMB_CREDENTIAL_FILE=${SCRATCH_SMB_CREDENTIAL_FILE}
GIT_CACHE_API_LISTEN_HOST=${LISTEN_HOST}
GIT_CACHE_API_PORT=${API_PORT}
GIT_CACHE_PUBLIC_GIT_BASE_URL=${PUBLIC_GIT_BASE_URL}
EOF
if [[ -f "${BLOB_FETCH_BASIC_AUTH_FILE}" ]]; then
  printf 'BLOB_FETCH_BASIC_AUTH_FILE=%s\n' "${BLOB_FETCH_BASIC_AUTH_FILE}" >>"${ENV_FILE}"
fi
if [[ -n "${GIT_SSH_COMMAND_VALUE}" ]]; then
  printf 'GIT_SSH_COMMAND=%s\n' "${GIT_SSH_COMMAND_VALUE}" >>"${ENV_FILE}"
fi
chmod 0600 "${ENV_FILE}"

cat >"${API_SERVICE_FILE}" <<EOF
[Unit]
Description=Proxmox runner Git object cache API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=${ENV_FILE}
ExecStart=${PYTHON_BIN} ${INSTALL_DIR}/proxmox-runner-git-cache.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=${CACHE_ROOT} ${BLOB_CACHE_ROOT} ${SCRATCH_ROOT}

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "${API_SERVICE_FILE}"

cat >"${GIT_SERVICE_FILE}" <<EOF
[Unit]
Description=Proxmox runner Git daemon
After=network-online.target ${API_SERVICE_NAME}
Wants=network-online.target

[Service]
Type=simple
ExecStart=${GIT_BIN} daemon --reuseaddr --verbose --export-all --base-path=${CACHE_ROOT} --listen=${LISTEN_HOST} --port=${GIT_PORT} ${CACHE_ROOT}
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=${CACHE_ROOT}

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "${GIT_SERVICE_FILE}"

systemctl daemon-reload
systemctl enable "${API_SERVICE_NAME}" "${GIT_SERVICE_NAME}"
systemctl restart "${API_SERVICE_NAME}" "${GIT_SERVICE_NAME}"
systemctl is-active --quiet "${API_SERVICE_NAME}"
systemctl is-active --quiet "${GIT_SERVICE_NAME}"
if [[ "${ENABLE_SCRATCH_SMB}" == "true" ]]; then
  systemctl enable smbd
  systemctl restart smbd
  systemctl is-active --quiet smbd
fi
