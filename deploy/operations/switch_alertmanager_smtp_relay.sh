#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_PATH=/etc/boost-gateway/alertmanager.yml
RELAY_ENV=/etc/boost-gateway/smtp-proxy.env
CURRENT_ROOT=/opt/boost-gateway/current
COMPOSE_FILE=${CURRENT_ROOT}/deploy/operations/docker-compose.production.yml
SUMMARY_PATH=/var/lib/boost-gateway-evidence/observability/smtp-relay-activation-summary.json
ALERTMANAGER_IMAGE=prom/alertmanager:v0.28.1

fail() {
  printf 'Alertmanager SMTP relay activation: FAIL: %s\n' "$*" >&2
  exit 1
}

compose_alertmanager() {
  docker compose \
    --env-file /etc/boost-gateway/compose-images.env \
    --env-file /etc/boost-gateway/compose.env \
    -f "${COMPOSE_FILE}" \
    up -d --no-build --no-deps --force-recreate --wait --wait-timeout 120 alertmanager
}

rollback() {
  local status=$?
  trap - ERR INT TERM
  if [[ ${CONFIG_REPLACED:-false} == true && -f ${ORIGINAL_CONFIG:-} ]]; then
    local restore_temp
    restore_temp=$(mktemp "${CONFIG_PATH}.rollback.XXXXXX")
    cp --preserve=mode,ownership "${ORIGINAL_CONFIG}" "${restore_temp}"
    mv "${restore_temp}" "${CONFIG_PATH}"
    compose_alertmanager >/dev/null 2>&1 || true
  fi
  rm -f "${CONFIG_TEMP:-}" "${ORIGINAL_CONFIG:-}"
  exit "${status}"
}

read_env_value() {
  local key=$1
  local value
  value=$(sed -n "s/^${key}=//p" "${RELAY_ENV}")
  [[ -n ${value} && ${value} != *$'\n'* ]] || fail "${key} is missing from relay environment"
  printf '%s\n' "${value}"
}

[[ ${EUID} -eq 0 ]] || fail "run with sudo"
for command in awk chown chmod cp curl date docker getent mktemp mv python3 rm sed sha256sum; do
  command -v "${command}" >/dev/null 2>&1 || fail "required command is missing: ${command}"
done
[[ -f ${CONFIG_PATH} && ! -L ${CONFIG_PATH} ]] || fail "Alertmanager config must be a regular file"
[[ -f ${RELAY_ENV} && ! -L ${RELAY_ENV} ]] || fail "relay environment must be a regular file"
[[ -f ${COMPOSE_FILE} && ! -L ${COMPOSE_FILE} ]] || fail "active production Compose file is missing"

RELAY_HOST=$(read_env_value RELAY_HOST)
RELAY_PORT=$(read_env_value RELAY_PORT)
RELAY_ADDRESS="${RELAY_HOST}:${RELAY_PORT}"
BEFORE_SHA256=$(sha256sum "${CONFIG_PATH}" | awk '{print $1}')
ORIGINAL_CONFIG=$(mktemp "${CONFIG_PATH}.original.XXXXXX")
cp --preserve=mode,ownership "${CONFIG_PATH}" "${ORIGINAL_CONFIG}"
CONFIG_REPLACED=false
trap rollback ERR INT TERM
CONFIG_TEMP=$(mktemp "${CONFIG_PATH}.XXXXXX")
python3 - "${CONFIG_PATH}" "${CONFIG_TEMP}" "${RELAY_ADDRESS}" <<'PY'
import sys

source, destination, relay = sys.argv[1:]
text = open(source, encoding="utf-8").read()
direct = "smtp_smarthost: smtp.gmail.com:587"
target = f"smtp_smarthost: {relay}"
if text.count(direct) == 1 and target not in text:
    text = text.replace(direct, target)
elif text.count(target) != 1 or direct in text:
    raise SystemExit("Alertmanager config has an unexpected SMTP smarthost contract")
with open(destination, "w", encoding="utf-8") as stream:
    stream.write(text)
PY
chown root:boost-gateway "${CONFIG_TEMP}"
chmod 0640 "${CONFIG_TEMP}"

GROUP_ID=$(getent group boost-gateway | awk -F: '{print $3}')
docker run --rm --pull never --network none --read-only --cap-drop ALL \
  --user "65534:${GROUP_ID}" \
  --volume "${CONFIG_TEMP}:/etc/alertmanager/alertmanager.yml:ro" \
  --volume /etc/boost-gateway/alertmanager-secrets:/etc/alertmanager/secrets:ro \
  --entrypoint /bin/amtool "${ALERTMANAGER_IMAGE}" \
  check-config /etc/alertmanager/alertmanager.yml >/dev/null
mv "${CONFIG_TEMP}" "${CONFIG_PATH}"
CONFIG_REPLACED=true
AFTER_SHA256=$(sha256sum "${CONFIG_PATH}" | awk '{print $1}')

compose_alertmanager
curl -fsS http://127.0.0.1:9093/-/ready >/dev/null || fail "Alertmanager is not ready after relay activation"
ACTIVE_CONFIG=$(curl -fsS http://127.0.0.1:9093/api/v2/status)
python3 - "${RELAY_ADDRESS}" "${ACTIVE_CONFIG}" <<'PY'
import json
import sys

relay = sys.argv[1]
status = json.loads(sys.argv[2])
active = status.get("config", {}).get("original", "")
if f"smtp_smarthost: {relay}" not in active:
    raise SystemExit("active Alertmanager config did not adopt the SMTP relay")
PY

export BEFORE_SHA256 AFTER_SHA256 RELAY_ADDRESS GENERATED_AT
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
    "alertmanager_config_sha256_before": os.environ["BEFORE_SHA256"],
    "alertmanager_config_sha256_after": os.environ["AFTER_SHA256"],
    "smtp_relay": os.environ["RELAY_ADDRESS"],
    "alertmanager_ready": True,
    "secret_material_recorded": False,
}
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(value, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
chown root:boost-gateway "${SUMMARY_TEMP}"
chmod 0640 "${SUMMARY_TEMP}"
mv "${SUMMARY_TEMP}" "${SUMMARY_PATH}"
rm -f "${ORIGINAL_CONFIG}"
trap - ERR INT TERM
printf 'Alertmanager SMTP relay activation: PASS relay=%s\n' "${RELAY_ADDRESS}"
printf 'summary: %s\n' "${SUMMARY_PATH}"
