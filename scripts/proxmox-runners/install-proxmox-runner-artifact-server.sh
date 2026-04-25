#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/etc/proxmox-runner-artifacts}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
ENV_FILE="${ENV_FILE:-${INSTALL_DIR}/proxmox-runner-artifacts.env}"
SERVICE_NAME="${SERVICE_NAME:-proxmox-runner-artifacts.service}"
SERVICE_FILE="${SERVICE_FILE:-${SYSTEMD_UNIT_DIR}/${SERVICE_NAME}}"

ARTIFACT_ROOT="${ARTIFACT_ROOT:-/var/lib/vz/runner-artifacts}"
LISTEN_HOST="${LISTEN_HOST:-127.0.0.1}"
LISTEN_PORT="${LISTEN_PORT:-18081}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

if [[ ! "${SERVICE_NAME}" =~ ^[A-Za-z0-9_.@-]+\.service$ ]]; then
  echo "SERVICE_NAME must be a systemd service file name." >&2
  exit 1
fi

if [[ ! "${LISTEN_HOST}" =~ ^[A-Za-z0-9_.:-]+$ ]]; then
  echo "LISTEN_HOST is invalid." >&2
  exit 1
fi

if [[ ! "${LISTEN_PORT}" =~ ^[0-9]+$ ]]; then
  echo "LISTEN_PORT must be numeric." >&2
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable is missing: ${PYTHON_BIN}" >&2
  exit 1
fi

install -d -m 0755 "${INSTALL_DIR}"
install -d -m 0755 "${ARTIFACT_ROOT}"

cat >"${ENV_FILE}" <<EOF
ARTIFACT_ROOT=${ARTIFACT_ROOT}
LISTEN_HOST=${LISTEN_HOST}
LISTEN_PORT=${LISTEN_PORT}
PYTHON_BIN=${PYTHON_BIN}
EOF
chmod 0644 "${ENV_FILE}"

cat >"${SERVICE_FILE}" <<EOF
[Unit]
Description=Proxmox runner artifact server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=${ENV_FILE}
ExecStart=${PYTHON_BIN} -m http.server ${LISTEN_PORT} --bind ${LISTEN_HOST} --directory ${ARTIFACT_ROOT}
Restart=always
RestartSec=5
User=root
Group=root

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "${SERVICE_FILE}"

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
systemctl is-active --quiet "${SERVICE_NAME}"
