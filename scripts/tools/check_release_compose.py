#!/usr/bin/env python3
"""Validate the resolved production Compose contract for release images."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_COMPOSE = ROOT / "deploy/operations/docker-compose.production.yml"
PROJECT_SERVICES = {
    "gateway",
    "login-backend",
    "room-backend",
    "battle-backend",
    "matchmaking-backend",
    "leaderboard-backend",
}
REQUIRED_RUNTIME_SERVICES = PROJECT_SERVICES | {
    "redis",
    "redis-exporter",
    "node-exporter",
    "cadvisor",
    "prometheus",
    "alertmanager",
    "grafana",
}
NODE_EXPORTER_IMAGE = "quay.io/prometheus/node-exporter:v1.8.2"
CADVISOR_IMAGE = "gcr.io/cadvisor/cadvisor:v0.49.1"
NODE_EXPORTER_BINDS = {
    ("/", "/host"),
    ("/run/dbus/system_bus_socket", "/var/run/dbus/system_bus_socket"),
    (
        "/var/lib/boost-gateway-evidence/metrics",
        "/var/lib/node_exporter/textfile_collector",
    ),
}
CADVISOR_BINDS = {
    ("/", "/rootfs"),
    ("/var/run", "/var/run"),
    ("/sys", "/sys"),
    ("/var/lib/docker", "/var/lib/docker"),
    ("/dev/disk", "/dev/disk"),
}
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
LOOPBACK_ADDRESSES = {"127.0.0.1", "::1"}


class ComposeContractError(RuntimeError):
    """Raised when Docker Compose cannot produce a validation document."""


def load_compose_document(
    compose_file: Path, *, environment: dict[str, str] | None = None
) -> dict[str, Any]:
    command = [
        "docker",
        "compose",
        "-f",
        str(compose_file.resolve()),
        "config",
        "--format",
        "json",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment or os.environ.copy(),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ComposeContractError(f"docker compose config failed: {detail}")
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ComposeContractError(
            f"docker compose returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise ComposeContractError("docker compose document is not a JSON object")
    return document


def _positive(value: object) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _positive_duration(value: object) -> bool:
    if _positive(value):
        return True
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ns|us|ms|s|m|h)", value)
    return match is not None and float(match.group(1)) > 0


def _logging_is_bounded(service: dict[str, Any]) -> bool:
    logging = service.get("logging")
    if not isinstance(logging, dict) or logging.get("driver") != "json-file":
        return False
    options = logging.get("options")
    if not isinstance(options, dict):
        return False
    max_size = str(options.get("max-size", ""))
    max_file = str(options.get("max-file", ""))
    return bool(re.fullmatch(r"[1-9][0-9]*[kKmMgG]", max_size)) and bool(
        re.fullmatch(r"[1-9][0-9]*", max_file)
    )


def _healthcheck_is_bounded(service: dict[str, Any]) -> bool:
    healthcheck = service.get("healthcheck")
    return (
        isinstance(healthcheck, dict)
        and bool(healthcheck.get("test"))
        and _positive_duration(healthcheck.get("interval"))
        and _positive_duration(healthcheck.get("timeout"))
        and _positive(healthcheck.get("retries"))
        and healthcheck.get("disable") is not True
    )


def _volume_entries(service: dict[str, Any]) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    raw_volumes = service.get("volumes")
    if not isinstance(raw_volumes, list):
        return entries
    for raw in raw_volumes:
        if isinstance(raw, dict):
            entries.append(
                (
                    str(raw.get("type", "")),
                    str(raw.get("source", "")),
                    str(raw.get("target", "")),
                )
            )
        elif isinstance(raw, str):
            parts = raw.split(":", 2)
            if len(parts) >= 2 and parts[0] and not parts[0].startswith((".", "/")):
                entries.append(("volume", parts[0], parts[1]))
    return entries


def _read_only_bind_entries(service: dict[str, Any]) -> set[tuple[str, str]]:
    entries: set[tuple[str, str]] = set()
    raw_volumes = service.get("volumes")
    if not isinstance(raw_volumes, list):
        return entries
    for raw in raw_volumes:
        if (
            isinstance(raw, dict)
            and raw.get("type") == "bind"
            and raw.get("read_only") is True
        ):
            entries.add((str(raw.get("source", "")), str(raw.get("target", ""))))
    return entries


def _command_arguments(service: dict[str, Any]) -> set[str]:
    command = service.get("command")
    if isinstance(command, list):
        return {str(argument) for argument in command}
    if isinstance(command, str):
        return set(command.split())
    return set()


def redis_persistence_mode(service: object) -> str:
    if not isinstance(service, dict):
        return "unknown"
    command = service.get("command")
    if isinstance(command, str):
        arguments = command.split()
    elif isinstance(command, list):
        arguments = [str(item) for item in command]
    else:
        return "unknown"
    for index, argument in enumerate(arguments):
        if argument == "--appendonly" and index + 1 < len(arguments):
            return "rdb_only" if arguments[index + 1].lower() == "no" else "aof_other"
    config_targets = {
        str(item.get("target", ""))
        for item in service.get("volumes", [])
        if isinstance(item, dict)
        and item.get("type") == "bind"
        and item.get("read_only") is True
    }
    if any(
        argument.lower().endswith(".conf") and argument in config_targets
        for argument in arguments[1:]
    ):
        return "aof_everysec_rdb"
    return "unknown"


def _prometheus_retention_days(service: dict[str, Any]) -> float | None:
    prefix = "--storage.tsdb.retention.time="
    for argument in _command_arguments(service):
        if not argument.startswith(prefix):
            continue
        value = argument[len(prefix) :]
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([dwy])", value)
        if match is None:
            return None
        multiplier = {"d": 1.0, "w": 7.0, "y": 365.0}[match.group(2)]
        return float(match.group(1)) * multiplier
    return None


def _validate_node_exporter(service: dict[str, Any], failures: list[str]) -> None:
    if service.get("image") != NODE_EXPORTER_IMAGE:
        failures.append(f"node-exporter: image must be {NODE_EXPORTER_IMAGE}")
    if service.get("pull_policy") != "never":
        failures.append("node-exporter: pull_policy must be never")
    if service.get("pid") != "host":
        failures.append("node-exporter: host PID namespace is required")
    volumes = service.get("volumes")
    if (
        not isinstance(volumes, list)
        or len(volumes) != len(NODE_EXPORTER_BINDS)
        or _read_only_bind_entries(service) != NODE_EXPORTER_BINDS
    ):
        failures.append(
            "node-exporter: exact read-only host and D-Bus binds are required"
        )
    required_arguments = {
        "--path.rootfs=/host",
        "--collector.hwmon",
        "--collector.thermal_zone",
        "--collector.systemd",
        "--collector.textfile.directory=/var/lib/node_exporter/textfile_collector",
    }
    if not required_arguments.issubset(_command_arguments(service)):
        failures.append(
            "node-exporter: required host and thermal collectors are missing"
        )
    if service.get("devices"):
        failures.append("node-exporter: host devices are forbidden")
    if service.get("ports"):
        failures.append("node-exporter: published ports are forbidden")


def _validate_cadvisor(service: dict[str, Any], failures: list[str]) -> None:
    if service.get("image") != CADVISOR_IMAGE:
        failures.append(f"cadvisor: image must be {CADVISOR_IMAGE}")
    if service.get("pull_policy") != "never":
        failures.append("cadvisor: pull_policy must be never")
    if service.get("privileged") is not True:
        failures.append("cadvisor: governed privileged mode is required")
    if service.get("read_only") is not True:
        failures.append("cadvisor: read-only root filesystem is required")
    volumes = service.get("volumes")
    if (
        not isinstance(volumes, list)
        or len(volumes) != len(CADVISOR_BINDS)
        or _read_only_bind_entries(service) != CADVISOR_BINDS
    ):
        failures.append("cadvisor: exact read-only host metric binds are required")
    devices = service.get("devices")
    expected_devices = [
        {"source": "/dev/kmsg", "target": "/dev/kmsg", "permissions": "rwm"}
    ]
    if devices != expected_devices:
        failures.append("cadvisor: /dev/kmsg must be the only host device")
    if service.get("tmpfs") != ["/tmp"]:
        failures.append("cadvisor: /tmp must be the only tmpfs mount")
    if service.get("ports"):
        failures.append("cadvisor: published ports are forbidden")


def _validate_prometheus(service: dict[str, Any], failures: list[str]) -> None:
    retention_days = _prometheus_retention_days(service)
    if retention_days is None or retention_days < 45:
        failures.append("prometheus: TSDB retention must be at least 45 days")
    data_volumes = [
        (source, target)
        for kind, source, target in _volume_entries(service)
        if kind == "volume" and target == "/prometheus"
    ]
    if len(data_volumes) != 1:
        failures.append(
            "prometheus: exactly one persistent /prometheus volume is required"
        )


def _validate_grafana(service: dict[str, Any], failures: list[str]) -> None:
    environment = service.get("environment")
    if not isinstance(environment, dict):
        failures.append("grafana: resolved admin credentials are required")
        return
    username = str(environment.get("GF_SECURITY_ADMIN_USER", "")).strip().lower()
    password = str(environment.get("GF_SECURITY_ADMIN_PASSWORD", ""))
    if not username or username == "admin":
        failures.append("grafana: default or empty admin username is forbidden")
    forbidden_passwords = {
        "admin",
        "password",
        "boost-gateway-change-me",
        "changeme",
    }
    if len(password) < 20 or password.lower() in forbidden_passwords:
        failures.append("grafana: default, empty, or short admin password is forbidden")


def _validate_alertmanager(service: dict[str, Any], failures: list[str]) -> None:
    config_binds = {
        (source, target) for source, target in _read_only_bind_entries(service)
    }
    expected_binds = {
        (
            "/etc/boost-gateway/alertmanager.yml",
            "/etc/alertmanager/alertmanager.yml",
        ),
        (
            "/etc/boost-gateway/alertmanager-secrets",
            "/etc/alertmanager/secrets",
        ),
    }
    if config_binds != expected_binds:
        failures.append(
            "alertmanager: exact root-managed config and secret binds are required"
        )
    if re.fullmatch(r"65534:[1-9][0-9]*", str(service.get("user", ""))) is None:
        failures.append(
            "alertmanager: unprivileged UID and governed host group are required"
        )


def _validate_redis(service: dict[str, Any], failures: list[str]) -> None:
    if service.get("user") != "redis":
        failures.append("redis: image redis user is required")
    if service.get("cap_add"):
        failures.append("redis: added Linux capabilities are forbidden")
    if service.get("cap_drop") != ["ALL"]:
        failures.append("redis: all Linux capabilities must be dropped")


def validate_compose_document(document: object) -> list[str]:
    failures: list[str] = []
    if not isinstance(document, dict):
        return ["Compose document must be a JSON object"]
    services = document.get("services")
    volumes = document.get("volumes")
    if not isinstance(services, dict):
        return ["Compose document has no services object"]
    if not isinstance(volumes, dict):
        failures.append("Compose document has no named volumes object")
        volumes = {}

    missing = sorted(REQUIRED_RUNTIME_SERVICES - set(services))
    if missing:
        failures.append("missing project services: " + ", ".join(missing))

    public_gateway_ports = 0
    for name, raw_service in sorted(services.items()):
        if not isinstance(raw_service, dict):
            failures.append(f"{name}: service definition is not an object")
            continue
        service = raw_service
        if "build" in service:
            failures.append(f"{name}: source build is forbidden")
        if service.get("network_mode") == "host":
            failures.append(f"{name}: host network mode is forbidden")
        if service.get("privileged") is True and name != "cadvisor":
            failures.append(f"{name}: privileged containers are forbidden")
        if service.get("restart") not in {"always", "unless-stopped"}:
            failures.append(f"{name}: restart policy must survive host restart")
        if not _positive(service.get("cpus")):
            failures.append(f"{name}: positive cpus limit is required")
        if not _positive(service.get("mem_limit")):
            failures.append(f"{name}: positive mem_limit is required")
        if not _positive(service.get("pids_limit")):
            failures.append(f"{name}: positive pids_limit is required")
        if not _healthcheck_is_bounded(service):
            failures.append(f"{name}: bounded healthcheck is required")
        if not _logging_is_bounded(service):
            failures.append(f"{name}: bounded json-file logging is required")

        if name in PROJECT_SERVICES:
            image = str(service.get("image", ""))
            if IMAGE_ID_RE.fullmatch(image) is None:
                failures.append(f"{name}: image must be an immutable sha256 image ID")
            if service.get("pull_policy") != "never":
                failures.append(f"{name}: pull_policy must be never")
            named_logs = [
                (source, target)
                for kind, source, target in _volume_entries(service)
                if kind == "volume" and target == "/app/logs"
            ]
            if len(named_logs) != 1:
                failures.append(
                    f"{name}: exactly one named /app/logs volume is required"
                )
            elif named_logs[0][0] not in volumes:
                failures.append(f"{name}: log volume is not declared at top level")

        if name == "node-exporter":
            _validate_node_exporter(service, failures)
        elif name == "cadvisor":
            _validate_cadvisor(service, failures)
        elif name == "prometheus":
            _validate_prometheus(service, failures)
        elif name == "grafana":
            _validate_grafana(service, failures)
        elif name == "alertmanager":
            _validate_alertmanager(service, failures)
        elif name == "redis":
            _validate_redis(service, failures)

        raw_ports = service.get("ports", [])
        if not isinstance(raw_ports, list):
            failures.append(f"{name}: ports must be a structured list")
            raw_ports = []
        for raw_port in raw_ports:
            if not isinstance(raw_port, dict):
                failures.append(f"{name}: port mapping is not a structured object")
                continue
            try:
                target = int(raw_port.get("target"))
                published = int(raw_port.get("published"))
            except (TypeError, ValueError):
                failures.append(
                    f"{name}: port mapping has invalid published or target port"
                )
                continue
            if raw_port.get("protocol", "tcp") != "tcp":
                failures.append(f"{name}: only TCP port mappings are supported")
                continue
            host_ip = str(raw_port.get("host_ip", ""))
            is_gateway_business = (
                name == "gateway" and published == 9201 and target == 9201
            )
            if is_gateway_business:
                if host_ip in LOOPBACK_ADDRESSES:
                    failures.append(
                        "gateway: business port 9201 must be externally reachable"
                    )
                else:
                    public_gateway_ports += 1
            elif host_ip not in LOOPBACK_ADDRESSES:
                failures.append(
                    f"{name}: published port {published}:{target} must bind to loopback"
                )

    if public_gateway_ports != 1:
        failures.append(
            "exactly one externally reachable gateway 9201 mapping is required"
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument(
        "--document",
        type=Path,
        help="Validate an already-resolved Compose JSON document instead of invoking Docker",
    )
    args = parser.parse_args()
    try:
        if args.document:
            document = json.loads(args.document.read_text(encoding="utf-8"))
        else:
            document = load_compose_document(args.compose_file)
    except (OSError, json.JSONDecodeError, ComposeContractError) as exc:
        print(f"release Compose contract: FAIL ({exc})", file=sys.stderr)
        return 1

    failures = validate_compose_document(document)
    if failures:
        print("release Compose contract: FAIL", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    if sys.platform == "linux" and os.geteuid() == 0:
        try:
            from scripts.tools.check_observability_preflight import (
                DEFAULT_ATTESTATION,
                DEFAULT_CONFIG,
                DEFAULT_ENV,
                DEFAULT_SUMMARY,
                validate_preflight,
            )

            summary = validate_preflight(
                DEFAULT_CONFIG, DEFAULT_ENV, DEFAULT_ATTESTATION
            )
            DEFAULT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
            atomic_content = json.dumps(summary, indent=2, sort_keys=True) + "\n"
            DEFAULT_SUMMARY.write_text(atomic_content, encoding="utf-8")
            os.chmod(DEFAULT_SUMMARY, 0o640)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print(
                f"release Compose contract: FAIL (observability: {exc})",
                file=sys.stderr,
            )
            return 1
    print("release Compose contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
