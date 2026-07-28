#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE_HOST=""
RUN_NOW=false

fail() {
  printf 'backup host units install: FAIL: %s\n' "$*" >&2
  exit 1
}

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
for path in \
  /etc/boost-gateway/backup.age-recipient \
  /etc/boost-gateway/backup-remote-host-id.sha256 \
  /etc/boost-gateway/backup-vault-ed25519 \
  /etc/boost-gateway/backup-vault-known-hosts \
  /opt/boost-gateway/current/record.json \
  /usr/local/bin/age
do
  [[ -f ${path} && ! -L ${path} ]] || fail "required regular file is missing: ${path}"
done
[[ $(stat -c '%u:%a' /etc/boost-gateway/backup-vault-ed25519) == 0:600 ]] \
  || fail 'backup SSH private key must be root-owned mode 0600'

install -d -o root -g boost-gateway -m 0750 \
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
