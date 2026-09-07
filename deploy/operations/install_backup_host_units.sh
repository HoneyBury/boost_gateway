#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE_HOST=""
RUN_NOW=false
CONFIG_DIR=/etc/boost-gateway
PRIVATE_KEY=${CONFIG_DIR}/backup-vault-ed25519
PUBLIC_KEY=${PRIVATE_KEY}.pub
PUBLIC_KEY_TEMP=""

fail() {
  printf 'backup host units install: FAIL: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ -n ${PUBLIC_KEY_TEMP} && -e ${PUBLIC_KEY_TEMP} ]]; then
    rm -f -- "${PUBLIC_KEY_TEMP}"
  fi
}
trap cleanup EXIT

# BEGIN TESTABLE: canonicalize_ed25519_public_key
canonicalize_ed25519_public_key() {
  local raw_public_key=${1-}
  local public_key_type public_key_material public_key_comment

  [[ -n ${raw_public_key} && ${raw_public_key} != *$'\n'* ]] || return 1
  if printf '%s' "${raw_public_key}" | LC_ALL=C grep -q '[[:cntrl:]]'; then
    return 1
  fi
  read -r public_key_type public_key_material public_key_comment \
    <<<"${raw_public_key}"
  [[ ${public_key_type} == ssh-ed25519 \
      && ${public_key_material} =~ ^[A-Za-z0-9+/]+={0,2}$ ]] \
    || return 1
  printf '%s %s\n' "${public_key_type}" "${public_key_material}"
}
# END TESTABLE: canonicalize_ed25519_public_key

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote-host)
      [[ $# -ge 2 ]] || fail '--remote-host requires a value'
      REMOTE_HOST=$2
      shift 2
      ;;
    --run-now)
      RUN_NOW=true
      shift
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'run with sudo on the Ubuntu operations host'
[[ ${REMOTE_HOST} =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$ ]] || fail 'remote host is invalid'
[[ -S /var/run/docker.sock ]] || fail 'Docker socket is unavailable'
getent group boost-gateway >/dev/null || fail 'boost-gateway group is missing'
[[ -d ${CONFIG_DIR} && ! -L ${CONFIG_DIR} ]] \
  || fail 'backup configuration directory must be a regular non-symlink directory'
[[ $(stat -c '%u' "${CONFIG_DIR}") == 0 ]] \
  || fail 'backup configuration directory must be root-owned'
CONFIG_DIR_MODE=$(stat -c '%a' "${CONFIG_DIR}")
(( (8#${CONFIG_DIR_MODE} & 0022) == 0 )) \
  || fail 'backup configuration directory must not be group/world writable'
for path in \
  /etc/boost-gateway/backup.age-recipient \
  /etc/boost-gateway/backup-remote-host-id.sha256 \
  "${PRIVATE_KEY}" \
  /etc/boost-gateway/backup-vault-known-hosts \
  /opt/boost-gateway/current/record.json \
  /usr/local/bin/age
do
  [[ -f ${path} && ! -L ${path} ]] || fail "required regular file is missing: ${path}"
done
[[ $(stat -c '%u:%a' "${PRIVATE_KEY}") == 0:600 ]] \
  || fail 'backup SSH private key must be root-owned mode 0600'
[[ $(stat -c '%h' "${PRIVATE_KEY}") == 1 ]] \
  || fail 'backup SSH private key must have exactly one hard link'
[[ -x /usr/bin/ssh-keygen ]] || fail 'OpenSSH ssh-keygen is required'

if [[ -e ${PUBLIC_KEY} || -L ${PUBLIC_KEY} ]]; then
  [[ -f ${PUBLIC_KEY} && ! -L ${PUBLIC_KEY} ]] \
    || fail 'backup SSH public key sidecar must be a regular non-symlink file'
  [[ $(stat -c '%u:%g' "${PUBLIC_KEY}") == 0:0 ]] \
    || fail 'backup SSH public key sidecar must be root-owned'
  [[ $(stat -c '%h' "${PUBLIC_KEY}") == 1 ]] \
    || fail 'backup SSH public key sidecar must have exactly one hard link'
  PUBLIC_KEY_MODE=$(stat -c '%a' "${PUBLIC_KEY}")
  (( (8#${PUBLIC_KEY_MODE} & 0022) == 0 )) \
    || fail 'backup SSH public key sidecar must not be group/world writable'
fi

RAW_DERIVED_PUBLIC_KEY=$(
  /usr/bin/ssh-keygen -y -P '' -f "${PRIVATE_KEY}" 2>/dev/null
) || fail 'backup SSH private key must be valid and unencrypted'
CANONICAL_PUBLIC_KEY=$(
  canonicalize_ed25519_public_key "${RAW_DERIVED_PUBLIC_KEY}"
) || fail 'backup SSH private key must contain one valid Ed25519 key with a safe comment'
unset RAW_DERIVED_PUBLIC_KEY

umask 077
PUBLIC_KEY_TEMP=$(mktemp "${CONFIG_DIR}/.backup-vault-ed25519.pub.XXXXXX")
printf '%s\n' "${CANONICAL_PUBLIC_KEY}" >"${PUBLIC_KEY_TEMP}"
chown root:root "${PUBLIC_KEY_TEMP}"
chmod 0644 "${PUBLIC_KEY_TEMP}"
mv -fT -- "${PUBLIC_KEY_TEMP}" "${PUBLIC_KEY}"
PUBLIC_KEY_TEMP=""
[[ -f ${PUBLIC_KEY} && ! -L ${PUBLIC_KEY} \
    && $(stat -c '%u:%g:%a:%h' "${PUBLIC_KEY}") == 0:0:644:1 ]] \
  || fail 'installed backup SSH public key sidecar is unsafe'
[[ $(<"${PUBLIC_KEY}") == "${CANONICAL_PUBLIC_KEY}" ]] \
  || fail 'installed backup SSH public key sidecar does not match the private key'

install -d -o root -g boost-gateway -m 0750 \
  /usr/local/libexec/boost-gateway/backup/scripts \
  /usr/local/libexec/boost-gateway/backup/scripts/lib \
  /usr/local/libexec/boost-gateway/backup/scripts/tools \
  /usr/local/libexec/boost-gateway/backup/deploy/operations \
  /usr/local/libexec/boost-gateway/backup/env/redis \
  /var/backups/boost-gateway/staging \
  /var/backups/boost-gateway/encrypted \
  /var/backups/boost-gateway/receipts \
  /var/lib/boost-gateway-evidence/recovery
install -o root -g root -m 0755 \
  "${ROOT}/scripts/tools/manage_backup_recovery.py" \
  "${ROOT}/scripts/tools/run_scheduled_backup.py" \
  /usr/local/libexec/boost-gateway/backup/scripts/tools/
install -o root -g root -m 0644 \
  "${ROOT}/scripts/__init__.py" \
  /usr/local/libexec/boost-gateway/backup/scripts/__init__.py
install -o root -g root -m 0644 \
  "${ROOT}/scripts/lib/__init__.py" \
  "${ROOT}/scripts/lib/backup_recovery.py" \
  "${ROOT}/scripts/lib/operations_host.py" \
  "${ROOT}/scripts/lib/evidence_provenance.py" \
  /usr/local/libexec/boost-gateway/backup/scripts/lib/
install -o root -g root -m 0644 \
  "${ROOT}/scripts/tools/__init__.py" \
  /usr/local/libexec/boost-gateway/backup/scripts/tools/__init__.py
install -o root -g root -m 0644 \
  "${ROOT}/deploy/operations/backup-recovery-policy.example.json" \
  /usr/local/libexec/boost-gateway/backup/deploy/operations/
install -o root -g root -m 0644 \
  "${ROOT}/env/redis/redis.production-validation.conf" \
  /usr/local/libexec/boost-gateway/backup/env/redis/
install -o root -g root -m 0644 \
  "${ROOT}/deploy/systemd/boost-gateway-backup.service" \
  "${ROOT}/deploy/systemd/boost-gateway-backup.timer" \
  /etc/systemd/system/

REMOTE_FILE=/etc/boost-gateway/backup-remote-host
if [[ -e ${REMOTE_FILE} || -L ${REMOTE_FILE} ]]; then
  [[ -f ${REMOTE_FILE} && ! -L ${REMOTE_FILE} ]] || fail 'remote host file is unsafe'
  [[ $(<"${REMOTE_FILE}") == "${REMOTE_HOST}" ]] || fail 'remote host file differs'
else
  TEMP=$(mktemp /etc/boost-gateway/.backup-remote-host.XXXXXX)
  printf '%s\n' "${REMOTE_HOST}" >"${TEMP}"
  chown root:root "${TEMP}"
  chmod 0600 "${TEMP}"
  mv "${TEMP}" "${REMOTE_FILE}"
fi
chown root:root "${REMOTE_FILE}"
chmod 0600 "${REMOTE_FILE}"

systemctl daemon-reload
systemctl enable --now boost-gateway-backup.timer
if [[ ${RUN_NOW} == true ]]; then
  systemctl start boost-gateway-backup.service
fi
systemctl is-enabled --quiet boost-gateway-backup.timer || fail 'backup timer is not enabled'
systemctl is-active --quiet boost-gateway-backup.timer || fail 'backup timer is not active'
if [[ ${RUN_NOW} == true ]]; then
  [[ $(systemctl show boost-gateway-backup.service --property=Result --value) == success ]] \
    || fail 'initial scheduled backup did not pass'
fi

printf 'backup host units install: PASS\n'
printf 'timer=boost-gateway-backup.timer\n'
printf 'remote_host=%s\n' "${REMOTE_HOST}"
printf 'initial_backup_run=%s\n' "${RUN_NOW}"
