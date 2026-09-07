#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VAULT_USER=boost-gateway-vault
VAULT_GROUP=boost-gateway-vault
VAULT_HOME=/var/lib/boost-gateway-vault-home
VAULT_ROOT=/srv/boost-gateway-vault
VAULT_IDENTITY=${VAULT_ROOT}/.vault-identity
VAULT_LOCK=${VAULT_ROOT}/.vault.lock
INSTALL_ROOT=/usr/local/libexec/boost-gateway-vault
POLICY_SOURCE=${ROOT}/deploy/operations/aliyun-backup-vault-retention-policy.json
POLICY_TARGET=${INSTALL_ROOT}/deploy/operations/aliyun-backup-vault-retention-policy.json
SOURCE_ADDRESS=""
SOURCE_PUBLIC_KEY_FILE=""
ALLOW_EXISTING_LAYOUT_MIGRATION=false

fail() {
  printf 'backup vault host units install: FAIL: %s\n' "$*" >&2
  exit 1
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
    --allow-existing-vault-layout-migration)
      ALLOW_EXISTING_LAYOUT_MIGRATION=true
      shift
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'run with sudo on the Linux vault host'
[[ -n ${SOURCE_ADDRESS} ]] || fail '--source-address is required'
[[ -n ${SOURCE_PUBLIC_KEY_FILE} ]] || fail '--source-public-key-file is required'
[[ -f ${SOURCE_PUBLIC_KEY_FILE} && ! -L ${SOURCE_PUBLIC_KEY_FILE} ]] \
  || fail 'source public key must be a regular non-symlink file'
[[ -f ${POLICY_SOURCE} && ! -L ${POLICY_SOURCE} ]] \
  || fail 'Aliyun vault retention policy must be a regular non-symlink file'
for command in \
  awk chown chmod cut dd find flock getent groupadd id install mktemp mv python3 rm \
  sha256sum ssh-keygen stat systemctl useradd usermod
do
  command -v "${command}" >/dev/null || fail "required command is missing: ${command}"
done

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${ROOT}" \
  python3 - "${POLICY_SOURCE}" <<'PY' \
  || fail 'Aliyun vault retention policy validation failed'
import sys
from pathlib import Path

from scripts.tools.run_backup_vault_retention import load_retention_policy

policy = Path(sys.argv[1])
load_retention_policy(policy, trusted_owner_uid=policy.stat().st_uid)
PY

python3 - "${SOURCE_ADDRESS}" <<'PY' \
  || fail 'source address must be a Tailscale IPv4 or IPv6 address'
import ipaddress
import sys

address = ipaddress.ip_address(sys.argv[1])
tailscale = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)
raise SystemExit(0 if any(address in network for network in tailscale) else 1)
PY

key_lines=$(awk 'NF {count++} END {print count+0}' "${SOURCE_PUBLIC_KEY_FILE}")
[[ ${key_lines} == 1 ]] || fail 'source public key file must contain exactly one key'
read -r key_type key_material _ <"${SOURCE_PUBLIC_KEY_FILE}"
[[ ${key_type} == ssh-ed25519 ]] || fail 'source public key must use Ed25519'
[[ ${key_material} =~ ^[A-Za-z0-9+/]+={0,2}$ ]] \
  || fail 'source public key encoding is invalid'
key_check=""
identity_tmp=""
lock_tmp=""
authorized_keys_tmp=""
cleanup() {
  [[ -z ${key_check} ]] || rm -f "${key_check}"
  [[ -z ${identity_tmp} ]] || rm -f "${identity_tmp}"
  [[ -z ${lock_tmp} ]] || rm -f "${lock_tmp}"
  [[ -z ${authorized_keys_tmp} ]] || rm -f "${authorized_keys_tmp}"
}
trap cleanup EXIT
key_check=$(mktemp)
printf '%s %s\n' "${key_type}" "${key_material}" >"${key_check}"
ssh-keygen -l -f "${key_check}" >/dev/null \
  || fail 'source public key is not a valid SSH key'

if ! getent group "${VAULT_GROUP}" >/dev/null; then
  groupadd --system "${VAULT_GROUP}"
fi
if ! getent passwd "${VAULT_USER}" >/dev/null; then
  useradd --system --gid "${VAULT_GROUP}" --home-dir "${VAULT_HOME}" \
    --create-home --shell /bin/sh "${VAULT_USER}"
else
  IFS=: read -r _ _ account_uid account_gid _ account_home account_shell \
    < <(getent passwd "${VAULT_USER}")
  expected_gid=$(getent group "${VAULT_GROUP}" | cut -d: -f3)
  [[ ${account_uid} != 0 && ${account_gid} == "${expected_gid}" \
      && ${account_home} == "${VAULT_HOME}" && ${account_shell} == /bin/sh ]] \
    || fail 'existing vault service account differs from the governed identity'
fi
[[ $(id -Gn "${VAULT_USER}") == "${VAULT_GROUP}" ]] \
  || fail 'vault service account must not have supplementary groups'
usermod --lock "${VAULT_USER}"

[[ ! -L ${VAULT_ROOT} ]] || fail "managed directory must not be a symlink: ${VAULT_ROOT}"
existing_vault_entry=""
if [[ -d ${VAULT_ROOT} ]]; then
  existing_vault_entry=$(find "${VAULT_ROOT}" -mindepth 1 -maxdepth 1 -print -quit)
fi
vault_lock_held=false
if [[ -e ${VAULT_LOCK} || -L ${VAULT_LOCK} ]]; then
  [[ -d ${VAULT_ROOT} && ! -L ${VAULT_ROOT} \
      && $(stat -c '%U:%G:%a' "${VAULT_ROOT}") == \
        "root:${VAULT_GROUP}:750" \
      && -f ${VAULT_LOCK} && ! -L ${VAULT_LOCK} \
      && $(stat -c '%U:%G:%a:%h' "${VAULT_LOCK}") == \
        "root:${VAULT_GROUP}:660:1" ]] \
    || fail 'existing vault lock/layout is unsafe; do not repair it in place'
  if systemctl cat boost-gateway-backup-vault-retention.timer >/dev/null 2>&1; then
    systemctl disable --now boost-gateway-backup-vault-retention.timer
  fi
  exec 9<>"${VAULT_LOCK}"
  flock -x 9
  vault_lock_held=true
elif [[ -n ${existing_vault_entry} \
    && ${ALLOW_EXISTING_LAYOUT_MIGRATION} != true ]]; then
  fail 'non-empty legacy vault requires --allow-existing-vault-layout-migration after quiescing all upload/prune callers'
fi

for path in \
  "${VAULT_HOME}" "${VAULT_HOME}/.ssh" "${VAULT_ROOT}" \
  "${VAULT_ROOT}/backups" "${VAULT_ROOT}/.incoming" \
  "${VAULT_ROOT}/known-good" "${VAULT_ROOT}/deletions" \
  "${VAULT_ROOT}/.trash" "${INSTALL_ROOT}"
do
  [[ ! -L ${path} ]] || fail "managed directory must not be a symlink: ${path}"
done
install -d -o root -g root -m 0755 "${VAULT_HOME}"
install -d -o root -g "${VAULT_GROUP}" -m 0750 "${VAULT_HOME}/.ssh"
install -d -o root -g "${VAULT_GROUP}" -m 0750 "${VAULT_ROOT}"
install -d -o "${VAULT_USER}" -g "${VAULT_GROUP}" -m 0700 \
  "${VAULT_ROOT}/backups" "${VAULT_ROOT}/.incoming" \
  "${VAULT_ROOT}/known-good" "${VAULT_ROOT}/deletions" \
  "${VAULT_ROOT}/.trash"
if [[ -e ${VAULT_IDENTITY} || -L ${VAULT_IDENTITY} ]]; then
  [[ -f ${VAULT_IDENTITY} && ! -L ${VAULT_IDENTITY} \
      && $(stat -c '%s' "${VAULT_IDENTITY}") == 32 ]] \
    || fail 'existing vault identity is unsafe'
else
  identity_tmp=$(mktemp "${VAULT_ROOT}/.vault-identity.XXXXXX")
  dd if=/dev/urandom of="${identity_tmp}" bs=32 count=1 status=none
  chown "${VAULT_USER}:${VAULT_GROUP}" "${identity_tmp}"
  chmod 0600 "${identity_tmp}"
  mv "${identity_tmp}" "${VAULT_IDENTITY}"
  identity_tmp=""
fi
chown "root:${VAULT_GROUP}" "${VAULT_IDENTITY}"
chmod 0640 "${VAULT_IDENTITY}"
if [[ -e ${VAULT_LOCK} || -L ${VAULT_LOCK} ]]; then
  [[ -f ${VAULT_LOCK} && ! -L ${VAULT_LOCK} \
      && $(stat -c '%h' "${VAULT_LOCK}") == 1 ]] \
    || fail 'existing vault lock is unsafe'
else
  lock_tmp=$(mktemp "${VAULT_ROOT}/.vault.lock.XXXXXX")
  chown "root:${VAULT_GROUP}" "${lock_tmp}"
  chmod 0660 "${lock_tmp}"
  mv "${lock_tmp}" "${VAULT_LOCK}"
  lock_tmp=""
fi
chown "root:${VAULT_GROUP}" "${VAULT_LOCK}"
chmod 0660 "${VAULT_LOCK}"
if [[ ${vault_lock_held} != true ]]; then
  exec 9<>"${VAULT_LOCK}"
  flock -x 9
  vault_lock_held=true
fi
[[ $(stat -c '%U:%G:%a' "${VAULT_ROOT}") == \
    "root:${VAULT_GROUP}:750" ]] \
  || fail 'vault root ownership or mode differs after installation'
for path in backups .incoming known-good deletions .trash; do
  [[ -d ${VAULT_ROOT}/${path} && ! -L ${VAULT_ROOT}/${path} \
      && $(stat -c '%U:%G:%a' "${VAULT_ROOT}/${path}") == \
        "${VAULT_USER}:${VAULT_GROUP}:700" ]] \
    || fail "vault data directory ownership or mode differs: ${path}"
done
[[ $(stat -c '%U:%G:%a:%s' "${VAULT_IDENTITY}") == \
    "root:${VAULT_GROUP}:640:32" ]] \
  || fail 'vault identity ownership, mode or size differs after installation'
[[ $(stat -c '%U:%G:%a:%h' "${VAULT_LOCK}") == \
    "root:${VAULT_GROUP}:660:1" ]] \
  || fail 'vault lock ownership, mode or link count differs after installation'

install -d -o root -g "${VAULT_GROUP}" -m 0750 \
  "${INSTALL_ROOT}" "${INSTALL_ROOT}/scripts" \
  "${INSTALL_ROOT}/scripts/lib" "${INSTALL_ROOT}/scripts/tools" \
  "${INSTALL_ROOT}/deploy" "${INSTALL_ROOT}/deploy/operations"
install -o root -g "${VAULT_GROUP}" -m 0640 \
  "${ROOT}/scripts/__init__.py" "${INSTALL_ROOT}/scripts/__init__.py"
install -o root -g "${VAULT_GROUP}" -m 0640 \
  "${ROOT}/scripts/lib/__init__.py" \
  "${ROOT}/scripts/lib/backup_recovery.py" \
  "${INSTALL_ROOT}/scripts/lib/"
install -o root -g "${VAULT_GROUP}" -m 0640 \
  "${ROOT}/scripts/tools/__init__.py" "${INSTALL_ROOT}/scripts/tools/__init__.py"
install -o root -g "${VAULT_GROUP}" -m 0550 \
  "${ROOT}/scripts/tools/backup_vault_ssh_receiver.py" \
  "${ROOT}/scripts/tools/manage_backup_recovery.py" \
  "${ROOT}/scripts/tools/run_backup_vault_retention.py" \
  "${INSTALL_ROOT}/scripts/tools/"
install -o root -g "${VAULT_GROUP}" -m 0640 \
  "${POLICY_SOURCE}" "${POLICY_TARGET}"

receiver_command="/usr/bin/python3 ${INSTALL_ROOT}/scripts/tools/backup_vault_ssh_receiver.py --vault-root ${VAULT_ROOT} --vault-identity-file ${VAULT_IDENTITY}"
authorized_keys="${VAULT_HOME}/.ssh/authorized_keys"
[[ ! -L ${authorized_keys} ]] || fail 'authorized_keys must not be a symlink'
authorized_keys_tmp=$(mktemp "${VAULT_HOME}/.ssh/.authorized_keys.XXXXXX")
printf 'from="%s",restrict,command="%s" %s %s\n' \
  "${SOURCE_ADDRESS}" "${receiver_command}" "${key_type}" "${key_material}" \
  >"${authorized_keys_tmp}"
chown "root:${VAULT_GROUP}" "${authorized_keys_tmp}"
chmod 0640 "${authorized_keys_tmp}"
mv "${authorized_keys_tmp}" "${authorized_keys}"
authorized_keys_tmp=""
[[ $(stat -c '%U:%G:%a' "${VAULT_HOME}") == root:root:755 \
    && $(stat -c '%U:%G:%a' "${VAULT_HOME}/.ssh") == \
      "root:${VAULT_GROUP}:750" \
    && $(stat -c '%U:%G:%a:%h' "${authorized_keys}") == \
      "root:${VAULT_GROUP}:640:1" ]] \
  || fail 'SSH home or authorized_keys permissions differ after installation'

install -o root -g root -m 0644 \
  "${ROOT}/deploy/systemd/boost-gateway-backup-vault-retention.service" \
  "${ROOT}/deploy/systemd/boost-gateway-backup-vault-retention.timer" \
  /etc/systemd/system/
systemctl daemon-reload
systemctl disable --now boost-gateway-backup-vault-retention.timer
[[ $(systemctl is-enabled boost-gateway-backup-vault-retention.timer) == disabled ]] \
  || fail 'backup vault retention timer must remain disabled after bootstrap'
if systemctl is-active --quiet boost-gateway-backup-vault-retention.timer; then
  fail 'backup vault retention timer must remain inactive after bootstrap'
fi

printf 'backup vault host units install: PASS\n'
printf 'vault_user=%s\n' "${VAULT_USER}"
printf 'vault_root=%s\n' "${VAULT_ROOT}"
printf 'vault_identity_sha256=%s\n' "$(sha256sum "${VAULT_IDENTITY}" | cut -d' ' -f1)"
printf 'retention_policy_sha256=%s\n' "$(sha256sum "${POLICY_TARGET}" | cut -d' ' -f1)"
printf 'retention_timer=boost-gateway-backup-vault-retention.timer (disabled)\n'
