#!/bin/bash
set -euo pipefail

CFG="/var/lib/rancher/k3s/server/cred/encryption-config.json"
BACKUP="/mnt/data/backup/k3s/encryption-config.json"
STATE="/var/lib/rancher/k3s/server/db/state.db"

has_key() {
  local path="$1"
  [ -s "$path" ] || return 1
  jq -e '((.resources // []) | map(.providers[]?.aescbc?.keys // []) | flatten | length) > 0' "$path" >/dev/null 2>&1
}

write_new_config() {
  local key
  key=$(openssl rand -base64 32)
  cat >"$CFG" <<EOF
{
  "kind": "EncryptionConfiguration",
  "apiVersion": "apiserver.config.k8s.io/v1",
  "resources": [
    {
      "resources": [ "secrets" ],
      "providers": [
        { "aescbc": { "keys": [ { "name": "key-$(date +%s)", "secret": "$key" } ] } },
        { "identity": {} }
      ]
    }
  ]
}
EOF
  chmod 600 "$CFG"
}

mkdir -p "$(dirname "$CFG")"

if [ -s "$STATE" ]; then
  rm -f "$CFG"
  exit 0
fi

if ! has_key "$CFG" && findmnt -n /mnt/data >/dev/null 2>&1 && has_key "$BACKUP"; then
  mkdir -p "$(dirname "$BACKUP")"
  cp "$BACKUP" "$CFG"
  chmod 600 "$CFG"
fi

if ! has_key "$CFG"; then
  write_new_config
  echo "generated encryption config"
fi

if findmnt -n /mnt/data >/dev/null 2>&1; then
  mkdir -p "$(dirname "$BACKUP")"
  cp "$CFG" "$BACKUP"
  chmod 600 "$BACKUP"
fi
