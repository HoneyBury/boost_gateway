#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG_DIR=/etc/boost-gateway
CONFIG_PATH=${CONFIG_DIR}/alertmanager.yml
SECRET_DIR=${CONFIG_DIR}/alertmanager-secrets
PASSWORD_PATH=${SECRET_DIR}/gmail-app-password
COMPOSE_ENV=${CONFIG_DIR}/compose.env
EVIDENCE_DIR=/var/lib/boost-gateway-evidence/observability
ATTESTATION=${EVIDENCE_DIR}/alert-delivery-attestation.json
DRILL_CONTAINER=boost-alertmanager-email-drill
ALERTMANAGER_IMAGE=prom/alertmanager:v0.28.1

fail() {
  printf 'Gmail Alertmanager configuration: FAIL: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"
}

cleanup() {
  docker rm -f "${DRILL_CONTAINER}" >/dev/null 2>&1 || true
}

write_compose_value() {
  local key=$1
  local value=$2
  local temporary
  temporary="$(mktemp "${COMPOSE_ENV}.XXXXXX")"
  awk -F= -v key="${key}" '$1 != key {print}' "${COMPOSE_ENV}" > "${temporary}"
  printf '%s=%s\n' "${key}" "${value}" >> "${temporary}"
  chown root:boost-gateway "${temporary}"
  chmod 0640 "${temporary}"
  mv "${temporary}" "${COMPOSE_ENV}"
}

if [[ ${EUID} -ne 0 ]]; then
  fail "run with sudo"
fi
for command in awk cat curl date docker getent grep mktemp openssl python3 seq sha256sum tr; do
  require_command "${command}"
done
getent group boost-gateway >/dev/null || fail "boost-gateway group is missing"
GROUP_ID="$(getent group boost-gateway | awk -F: '{print $3}')"
[[ ${GROUP_ID} =~ ^[0-9]+$ ]] || fail "boost-gateway group ID is invalid"

read -r -p 'Gmail sender address: ' GMAIL_ADDRESS
read -r -p 'QQ recipient address: ' QQ_ADDRESS
[[ ${GMAIL_ADDRESS} =~ ^[A-Za-z0-9._%+-]+@gmail\.com$ ]] || fail "sender must be a Gmail address"
[[ ${QQ_ADDRESS} =~ ^[A-Za-z0-9._%+-]+@qq\.com$ ]] || fail "recipient must be a QQ Mail address"

read -r -s -p 'Gmail 16-character app password: ' RAW_APP_PASSWORD
printf '\n'
APP_PASSWORD="$(printf '%s' "${RAW_APP_PASSWORD}" | tr -d '[:space:]')"
unset RAW_APP_PASSWORD
[[ ${#APP_PASSWORD} -eq 16 && ${APP_PASSWORD} =~ ^[A-Za-z0-9]+$ ]] || \
  fail "app password must contain 16 letters or digits"

install -d -o root -g boost-gateway -m 0750 \
  "${CONFIG_DIR}" "${SECRET_DIR}" "${EVIDENCE_DIR}"
umask 0027
PASSWORD_TEMP="$(mktemp "${PASSWORD_PATH}.XXXXXX")"
printf '%s\n' "${APP_PASSWORD}" > "${PASSWORD_TEMP}"
unset APP_PASSWORD
chown root:boost-gateway "${PASSWORD_TEMP}"
chmod 0640 "${PASSWORD_TEMP}"
mv "${PASSWORD_TEMP}" "${PASSWORD_PATH}"

CONFIG_TEMP="$(mktemp "${CONFIG_PATH}.XXXXXX")"
cat > "${CONFIG_TEMP}" <<EOF
global:
  resolve_timeout: 5m
  smtp_smarthost: smtp.gmail.com:587
  smtp_from: '${GMAIL_ADDRESS}'
  smtp_auth_username: '${GMAIL_ADDRESS}'
  smtp_auth_password_file: /etc/alertmanager/secrets/gmail-app-password
  smtp_require_tls: true

route:
  receiver: operations-email
  group_by:
    - alertname
    - component
    - drill_id
  group_wait: 5s
  group_interval: 1m
  repeat_interval: 4h

receivers:
  - name: operations-email
    email_configs:
      - to: '${QQ_ADDRESS}'
        send_resolved: true
        headers:
          Subject: '[Boost Gateway] {{ .Status | toUpper }} {{ .CommonLabels.alertname }} {{ .CommonLabels.drill_id }}'

inhibit_rules:
  - source_matchers:
      - severity="critical"
    target_matchers:
      - severity="warning"
    equal:
      - alertname
      - component
EOF
chown root:boost-gateway "${CONFIG_TEMP}"
chmod 0640 "${CONFIG_TEMP}"
mv "${CONFIG_TEMP}" "${CONFIG_PATH}"

if [[ ! -e ${COMPOSE_ENV} ]]; then
  : > "${COMPOSE_ENV}"
fi
if ! grep -q '^GRAFANA_ADMIN_USER=' "${COMPOSE_ENV}"; then
  write_compose_value GRAFANA_ADMIN_USER boost-gateway-operator
fi
if ! grep -q '^GRAFANA_ADMIN_PASSWORD=' "${COMPOSE_ENV}"; then
  write_compose_value GRAFANA_ADMIN_PASSWORD "$(openssl rand -hex 32)"
fi
write_compose_value BOOST_GATEWAY_GID "${GROUP_ID}"

docker run --rm --pull never --network none --read-only --cap-drop ALL \
  --user "65534:${GROUP_ID}" \
  --volume "${CONFIG_PATH}:/etc/alertmanager/alertmanager.yml:ro" \
  --volume "${SECRET_DIR}:/etc/alertmanager/secrets:ro" \
  --entrypoint /bin/amtool "${ALERTMANAGER_IMAGE}" \
  check-config /etc/alertmanager/alertmanager.yml

trap cleanup EXIT INT TERM
cleanup
docker run -d --name "${DRILL_CONTAINER}" --pull never \
  --network bridge --publish 127.0.0.1:19093:9093 \
  --read-only --cap-drop ALL --user "65534:${GROUP_ID}" \
  --volume "${CONFIG_PATH}:/etc/alertmanager/alertmanager.yml:ro" \
  --volume "${SECRET_DIR}:/etc/alertmanager/secrets:ro" \
  --tmpfs "/alertmanager:rw,noexec,nosuid,size=16m,uid=65534,gid=${GROUP_ID}" \
  --entrypoint /bin/alertmanager "${ALERTMANAGER_IMAGE}" \
  --config.file=/etc/alertmanager/alertmanager.yml \
  --storage.path=/alertmanager >/dev/null

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:19093/-/ready >/dev/null; then
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:19093/-/ready >/dev/null || \
  fail "temporary Alertmanager did not become ready"

DRILL_ID="todo0011-$(date -u +%Y%m%dT%H%M%SZ)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
curl -fsS -H 'Content-Type: application/json' \
  --data "[{\"labels\":{\"alertname\":\"TODO0011EmailDeliveryDrill\",\"component\":\"observability\",\"severity\":\"warning\",\"drill_id\":\"${DRILL_ID}\"},\"annotations\":{\"summary\":\"Boost Gateway firing email delivery drill\"},\"startsAt\":\"${STARTED_AT}\"}]" \
  http://127.0.0.1:19093/api/v2/alerts >/dev/null

printf 'Firing alert submitted: %s\n' "${DRILL_ID}"
printf 'Wait for the FIRING email in QQ Mail. Open its raw source and locate Message-ID.\n'
if ! read -r -t 600 -p 'FIRING email Message-ID: ' FIRING_ID; then
  docker logs --tail 100 "${DRILL_CONTAINER}" >&2 || true
  fail "timed out waiting for the firing email confirmation"
fi
[[ -n ${FIRING_ID} && ${FIRING_ID} != *$'\n'* && ${FIRING_ID} != *$'\r'* ]] || \
  fail "firing Message-ID is invalid"
FIRING_OBSERVED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

RESOLVED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
curl -fsS -H 'Content-Type: application/json' \
  --data "[{\"labels\":{\"alertname\":\"TODO0011EmailDeliveryDrill\",\"component\":\"observability\",\"severity\":\"warning\",\"drill_id\":\"${DRILL_ID}\"},\"annotations\":{\"summary\":\"Boost Gateway resolved email delivery drill\"},\"startsAt\":\"${STARTED_AT}\",\"endsAt\":\"${RESOLVED_AT}\"}]" \
  http://127.0.0.1:19093/api/v2/alerts >/dev/null

printf 'Resolved alert submitted: %s\n' "${DRILL_ID}"
printf 'Wait for the RESOLVED email in QQ Mail and locate its Message-ID.\n'
if ! read -r -t 600 -p 'RESOLVED email Message-ID: ' RESOLVED_ID; then
  docker logs --tail 100 "${DRILL_CONTAINER}" >&2 || true
  fail "timed out waiting for the resolved email confirmation"
fi
[[ -n ${RESOLVED_ID} && ${RESOLVED_ID} != *$'\n'* && ${RESOLVED_ID} != *$'\r'* ]] || \
  fail "resolved Message-ID is invalid"
RESOLVED_OBSERVED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TESTED_AT="${RESOLVED_OBSERVED_AT}"
CONFIG_SHA256="$(sha256sum "${CONFIG_PATH}" | awk '{print $1}')"
HOST_ID_SHA256="$(sha256sum /etc/machine-id | awk '{print $1}')"

ATTESTATION_TEMP="$(mktemp "${ATTESTATION}.XXXXXX")"
export DRILL_ID CONFIG_SHA256 HOST_ID_SHA256 TESTED_AT
export FIRING_ID FIRING_OBSERVED_AT RESOLVED_ID RESOLVED_OBSERVED_AT
python3 - "${ATTESTATION_TEMP}" <<'PY'
import json
import os
import sys

value = {
    "schema_version": 1,
    "overall_pass": True,
    "receiver": "operations-email",
    "drill_id": os.environ["DRILL_ID"],
    "alertmanager_config_sha256": os.environ["CONFIG_SHA256"],
    "host_id_sha256": os.environ["HOST_ID_SHA256"],
    "tested_at": os.environ["TESTED_AT"],
    "firing_delivery": {
        "id": os.environ["FIRING_ID"],
        "observed_at": os.environ["FIRING_OBSERVED_AT"],
    },
    "resolved_delivery": {
        "id": os.environ["RESOLVED_ID"],
        "observed_at": os.environ["RESOLVED_OBSERVED_AT"],
    },
}
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(value, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
unset FIRING_ID RESOLVED_ID
chown root:boost-gateway "${ATTESTATION_TEMP}"
chmod 0640 "${ATTESTATION_TEMP}"
mv "${ATTESTATION_TEMP}" "${ATTESTATION}"

python3 "${ROOT}/scripts/tools/check_observability_preflight.py"
printf 'Gmail Alertmanager configuration: PASS drill_id=%s\n' "${DRILL_ID}"
