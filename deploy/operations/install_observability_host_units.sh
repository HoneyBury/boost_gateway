#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  printf 'observability host units: FAIL: run with sudo\n' >&2
  exit 1
fi
if [[ ! -S /var/run/docker.sock ]]; then
  printf 'observability host units: FAIL: Docker socket is unavailable\n' >&2
  exit 1
fi
getent group boost-gateway >/dev/null

install -d -o root -g boost-gateway -m 0750 \
  /usr/local/libexec/boost-gateway
# Metrics contain no secrets and node-exporter runs as an unprivileged container user.
install -d -o root -g boost-gateway -m 0755 \
  /var/lib/boost-gateway-evidence/metrics
install -o root -g root -m 0755 \
  "${ROOT}/scripts/tools/collect_container_restart_metrics.py" \
  /usr/local/libexec/boost-gateway/collect_container_restart_metrics.py
install -o root -g root -m 0644 \
  "${ROOT}/deploy/systemd/boost-gateway-container-metrics.service" \
  "${ROOT}/deploy/systemd/boost-gateway-container-metrics.timer" \
  /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now boost-gateway-container-metrics.timer
systemctl start boost-gateway-container-metrics.service
printf 'observability host units: PASS\n'
