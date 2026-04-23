#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

read_output() {
  local key="$1"
  local file="$2"
  sed -n "s/^${key}=//p" "${file}" | tail -n 1
}

run_snapshot_resolver() {
  local output="$1"
  shift
  : > "${output}"
  env GITHUB_OUTPUT="${output}" "$@" bash "${script_dir}/resolve-snapshot-matrix.sh" >/dev/null
}

run_restore_resolver() {
  local output="$1"
  shift
  : > "${output}"
  env GITHUB_OUTPUT="${output}" "$@" bash "${script_dir}/resolve-restore-matrix.sh" >/dev/null
}

snapshot_all="${work_dir}/snapshot-all.out"
run_snapshot_resolver "${snapshot_all}" SNAPSHOTS_TARGET_NAMES=all GITHUB_EVENT_NAME=schedule GITHUB_RUN_ID=local
snapshot_all_matrix="$(read_output matrix "${snapshot_all}")"
jq -e '.include | length == 4' <<< "${snapshot_all_matrix}" >/dev/null

snapshot_jenkins="${work_dir}/snapshot-jenkins.out"
run_snapshot_resolver "${snapshot_jenkins}" SNAPSHOTS_TARGET_NAMES=jenkins GITHUB_EVENT_NAME=workflow_dispatch SNAPSHOTS_MANUAL_TTL_HOURS=24 SNAPSHOTS_MANUAL_SNAPSHOT_NAME=local-test GITHUB_RUN_ID=local
snapshot_jenkins_matrix="$(read_output matrix "${snapshot_jenkins}")"
jq -e '.include | length == 1 and .[0].target_key == "jenkins" and .[0].volume_name == "jenkins-prod-data"' <<< "${snapshot_jenkins_matrix}" >/dev/null
[[ "$(read_output snapshot_kind "${snapshot_jenkins}")" == "manual" ]]
[[ "$(read_output requested_manual_snapshot_name "${snapshot_jenkins}")" == "local-test" ]]

restore_all="${work_dir}/restore-all.out"
run_restore_resolver "${restore_all}" SNAPSHOTS_TARGET_NAMES=all
restore_all_matrix="$(read_output matrix "${restore_all}")"
jq -e '.include | length == 4' <<< "${restore_all_matrix}" >/dev/null

restore_jenkins="${work_dir}/restore-jenkins.out"
run_restore_resolver "${restore_jenkins}" SNAPSHOTS_TARGET_NAMES=jenkins
restore_jenkins_matrix="$(read_output matrix "${restore_jenkins}")"
jq -e '.include | length == 1 and .[0].target_key == "jenkins" and .[0].instance_type == "DEV1-S"' <<< "${restore_jenkins_matrix}" >/dev/null

if env SNAPSHOTS_TARGET_NAMES=unknown bash "${script_dir}/resolve-snapshot-matrix.sh" >/dev/null 2>&1; then
  echo "Snapshot resolver accepted an unknown target" >&2
  exit 1
fi

if env SNAPSHOTS_TARGET_NAMES=unknown bash "${script_dir}/resolve-restore-matrix.sh" >/dev/null 2>&1; then
  echo "Restore resolver accepted an unknown target" >&2
  exit 1
fi

echo "Snapshot and restore target selection checks passed."
