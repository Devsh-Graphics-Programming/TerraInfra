#!/bin/bash
set -euo pipefail

STATUS_DIR="/var/lib/terra-restore-drill"
STATUS_FILE="${STATUS_DIR}/status.json"
CHECKS_FILE="${STATUS_DIR}/checks.jsonl"

if [ -f /etc/default/terra-restore-drill ]; then
  # shellcheck disable=SC1091
  . /etc/default/terra-restore-drill
fi

if [ -f /etc/default/terra-data ]; then
  # shellcheck disable=SC1091
  . /etc/default/terra-data
fi

export LUKS_KEY_URL="${LUKS_KEY_URL:-}"
export LUKS_KEY_ACCESS_KEY="${LUKS_KEY_ACCESS_KEY:-}"
export LUKS_KEY_SECRET_KEY="${LUKS_KEY_SECRET_KEY:-}"
export ALLOW_LUKS_FORMAT="${ALLOW_LUKS_FORMAT:-false}"

TARGET_KEY="${TARGET_KEY:-}"
PHASE="init"
STATUS_FINALIZED=0

mkdir -p "${STATUS_DIR}"
: > "${CHECKS_FILE}"

record_check() {
  local name="$1"
  local status="$2"
  local message="$3"
  jq -cn \
    --arg name "${name}" \
    --arg status "${status}" \
    --arg message "${message}" \
    '{name: $name, status: $status, message: $message}' \
    >> "${CHECKS_FILE}"
}

pass_check() {
  local name="$1"
  local message="$2"
  record_check "${name}" "passed" "${message}"
}

redact_detail() {
  sed -E \
    -e 's/[a-z]{2}-[a-z]+-[0-9]\/[0-9a-fA-F-]{36}/<scw-id>/g' \
    -e 's/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/<uuid>/g' \
    -e 's#(https?://[^?[:space:]]+)\?[^[:space:]]+#\1?<redacted-query>#g' \
    -e 's#s3://terra-luks-keys/[^[:space:]]+#s3://terra-luks-keys/<object>#g' \
    -e 's#(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|LUKS_KEY_ACCESS_KEY|LUKS_KEY_SECRET_KEY|LUKS_KEY_URL)=[^[:space:]]+#\1=<redacted>#g'
}

log_excerpt() {
  local path="$1"
  tail -n 120 "${path}" 2>/dev/null | redact_detail | head -c 3000 || true
}

upload_status() {
  if [ -z "${RESULT_BUCKET_NAME:-}" ] || [ -z "${RESULT_OBJECT_KEY:-}" ]; then
    return
  fi
  if [ -z "${LUKS_KEY_ACCESS_KEY:-}" ] || [ -z "${LUKS_KEY_SECRET_KEY:-}" ]; then
    return
  fi
  AWS_ACCESS_KEY_ID="${LUKS_KEY_ACCESS_KEY}" \
  AWS_SECRET_ACCESS_KEY="${LUKS_KEY_SECRET_KEY}" \
  AWS_DEFAULT_REGION="${RESULT_REGION:-fr-par}" \
    aws --endpoint-url="${RESULT_ENDPOINT:-https://s3.fr-par.scw.cloud}" \
      s3 cp "${STATUS_FILE}" "s3://${RESULT_BUCKET_NAME}/${RESULT_OBJECT_KEY}" \
      >/dev/null 2>&1 || true
}

write_status() {
  local status="$1"
  local message="$2"
  local detail="${3:-}"
  jq -n \
    --slurpfile checks "${CHECKS_FILE}" \
    --arg target "${TARGET_KEY}" \
    --arg phase "${PHASE}" \
    --arg status "${status}" \
    --arg message "${message}" \
    --arg detail "${detail}" \
    --arg timestamp "$(date -u +%FT%TZ)" \
    '{target: $target, phase: $phase, status: $status, message: $message, timestamp: $timestamp, checks: $checks}
      + (if $detail == "" then {} else {detail: $detail} end)' \
    > "${STATUS_FILE}"
  upload_status
}

fail() {
  local message="$1"
  local detail="${2:-}"
  record_check "${PHASE}" "failed" "${message}"
  write_status "failed" "${message}" "${detail}"
  STATUS_FINALIZED=1
  echo "[restore-drill] ${message}" >&2
  if [ -n "${detail}" ]; then
    echo "${detail}" >&2
  fi
  exit 1
}

on_exit() {
  local rc=$?
  cleanup_containers
  if [ "${rc}" -ne 0 ] && [ "${STATUS_FINALIZED}" -eq 0 ]; then
    record_check "${PHASE}" "failed" "restore drill failed in phase ${PHASE}"
    write_status "failed" "restore drill failed in phase ${PHASE}"
  fi
}

log() {
  echo "[restore-drill][${TARGET_KEY}][${PHASE}] $*"
}

require_path() {
  local path="$1"
  [ -e "${path}" ] || fail "required path is missing: ${path}"
}

wait_for_http() {
  local url="$1"
  local seconds="${2:-300}"
  local end=$((SECONDS + seconds))
  until curl -fsS "${url}" >/dev/null 2>&1; do
    if [ "${SECONDS}" -ge "${end}" ]; then
      fail "HTTP health check timed out: ${url}"
    fi
    sleep 5
  done
}

wait_for_tcp() {
  local host="$1"
  local port="$2"
  local seconds="${3:-180}"
  local end=$((SECONDS + seconds))
  until timeout 2 bash -c ":</dev/tcp/${host}/${port}" >/dev/null 2>&1; do
    if [ "${SECONDS}" -ge "${end}" ]; then
      fail "TCP health check timed out: ${host}:${port}"
    fi
    sleep 5
  done
}

wait_for_exec() {
  local seconds="$1"
  shift
  local end=$((SECONDS + seconds))
  until "$@" >/dev/null 2>&1; do
    if [ "${SECONDS}" -ge "${end}" ]; then
      fail "command health check timed out: $*"
    fi
    sleep 5
  done
}

run_container() {
  local name="$1"
  shift
  docker rm -f "${name}" >/dev/null 2>&1 || true
  docker run -d --name "${name}" "$@"
}

cleanup_containers() {
  docker rm -f drill-mongo drill-minio drill-rabbit drill-jenkins drill-grafana drill-oncall-grafana drill-mariadb >/dev/null 2>&1 || true
}

run_mount_check() {
  local mount_log="${STATUS_DIR}/mount.log"
  local end=$((SECONDS + 300))
  : > "${mount_log}"

  while [ "${SECONDS}" -lt "${end}" ]; do
    if /usr/local/bin/ensure-data-mount.sh >> "${mount_log}" 2>&1 && findmnt -n /mnt/data >> "${mount_log}" 2>&1; then
      return 0
    fi
    lsblk -o NAME,TYPE,FSTYPE,SIZE,MOUNTPOINTS >> "${mount_log}" 2>&1 || true
    echo "[restore-drill] /mnt/data is not ready yet; retrying" >> "${mount_log}"
    sleep 10
  done

  /usr/local/bin/ensure-data-mount.sh >> "${mount_log}" 2>&1 || true
  findmnt -n /mnt/data >> "${mount_log}" 2>&1 || true
  lsblk -o NAME,TYPE,FSTYPE,SIZE,MOUNTPOINTS >> "${mount_log}" 2>&1 || true
  return 1
}

trap on_exit EXIT

PHASE="mount"
write_status "running" "mounting restored data volume"
if ! run_mount_check; then
  fail "restored data mount setup failed" "$(log_excerpt "${STATUS_DIR}/mount.log")"
fi
pass_check "data-mount" "restored data volume is mounted"

PHASE="docker"
write_status "running" "checking docker runtime"
systemctl is-active --quiet docker || systemctl start docker
docker info >/dev/null
pass_check "docker" "docker runtime is available"

case "${TARGET_KEY}" in
  chat)
    PHASE="chat-mongo"
    write_status "running" "checking MongoDB from restored chat data"
    require_path "/mnt/data/stoat/self-hosted/data/db"
    pass_check "mongodb-data" "restored MongoDB data path exists"
    run_container drill-mongo \
      -p 127.0.0.1:27017:27017 \
      -v /mnt/data/stoat/self-hosted/data/db:/data/db \
      docker.io/mongo
    wait_for_exec 300 docker exec drill-mongo mongosh localhost:27017/test --quiet --eval 'db.runCommand("ping").ok'
    pass_check "mongodb-ping" "MongoDB ping succeeded"

    PHASE="chat-minio"
    write_status "running" "checking MinIO from restored chat data"
    require_path "/mnt/data/stoat/self-hosted/data/minio"
    pass_check "minio-data" "restored MinIO data path exists"
    run_container drill-minio \
      -p 127.0.0.1:9000:9000 \
      -e MINIO_ROOT_USER=restorecheck \
      -e MINIO_ROOT_PASSWORD=restorecheck123 \
      -e MINIO_DOMAIN=minio \
      -v /mnt/data/stoat/self-hosted/data/minio:/data \
      docker.io/minio/minio server /data
    wait_for_http "http://127.0.0.1:9000/minio/health/ready" 300
    pass_check "minio-health" "MinIO health endpoint is ready"

    PHASE="chat-rabbit"
    write_status "running" "checking RabbitMQ from restored chat data"
    require_path "/mnt/data/stoat/self-hosted/data/rabbit"
    pass_check "rabbitmq-data" "restored RabbitMQ data path exists"
    run_container drill-rabbit \
      --hostname rabbit-0 \
      -p 127.0.0.1:5672:5672 \
      -e RABBITMQ_NODENAME=rabbit@rabbit-0 \
      -v /mnt/data/stoat/self-hosted/data/rabbit:/var/lib/rabbitmq \
      docker.io/rabbitmq:4
    wait_for_exec 300 docker exec drill-rabbit rabbitmq-diagnostics -q ping
    pass_check "rabbitmq-ping" "RabbitMQ ping succeeded"
    ;;

  jenkins)
    PHASE="jenkins"
    write_status "running" "checking Jenkins from restored home"
    require_path "/mnt/data/jenkins/home"
    pass_check "jenkins-home" "restored Jenkins home path exists"
    require_path "/mnt/data/jenkins/home/config.xml"
    pass_check "jenkins-config" "restored Jenkins controller config exists"
    require_path "/mnt/data/jenkins/home/secrets/master.key"
    pass_check "jenkins-master-key" "restored Jenkins master key exists"
    require_path "/mnt/data/jenkins/home/plugins"
    pass_check "jenkins-plugins" "restored Jenkins plugins directory exists"
    require_path "/mnt/data/jenkins/home/jobs/system/jobs/smoke/config.xml"
    pass_check "jenkins-smoke-job" "restored smoke job configuration exists"
    require_path "/mnt/data/jenkins/home/jobs/ci/config.xml"
    pass_check "jenkins-ci-folder" "restored CI folder configuration exists"
    require_path "/mnt/data/jenkins/home/jobs/ci/jobs/ditt/config.xml"
    pass_check "jenkins-ditt-folder" "restored DITT folder configuration exists"
    require_path "/mnt/data/jenkins/home/jobs/ci/jobs/ditt/jobs/store-smoke/config.xml"
    pass_check "jenkins-store-smoke-job" "restored store smoke job configuration exists"
    require_path "/mnt/data/jenkins/home/jobs/ci/jobs/ditt/jobs/ex40-report-plan/config.xml"
    pass_check "jenkins-ex40-plan-job" "restored EX40 report plan job configuration exists"
    run_container drill-jenkins \
      -p 127.0.0.1:8080:8080 \
      -e JAVA_OPTS=-Djenkins.install.runSetupWizard=false \
      -e PROMETHEUS_NAMESPACE=jenkins \
      -e COLLECT_DISK_USAGE=false \
      -e COLLECTING_METRICS_PERIOD_IN_SECONDS=120 \
      -v /mnt/data/jenkins/home:/var/jenkins_home \
      docker.io/jenkins/jenkins:lts-jdk21
    wait_for_http "http://127.0.0.1:8080/login" 600
    pass_check "jenkins-login" "Jenkins login endpoint responded"
    metrics_code="$(curl -sS -o /dev/null -w "%{http_code}" "http://127.0.0.1:8080/prometheus/" || true)"
    if [ "${metrics_code}" != "401" ] && [ "${metrics_code}" != "403" ]; then
      fail "Jenkins Prometheus endpoint did not require authentication"
    fi
    pass_check "jenkins-metrics-auth" "Jenkins Prometheus endpoint requires authentication"
    ;;

  observability)
    PHASE="grafana"
    write_status "running" "checking Grafana from restored data"
    require_path "/mnt/data/grafana"
    pass_check "grafana-data" "restored Grafana data path exists"
    run_container drill-grafana \
      -p 127.0.0.1:3000:3000 \
      -v /mnt/data/grafana:/var/lib/grafana \
      docker.io/grafana/grafana:11.2.2
    wait_for_http "http://127.0.0.1:3000/api/health" 300
    pass_check "grafana-health" "Grafana health endpoint responded"
    docker rm -f drill-grafana >/dev/null 2>&1 || true

    PHASE="oncall-grafana"
    write_status "running" "checking OnCall Grafana from restored data"
    require_path "/mnt/data/oncall-grafana"
    pass_check "oncall-grafana-data" "restored OnCall Grafana data path exists"
    run_container drill-oncall-grafana \
      -p 127.0.0.1:3001:3000 \
      -v /mnt/data/oncall-grafana:/var/lib/grafana \
      docker.io/grafana/grafana:11.2.2
    wait_for_http "http://127.0.0.1:3001/api/health" 300
    pass_check "oncall-grafana-health" "OnCall Grafana health endpoint responded"

    PHASE="prometheus-data"
    write_status "running" "checking local-path data root"
    require_path "/mnt/data/local-path"
    pass_check "local-path-data" "restored local-path data root exists"
    ;;

  node1-main)
    PHASE="kimai-mariadb"
    write_status "running" "checking MariaDB from restored node1 data"
    require_path "/mnt/data/mariadb"
    pass_check "mariadb-data" "restored MariaDB data path exists"
    require_path "/mnt/data/kimai-var"
    pass_check "kimai-var" "restored Kimai var path exists"
    run_container drill-mariadb \
      -p 127.0.0.1:3306:3306 \
      -v /mnt/data/mariadb:/var/lib/mysql \
      docker.io/mariadb:11
    wait_for_tcp "127.0.0.1" "3306" 300
    pass_check "mariadb-tcp" "MariaDB port responded locally"
    ;;

  *)
    fail "unknown restore drill target: ${TARGET_KEY}"
    ;;
esac

PHASE="complete"
write_status "success" "restore drill passed"
STATUS_FINALIZED=1
log "restore drill passed"
