#!/usr/bin/env bash
set -Eeuo pipefail

# Initial, single-release deployment for TODO-0009. This deliberately does not
# implement upgrade, rollback, or an automatic host reboot.

TAG="v3.6.2"
VERSION="3.6.2"
EXPECTED_COMMIT="ac99ae353a2a6e846f934c8d81c78a07f420f683"
EXPECTED_MANIFEST_SHA256="388a6b38e0e2470d7b2f289ba1b02c804ffdce79dd455eb2b71cbb6ad8b710bd"
STAGING="${BOOST_GATEWAY_STAGING:-/home/honeybury/boost-gateway-v3.6.2-verified-r3}"
STAGING_SUMMARY="${BOOST_GATEWAY_STAGING_SUMMARY:-/home/honeybury/release-runtime-staging-r3-summary.json}"
CONTROLLER="${BOOST_GATEWAY_CONTROLLER:-/home/honeybury/boost-gateway-todo0009}"
RELEASE_DIR="/opt/boost-gateway/releases/${TAG}-deploy-r3"
FAILED_INITIAL_RELEASE_DIR="/opt/boost-gateway/releases/${TAG}"
FAILED_INITIAL_MANIFEST_SHA256="d1f96c7fb445ebcab6a879f54448f8475485624db89a998c3af946983886b016"
CURRENT="/opt/boost-gateway/current"
EVIDENCE_DIR="/var/lib/boost-gateway-evidence/release"
GH_ARCHIVE="/tmp/gh-2.96.0-linux-amd64/gh_2.96.0_linux_amd64.tar.gz"
GH_ARCHIVE_SHA256="83d5c2ccad5498f58bf6368acb1ab32588cf43ab3a4b1c301bf36328b1c8bd60"
GH_EXTRACT_ROOT="/tmp/gh-2.96.0-linux-amd64"
UBUNTU_IMAGE="ubuntu@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90"
DOCKER_PROXY_URL="${BOOST_GATEWAY_DOCKER_PROXY_URL:-http://127.0.0.1:7890}"
DOCKER_PROXY_DROPIN="/etc/systemd/system/docker.service.d/boost-gateway-proxy.conf"

die() {
  printf 'verified release deploy: FAIL: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is missing: $1"
}

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

guard_target_host() {
  [[ "$(uname -s)" == "Linux" ]] || die "this script only runs on Linux; current system is $(uname -s)"
  [[ "$(uname -m)" == "x86_64" ]] || die "this script only runs on x86_64; current architecture is $(uname -m)"
  [[ -r /etc/os-release ]] || die "/etc/os-release is unavailable"
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] ||
    die "this script requires Ubuntu 24.04; observed ${ID:-unknown} ${VERSION_ID:-unknown}"
  [[ -d /run/systemd/system ]] || die "systemd is not the active init system"
  [[ "${EUID}" -eq 0 ]] || die "run with sudo on the Ubuntu host"
  getent passwd boost-gateway >/dev/null || die "boost-gateway service identity is missing"
  systemctl is-active --quiet docker.service || die "docker.service is not active"
  docker compose version >/dev/null || die "Docker Compose v2 is unavailable"
}

validate_inputs() {
  [[ -f "${STAGING}/manifest.json" ]] || die "verified staging manifest is missing: ${STAGING}/manifest.json"
  [[ -f "${CONTROLLER}/deploy/operations/operations-host-policy.json" ]] ||
    die "controller policy is missing"
  [[ -f "${CONTROLLER}/scripts/check_operations_host.py" ]] ||
    die "operations host checker is missing"
  [[ -f "${GH_ARCHIVE}" ]] || die "checksum-verified GitHub CLI archive is missing: ${GH_ARCHIVE}"

  local manifest_sha tag commit passed
  manifest_sha="$(sha256_file "${STAGING}/manifest.json")"
  [[ "${manifest_sha}" == "${EXPECTED_MANIFEST_SHA256}" ]] ||
    die "staging manifest SHA-256 mismatch: ${manifest_sha}"
  tag="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag"])' "${STAGING}/manifest.json")"
  commit="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit"])' "${STAGING}/manifest.json")"
  [[ "${tag}" == "${TAG}" ]] || die "staging tag mismatch: ${tag}"
  [[ "${commit}" == "${EXPECTED_COMMIT}" ]] || die "staging commit mismatch: ${commit}"
  passed="$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["overall_pass"]).lower())' "${STAGING_SUMMARY}")"
  [[ "${passed}" == "true" ]] || die "release staging summary is not passing"

  local gh_sha
  gh_sha="$(sha256_file "${GH_ARCHIVE}")"
  [[ "${gh_sha}" == "${GH_ARCHIVE_SHA256}" ]] || die "GitHub CLI archive SHA-256 mismatch: ${gh_sha}"
}

install_release() {
  install -d -o root -g boost-gateway -m 0750 /opt/boost-gateway/releases "${EVIDENCE_DIR}"

  if [[ ! -e "${RELEASE_DIR}" ]]; then
    install -d -o root -g boost-gateway -m 0750 "${RELEASE_DIR}"
    cp -a "${STAGING}/." "${RELEASE_DIR}/"
    chown -R root:boost-gateway "${RELEASE_DIR}"
  else
    local installed_sha
    installed_sha="$(sha256_file "${RELEASE_DIR}/manifest.json")"
    [[ "${installed_sha}" == "${EXPECTED_MANIFEST_SHA256}" ]] ||
      die "existing release directory has a different manifest: ${RELEASE_DIR}"
  fi

  if [[ -L "${CURRENT}" ]]; then
    local current_target
    current_target="$(readlink -f "${CURRENT}")"
    if [[ "${current_target}" != "${RELEASE_DIR}" ]]; then
      if [[ "${current_target}" == "${FAILED_INITIAL_RELEASE_DIR}" ]] &&
        [[ "$(sha256_file "${FAILED_INITIAL_RELEASE_DIR}/manifest.json")" == "${FAILED_INITIAL_MANIFEST_SHA256}" ]] &&
        ! systemctl is-active --quiet boost-gateway-compose.service; then
        ln -sfn "${RELEASE_DIR}" "${CURRENT}"
      else
        die "current points to another active or unrecognized release; TODO-0010 upgrade is required"
      fi
    fi
  elif [[ -e "${CURRENT}" ]]; then
    die "current exists and is not the expected symlink"
  else
    ln -s "${RELEASE_DIR}" "${CURRENT}"
  fi

  install -o root -g root -m 0644 \
    "${CURRENT}/deploy/systemd/boost-gateway-compose.service" \
    /etc/systemd/system/boost-gateway-compose.service
  install -o root -g boost-gateway -m 0640 \
    "${CONTROLLER}/deploy/operations/operations-host-policy.json" \
    /etc/boost-gateway/operations-host-policy.json
  install -o root -g boost-gateway -m 0640 \
    "${STAGING_SUMMARY}" \
    "${EVIDENCE_DIR}/release-runtime-staging-summary.json"
}

install_gh() {
  tar -xzf "${GH_ARCHIVE}" -C "${GH_EXTRACT_ROOT}"
  install -o root -g root -m 0755 \
    "${GH_EXTRACT_ROOT}/gh_2.96.0_linux_amd64/bin/gh" /usr/local/bin/gh
  [[ "$(/usr/local/bin/gh version | head -1)" == "gh version 2.96.0 (2026-07-02)" ]] ||
    die "installed GitHub CLI version is unexpected"
}

configure_docker_proxy() {
  timeout 3 bash -c '</dev/tcp/127.0.0.1/7890' 2>/dev/null ||
    die "Mihomo proxy is not listening on 127.0.0.1:7890"
  local temporary
  temporary="$(mktemp)"
  printf '%s\n' \
    '[Service]' \
    "Environment=\"HTTP_PROXY=${DOCKER_PROXY_URL}\"" \
    "Environment=\"HTTPS_PROXY=${DOCKER_PROXY_URL}\"" \
    'Environment="NO_PROXY=localhost,127.0.0.1,::1"' > "${temporary}"
  if [[ ! -f "${DOCKER_PROXY_DROPIN}" ]] || ! cmp -s "${temporary}" "${DOCKER_PROXY_DROPIN}"; then
    install -d -o root -g root -m 0755 /etc/systemd/system/docker.service.d
    install -o root -g root -m 0644 "${temporary}" "${DOCKER_PROXY_DROPIN}"
    systemctl daemon-reload
    systemctl restart docker.service
    systemctl is-active --quiet docker.service || die "docker.service did not recover after proxy configuration"
  fi
  rm -f "${temporary}"
  systemctl show docker.service -p Environment | grep -Fq "HTTPS_PROXY=${DOCKER_PROXY_URL}" ||
    die "Docker daemon did not load the Mihomo proxy environment"
}

pull_runtime_images() {
  local image
  for image in \
    "${UBUNTU_IMAGE}" \
    redis:7-alpine \
    oliver006/redis_exporter:v1.69.0 \
    prom/prometheus:v2.53.0 \
    prom/alertmanager:v0.28.1 \
    grafana/grafana:10.4.2; do
    printf 'Pulling %s\n' "${image}"
    docker pull "${image}"
  done
}

build_images() {
  python3 "${CURRENT}/scripts/tools/build_release_images.py" \
    --staging-dir "${CURRENT}" \
    --env-path /etc/boost-gateway/compose-images.env \
    --summary-path "${EVIDENCE_DIR}/image-build-summary.json"
  chown root:boost-gateway /etc/boost-gateway/compose-images.env
  chmod 0640 /etc/boost-gateway/compose-images.env
}

prepare_secrets() {
  if [[ ! -e /etc/boost-gateway/compose.env ]]; then
    umask 0027
    printf 'GRAFANA_ADMIN_PASSWORD=%s\n' "$(openssl rand -hex 32)" > /etc/boost-gateway/compose.env
  fi
  chown root:boost-gateway /etc/boost-gateway/compose.env
  chmod 0640 /etc/boost-gateway/compose.env
}

start_and_verify() {
  set -a
  # shellcheck disable=SC1091
  source /etc/boost-gateway/compose-images.env
  # shellcheck disable=SC1091
  source /etc/boost-gateway/compose.env
  set +a
  systemctl daemon-reload
  systemctl enable --now boost-gateway-compose.service

  python3 "${CURRENT}/scripts/tools/verify_release_deployment.py" \
    --staging-dir "${CURRENT}" \
    --compose-file "${CURRENT}/deploy/operations/docker-compose.production.yml" \
    --summary-path "${EVIDENCE_DIR}/deployment-verification-summary.json"
  python3 "${CONTROLLER}/scripts/check_operations_host.py" admit \
    --policy /etc/boost-gateway/operations-host-policy.json \
    --summary-path /var/lib/boost-gateway-evidence/host-admission-summary.json
}

on_error() {
  local exit_code=$?
  printf 'verified release deploy: diagnostic snapshot follows\n' >&2
  systemctl --no-pager --full status boost-gateway-compose.service >&2 || true
  docker compose -f "${CURRENT}/deploy/operations/docker-compose.production.yml" ps >&2 || true
  docker ps -a --format 'table {{.Names}}\t{{.Status}}' >&2 || true
  for container in boost-redis boost-gateway boost-prometheus boost-grafana; do
    printf '%s logs:\n' "${container}" >&2
    docker logs --tail 80 "${container}" >&2 || true
  done
  exit "${exit_code}"
}

main() {
  guard_target_host
  require_command awk
  require_command docker
  require_command cmp
  require_command openssl
  require_command python3
  require_command sha256sum
  require_command systemctl
  require_command tar
  require_command timeout
  validate_inputs
  trap on_error ERR

  printf 'Target guard: PASS (Ubuntu 24.04 x86_64)\n'
  install_gh
  install_release
  configure_docker_proxy
  pull_runtime_images
  build_images
  prepare_secrets
  start_and_verify
  trap - ERR
  printf 'verified release deploy: PASS\n'
  printf 'deployment summary: %s\n' "${EVIDENCE_DIR}/deployment-verification-summary.json"
  printf 'Next step: prepare the governed real reboot; this script does not reboot automatically.\n'
}

main "$@"
