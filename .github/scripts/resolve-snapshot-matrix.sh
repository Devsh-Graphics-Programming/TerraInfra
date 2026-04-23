#!/usr/bin/env bash
set -euo pipefail

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command is missing: $1" >&2
    exit 1
  fi
}

write_output() {
  local key="$1"
  local value="$2"

  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "${key}=${value}" >> "${GITHUB_OUTPUT}"
  else
    echo "${key}=${value}"
  fi
}

require_command jq

default_snapshot_targets="$(jq -cn '{
  "node1-main": {
    volume_name: "devsh-k3s-prod-data-node1",
    name_prefix: "devsh-k3s-prod-data",
    tags: ["node1", "kimai"]
  },
  chat: {
    volume_name: "prod-chat-01-data",
    name_prefix: "prod-chat-01-data",
    tags: ["chat", "stoatchat"]
  },
  jenkins: {
    volume_name: "jenkins-prod-data",
    name_prefix: "jenkins-prod-data",
    tags: ["jenkins", "ci-controller"]
  },
  observability: {
    volume_name: "prod-observability-01-data",
    name_prefix: "prod-observability-01-data",
    tags: ["observability", "monitoring"]
  }
}')"

requested_targets="$(echo "${SNAPSHOTS_TARGET_NAMES:-all}" | tr -d '[:space:]')"
if [[ -z "${requested_targets}" || "${requested_targets}" == "all" ]]; then
  snapshot_targets="${default_snapshot_targets}"
else
  snapshot_targets="$(jq -cn --arg names "${requested_targets}" --arg defaults_json "${default_snapshot_targets}" '
    ($defaults_json | fromjson) as $defaults |
    ($names | split(",") | map(select(length > 0))) as $wanted |
    if ($wanted | length) == 0 then
      error("No snapshot targets selected")
    elif any($wanted[]; $defaults[.] == null) then
      error("Unknown snapshot target in " + $names)
    else
      $defaults | with_entries(select(.key as $k | $wanted | index($k)))
    end
  ')"
fi

event_name="${GITHUB_EVENT_NAME:-}"
snapshot_kind="auto"
retention="24h (rotating, max 1 kept)"
requested_manual_snapshot_name=""

if [[ "${event_name}" == "workflow_dispatch" ]]; then
  snapshot_kind="manual"
  ttl_hours="${SNAPSHOTS_MANUAL_TTL_HOURS:-24}"
  if ! [[ "${ttl_hours}" =~ ^[0-9]+$ ]] || (( ttl_hours <= 0 )); then
    echo "Invalid manual_ttl_hours='${ttl_hours}' (expected positive integer)" >&2
    exit 1
  fi

  retention="${ttl_hours}h (Terraform-managed)"
  requested_manual_snapshot_name="${SNAPSHOTS_MANUAL_SNAPSHOT_NAME:-}"
  if [[ -z "${requested_manual_snapshot_name}" ]]; then
    requested_manual_snapshot_name="$(date -u +%Y%m%dT%H%M%SZ)-run${GITHUB_RUN_ID:-local}"
  fi
fi

matrix="$(jq -cn --argjson targets "${snapshot_targets}" '{
  include: [
    $targets
    | to_entries[]
    | {
        target_key: .key,
        volume_name: .value.volume_name,
        name_prefix: .value.name_prefix,
        tags: (.value.tags // [])
      }
  ]
}')"
target_keys="$(echo "${snapshot_targets}" | jq -c 'keys')"

echo "Snapshot targets:"
echo "${target_keys}" | jq -r '.[]'

write_output "matrix" "${matrix}"
write_output "target_keys" "${target_keys}"
write_output "snapshot_kind" "${snapshot_kind}"
write_output "requested_manual_snapshot_name" "${requested_manual_snapshot_name}"
write_output "retention" "${retention}"
