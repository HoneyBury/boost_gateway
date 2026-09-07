#!/usr/bin/env bash
set -Eeuo pipefail

FORWARD_USER=boost-gateway-alert-forward
FORWARD_GROUP=boost-gateway-alert-forward
FORWARD_HOME=/var/lib/boost-gateway-alert-forward
SSH_DIRECTORY=${FORWARD_HOME}/.ssh
AUTHORIZED_KEYS=${SSH_DIRECTORY}/authorized_keys
SSHD_CONFIG_DIRECTORY=/etc/ssh/sshd_config.d
SSHD_DROP_IN=${SSHD_CONFIG_DIRECTORY}/05-boost-gateway-alertmanager-forward.conf
SSHD_BIN=/usr/sbin/sshd
MANAGED_MARKER='# Managed by install_alertmanager_forward_target.sh. Do not edit in place.'
SOURCE_ADDRESS=""
SOURCE_PUBLIC_KEY_FILE=""
SSH_SERVICE=""

KEY_CHECK=""
AUTHORIZED_KEYS_TEMP=""
AUTHORIZED_KEYS_BACKUP=""
SSHD_DROP_IN_TEMP=""
SSHD_DROP_IN_BACKUP=""
AUTHORIZED_KEYS_EXISTED=false
AUTHORIZED_KEYS_REPLACED=false
SSHD_DROP_IN_EXISTED=false
SSHD_DROP_IN_REPLACED=false
RELOAD_ATTEMPTED=false
INSTALL_COMMITTED=false

fail() {
  printf 'Alertmanager forward target install: FAIL: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'Alertmanager forward target install: WARNING: %s\n' "$*" >&2
}

cleanup() {
  local status=$?
  trap - EXIT
  set +e

  rm -f -- \
    "${KEY_CHECK}" "${AUTHORIZED_KEYS_TEMP}" "${SSHD_DROP_IN_TEMP}"

  if [[ ${INSTALL_COMMITTED} != true ]]; then
    if [[ ${SSHD_DROP_IN_REPLACED} == true ]]; then
      rm -f -- "${SSHD_DROP_IN}"
      if [[ ${SSHD_DROP_IN_EXISTED} == true ]]; then
        mv -f -- "${SSHD_DROP_IN_BACKUP}" "${SSHD_DROP_IN}"
        SSHD_DROP_IN_BACKUP=""
      fi
    fi

    if [[ ${AUTHORIZED_KEYS_REPLACED} == true ]]; then
      rm -f -- "${AUTHORIZED_KEYS}"
      if [[ ${AUTHORIZED_KEYS_EXISTED} == true ]]; then
        mv -f -- "${AUTHORIZED_KEYS_BACKUP}" "${AUTHORIZED_KEYS}"
        AUTHORIZED_KEYS_BACKUP=""
      fi
    fi

    if [[ ${SSHD_DROP_IN_REPLACED} == true ]]; then
      "${SSHD_BIN}" -t \
        || warn 'restored sshd configuration did not pass sshd -t'
      if [[ ${RELOAD_ATTEMPTED} == true && -n ${SSH_SERVICE} ]]; then
        systemctl reload "${SSH_SERVICE}" \
          || warn 'could not reload the restored sshd configuration'
      fi
    fi
  fi

  rm -f -- "${AUTHORIZED_KEYS_BACKUP}" "${SSHD_DROP_IN_BACKUP}"
  exit "${status}"
}
trap cleanup EXIT

require_safe_root_directory_or_absent() {
  local path=$1
  local allowed_group_id=$2
  local owner_group owner_user mode

  [[ ! -L ${path} ]] || fail "managed directory must not be a symlink: ${path}"
  [[ ! -e ${path} || -d ${path} ]] \
    || fail "managed path must be a directory: ${path}"
  if [[ -d ${path} ]]; then
    owner_user=$(stat -c '%u' "${path}")
    owner_group=$(stat -c '%g' "${path}")
    mode=$(stat -c '%a' "${path}")
    [[ ${owner_user} == 0 ]] \
      || fail "existing managed directory must be root-owned: ${path}"
    [[ ${owner_group} == 0 || ${owner_group} == "${allowed_group_id}" ]] \
      || fail "existing managed directory has an unexpected group: ${path}"
    (( (8#${mode} & 0022) == 0 )) \
      || fail "existing managed directory must not be group/world writable: ${path}"
  fi
}

require_safe_root_file_or_absent() {
  local path=$1
  local allowed_group_id=$2
  local links mode owner_group owner_user

  [[ ! -L ${path} ]] || fail "managed file must not be a symlink: ${path}"
  [[ ! -e ${path} || -f ${path} ]] \
    || fail "managed path must be a regular file: ${path}"
  if [[ -f ${path} ]]; then
    owner_user=$(stat -c '%u' "${path}")
    owner_group=$(stat -c '%g' "${path}")
    links=$(stat -c '%h' "${path}")
    mode=$(stat -c '%a' "${path}")
    [[ ${owner_user} == 0 ]] \
      || fail "existing managed file must be root-owned: ${path}"
    [[ ${owner_group} == 0 || ${owner_group} == "${allowed_group_id}" ]] \
      || fail "existing managed file has an unexpected group: ${path}"
    [[ ${links} == 1 ]] \
      || fail "existing managed file must have exactly one hard link: ${path}"
    (( (8#${mode} & 0022) == 0 )) \
      || fail "existing managed file must not be group/world writable: ${path}"
  fi
}

require_managed_marker_if_present() {
  local path=$1
  local first_line=""

  if [[ -f ${path} ]]; then
    IFS= read -r first_line <"${path}" || true
    [[ ${first_line} == "${MANAGED_MARKER}" ]] \
      || fail "refusing to overwrite an unmanaged file: ${path}"
  fi
}

require_effective_setting() {
  local settings=$1
  local expected=$2

  grep -Fqx -- "${expected}" <<<"${settings}" \
    || fail "effective sshd policy is not '${expected}'"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-address)
      [[ $# -ge 2 ]] || fail '--source-address requires a value'
      SOURCE_ADDRESS=$2
      shift 2
      ;;
    --source-public-key-file)
      [[ $# -ge 2 ]] || fail '--source-public-key-file requires a value'
      SOURCE_PUBLIC_KEY_FILE=$2
      shift 2
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] \
  || fail 'run with sudo on the Alertmanager host'
[[ -n ${SOURCE_ADDRESS} ]] || fail '--source-address is required'
[[ -n ${SOURCE_PUBLIC_KEY_FILE} ]] \
  || fail '--source-public-key-file is required'
[[ -f ${SOURCE_PUBLIC_KEY_FILE} && ! -L ${SOURCE_PUBLIC_KEY_FILE} ]] \
  || fail 'source public key must be a regular non-symlink file'

for command in \
  awk cat chmod chown cp cut getent grep groupadd id install mktemp mv python3 \
  rm ssh-keygen stat systemctl useradd usermod
do
  command -v "${command}" >/dev/null \
    || fail "required command is missing: ${command}"
done
[[ -x ${SSHD_BIN} ]] || fail "OpenSSH server is missing: ${SSHD_BIN}"
[[ -x /usr/sbin/nologin ]] || fail '/usr/sbin/nologin is required'

if ! SOURCE_ADDRESS=$(python3 - "${SOURCE_ADDRESS}" <<'PY'
import ipaddress
import sys

raw = sys.argv[1]
if "%" in raw:
    raise SystemExit(1)
address = ipaddress.ip_address(raw)
tailscale_ranges = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)
if not any(address in network for network in tailscale_ranges):
    raise SystemExit(1)
print(address.compressed)
PY
); then
  fail 'source address must be one exact Tailscale IPv4 or IPv6 address'
fi

key_lines=$(awk '!/^[[:space:]]*(#|$)/ {count++} END {print count+0}' \
  "${SOURCE_PUBLIC_KEY_FILE}")
[[ ${key_lines} == 1 ]] \
  || fail 'source public key file must contain exactly one key'
key_record=$(awk '!/^[[:space:]]*(#|$)/ {print; exit}' \
  "${SOURCE_PUBLIC_KEY_FILE}")
read -r key_type key_material _ <<<"${key_record}"
[[ ${key_type} == ssh-ed25519 ]] \
  || fail 'source public key must use Ed25519'
[[ ${key_material} =~ ^[A-Za-z0-9+/]+={0,2}$ ]] \
  || fail 'source public key encoding is invalid'
KEY_CHECK=$(mktemp)
printf '%s %s\n' "${key_type}" "${key_material}" >"${KEY_CHECK}"
chmod 0600 "${KEY_CHECK}"
ssh-keygen -l -f "${KEY_CHECK}" >/dev/null \
  || fail 'source public key is not a valid Ed25519 key'
key_fingerprint=$(ssh-keygen -l -f "${KEY_CHECK}" | awk '{print $2}')

# Refuse to build on a syntactically invalid baseline. This check deliberately
# precedes every account, key and drop-in mutation.
"${SSHD_BIN}" -t || fail 'existing sshd configuration is invalid'
if systemctl is-active --quiet ssh.service; then
  SSH_SERVICE=ssh.service
elif systemctl is-active --quiet sshd.service; then
  SSH_SERVICE=sshd.service
else
  fail 'neither ssh.service nor sshd.service is active'
fi

if ! getent group "${FORWARD_GROUP}" >/dev/null; then
  groupadd --system "${FORWARD_GROUP}"
fi
group_record=$(getent group "${FORWARD_GROUP}")
IFS=: read -r group_name _ group_id group_members <<<"${group_record}"
[[ ${group_name} == "${FORWARD_GROUP}" && ${group_id} != 0 ]] \
  || fail 'existing forward group differs from the governed identity'
[[ -z ${group_members} ]] \
  || fail 'forward group must not list supplementary members'

if ! getent passwd "${FORWARD_USER}" >/dev/null; then
  useradd --system --gid "${FORWARD_GROUP}" --home-dir "${FORWARD_HOME}" \
    --no-create-home --shell /usr/sbin/nologin "${FORWARD_USER}"
fi
passwd_record=$(getent passwd "${FORWARD_USER}")
IFS=: read -r account_name _ account_uid account_gid _ account_home account_shell \
  <<<"${passwd_record}"
[[ ${account_name} == "${FORWARD_USER}" && ${account_uid} != 0 \
    && ${account_gid} == "${group_id}" \
    && ${account_home} == "${FORWARD_HOME}" \
    && ${account_shell} == /usr/sbin/nologin ]] \
  || fail 'existing forward account differs from the governed identity'
[[ $(id -Gn "${FORWARD_USER}") == "${FORWARD_GROUP}" ]] \
  || fail 'forward account must not belong to supplementary groups'

require_safe_root_directory_or_absent "${FORWARD_HOME}" 0
require_safe_root_directory_or_absent "${SSH_DIRECTORY}" "${group_id}"
require_safe_root_directory_or_absent "${SSHD_CONFIG_DIRECTORY}" 0
require_safe_root_file_or_absent "${AUTHORIZED_KEYS}" "${group_id}"
require_safe_root_file_or_absent "${SSHD_DROP_IN}" 0
require_managed_marker_if_present "${AUTHORIZED_KEYS}"
require_managed_marker_if_present "${SSHD_DROP_IN}"
usermod --lock "${FORWARD_USER}"

install -d -o root -g root -m 0755 "${FORWARD_HOME}"
install -d -o root -g "${FORWARD_GROUP}" -m 0750 "${SSH_DIRECTORY}"
install -d -o root -g root -m 0755 "${SSHD_CONFIG_DIRECTORY}"

AUTHORIZED_KEYS_TEMP=$(mktemp "${SSH_DIRECTORY}/.authorized_keys.XXXXXX")
printf '%s\n' "${MANAGED_MARKER}" >"${AUTHORIZED_KEYS_TEMP}"
printf 'from="%s",command="/bin/false",restrict,port-forwarding,permitopen="127.0.0.1:9093" ssh-ed25519 %s boost-gateway-alert-forward\n' \
  "${SOURCE_ADDRESS}" "${key_material}" >>"${AUTHORIZED_KEYS_TEMP}"
chown root:"${FORWARD_GROUP}" "${AUTHORIZED_KEYS_TEMP}"
chmod 0640 "${AUTHORIZED_KEYS_TEMP}"

SSHD_DROP_IN_TEMP=$(mktemp \
  "${SSHD_CONFIG_DIRECTORY}/.05-boost-gateway-alertmanager-forward.XXXXXX")
cat >"${SSHD_DROP_IN_TEMP}" <<EOF
${MANAGED_MARKER}
Match User ${FORWARD_USER}
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthorizedKeysFile .ssh/authorized_keys
    AuthorizedKeysCommand none
    ForceCommand /bin/false
    MaxSessions 0
    DisableForwarding no
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:9093
    PermitListen none
    AllowStreamLocalForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    PermitTunnel no
    PermitUserRC no
    GatewayPorts no
Match all
EOF
chown root:root "${SSHD_DROP_IN_TEMP}"
chmod 0644 "${SSHD_DROP_IN_TEMP}"

if [[ -f ${AUTHORIZED_KEYS} ]]; then
  AUTHORIZED_KEYS_EXISTED=true
  AUTHORIZED_KEYS_BACKUP=$(mktemp "${SSH_DIRECTORY}/.authorized_keys.rollback.XXXXXX")
  cp --preserve=mode,ownership,timestamps -- \
    "${AUTHORIZED_KEYS}" "${AUTHORIZED_KEYS_BACKUP}"
fi
if [[ -f ${SSHD_DROP_IN} ]]; then
  SSHD_DROP_IN_EXISTED=true
  SSHD_DROP_IN_BACKUP=$(mktemp \
    "${SSHD_CONFIG_DIRECTORY}/.05-boost-gateway-alertmanager-forward.rollback.XXXXXX")
  cp --preserve=mode,ownership,timestamps -- \
    "${SSHD_DROP_IN}" "${SSHD_DROP_IN_BACKUP}"
fi

mv -f -- "${AUTHORIZED_KEYS_TEMP}" "${AUTHORIZED_KEYS}"
AUTHORIZED_KEYS_TEMP=""
AUTHORIZED_KEYS_REPLACED=true
mv -f -- "${SSHD_DROP_IN_TEMP}" "${SSHD_DROP_IN}"
SSHD_DROP_IN_TEMP=""
SSHD_DROP_IN_REPLACED=true

[[ $(stat -c '%u:%g:%a' "${FORWARD_HOME}") == 0:0:755 ]] \
  || fail 'managed forward home ownership or mode differs after installation'
[[ $(stat -c '%u:%g:%a' "${SSH_DIRECTORY}") == "0:${group_id}:750" ]] \
  || fail 'managed .ssh ownership or mode differs after installation'
[[ $(stat -c '%u:%g:%a' "${AUTHORIZED_KEYS}") == "0:${group_id}:640" ]] \
  || fail 'managed authorized_keys ownership or mode differs after installation'
[[ $(stat -c '%u:%g:%a' "${SSHD_DROP_IN}") == 0:0:644 ]] \
  || fail 'managed sshd drop-in ownership or mode differs after installation'

# Validate both syntax and the resolved Match policy. The effective-value check
# also detects an absent Include or an earlier conflicting Match declaration.
"${SSHD_BIN}" -t || fail 'installed sshd drop-in failed syntax validation'
effective_settings=$("${SSHD_BIN}" -T \
  -C "user=${FORWARD_USER},host=localhost,addr=${SOURCE_ADDRESS}") \
  || fail 'could not resolve the installed forward account policy'
for expected_setting in \
  'authenticationmethods publickey' \
  'pubkeyauthentication yes' \
  'passwordauthentication no' \
  'kbdinteractiveauthentication no' \
  'authorizedkeysfile .ssh/authorized_keys' \
  'authorizedkeyscommand none' \
  'forcecommand /bin/false' \
  'maxsessions 0' \
  'disableforwarding no' \
  'allowtcpforwarding local' \
  'permitopen 127.0.0.1:9093' \
  'permitlisten none' \
  'allowstreamlocalforwarding no' \
  'allowagentforwarding no' \
  'x11forwarding no' \
  'permittty no' \
  'permittunnel no' \
  'permituserrc no' \
  'gatewayports no'
do
  require_effective_setting "${effective_settings}" "${expected_setting}"
done

# Re-run syntax validation immediately before reload so the reload never acts
# on configuration other than the exact state validated above.
"${SSHD_BIN}" -t || fail 'sshd configuration changed before reload'
RELOAD_ATTEMPTED=true
systemctl reload "${SSH_SERVICE}" || fail "could not reload ${SSH_SERVICE}"
systemctl is-active --quiet "${SSH_SERVICE}" \
  || fail "${SSH_SERVICE} is not active after reload"

post_reload_settings=$("${SSHD_BIN}" -T \
  -C "user=${FORWARD_USER},host=localhost,addr=${SOURCE_ADDRESS}") \
  || fail 'could not resolve the reloaded forward account policy'
for expected_setting in \
  'authenticationmethods publickey' \
  'passwordauthentication no' \
  'kbdinteractiveauthentication no' \
  'maxsessions 0' \
  'disableforwarding no' \
  'allowtcpforwarding local' \
  'permitopen 127.0.0.1:9093' \
  'permitlisten none' \
  'allowstreamlocalforwarding no' \
  'allowagentforwarding no' \
  'x11forwarding no' \
  'permittty no'
do
  require_effective_setting "${post_reload_settings}" "${expected_setting}"
done

INSTALL_COMMITTED=true
rm -f -- "${AUTHORIZED_KEYS_BACKUP}" "${SSHD_DROP_IN_BACKUP}"
AUTHORIZED_KEYS_BACKUP=""
SSHD_DROP_IN_BACKUP=""

printf 'Alertmanager forward target install: PASS\n'
printf 'forward_user=%s\n' "${FORWARD_USER}"
printf 'source_address=%s\n' "${SOURCE_ADDRESS}"
printf 'authorized_keys=%s\n' "${AUTHORIZED_KEYS}"
printf 'sshd_drop_in=%s\n' "${SSHD_DROP_IN}"
printf 'public_key_fingerprint=%s\n' "${key_fingerprint}"
