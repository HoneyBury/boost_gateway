#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE=${ROOT}/deploy/operations/offhost-sshd-hardening.conf
TARGET=/etc/ssh/sshd_config.d/00-boost-gateway-offhost.conf
BACKUP=""
HAD_TARGET=false
COMMITTED=false

fail() {
  printf 'off-host SSH hardening install: FAIL: %s\n' "$*" >&2
  exit 1
}

restore_on_failure() {
  if [[ ${COMMITTED} == true ]]; then
    return
  fi
  if [[ ${HAD_TARGET} == true && -n ${BACKUP} && -f ${BACKUP} ]]; then
    install -o root -g root -m 0644 "${BACKUP}" "${TARGET}"
  elif [[ ${HAD_TARGET} == false && -f ${TARGET} ]]; then
    rm -f -- "${TARGET}"
  fi
}

cleanup() {
  restore_on_failure
  if [[ -n ${BACKUP} && -e ${BACKUP} ]]; then
    rm -f -- "${BACKUP}"
  fi
}
trap cleanup EXIT

[[ $# -eq 0 ]] || fail 'this installer does not accept arguments'
[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'run with sudo on the off-host observer'
[[ -f ${SOURCE} && ! -L ${SOURCE} ]] || fail 'managed sshd fragment is unavailable'
for command in install mktemp rm systemctl; do
  command -v "${command}" >/dev/null || fail "required command is missing: ${command}"
done
[[ -x /usr/sbin/sshd ]] || fail '/usr/sbin/sshd is unavailable'
[[ -d /etc/ssh/sshd_config.d && ! -L /etc/ssh/sshd_config.d ]] \
  || fail 'sshd_config.d must be a non-symlink directory'

if [[ -e ${TARGET} || -L ${TARGET} ]]; then
  [[ -f ${TARGET} && ! -L ${TARGET} ]] || fail 'managed sshd target is unsafe'
  HAD_TARGET=true
  BACKUP=$(mktemp /etc/ssh/sshd_config.d/.boost-gateway-sshd-backup.XXXXXX)
  install -o root -g root -m 0600 "${TARGET}" "${BACKUP}"
fi

# The 00- prefix is deliberate. Ubuntu includes sshd_config.d before its later
# global defaults, and OpenSSH uses the first obtained value for these keys.
install -o root -g root -m 0644 "${SOURCE}" "${TARGET}"
/usr/sbin/sshd -t || fail 'candidate sshd configuration is invalid'

effective=$(/usr/sbin/sshd -T)
effective_value() {
  local key=$1
  awk -v key="${key}" '$1 == key { print $2; exit }' <<<"${effective}"
}
[[ $(effective_value pubkeyauthentication) == yes ]] \
  || fail 'effective PubkeyAuthentication is not yes'
[[ $(effective_value passwordauthentication) == no ]] \
  || fail 'effective PasswordAuthentication is not no'
[[ $(effective_value kbdinteractiveauthentication) == no ]] \
  || fail 'effective KbdInteractiveAuthentication is not no'
case "$(effective_value permitrootlogin)" in
  prohibit-password|without-password) ;;
  *) fail 'effective PermitRootLogin is not key-only' ;;
esac

systemctl reload ssh.service
systemctl is-active --quiet ssh.service || fail 'ssh.service is not active after reload'
/usr/sbin/sshd -t || fail 'reloaded sshd configuration is invalid'
COMMITTED=true

printf 'off-host SSH hardening install: PASS\n'
printf 'target=%s\n' "${TARGET}"
printf 'password_authentication=no\n'
printf 'root_authentication=publickey-only-break-glass\n'
