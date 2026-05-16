#!/bin/bash
set -euo pipefail

log() {
  echo "[ensure-data-mount] $*"
}

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

ensure_local_path_storage_on_data() {
  local target="/mnt/data/local-path"
  local link="/var/lib/rancher/k3s/storage"

  mkdir -p "${target}" /var/lib/rancher/k3s
  chmod 0777 "${target}" || true

  if [ -L "${link}" ]; then
    if [ "$(readlink "${link}")" != "${target}" ]; then
      log "${link} is already a symlink to a different target; leaving it unchanged"
    fi
    return
  fi

  if [ -e "${link}" ]; then
    if [ -d "${link}" ] && [ -z "$(find "${link}" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
      rmdir "${link}"
    else
      log "${link} already exists and is not empty; leaving it unchanged"
      return
    fi
  fi

  ln -s "${target}" "${link}"
  log "Linked ${link} to ${target}"
}

if findmnt -n /mnt/data >/dev/null 2>&1; then
  log "/mnt/data already mounted"
  ensure_local_path_storage_on_data
  exit 0
fi

ROOT_DEV=$(findmnt -n -o SOURCE / | sed 's/[0-9]*$//')
DATA_DEV=$(lsblk -ndo NAME,TYPE | awk -v root="${ROOT_DEV##*/}" '$2=="disk" && $1!=root {print "/dev/"$1; exit}')
if [ -z "${DATA_DEV}" ]; then
  log "No data device found; skipping"
  exit 0
fi
DATA_DEV="/dev/${DATA_DEV##*/}"
FSTYPE=$(lsblk -no FSTYPE "${DATA_DEV}" || true)
mkdir -p /mnt/data

ensure_fstab_uuid() {
  local uuid="$1"
  local entry="UUID=${uuid} /mnt/data ext4 defaults,nofail 0 2"
  if ! grep -q "UUID=${uuid} /mnt/data" /etc/fstab 2>/dev/null; then
    echo "${entry}" >> /etc/fstab
  fi
}

# Non-LUKS filesystem present
if [ -n "${FSTYPE}" ] && [ "${FSTYPE}" != "crypto_LUKS" ]; then
  log "Found existing ${FSTYPE} on ${DATA_DEV}; mounting directly"
  UUID=$(blkid -s UUID -o value "${DATA_DEV}")
  if [ -n "${UUID}" ]; then
    ensure_fstab_uuid "${UUID}"
  fi
  mount -a
  mkdir -p /mnt/data/mariadb /mnt/data/kimai-var
  ensure_local_path_storage_on_data
  log "Mounted /mnt/data without LUKS"
  exit 0
fi

fetch_key() {
  local dest="$1"
  if [ -n "${LUKS_KEY_URL:-}" ]; then
    curl -fsSL "${LUKS_KEY_URL}" -o "${dest}"
    return
  fi
  if [ -n "${LUKS_KEY_ACCESS_KEY:-}" ] && [ -n "${LUKS_KEY_SECRET_KEY:-}" ]; then
    AWS_ACCESS_KEY_ID="${LUKS_KEY_ACCESS_KEY}" \
    AWS_SECRET_ACCESS_KEY="${LUKS_KEY_SECRET_KEY}" \
    AWS_DEFAULT_REGION="fr-par" \
      aws --endpoint-url=https://s3.fr-par.scw.cloud s3 cp s3://terra-luks-keys/luks.key "${dest}"
    return
  fi
  log "No LUKS key source provided (URL or access/secret required)"
  exit 1
}

KEY_FILE=$(mktemp /dev/shm/luks.key.XXXXXX)
cleanup() {
  shred -u "${KEY_FILE}" 2>/dev/null || true
}
trap cleanup EXIT
fetch_key "${KEY_FILE}"

# Fresh disk: format + set up LUKS
if [ -z "${FSTYPE}" ]; then
  if ! is_true "${ALLOW_LUKS_FORMAT:-false}"; then
    log "No filesystem on ${DATA_DEV} and formatting not allowed (set ALLOW_LUKS_FORMAT=true)"
    exit 1
  fi
  log "Formatting ${DATA_DEV} as LUKS2"
  cryptsetup luksFormat --type luks2 --batch-mode --key-file "${KEY_FILE}" "${DATA_DEV}"
fi

log "Opening LUKS volume on ${DATA_DEV}"
cryptsetup luksOpen "${DATA_DEV}" data_crypt --key-file "${KEY_FILE}"

MAPPER_FSTYPE=$(lsblk -no FSTYPE /dev/mapper/data_crypt || true)
if [ -z "${MAPPER_FSTYPE}" ]; then
  log "Creating ext4 filesystem on /dev/mapper/data_crypt"
  mkfs.ext4 -F /dev/mapper/data_crypt
fi

UUID=$(blkid -s UUID -o value /dev/mapper/data_crypt)
if [ -n "${UUID}" ]; then
  ensure_fstab_uuid "${UUID}"
fi

mount -a
mkdir -p /mnt/data/mariadb /mnt/data/kimai-var
ensure_local_path_storage_on_data
log "Mounted /mnt/data via LUKS"
