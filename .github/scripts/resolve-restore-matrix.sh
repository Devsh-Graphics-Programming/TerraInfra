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

all_targets='["chat","jenkins","node1-main","observability"]'
requested_targets="$(echo "${SNAPSHOTS_TARGET_NAMES:-all}" | tr -d '[:space:]')"
wanted="$(jq -cn --arg names "${requested_targets}" --argjson all "${all_targets}" '
  if ($names == "" or $names == "all") then
    $all
  else
    ($names | split(",") | map(select(length > 0))) as $wanted |
    if ($wanted | length) == 0 then
      error("No restore targets selected")
    elif any($wanted[]; . as $target | ($all | index($target) | not)) then
      error("Unknown restore target in " + $names)
    else
      $wanted
    end
  end
')"

matrix="$(jq -cn --argjson wanted "${wanted}" '
  def instance_type($key):
    if $key == "chat" then "DEV1-M"
    elif $key == "jenkins" then "DEV1-S"
    elif $key == "observability" then "DEV1-S"
    else "DEV1-S"
    end;
  {
    include: [
      $wanted[] as $key |
      {
        target_key: $key,
        instance_type: instance_type($key)
      }
    ]
  }
')"

echo "Restore targets:"
echo "${matrix}" | jq -r '.include[].target_key'

write_output "matrix" "${matrix}"
