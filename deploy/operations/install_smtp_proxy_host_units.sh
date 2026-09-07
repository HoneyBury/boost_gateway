#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROXY_ADDRESS="${BOOST_GATEWAY_CONNECT_PROXY:-127.0.0.1:7890}"
SMTP_ADDRESS="${BOOST_GATEWAY_SMTP_UPSTREAM:-smtp.gmail.com:587}"
LISTEN_PORT="${BOOST_GATEWAY_SMTP_RELAY_PORT:-1587}"
CONTAINER="${BOOST_GATEWAY_ALERTMANAGER_CONTAINER:-boost-alertmanager}"
ADDITIONAL_CLIENTS="${BOOST_GATEWAY_SMTP_RELAY_ADDITIONAL_CLIENTS:-}"
CONFIG_DIR=/etc/boost-gateway
ENV_PATH="${CONFIG_DIR}/smtp-proxy.env"
SUMMARY_PATH=/var/lib/boost-gateway-evidence/observability/smtp-proxy-install-summary.json
SOCKET_UNIT=boost-gateway-smtp-proxy.socket
HEALTH_SERVICE=boost-gateway-smtp-proxy-health.service
HEALTH_TIMER=boost-gateway-smtp-proxy-health.timer
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
for command in awk chown chmod date dirname docker getent install ip mktemp mv nc openssl python3 sha256sum sort systemctl timeout ufw; do
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

CLIENT_CONTAINERS=("${CONTAINER}")
CLIENT_PORTS=("${LISTEN_PORT}")
if [[ -n ${ADDITIONAL_CLIENTS} ]]; then
  IFS=',' read -r -a additional_specs <<<"${ADDITIONAL_CLIENTS}"
  for spec in "${additional_specs[@]}"; do
    [[ ${spec} =~ ^([A-Za-z0-9][A-Za-z0-9_.-]*):([0-9]+)$ ]] || \
      fail "additional clients must use container:port entries"
    additional_port=${BASH_REMATCH[2]}
    [[ ${additional_port} -ge 1 && ${additional_port} -le 65535 ]] || \
      fail "additional client port is invalid: ${additional_port}"
    CLIENT_CONTAINERS+=("${BASH_REMATCH[1]}")
    CLIENT_PORTS+=("${additional_port}")
  done
fi

RELAY_HOSTS=()
NETWORK_IDS=()
NETWORK_SUBNETS=()
BRIDGE_NAMES=()
for index in "${!CLIENT_CONTAINERS[@]}"; do
  client=${CLIENT_CONTAINERS[index]}
  mapfile -t gateways < <(
    docker inspect --format '{{range .NetworkSettings.Networks}}{{println .Gateway}}{{end}}' \
      "${client}" | awk 'NF' | sort -u
  )
  [[ ${#gateways[@]} -eq 1 ]] || fail "${client} must have exactly one Docker network gateway"
  relay_host=${gateways[0]}
  mapfile -t network_ids < <(
    docker inspect --format '{{range .NetworkSettings.Networks}}{{println .NetworkID}}{{end}}' \
      "${client}" | awk 'NF' | sort -u
  )
  [[ ${#network_ids[@]} -eq 1 && ${network_ids[0]} =~ ^[0-9a-f]{64}$ ]] || \
    fail "${client} must have exactly one valid Docker network ID"
  network_id=${network_ids[0]}
  bridge_name=$(docker network inspect \
    --format '{{index .Options "com.docker.network.bridge.name"}}' "${network_id}")
  if [[ -z ${bridge_name} || ${bridge_name} == '<no value>' ]]; then
    bridge_name="br-${network_id:0:12}"
  fi
  [[ ${bridge_name} =~ ^[A-Za-z0-9_.-]+$ ]] || fail "Docker bridge name is invalid"
  ip link show "${bridge_name}" >/dev/null 2>&1 || fail "Docker bridge interface is missing"
  mapfile -t network_subnets < <(
    docker network inspect --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' \
      "${network_id}" | awk 'NF' | sort -u
  )
  [[ ${#network_subnets[@]} -eq 1 ]] || fail "${client} network must have exactly one subnet"
  network_subnet=${network_subnets[0]}
  python3 - "${relay_host}" "${network_subnet}" <<'PY'
import ipaddress
import sys

value = ipaddress.ip_address(sys.argv[1])
network = ipaddress.ip_network(sys.argv[2])
if (
    value.version != 4
    or not value.is_private
    or value.is_loopback
    or network.version != 4
    or not network.is_private
    or value not in network
):
    raise SystemExit("Docker gateway must be a private non-loopback IPv4 address")
PY
  endpoint="${relay_host}:${CLIENT_PORTS[index]}"
  for existing_index in "${!RELAY_HOSTS[@]}"; do
    [[ ${RELAY_HOSTS[existing_index]}:${CLIENT_PORTS[existing_index]} != "${endpoint}" ]] || \
      fail "duplicate relay endpoint: ${endpoint}"
  done
  RELAY_HOSTS+=("${relay_host}")
  NETWORK_IDS+=("${network_id}")
  NETWORK_SUBNETS+=("${network_subnet}")
  BRIDGE_NAMES+=("${bridge_name}")
done
RELAY_HOST=${RELAY_HOSTS[0]}
NETWORK_ID=${NETWORK_IDS[0]}
NETWORK_SUBNET=${NETWORK_SUBNETS[0]}
BRIDGE_NAME=${BRIDGE_NAMES[0]}

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
  "${ROOT}/deploy/systemd/${HEALTH_SERVICE}" \
  "${ROOT}/deploy/systemd/${HEALTH_TIMER}" \
  /etc/systemd/system/
install -d -o root -g root -m 0755 "${DROP_IN_DIR}"
DROP_IN_TEMP=$(mktemp "${DROP_IN_DIR}/10-production-bridge.conf.XXXXXX")
{
  printf '[Socket]\n'
  printf 'ListenStream=\n'
  printf 'FreeBind=true\n'
  for index in "${!RELAY_HOSTS[@]}"; do
    printf 'ListenStream=%s:%s\n' "${RELAY_HOSTS[index]}" "${CLIENT_PORTS[index]}"
  done
} >"${DROP_IN_TEMP}"
chown root:root "${DROP_IN_TEMP}"
chmod 0644 "${DROP_IN_TEMP}"
mv "${DROP_IN_TEMP}" "${DROP_IN_DIR}/10-production-bridge.conf"

systemctl is-active --quiet ufw.service || fail "ufw.service is not active"
for index in "${!RELAY_HOSTS[@]}"; do
  ufw allow in on "${BRIDGE_NAMES[index]}" \
    from "${NETWORK_SUBNETS[index]}" to "${RELAY_HOSTS[index]}" \
    port "${CLIENT_PORTS[index]}" proto tcp \
    comment 'BoostGateway SMTP relay' >/dev/null
done
systemctl daemon-reload
systemctl reset-failed "${SOCKET_UNIT}" >/dev/null 2>&1 || true
systemctl enable --now "${SOCKET_UNIT}" "${HEALTH_TIMER}"
systemctl is-active --quiet "${SOCKET_UNIT}" || fail "relay socket did not become active"
systemctl is-active --quiet "${HEALTH_TIMER}" || fail "relay health timer did not become active"
for index in "${!RELAY_HOSTS[@]}"; do
  timeout 30 openssl s_client \
    -starttls smtp \
    -connect "${RELAY_HOSTS[index]}:${CLIENT_PORTS[index]}" \
    -servername "${SMTP_HOST}" \
    -brief </dev/null >/dev/null 2>&1 || \
    fail "installed relay cannot serve ${CLIENT_CONTAINERS[index]}"
done

export PROXY_HOST PROXY_PORT SMTP_HOST SMTP_PORT RELAY_HOST LISTEN_PORT
export NETWORK_ID NETWORK_SUBNET BRIDGE_NAME
export SOCKET_SHA256 SERVICE_SHA256 HEALTH_SERVICE_SHA256 HEALTH_TIMER_SHA256
export GENERATED_AT CLIENT_DESCRIPTORS
SOCKET_SHA256=$(sha256sum "${ROOT}/deploy/systemd/boost-gateway-smtp-proxy.socket" | awk '{print $1}')
SERVICE_SHA256=$(sha256sum "${ROOT}/deploy/systemd/boost-gateway-smtp-proxy@.service" | awk '{print $1}')
HEALTH_SERVICE_SHA256=$(sha256sum "${ROOT}/deploy/systemd/${HEALTH_SERVICE}" | awk '{print $1}')
HEALTH_TIMER_SHA256=$(sha256sum "${ROOT}/deploy/systemd/${HEALTH_TIMER}" | awk '{print $1}')
CLIENT_DESCRIPTORS=""
for index in "${!RELAY_HOSTS[@]}"; do
  descriptor="${CLIENT_CONTAINERS[index]}|${RELAY_HOSTS[index]}|${CLIENT_PORTS[index]}|${NETWORK_IDS[index]}|${NETWORK_SUBNETS[index]}|${BRIDGE_NAMES[index]}"
  CLIENT_DESCRIPTORS+="${CLIENT_DESCRIPTORS:+;}${descriptor}"
done
GENERATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
SUMMARY_TEMP=$(mktemp "${SUMMARY_PATH}.XXXXXX")
python3 - "${SUMMARY_TEMP}" <<'PY'
import json
import os
import sys

value = {
    "schema_version": 2,
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
    "firewall": {
        "interface": os.environ["BRIDGE_NAME"],
        "source_subnet": os.environ["NETWORK_SUBNET"],
        "destination": os.environ["RELAY_HOST"],
        "port": int(os.environ["LISTEN_PORT"]),
        "protocol": "tcp",
        "ufw_active": True,
    },
    "smtp_upstream": {
        "host": os.environ["SMTP_HOST"],
        "port": int(os.environ["SMTP_PORT"]),
        "starttls_verified": True,
    },
    "unit_sha256": {
        "health_service": os.environ["HEALTH_SERVICE_SHA256"],
        "health_timer": os.environ["HEALTH_TIMER_SHA256"],
        "socket": os.environ["SOCKET_SHA256"],
        "service": os.environ["SERVICE_SHA256"],
    },
    "secret_material_recorded": False,
}
value["clients"] = [
    {
        "container": fields[0],
        "host": fields[1],
        "port": int(fields[2]),
        "network_id": fields[3],
        "subnet": fields[4],
        "bridge": fields[5],
        "starttls_verified": True,
    }
    for descriptor in os.environ["CLIENT_DESCRIPTORS"].split(";")
    for fields in [descriptor.split("|")]
]
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(value, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
chown root:boost-gateway "${SUMMARY_TEMP}"
chmod 0640 "${SUMMARY_TEMP}"
mv "${SUMMARY_TEMP}" "${SUMMARY_PATH}"
printf 'SMTP CONNECT relay: PASS clients=%s upstream=%s:%s\n' \
  "${#CLIENT_CONTAINERS[@]}" "${SMTP_HOST}" "${SMTP_PORT}"
printf 'summary: %s\n' "${SUMMARY_PATH}"
