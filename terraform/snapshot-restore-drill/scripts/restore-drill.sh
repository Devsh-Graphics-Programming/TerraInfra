#!/bin/bash
set -euo pipefail

STATUS_DIR="/var/lib/terra-restore-drill"
STATUS_FILE="${STATUS_DIR}/status.json"

if [ -f /etc/default/terra-restore-drill ]; then
  # shellcheck disable=SC1091
  . /etc/default/terra-restore-drill
fi

TARGET_KEY="${TARGET_KEY:-}"
PHASE="init"

mkdir -p "${STATUS_DIR}"

write_status() {
  local status="$1"
  local message="$2"
  jq -n \
    --arg target "${TARGET_KEY}" \
    --arg phase "${PHASE}" \
    --arg status "${status}" \
    --arg message "${message}" \
    --arg timestamp "$(date -u +%FT%TZ)" \
    '{target: $target, phase: $phase, status: $status, message: $message, timestamp: $timestamp}' \
    > "${STATUS_FILE}"
}

fail() {
  local message="$1"
  write_status "failed" "${message}"
  echo "[restore-drill] ${message}" >&2
  exit 1
}

on_exit() {
  local rc=$?
  cleanup_containers
  if [ "${rc}" -ne 0 ]; then
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

trap on_exit EXIT

PHASE="mount"
write_status "running" "checking restored data mount"
findmnt -n /mnt/data >/dev/null 2>&1 || fail "/mnt/data is not mounted"

PHASE="docker"
write_status "running" "checking docker runtime"
systemctl is-active --quiet docker || systemctl start docker
docker info >/dev/null

case "${TARGET_KEY}" in
  chat)
    PHASE="chat-mongo"
    write_status "running" "checking MongoDB from restored chat data"
    require_path "/mnt/data/stoat/self-hosted/data/db"
    run_container drill-mongo \
      -p 127.0.0.1:27017:27017 \
      -v /mnt/data/stoat/self-hosted/data/db:/data/db \
      docker.io/mongo
    wait_for_exec 300 docker exec drill-mongo mongosh localhost:27017/test --quiet --eval 'db.runCommand("ping").ok'

    PHASE="chat-minio"
    write_status "running" "checking MinIO from restored chat data"
    require_path "/mnt/data/stoat/self-hosted/data/minio"
    run_container drill-minio \
      -p 127.0.0.1:9000:9000 \
      -e MINIO_ROOT_USER=restorecheck \
      -e MINIO_ROOT_PASSWORD=restorecheck123 \
      -e MINIO_DOMAIN=minio \
      -v /mnt/data/stoat/self-hosted/data/minio:/data \
      docker.io/minio/minio server /data
    wait_for_http "http://127.0.0.1:9000/minio/health/ready" 300

    PHASE="chat-rabbit"
    write_status "running" "checking RabbitMQ from restored chat data"
    require_path "/mnt/data/stoat/self-hosted/data/rabbit"
    run_container drill-rabbit \
      --hostname rabbit-0 \
      -p 127.0.0.1:5672:5672 \
      -e RABBITMQ_NODENAME=rabbit@rabbit-0 \
      -v /mnt/data/stoat/self-hosted/data/rabbit:/var/lib/rabbitmq \
      docker.io/rabbitmq:4
    wait_for_exec 300 docker exec drill-rabbit rabbitmq-diagnostics -q ping
    ;;

  jenkins)
    PHASE="jenkins"
    write_status "running" "checking Jenkins from restored home"
    require_path "/mnt/data/jenkins/home"
    run_container drill-jenkins \
      -p 127.0.0.1:8080:8080 \
      -e JAVA_OPTS=-Djenkins.install.runSetupWizard=false \
      -v /mnt/data/jenkins/home:/var/jenkins_home \
      docker.io/jenkins/jenkins:lts-jdk21
    wait_for_http "http://127.0.0.1:8080/login" 600
    ;;

  observability)
    PHASE="grafana"
    write_status "running" "checking Grafana from restored data"
    require_path "/mnt/data/grafana"
    run_container drill-grafana \
      -p 127.0.0.1:3000:3000 \
      -v /mnt/data/grafana:/var/lib/grafana \
      docker.io/grafana/grafana:11.2.2
    wait_for_http "http://127.0.0.1:3000/api/health" 300
    docker rm -f drill-grafana >/dev/null 2>&1 || true

    PHASE="oncall-grafana"
    write_status "running" "checking OnCall Grafana from restored data"
    require_path "/mnt/data/oncall-grafana"
    run_container drill-oncall-grafana \
      -p 127.0.0.1:3001:3000 \
      -v /mnt/data/oncall-grafana:/var/lib/grafana \
      docker.io/grafana/grafana:11.2.2
    wait_for_http "http://127.0.0.1:3001/api/health" 300

    PHASE="prometheus-data"
    write_status "running" "checking local-path data root"
    require_path "/mnt/data/local-path"
    ;;

  node1-main)
    PHASE="kimai-mariadb"
    write_status "running" "checking MariaDB from restored node1 data"
    require_path "/mnt/data/mariadb"
    require_path "/mnt/data/kimai-var"
    run_container drill-mariadb \
      -p 127.0.0.1:3306:3306 \
      -v /mnt/data/mariadb:/var/lib/mysql \
      docker.io/mariadb:11
    wait_for_tcp "127.0.0.1" "3306" 300
    ;;

  *)
    fail "unknown restore drill target: ${TARGET_KEY}"
    ;;
esac

PHASE="complete"
write_status "success" "restore drill passed"
log "restore drill passed"
