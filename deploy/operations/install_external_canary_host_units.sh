#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN=/opt/boost-gateway-canary/venv/bin/python
ENVIRONMENT_FILE=""
DEPLOYMENT_RECORD=""
RUN_NOW=false

fail() {
  printf 'external canary host units install: FAIL: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment-file)
      [[ $# -ge 2 ]] || fail '--environment-file requires a value'
      ENVIRONMENT_FILE=$2
      shift 2
      ;;
    --deployment-record)
      [[ $# -ge 2 ]] || fail '--deployment-record requires a value'
      DEPLOYMENT_RECORD=$2
      shift 2
      ;;
    --run-now)
      RUN_NOW=true
      shift
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'run with sudo on the external canary host'
[[ -n ${ENVIRONMENT_FILE} && -f ${ENVIRONMENT_FILE} && ! -L ${ENVIRONMENT_FILE} ]] \
  || fail '--environment-file must name a regular non-symlink file'
[[ -n ${DEPLOYMENT_RECORD} && -f ${DEPLOYMENT_RECORD} && ! -L ${DEPLOYMENT_RECORD} ]] \
  || fail '--deployment-record must name the exported immutable deployment record'
[[ $(stat -c '%u:%a' "${ENVIRONMENT_FILE}") == 0:600 ]] \
  || fail 'environment file must be root-owned mode 0600'
[[ -x ${PYTHON_BIN} ]] \
  || fail 'released SDK venv is missing: /opt/boost-gateway-canary/venv/bin/python'

if ! getent group boost-gateway-canary >/dev/null; then
  groupadd --system boost-gateway-canary
fi
if ! getent passwd boost-gateway-canary >/dev/null; then
  useradd --system --gid boost-gateway-canary --home-dir /var/lib/boost-gateway-canary \
    --shell /usr/sbin/nologin boost-gateway-canary
fi

install -d -o root -g boost-gateway-canary -m 0750 \
  /etc/boost-gateway-canary \
  /usr/local/libexec/boost-gateway-canary
install -d -o boost-gateway-canary -g boost-gateway-canary -m 0750 \
  /var/lib/boost-gateway-canary \
  /var/lib/boost-gateway-canary/samples \
  /var/lib/boost-gateway-canary/incidents \
  /var/lib/boost-gateway-canary/aggregates
install -o root -g boost-gateway-canary -m 0750 \
  "${ROOT}/scripts/tools/external_business_canary.py" \
  /usr/local/libexec/boost-gateway-canary/external_business_canary.py
install -o root -g boost-gateway-canary -m 0640 \
  "${ENVIRONMENT_FILE}" /etc/boost-gateway-canary/environment
install -o root -g boost-gateway-canary -m 0640 \
  "${DEPLOYMENT_RECORD}" /etc/boost-gateway-canary/deployment-record.json
install -o root -g root -m 0644 \
  "${ROOT}/deploy/systemd/boost-gateway-external-canary@.service" \
  "${ROOT}/deploy/systemd/boost-gateway-external-canary.timer" \
  "${ROOT}/deploy/systemd/boost-gateway-external-canary-watchdog.timer" \
  /etc/systemd/system/

runuser -u boost-gateway-canary -- "${PYTHON_BIN}" -c \
  'import boost_gateway_sdk as sdk; sdk.assert_compatible_version()' \
  || fail 'released BoostGateway Python SDK 4.2.0 is not importable by the canary user'
systemctl daemon-reload
systemctl start boost-gateway-external-canary@validate.service
[[ $(systemctl show boost-gateway-external-canary@validate.service --property=Result --value) == success ]] \
  || fail 'canary environment, candidate record or released SDK validation failed'
systemctl enable --now \
  boost-gateway-external-canary.timer \
  boost-gateway-external-canary-watchdog.timer
if [[ ${RUN_NOW} == true ]]; then
  systemctl start boost-gateway-external-canary@run.service
  [[ $(systemctl show boost-gateway-external-canary@run.service --property=Result --value) == success ]] \
    || fail 'initial external canary did not pass'
fi
systemctl is-active --quiet boost-gateway-external-canary.timer \
  || fail 'canary timer is not active'
systemctl is-active --quiet boost-gateway-external-canary-watchdog.timer \
  || fail 'canary watchdog timer is not active'

printf 'external canary host units install: PASS\n'
printf 'timer=boost-gateway-external-canary.timer\n'
printf 'watchdog_timer=boost-gateway-external-canary-watchdog.timer\n'
printf 'initial_canary_run=%s\n' "${RUN_NOW}"
