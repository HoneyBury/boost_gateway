#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROXY_ADDRESS="${BOOST_GATEWAY_CONNECT_PROXY:-127.0.0.1:7890}"
SMTP_ADDRESS="${BOOST_GATEWAY_SMTP_UPSTREAM:-smtp.gmail.com:587}"
LISTEN_PORT="${BOOST_GATEWAY_SMTP_RELAY_PORT:-1587}"
CONTAINER="${BOOST_GATEWAY_ALERTMANAGER_CONTAINER:-boost-alertmanager}"
CONFIG_DIR=/etc/boost-gateway
ENV_PATH="${CONFIG_DIR}/smtp-proxy.env"
SUMMARY_PATH=/var/lib/boost-gateway-evidence/observability/smtp-proxy-install-summary.json
SOCKET_UNIT=boost-gateway-smtp-proxy.socket
DROP_IN_DIR="/etc/systemd/system/${SOCKET_UNIT}.d"

fail() {
  printf 'SMTP CONNECT relay: FAIL: %s\n' "$*" >&2
  exit 1
}

split_address() {
  local value=$1
  local label=$2
  local host=${value%:*}
  local port=${value##*:}
  [[ ${host} != "${value}" && ${host} =~ ^[A-Za-z0-9.-]+$ ]] || \
    fail "${label} must use host:port without credentials"
  [[ ${port} =~ ^[0-9]+$ && ${port} -ge 1 && ${port} -le 65535 ]] || \
    fail "${label} port is invalid"
  printf '%s\n%s\n' "${host}" "${port}"
}

[[ ${EUID} -eq 0 ]] || fail "run with sudo"
for command in awk chown chmod date dirname docker getent install mktemp mv nc openssl python3 sha256sum sort systemctl timeout; do
  command -v "${command}" >/dev/null 2>&1 || fail "required command is missing: ${command}"
done
getent group boost-gateway >/dev/null || fail "boost-gateway group is missing"
systemctl is-active --quiet mihomo.service || fail "mihomo.service is not active"

mapfile -t proxy_parts < <(split_address "${PROXY_ADDRESS}" "CONNECT proxy")
mapfile -t smtp_parts < <(split_address "${SMTP_ADDRESS}" "SMTP upstream")
PROXY_HOST=${proxy_parts[0]}
PROXY_PORT=${proxy_parts[1]}
SMTP_HOST=${smtp_parts[0]}
SMTP_PORT=${smtp_parts[1]}
[[ ${LISTEN_PORT} =~ ^[0-9]+$ && ${LISTEN_PORT} -ge 1024 && ${LISTEN_PORT} -le 65535 ]] || \
  fail "relay listen port must be between 1024 and 65535"

mapfile -t gateways < <(
  docker inspect --format '{{range .NetworkSettings.Networks}}{{println .Gateway}}{{end}}' \
    "${CONTAINER}" | awk 'NF' | sort -u
)
[[ ${#gateways[@]} -eq 1 ]] || fail "Alertmanager must have exactly one Docker network gateway"
RELAY_HOST=${gateways[0]}
python3 - "${RELAY_HOST}" <<'PY'
import ipaddress
import sys

value = ipaddress.ip_address(sys.argv[1])
if value.version != 4 or not value.is_private or value.is_loopback:
    raise SystemExit("Docker gateway must be a private non-loopback IPv4 address")
PY

timeout 30 openssl s_client \
  -proxy "${PROXY_HOST}:${PROXY_PORT}" \
  -starttls smtp \
  -connect "${SMTP_HOST}:${SMTP_PORT}" \
  -servername "${SMTP_HOST}" \
  -brief </dev/null >/dev/null 2>&1 || fail "CONNECT proxy cannot reach the SMTP upstream"

install -d -o root -g boost-gateway -m 0750 "${CONFIG_DIR}" "$(dirname "${SUMMARY_PATH}")"
ENV_TEMP=$(mktemp "${ENV_PATH}.XXXXXX")
{
  printf 'PROXY_HOST=%s\n' "${PROXY_HOST}"
  printf 'PROXY_PORT=%s\n' "${PROXY_PORT}"
  printf 'SMTP_HOST=%s\n' "${SMTP_HOST}"
  printf 'SMTP_PORT=%s\n' "${SMTP_PORT}"
  printf 'RELAY_HOST=%s\n' "${RELAY_HOST}"
  printf 'RELAY_PORT=%s\n' "${LISTEN_PORT}"
} >"${ENV_TEMP}"
chown root:root "${ENV_TEMP}"
chmod 0644 "${ENV_TEMP}"
mv "${ENV_TEMP}" "${ENV_PATH}"

install -o root -g root -m 0644 \
  "${ROOT}/deploy/systemd/boost-gateway-smtp-proxy.socket" \
  "${ROOT}/deploy/systemd/boost-gateway-smtp-proxy@.service" \
  /etc/systemd/system/
install -d -o root -g root -m 0755 "${DROP_IN_DIR}"
DROP_IN_TEMP=$(mktemp "${DROP_IN_DIR}/10-production-bridge.conf.XXXXXX")
{
  printf '[Socket]\n'
  printf 'ListenStream=\n'
  printf 'ListenStream=%s:%s\n' "${RELAY_HOST}" "${LISTEN_PORT}"
} >"${DROP_IN_TEMP}"
chown root:root "${DROP_IN_TEMP}"
chmod 0644 "${DROP_IN_TEMP}"
mv "${DROP_IN_TEMP}" "${DROP_IN_DIR}/10-production-bridge.conf"

systemctl daemon-reload
systemctl enable --now "${SOCKET_UNIT}"
systemctl is-active --quiet "${SOCKET_UNIT}" || fail "relay socket did not become active"
timeout 30 openssl s_client \
  -starttls smtp \
  -connect "${RELAY_HOST}:${LISTEN_PORT}" \
  -servername "${SMTP_HOST}" \
  -brief </dev/null >/dev/null 2>&1 || fail "installed relay cannot reach the SMTP upstream"

export PROXY_HOST PROXY_PORT SMTP_HOST SMTP_PORT RELAY_HOST LISTEN_PORT
export SOCKET_SHA256 SERVICE_SHA256 GENERATED_AT
SOCKET_SHA256=$(sha256sum "${ROOT}/deploy/systemd/boost-gateway-smtp-proxy.socket" | awk '{print $1}')
SERVICE_SHA256=$(sha256sum "${ROOT}/deploy/systemd/boost-gateway-smtp-proxy@.service" | awk '{print $1}')
GENERATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
SUMMARY_TEMP=$(mktemp "${SUMMARY_PATH}.XXXXXX")
python3 - "${SUMMARY_TEMP}" <<'PY'
import json
import os
import sys

value = {
    "schema_version": 1,
    "generated_at": os.environ["GENERATED_AT"],
    "overall_pass": True,
    "proxy": {
        "host": os.environ["PROXY_HOST"],
        "port": int(os.environ["PROXY_PORT"]),
        "protocol": "http-connect",
    },
    "relay": {
        "host": os.environ["RELAY_HOST"],
        "port": int(os.environ["LISTEN_PORT"]),
        "scope": "production-docker-bridge",
    },
    "smtp_upstream": {
        "host": os.environ["SMTP_HOST"],
        "port": int(os.environ["SMTP_PORT"]),
        "starttls_verified": True,
    },
    "unit_sha256": {
        "socket": os.environ["SOCKET_SHA256"],
        "service": os.environ["SERVICE_SHA256"],
    },
    "secret_material_recorded": False,
}
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(value, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
chown root:boost-gateway "${SUMMARY_TEMP}"
chmod 0640 "${SUMMARY_TEMP}"
mv "${SUMMARY_TEMP}" "${SUMMARY_PATH}"
printf 'SMTP CONNECT relay: PASS relay=%s:%s upstream=%s:%s\n' \
  "${RELAY_HOST}" "${LISTEN_PORT}" "${SMTP_HOST}" "${SMTP_PORT}"
printf 'summary: %s\n' "${SUMMARY_PATH}"
