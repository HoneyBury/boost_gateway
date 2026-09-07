#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVICE=boost-gateway-canary-alertmanager-forward.service
CONFIG_DIR=/etc/boost-gateway-canary
PARAMETER_FILE=${CONFIG_DIR}/alertmanager-forward.env
INSTALLED_IDENTITY=${CONFIG_DIR}/alertmanager-forward-ed25519
INSTALLED_KNOWN_HOSTS=${CONFIG_DIR}/alertmanager-forward-known-hosts
CANARY_CONFIG_GROUP=boost-gateway-canary
FORWARD_USER=boost-gateway-canary-forward
FORWARD_GROUP=boost-gateway-canary-forward
FORWARD_HOME=/var/lib/boost-gateway-canary-forward
TARGET_FORWARD_USER=boost-gateway-alert-forward
SSH_TARGET=""
IDENTITY_FILE=""
KNOWN_HOSTS_FILE=""
TEMP_PARAMETER=""

fail() {
  printf 'external canary Alertmanager forward install: FAIL: %s\n' "$*" >&2
  exit 1
}

install_secret() {
  local source=$1
  local destination=$2

  if [[ -e ${destination} && ${source} -ef ${destination} ]]; then
    chown root:root "${destination}"
    chmod 0600 "${destination}"
    return
  fi
  install -o root -g root -m 0600 "${source}" "${destination}"
}

cleanup() {
  if [[ -n ${TEMP_PARAMETER} && -e ${TEMP_PARAMETER} ]]; then
    rm -f -- "${TEMP_PARAMETER}"
  fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh-target)
      [[ $# -ge 2 ]] || fail '--ssh-target requires a value'
      SSH_TARGET=$2
      shift 2
      ;;
    --ssh-identity-file)
      [[ $# -ge 2 ]] || fail '--ssh-identity-file requires a value'
      IDENTITY_FILE=$2
      shift 2
      ;;
    --ssh-known-hosts)
      [[ $# -ge 2 ]] || fail '--ssh-known-hosts requires a value'
      KNOWN_HOSTS_FILE=$2
      shift 2
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] \
  || fail 'run with sudo on the external canary host'
[[ ${SSH_TARGET} =~ ^[A-Za-z_][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9.-]*$ ]] \
  || fail '--ssh-target must be a user and host without options or a port'
[[ ${SSH_TARGET%%@*} == "${TARGET_FORWARD_USER}" ]] \
  || fail "--ssh-target user must be ${TARGET_FORWARD_USER}"
[[ -n ${IDENTITY_FILE} && -f ${IDENTITY_FILE} && ! -L ${IDENTITY_FILE} ]] \
  || fail '--ssh-identity-file must name a regular non-symlink file'
[[ $(stat -c '%u:%a' "${IDENTITY_FILE}") == 0:600 ]] \
  || fail 'SSH identity must be root-owned mode 0600'
[[ -n ${KNOWN_HOSTS_FILE} && -f ${KNOWN_HOSTS_FILE} && ! -L ${KNOWN_HOSTS_FILE} ]] \
  || fail '--ssh-known-hosts must name a regular non-symlink file'
[[ -s ${KNOWN_HOSTS_FILE} ]] || fail 'SSH known_hosts file must not be empty'
[[ $(stat -c '%u:%a' "${KNOWN_HOSTS_FILE}") == 0:600 ]] \
  || fail 'SSH known_hosts must be root-owned mode 0600'
[[ -x /usr/bin/ssh && -x /usr/bin/ssh-keygen ]] \
  || fail 'OpenSSH client tools are required'
[[ -x /usr/bin/curl ]] || fail 'curl is required to verify the fixed forward'
IDENTITY_PUBLIC_KEY=$(
  /usr/bin/ssh-keygen -y -P '' -f "${IDENTITY_FILE}" 2>/dev/null
) || fail 'SSH identity must be a valid unencrypted private key'
[[ ${IDENTITY_PUBLIC_KEY} == "ssh-ed25519 "* ]] \
  || fail 'SSH identity must use Ed25519'
TARGET_HOST=${SSH_TARGET#*@}
/usr/bin/ssh-keygen -F "${TARGET_HOST}" -f "${KNOWN_HOSTS_FILE}" >/dev/null \
  || fail 'SSH known_hosts does not pin the requested target host'

if ! getent group "${CANARY_CONFIG_GROUP}" >/dev/null; then
  groupadd --system "${CANARY_CONFIG_GROUP}"
fi
CANARY_CONFIG_GROUP_ID=$(getent group "${CANARY_CONFIG_GROUP}" | cut -d: -f3)
[[ ${CANARY_CONFIG_GROUP_ID} != 0 ]] \
  || fail 'canary configuration group must not be root'
if ! getent group "${FORWARD_GROUP}" >/dev/null; then
  groupadd --system "${FORWARD_GROUP}"
fi
if ! getent passwd "${FORWARD_USER}" >/dev/null; then
  useradd --system --gid "${FORWARD_GROUP}" \
    --home-dir "${FORWARD_HOME}" --shell /usr/sbin/nologin \
    "${FORWARD_USER}"
fi
FORWARD_GROUP_ID=$(getent group "${FORWARD_GROUP}" | cut -d: -f3)
IFS=: read -r _ _ FORWARD_USER_ID FORWARD_PRIMARY_GROUP_ID _ \
  OBSERVED_FORWARD_HOME FORWARD_SHELL < <(getent passwd "${FORWARD_USER}")
[[ ${FORWARD_USER_ID} != 0 && ${FORWARD_GROUP_ID} != 0 ]] \
  || fail 'forward service account and group must not be root'
[[ ${FORWARD_PRIMARY_GROUP_ID} == "${FORWARD_GROUP_ID}" ]] \
  || fail 'forward service account must use its dedicated primary group'
[[ ${OBSERVED_FORWARD_HOME} == "${FORWARD_HOME}" ]] \
  || fail 'forward service account has an unexpected home directory'
[[ ${FORWARD_SHELL} == /usr/sbin/nologin ]] \
  || fail 'forward service account must use /usr/sbin/nologin'
[[ $(id -Gn "${FORWARD_USER}") == "${FORWARD_GROUP}" ]] \
  || fail 'forward service account must not belong to supplementary groups'

install -d -o root -g "${CANARY_CONFIG_GROUP}" -m 0750 "${CONFIG_DIR}"
install -d -o "${FORWARD_USER}" -g "${FORWARD_GROUP}" -m 0750 \
  "${FORWARD_HOME}"
install_secret "${IDENTITY_FILE}" "${INSTALLED_IDENTITY}"
install_secret "${KNOWN_HOSTS_FILE}" "${INSTALLED_KNOWN_HOSTS}"

umask 077
TEMP_PARAMETER=$(mktemp "${CONFIG_DIR}/.alertmanager-forward.env.XXXXXX")
printf 'BOOST_GATEWAY_CANARY_FORWARD_TARGET=%s\n' "${SSH_TARGET}" \
  >"${TEMP_PARAMETER}"
chown root:root "${TEMP_PARAMETER}"
chmod 0600 "${TEMP_PARAMETER}"
mv -f -- "${TEMP_PARAMETER}" "${PARAMETER_FILE}"
TEMP_PARAMETER=""

install -o root -g root -m 0644 \
  "${ROOT}/deploy/systemd/${SERVICE}" \
  "/etc/systemd/system/${SERVICE}"

systemctl daemon-reload
systemctl enable "${SERVICE}" >/dev/null
systemctl restart "${SERVICE}"
systemctl is-active --quiet "${SERVICE}" \
  || fail 'Alertmanager SSH forward is not active'

FORWARD_READY=false
for _ in {1..15}; do
  if curl --silent --show-error --fail --max-time 2 \
    http://127.0.0.1:19093/-/ready >/dev/null 2>&1; then
    FORWARD_READY=true
    break
  fi
  sleep 1
done
[[ ${FORWARD_READY} == true ]] \
  || fail 'forwarded production Alertmanager did not become ready'

printf 'external canary Alertmanager forward install: PASS\n'
printf 'service=%s\n' "${SERVICE}"
printf 'local_endpoint=http://127.0.0.1:19093\n'
printf 'target_authorization_installer=deploy/operations/install_alertmanager_forward_target.sh\n'
printf '%s\n' \
  'required_authorized_keys_options=from="<external-canary-tailscale-address>",command="/bin/false",restrict,port-forwarding,permitopen="127.0.0.1:9093"'
