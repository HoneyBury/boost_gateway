#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage: run_release_failure_drill.sh <candidate-deployment-id> <expected-current-id> <candidate-gateway-image-id>

Runs a controlled post-activation verification failure by pausing Prometheus,
then proves that the release lifecycle manager restores the verified current.
USAGE
}

[[ $# -eq 3 ]] || {
  usage >&2
  exit 2
}

[[ ${EUID} -eq 0 ]] || {
  printf 'release failure drill: FAIL: run with sudo\n' >&2
  exit 1
}

MANAGER=${BOOST_GATEWAY_LIFECYCLE_MANAGER:-/home/honeybury/boost-gateway-controller/scripts/manage_release_deployment.py}
CANDIDATE=$1
EXPECTED_CURRENT=$2
TARGET_GATEWAY=$3
PAUSED_CONTAINER=boost-prometheus
PAUSE_SECONDS=${BOOST_GATEWAY_DRILL_PAUSE_SECONDS:-120}
WATCH_TIMEOUT_SECONDS=${BOOST_GATEWAY_DRILL_WATCH_TIMEOUT_SECONDS:-180}
TRANSACTION_ROOT=/var/lib/boost-gateway/deployment-transactions

[[ -f ${MANAGER} ]] || {
  printf 'release failure drill: FAIL: manager is missing: %s\n' "${MANAGER}" >&2
  exit 1
}
[[ ${TARGET_GATEWAY} =~ ^sha256:[0-9a-f]{64}$ ]] || {
  printf 'release failure drill: FAIL: target gateway image ID is invalid\n' >&2
  exit 1
}
[[ ${PAUSE_SECONDS} =~ ^[0-9]+$ && ${PAUSE_SECONDS} -ge 90 ]] || {
  printf 'release failure drill: FAIL: pause must be at least 90 seconds\n' >&2
  exit 1
}

cleanup() {
  docker unpause "${PAUSED_CONTAINER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

candidate_is_activated() {
  python3 - "${TRANSACTION_ROOT}" "${CANDIDATE}" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
candidate = sys.argv[2]
for record_path in sorted(root.glob("*/record.json"), reverse=True):
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("operation") == "upgrade" and record.get("candidate") == candidate:
        raise SystemExit(0 if record.get("status") == "candidate_activated" else 1)
raise SystemExit(1)
PY
}

initial_status=$(python3 "${MANAGER}" status)
STATUS_JSON=${initial_status} EXPECTED_CURRENT=${EXPECTED_CURRENT} python3 - <<'PY'
import json
import os

status = json.loads(os.environ["STATUS_JSON"])
assert status["overall_pass"] is True, status
assert status["current"] == os.environ["EXPECTED_CURRENT"], status
PY

(
  deadline=$((SECONDS + WATCH_TIMEOUT_SECONDS))
  while ! candidate_is_activated; do
    if ((SECONDS >= deadline)); then
      printf 'fault worker: candidate transaction did not become active\n' >&2
      exit 1
    fi
    sleep 0.2
  done

  if [[ $(docker inspect --format '{{.Image}}' boost-gateway) != "${TARGET_GATEWAY}" ]]; then
    printf 'fault worker: active gateway image differs from candidate\n' >&2
    exit 1
  fi

  while ! docker pause "${PAUSED_CONTAINER}" >/dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
      printf 'fault worker: active Prometheus container could not be paused\n' >&2
      exit 1
    fi
    sleep 0.2
  done
  printf 'fault worker: Prometheus paused\n'
  sleep "${PAUSE_SECONDS}"
  docker unpause "${PAUSED_CONTAINER}" >/dev/null
  printf 'fault worker: Prometheus resumed\n'
) &
fault_pid=$!

set +e
python3 "${MANAGER}" upgrade --deployment-id "${CANDIDATE}"
upgrade_rc=$?
set -e

if ! wait "${fault_pid}"; then
  printf 'release failure drill: FAIL: fault injection did not complete\n' >&2
  exit 1
fi
cleanup

if [[ ${upgrade_rc} -eq 0 ]]; then
  printf 'release failure drill: FAIL: candidate unexpectedly passed\n' >&2
  exit 1
fi

status_json=$(python3 "${MANAGER}" status)
printf '%s\n' "${status_json}"
STATUS_JSON=${status_json} EXPECTED_CURRENT=${EXPECTED_CURRENT} python3 - <<'PY'
import json
import os

status = json.loads(os.environ["STATUS_JSON"])
assert status["overall_pass"] is True, status
assert status["current"] == os.environ["EXPECTED_CURRENT"], status
PY

python3 - "${TRANSACTION_ROOT}" "${CANDIDATE}" "${EXPECTED_CURRENT}" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
candidate = sys.argv[2]
expected_current = sys.argv[3]
match = None
for record_path in sorted(root.glob("*/record.json"), reverse=True):
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("operation") == "upgrade" and record.get("candidate") == candidate:
        match = (record_path.parent, record)
        break

assert match is not None, "failure-drill transaction not found"
transaction, record = match
assert record["status"] == "rolled_back", record
assert record["restored_current"] == expected_current, record

failed = json.loads(
    (transaction / "deployment-verification-summary.json").read_text(encoding="utf-8")
)
recovered = json.loads(
    (transaction / "recovery-verification-summary.json").read_text(encoding="utf-8")
)
assert failed["overall_pass"] is False, failed
assert recovered["overall_pass"] is True, recovered

print(f"transaction={record['transaction_id']}")
print(f"failure_step={failed.get('failed_step')}")
print(f"restored_current={record['restored_current']}")
print("TODO-0010 automatic recovery drill: PASS")
PY
