#!/usr/bin/env bash
set -euo pipefail

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command is missing: $1" >&2
    exit 1
  fi
}

require_env() {
  if [[ -z "${!1:-}" ]]; then
    echo "Required environment variable is missing: $1" >&2
    exit 1
  fi
}

require_command curl
require_command jq
require_command terraform

require_env SCW_SECRET_KEY
require_env SNAPSHOTS_TFSTATE_BUCKET
require_env SNAPSHOTS_TFSTATE_ENDPOINT
require_env SNAPSHOTS_TFSTATE_REGION
require_env TF_VAR_project_id

ZONE="${RESTORE_DRILL_ZONE:-fr-par-1}"
TARGET_KEYS_CSV="${RESTORE_DRILL_TARGET_KEYS:-chat,jenkins,node1-main,observability}"
MIN_AGE_HOURS="${RESTORE_DRILL_JANITOR_MIN_AGE_HOURS:-6}"
CURRENT_RUN_ID="${RESTORE_DRILL_CURRENT_RUN_ID:-${GITHUB_RUN_ID:-manual}-${GITHUB_RUN_ATTEMPT:-0}}"
PROJECT_ID="${TF_VAR_project_id}"
API_BASE="https://api.scaleway.com"

if ! [[ "${MIN_AGE_HOURS}" =~ ^[0-9]+$ ]] || (( MIN_AGE_HOURS < 1 )); then
  echo "RESTORE_DRILL_JANITOR_MIN_AGE_HOURS must be a positive integer" >&2
  exit 1
fi

IFS=',' read -r -a TARGET_KEYS <<< "${TARGET_KEYS_CSV}"
targets_json="$(
  for target in "${TARGET_KEYS[@]}"; do
    target="$(echo "${target}" | tr -d '[:space:]')"
    if [[ -n "${target}" ]]; then
      printf '%s\n' "${target}"
    fi
  done | jq -R . | jq -cs .
)"

if [[ "$(jq 'length' <<< "${targets_json}")" == "0" ]]; then
  echo "No restore-drill targets configured for janitor" >&2
  exit 1
fi

cutoff_epoch="$(date -u -d "-${MIN_AGE_HOURS} hours" +%s)"
work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

redact_tf_output() {
  sed -E \
    -e 's/[a-z]{2}-[a-z]+-[0-9]\/[0-9a-fA-F-]{36}/<scw-id>/g' \
    -e 's/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/<uuid>/g'
}

api_get() {
  local path="$1"
  local output="$2"
  curl -fsS --retry 3 --retry-all-errors \
    -H "X-Auth-Token: ${SCW_SECRET_KEY}" \
    -H "Content-Type: application/json" \
    "${API_BASE}/${path}" \
    -o "${output}"
}

api_delete() {
  local path="$1"
  curl -fsS --retry 3 --retry-all-errors \
    -X DELETE \
    -H "X-Auth-Token: ${SCW_SECRET_KEY}" \
    -H "Content-Type: application/json" \
    "${API_BASE}/${path}" \
    -o /dev/null
}

api_post() {
  local path="$1"
  local body="$2"
  curl -fsS --retry 3 --retry-all-errors \
    -X POST \
    -H "X-Auth-Token: ${SCW_SECRET_KEY}" \
    -H "Content-Type: application/json" \
    -d "${body}" \
    "${API_BASE}/${path}" \
    -o /dev/null
}

fetch_collection() {
  local base_path="$1"
  local array_key="$2"
  local output="$3"
  local page=1
  local per_page=100

  echo "[]" > "${output}"
  while true; do
    local separator="?"
    if [[ "${base_path}" == *"?"* ]]; then
      separator="&"
    fi

    local page_file="${work_dir}/${array_key}-${page}.json"
    local page_items="${work_dir}/${array_key}-${page}-items.json"
    api_get "${base_path}${separator}page=${page}&per_page=${per_page}" "${page_file}"
    jq -c --arg key "${array_key}" '.[$key] // []' "${page_file}" > "${page_items}"

    jq -s '.[0] + .[1]' "${output}" "${page_items}" > "${output}.next"
    mv "${output}.next" "${output}"

    local count
    count="$(jq 'length' "${page_items}")"
    if (( count < per_page )); then
      break
    fi
    page=$((page + 1))
  done
}

write_candidates() {
  local input="$1"
  local kind="$2"
  local output="$3"

  jq -c \
    --arg project "${PROJECT_ID}" \
    --arg current_run "${CURRENT_RUN_ID}" \
    --arg kind "${kind}" \
    --argjson cutoff "${cutoff_epoch}" \
    --argjson targets "${targets_json}" \
    '
      def has_tag($resource; $tag): (($resource.tags // []) | index($tag)) != null;
      def has_base_tags($resource): has_tag($resource; "devsh") and has_tag($resource; "restore-drill");
      def has_current_run($resource): has_tag($resource; $current_run);
      def has_allowed_target($resource):
        any($targets[]; has_tag($resource; .) or (($resource.name // "") | startswith("restore-drill-" + . + "-")));
      def has_safe_name($resource):
        if $kind == "server" then
          any($targets[]; (($resource.name // "") | startswith("restore-drill-" + . + "-")))
        elif $kind == "volume" then
          any($targets[]; (($resource.name // "") | startswith("restore-drill-" + . + "-") and endswith("-data")))
        elif $kind == "security_group" then
          any($targets[]; (($resource.name // "") | startswith("restore-drill-" + . + "-") and endswith("-sg")))
        elif $kind == "ip" then
          any($targets[]; has_tag($resource; .))
        else
          false
        end;
      def api_epoch($resource):
        (($resource.creation_date // $resource.created_at // $resource.updated_at // "")
          | tostring
          | sub("\\.[0-9]+"; "")
          | sub("\\+00:00$"; "Z")
          | fromdateiso8601?);
      def created_tag_epoch($resource):
        (((($resource.tags // [])
          | map(select(test("^created-[0-9]{8}T[0-9]{6}Z$")))
          | first? // "")
          | sub("^created-"; "")
          | strptime("%Y%m%dT%H%M%SZ"))? | mktime?);
      def resource_epoch($resource): api_epoch($resource) // created_tag_epoch($resource);
      [
        .[]
        | . as $resource
        | select(($resource.project // $resource.project_id // "") == $project)
        | select(has_base_tags($resource))
        | select(has_allowed_target($resource))
        | select(has_safe_name($resource))
        | select(has_current_run($resource) | not)
        | select((resource_epoch($resource) // 0) > 0 and resource_epoch($resource) < $cutoff)
        | select(($resource.protected // false) == false)
        | {
            id: $resource.id,
            state: ($resource.state // ""),
            status: ($resource.status // ""),
            references: ($resource.references // []),
            server: ($resource.server // null),
            project_default: ($resource.project_default // $resource.projectDefault // false)
          }
      ]
    ' "${input}" > "${output}"
}

count_json_array() {
  jq 'length' "$1"
}

filter_deletable_volumes() {
  jq -c '[.[] | select((.status // "") == "available") | select((.references // []) | length == 0)]' "$1" > "$2"
}

filter_deletable_ips() {
  jq -c '[.[] | select((.state // "") == "detached") | select(.server == null)]' "$1" > "$2"
}

filter_deletable_security_groups() {
  jq -c '[.[] | select((.project_default // false) == false)]' "$1" > "$2"
}

state_checked_targets=0
state_destroyed_targets=0
api_failures=0
servers_deleted=0
volumes_deleted=0
ips_deleted=0
security_groups_deleted=0
skipped_total=0

cleanup_state_target() {
  local target="$1"

  export TF_VAR_target_key="${target}"
  export TF_VAR_snapshot_id="${ZONE}/00000000-0000-0000-0000-000000000000"
  export TF_VAR_run_id="janitor"
  export TF_VAR_created_at="janitor"
  export TF_VAR_instance_type="DEV1-S"
  export TF_VAR_ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA janitor"
  export TF_VAR_luks_key_access_key="janitor"
  export TF_VAR_luks_key_secret_key="janitor"
  export TF_VAR_result_bucket_name="${SNAPSHOTS_TFSTATE_BUCKET}"
  export TF_VAR_result_object_key=""
  export TF_VAR_result_region="${SNAPSHOTS_TFSTATE_REGION}"
  export TF_VAR_result_endpoint="${SNAPSHOTS_TFSTATE_ENDPOINT}"

  terraform -chdir=terraform/snapshot-restore-drill init -input=false -reconfigure \
    -backend-config="bucket=${SNAPSHOTS_TFSTATE_BUCKET}" \
    -backend-config="key=terraform/snapshot-restore-drill/${target}.tfstate" \
    -backend-config="region=${SNAPSHOTS_TFSTATE_REGION}" \
    -backend-config="endpoint=${SNAPSHOTS_TFSTATE_ENDPOINT}" \
    -backend-config="skip_requesting_account_id=true" \
    -backend-config="skip_credentials_validation=true" \
    -backend-config="skip_metadata_api_check=true" \
    -backend-config="skip_region_validation=true" \
    -backend-config="force_path_style=true" \
    >/dev/null

  state_checked_targets=$((state_checked_targets + 1))

  local resources
  if resources="$(terraform -chdir=terraform/snapshot-restore-drill state list 2>/dev/null)"; then
    :
  else
    resources=""
  fi

  local resource_count
  resource_count="$(sed '/^[[:space:]]*$/d' <<< "${resources}" | wc -l | tr -d '[:space:]')"
  if (( resource_count == 0 )); then
    return
  fi

  state_destroyed_targets=$((state_destroyed_targets + 1))
  terraform -chdir=terraform/snapshot-restore-drill destroy -auto-approve -no-color -parallelism=1 2>&1 | redact_tf_output

  local remaining
  if remaining="$(terraform -chdir=terraform/snapshot-restore-drill state list 2>/dev/null)"; then
    :
  else
    remaining=""
  fi
  local remaining_count
  remaining_count="$(sed '/^[[:space:]]*$/d' <<< "${remaining}" | wc -l | tr -d '[:space:]')"
  if (( remaining_count != 0 )); then
    echo "Restore-drill state cleanup left managed resources behind for target ${target}" >&2
    exit 1
  fi
}

echo "Restore-drill janitor: checking Terraform restore-drill state."
for target in "${TARGET_KEYS[@]}"; do
  target="$(echo "${target}" | tr -d '[:space:]')"
  if [[ -n "${target}" ]]; then
    cleanup_state_target "${target}"
  fi
done

echo "Restore-drill janitor: checking tagged Scaleway resources older than ${MIN_AGE_HOURS}h."

servers_all="${work_dir}/servers-all.json"
servers_candidates="${work_dir}/servers-candidates.json"
fetch_collection "instance/v1/zones/${ZONE}/servers?project=${PROJECT_ID}&order=creation_date_asc" "servers" "${servers_all}"
write_candidates "${servers_all}" "server" "${servers_candidates}"

while IFS= read -r row; do
  [[ -z "${row}" ]] && continue
  id="$(jq -r '.id' <<< "${row}")"
  state="$(jq -r '.state // ""' <<< "${row}")"
  if [[ "${state}" == "stopped" || "${state}" == "stopped_in_place" || "${state}" == "stopped in place" ]]; then
    if api_delete "instance/v1/zones/${ZONE}/servers/${id}" >/dev/null 2>&1; then
      servers_deleted=$((servers_deleted + 1))
    else
      api_failures=$((api_failures + 1))
      echo "Failed to delete a stale restore-drill server candidate." >&2
    fi
  else
    if api_post "instance/v1/zones/${ZONE}/servers/${id}/action" '{"action":"terminate"}' >/dev/null 2>&1 || api_delete "instance/v1/zones/${ZONE}/servers/${id}" >/dev/null 2>&1; then
      servers_deleted=$((servers_deleted + 1))
    else
      api_failures=$((api_failures + 1))
      echo "Failed to terminate a stale restore-drill server candidate." >&2
    fi
  fi
done < <(jq -c '.[]' "${servers_candidates}")

if (( servers_deleted > 0 )); then
  sleep 60
fi

volumes_all="${work_dir}/volumes-all.json"
volumes_candidates="${work_dir}/volumes-candidates.json"
volumes_deletable="${work_dir}/volumes-deletable.json"
fetch_collection "block/v1alpha1/zones/${ZONE}/volumes?project_id=${PROJECT_ID}&order_by=created_at_asc" "volumes" "${volumes_all}"
write_candidates "${volumes_all}" "volume" "${volumes_candidates}"
filter_deletable_volumes "${volumes_candidates}" "${volumes_deletable}"

while IFS= read -r row; do
  [[ -z "${row}" ]] && continue
  id="$(jq -r '.id' <<< "${row}")"
  if api_delete "block/v1alpha1/zones/${ZONE}/volumes/${id}" >/dev/null 2>&1; then
    volumes_deleted=$((volumes_deleted + 1))
  else
    api_failures=$((api_failures + 1))
    echo "Failed to delete a stale restore-drill volume candidate." >&2
  fi
done < <(jq -c '.[]' "${volumes_deletable}")

ips_all="${work_dir}/ips-all.json"
ips_candidates="${work_dir}/ips-candidates.json"
ips_deletable="${work_dir}/ips-deletable.json"
fetch_collection "instance/v1/zones/${ZONE}/ips?project=${PROJECT_ID}" "ips" "${ips_all}"
write_candidates "${ips_all}" "ip" "${ips_candidates}"
filter_deletable_ips "${ips_candidates}" "${ips_deletable}"

while IFS= read -r row; do
  [[ -z "${row}" ]] && continue
  id="$(jq -r '.id' <<< "${row}")"
  if api_delete "instance/v1/zones/${ZONE}/ips/${id}" >/dev/null 2>&1; then
    ips_deleted=$((ips_deleted + 1))
  else
    api_failures=$((api_failures + 1))
    echo "Failed to delete a stale restore-drill IP candidate." >&2
  fi
done < <(jq -c '.[]' "${ips_deletable}")

security_groups_all="${work_dir}/security-groups-all.json"
security_groups_candidates="${work_dir}/security-groups-candidates.json"
security_groups_deletable="${work_dir}/security-groups-deletable.json"
fetch_collection "instance/v1/zones/${ZONE}/security_groups?project=${PROJECT_ID}" "security_groups" "${security_groups_all}"
write_candidates "${security_groups_all}" "security_group" "${security_groups_candidates}"
filter_deletable_security_groups "${security_groups_candidates}" "${security_groups_deletable}"

while IFS= read -r row; do
  [[ -z "${row}" ]] && continue
  id="$(jq -r '.id' <<< "${row}")"
  if api_delete "instance/v1/zones/${ZONE}/security_groups/${id}" >/dev/null 2>&1; then
    security_groups_deleted=$((security_groups_deleted + 1))
  else
    api_failures=$((api_failures + 1))
    echo "Failed to delete a stale restore-drill security group candidate." >&2
  fi
done < <(jq -c '.[]' "${security_groups_deletable}")

volumes_skipped=$(( $(count_json_array "${volumes_candidates}") - $(count_json_array "${volumes_deletable}") ))
ips_skipped=$(( $(count_json_array "${ips_candidates}") - $(count_json_array "${ips_deletable}") ))
security_groups_skipped=$(( $(count_json_array "${security_groups_candidates}") - $(count_json_array "${security_groups_deletable}") ))
skipped_total=$((volumes_skipped + ips_skipped + security_groups_skipped))
deleted_total=$((servers_deleted + volumes_deleted + ips_deleted + security_groups_deleted))

status="success"
if (( api_failures > 0 )); then
  status="failed"
fi

summary="checked ${state_checked_targets} target state files; cleaned ${state_destroyed_targets} states with leftovers; deleted ${deleted_total} stale tagged resources; skipped ${skipped_total} safety-gated candidates"

jq -n \
  --arg status "${status}" \
  --arg summary "${summary}" \
  --argjson state_checked "${state_checked_targets}" \
  --argjson state_destroyed "${state_destroyed_targets}" \
  --argjson deleted_total "${deleted_total}" \
  --argjson skipped_total "${skipped_total}" \
  --argjson api_failures "${api_failures}" \
  --argjson servers_deleted "${servers_deleted}" \
  --argjson volumes_deleted "${volumes_deleted}" \
  --argjson ips_deleted "${ips_deleted}" \
  --argjson security_groups_deleted "${security_groups_deleted}" \
  --argjson min_age_hours "${MIN_AGE_HOURS}" \
  --arg timestamp "$(date -u +%FT%TZ)" \
  '{
    status: $status,
    summary: $summary,
    state_checked_targets: $state_checked,
    state_destroyed_targets: $state_destroyed,
    api_deleted_total: $deleted_total,
    api_skipped_total: $skipped_total,
    api_failures: $api_failures,
    deleted: {
      servers: $servers_deleted,
      volumes: $volumes_deleted,
      ips: $ips_deleted,
      security_groups: $security_groups_deleted
    },
    min_age_hours: $min_age_hours,
    timestamp: $timestamp
  }' > restore-janitor-result.json

{
  echo "## Restore-drill janitor"
  echo
  echo "| Area | Result |"
  echo "| --- | --- |"
  echo "| Overall status | ${status} |"
  echo "| Terraform state sweep | Checked ${state_checked_targets} per-target restore-drill state files. Cleaned ${state_destroyed_targets} files that still had managed restore-drill resources. |"
  echo "| Tagged Scaleway sweep | Deleted ${deleted_total} stale tagged resources. Left ${skipped_total} candidates untouched because they did not pass the delete safety gates. |"
  echo "| Deleted breakdown | Servers ${servers_deleted}, volumes ${volumes_deleted}, IPs ${ips_deleted}, security groups ${security_groups_deleted}. |"
  echo "| Safety window | Only restore-drill resources older than ${MIN_AGE_HOURS}h are eligible for API cleanup. |"
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "summary=${summary}" >> "${GITHUB_OUTPUT}"
  echo "status=${status}" >> "${GITHUB_OUTPUT}"
fi

echo "Restore-drill janitor: ${summary}."

if [[ "${status}" != "success" ]]; then
  exit 1
fi
