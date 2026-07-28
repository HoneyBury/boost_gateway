#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

fail() {
  printf 'Redis persistence collector install: FAIL: %s\n' "$*" >&2
  exit 1
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'run with sudo on the Ubuntu operations host'
[[ -S /var/run/docker.sock ]] || fail 'Docker socket is unavailable'
getent group boost-gateway >/dev/null || fail 'boost-gateway group is missing'

install -d -o root -g boost-gateway -m 0750 \
  /usr/local/libexec/boost-gateway
install -d -o root -g boost-gateway -m 0755 \
  /var/lib/boost-gateway-evidence/metrics
install -o root -g root -m 0755 \
  "${ROOT}/scripts/tools/collect_container_restart_metrics.py" \
  /usr/local/libexec/boost-gateway/collect_container_restart_metrics.py
install -o root -g root -m 0755 \
  "${ROOT}/scripts/tools/collect_redis_persistence_metrics.py" \
  /usr/local/libexec/boost-gateway/collect_redis_persistence_metrics.py
install -o root -g root -m 0644 \
  "${ROOT}/deploy/systemd/boost-gateway-container-metrics.service" \
  "${ROOT}/deploy/systemd/boost-gateway-container-metrics.timer" \
  /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now boost-gateway-container-metrics.timer
systemctl start boost-gateway-container-metrics.service

METRIC=/var/lib/boost-gateway-evidence/metrics/redis-persistence.prom
[[ -f "$METRIC" && ! -L "$METRIC" ]] || fail 'metric output is missing or unsafe'
grep -Fxq 'boost_gateway_redis_persistence_collection_success 1' "$METRIC" \
  || fail 'initial Redis persistence collection was incomplete'

printf 'Redis persistence collector install: PASS\n'
printf 'This script did not change Redis, Compose, volumes, or backup timers.\n'
