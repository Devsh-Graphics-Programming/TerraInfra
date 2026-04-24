#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/etc/proxmox-runner-tunnel}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
ENV_FILE="${ENV_FILE:-${INSTALL_DIR}/proxmox-runner-tunnel.env}"
SERVICE_NAME="${SERVICE_NAME:-proxmox-runner-tunnel.service}"
SERVICE_FILE="${SERVICE_FILE:-${SYSTEMD_UNIT_DIR}/${SERVICE_NAME}}"

TUNNEL_USER="${TUNNEL_USER:-tunnel}"
JENKINS_TUNNEL_HOST="${JENKINS_TUNNEL_HOST:-}"
JENKINS_TUNNEL_PORT="${JENKINS_TUNNEL_PORT:-30222}"
REMOTE_BIND_HOST="${REMOTE_BIND_HOST:-127.0.0.1}"
REMOTE_BIND_PORT="${REMOTE_BIND_PORT:-}"
PROXMOX_API_HOST="${PROXMOX_API_HOST:-127.0.0.1}"
PROXMOX_API_PORT="${PROXMOX_API_PORT:-8006}"
SSH_KEY_PATH="${SSH_KEY_PATH:-${INSTALL_DIR}/id_ed25519}"
KNOWN_HOSTS_PATH="${KNOWN_HOSTS_PATH:-${INSTALL_DIR}/known_hosts}"

if [[ -z "${JENKINS_TUNNEL_HOST}" ]]; then
  echo "JENKINS_TUNNEL_HOST is required." >&2
  exit 1
fi

if [[ -z "${REMOTE_BIND_PORT}" ]]; then
  echo "REMOTE_BIND_PORT is required." >&2
  exit 1
fi

if [[ ! "${SERVICE_NAME}" =~ ^[A-Za-z0-9_.@-]+\.service$ ]]; then
  echo "SERVICE_NAME must be a systemd service file name." >&2
  exit 1
fi

if [[ ! "${REMOTE_BIND_HOST}" =~ ^[A-Za-z0-9_.:-]+$ ]]; then
  echo "REMOTE_BIND_HOST is invalid." >&2
  exit 1
fi

if [[ ! "${REMOTE_BIND_PORT}" =~ ^[0-9]+$ ]]; then
  echo "REMOTE_BIND_PORT must be numeric." >&2
  exit 1
fi

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "SSH key file is missing: ${SSH_KEY_PATH}" >&2
  exit 1
fi

if [[ ! -f "${KNOWN_HOSTS_PATH}" ]]; then
  echo "known_hosts file is missing: ${KNOWN_HOSTS_PATH}" >&2
  exit 1
fi

install -d -m 0700 "${INSTALL_DIR}"
if [[ "${SSH_KEY_PATH}" != "${INSTALL_DIR}/id_ed25519" ]]; then
  install -m 0600 "${SSH_KEY_PATH}" "${INSTALL_DIR}/id_ed25519"
fi
if [[ "${KNOWN_HOSTS_PATH}" != "${INSTALL_DIR}/known_hosts" ]]; then
  install -m 0644 "${KNOWN_HOSTS_PATH}" "${INSTALL_DIR}/known_hosts"
fi

cat >"${ENV_FILE}" <<EOF
TUNNEL_USER=${TUNNEL_USER}
JENKINS_TUNNEL_HOST=${JENKINS_TUNNEL_HOST}
JENKINS_TUNNEL_PORT=${JENKINS_TUNNEL_PORT}
REMOTE_BIND_HOST=${REMOTE_BIND_HOST}
REMOTE_BIND_PORT=${REMOTE_BIND_PORT}
PROXMOX_API_HOST=${PROXMOX_API_HOST}
PROXMOX_API_PORT=${PROXMOX_API_PORT}
EOF
chmod 0600 "${ENV_FILE}"

cat >"${SERVICE_FILE}" <<EOF
[Unit]
Description=Proxmox runner reverse tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=${ENV_FILE}
ExecStart=/usr/bin/ssh -N \\
  -o ExitOnForwardFailure=yes \\
  -o ServerAliveInterval=30 \\
  -o ServerAliveCountMax=3 \\
  -o StrictHostKeyChecking=yes \\
  -o UserKnownHostsFile=${INSTALL_DIR}/known_hosts \\
  -o IdentitiesOnly=yes \\
  -i ${INSTALL_DIR}/id_ed25519 \\
  -R \${REMOTE_BIND_HOST}:\${REMOTE_BIND_PORT}:\${PROXMOX_API_HOST}:\${PROXMOX_API_PORT} \\
  \${TUNNEL_USER}@\${JENKINS_TUNNEL_HOST} -p \${JENKINS_TUNNEL_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "${SERVICE_FILE}"

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
systemctl is-active --quiet "${SERVICE_NAME}"
