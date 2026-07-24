#!/usr/bin/env python3
"""Fail-closed admission and reboot verification for the Ubuntu operations host."""

from __future__ import annotations

import argparse
import grp
import hashlib
import ipaddress
import json
import os
import platform
import pwd
import re
import socket
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY = ROOT / "deploy/operations/operations-host-policy.json"
DEFAULT_SUMMARY = Path("runtime/validation/operations-host-admission-summary.json")
DEFAULT_REBOOT_MARKER = Path("/etc/boost-gateway/reboot-challenge.json")


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class Report:
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str, **facts: Any) -> None:
        check: dict[str, Any] = {"name": name, "passed": passed, "detail": detail}
        check.update(facts)
        self.checks.append(check)

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [check for check in self.checks if not check["passed"]]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def run(command: Sequence[str], timeout: int = 15) -> CommandResult:
    try:
        environment = dict(os.environ)
        environment.update({"LANG": "C", "LC_ALL": "C"})
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
        return CommandResult(
            tuple(command), completed.returncode, completed.stdout, completed.stderr
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(tuple(command), 127, "", f"{type(exc).__name__}: {exc}")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def atomic_write_json(path: Path, value: dict[str, Any], mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def parse_lscpu_json(text: str) -> dict[str, str]:
    parsed = json.loads(text)
    rows = parsed.get("lscpu", []) if isinstance(parsed, dict) else []
    return {
        str(row.get("field", "")).rstrip(":"): str(row.get("data", ""))
        for row in rows
        if isinstance(row, dict)
    }


def parse_meminfo(text: str) -> int:
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB$", text, flags=re.MULTILINE)
    if not match:
        raise ValueError("MemTotal is missing from /proc/meminfo")
    return int(match.group(1)) * 1024


def parse_version_major(text: str) -> int | None:
    match = re.search(r"(?:^|[^0-9])(\d+)(?:\.\d+)", text)
    return int(match.group(1)) if match else None


def listener_host_port(local_address: str) -> tuple[str, int]:
    value = local_address.strip()
    if value.startswith("[") and "]:" in value:
        host, port = value[1:].rsplit("]:", 1)
    else:
        host, port = value.rsplit(":", 1)
    return host, int(port)


def parse_ss_listeners(text: str) -> list[dict[str, Any]]:
    listeners: list[dict[str, Any]] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        host, port = listener_host_port(fields[3])
        listeners.append({"host": host, "port": port})
    return listeners


def address_scope(host: str, trusted_cidrs: Sequence[str]) -> str:
    normalized = host.split("%", 1)[0]
    if normalized in {"*", "0.0.0.0", "::"}:
        return "wildcard"
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return "unknown"
    if address.is_loopback:
        return "loopback"
    trusted_networks = [ipaddress.ip_network(cidr) for cidr in trusted_cidrs]
    if any(
        address.version == network.version and address in network
        for network in trusted_networks
    ):
        return "trusted"
    return "public"


def evaluate_listener_boundary(
    listeners: Sequence[dict[str, Any]],
    policy: dict[str, Any],
    require_public_listener: bool = True,
) -> tuple[bool, list[dict[str, Any]], list[str]]:
    public_ports = {int(value) for value in policy["public_tcp_ports"]}
    restricted_ports = {int(value) for value in policy["restricted_tcp_ports"]}
    firewall_protected_ports = {
        int(value) for value in policy.get("firewall_protected_tcp_ports", [])
    }
    trusted_cidrs = [str(value) for value in policy["trusted_cidrs"]]
    evaluated: list[dict[str, Any]] = []
    errors: list[str] = []
    public_seen: set[int] = set()
    for listener in listeners:
        host = str(listener["host"])
        port = int(listener["port"])
        scope = address_scope(host, trusted_cidrs)
        evaluated.append({"host": host, "port": port, "scope": scope})
        externally_reachable = scope in {"wildcard", "public"}
        if externally_reachable:
            public_seen.add(port)
            if port not in public_ports and port not in firewall_protected_ports:
                errors.append(
                    f"TCP {host}:{port} is externally reachable but is not an approved public port"
                )
        if (
            port in restricted_ports
            and scope not in {"loopback", "trusted"}
            and port not in firewall_protected_ports
        ):
            errors.append(
                f"restricted TCP {host}:{port} is not bound to loopback or a trusted network"
            )
        if scope == "unknown":
            errors.append(f"cannot classify listener address {host}:{port}")
    required = int(policy["required_public_listener"])
    if require_public_listener and required not in public_seen:
        errors.append(f"required public gateway listener TCP {required} is absent")
    return not errors, evaluated, errors


def evaluate_ufw_policy(
    text: str, network_policy: dict[str, Any]
) -> tuple[bool, list[str]]:
    lowered = text.lower()
    errors: list[str] = []
    if "status: active" not in lowered:
        errors.append("UFW is not active")
    if re.search(r"default:\s+deny\s+\(incoming\)", lowered) is None:
        errors.append("UFW incoming default is not deny")
    public_ports = {int(value) for value in network_policy["public_tcp_ports"]}
    trusted = [
        ipaddress.ip_network(str(value)) for value in network_policy["trusted_cidrs"]
    ]
    required_trusted_ports = {
        int(value) for value in network_policy.get("required_trusted_tcp_ports", [])
    }
    trusted_ports_seen: set[int] = set()
    public_gateway_allowed = False
    for line in text.splitlines():
        if "allow" not in line.lower() or line.lstrip().lower().startswith("default:"):
            continue
        fields = line.replace(" (v6)", "").split()
        if not fields:
            continue
        port_match = re.match(r"(\d+)(?:/tcp)?$", fields[0])
        if not port_match:
            errors.append(f"cannot classify UFW allow rule: {line.strip()}")
            continue
        port = int(port_match.group(1))
        source = fields[-1].replace("(v6)", "").strip()
        if source.lower() == "anywhere":
            if port in public_ports:
                public_gateway_allowed = True
            else:
                errors.append(f"UFW allows non-gateway TCP {port} from Anywhere")
            continue
        try:
            source_network = ipaddress.ip_network(source, strict=False)
        except ValueError:
            errors.append(f"cannot classify UFW allow source {source!r} for TCP {port}")
            continue
        if not any(
            source_network.version == network.version
            and source_network.subnet_of(network)
            for network in trusted
        ):
            errors.append(
                f"UFW allow source {source} for TCP {port} is outside trusted networks"
            )
        else:
            trusted_ports_seen.add(port)
    required = int(network_policy["required_public_listener"])
    if required in public_ports and not public_gateway_allowed:
        errors.append(f"UFW does not allow public gateway TCP {required}")
    missing_trusted_ports = sorted(required_trusted_ports - trusted_ports_seen)
    if missing_trusted_ports:
        errors.append(
            "UFW lacks trusted-network allow rules for TCP ports "
            + ", ".join(str(port) for port in missing_trusted_ports)
        )
    return not errors, errors


def machine_id_sha256(path: Path = Path("/etc/machine-id")) -> str:
    value = path.read_bytes()
    if not value.strip():
        raise ValueError("machine-id is empty")
    return hashlib.sha256(value).hexdigest()


def boot_id(path: Path = Path("/proc/sys/kernel/random/boot_id")) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("boot_id is empty")
    return value


def evaluate_reboot_marker(
    marker: dict[str, Any], host_id: str, current_boot_id: str
) -> bool:
    boot_id_after = marker.get("boot_id_after")
    return (
        marker.get("schema_version") == 1
        and marker.get("host_id_sha256") == host_id
        and bool(marker.get("boot_id_before"))
        and marker.get("boot_id_before") != current_boot_id
        and boot_id_after in {None, current_boot_id}
    )


def smartctl_health_command(device_args: Sequence[str]) -> list[str]:
    if not device_args:
        raise ValueError("SMART device arguments are empty")
    return ["smartctl", "-H", "-j", *device_args[1:], device_args[0]]


def check_command(report: Report, name: str, command: Sequence[str]) -> CommandResult:
    result = run(command)
    report.add(
        name,
        result.returncode == 0,
        (
            "command completed"
            if result.returncode == 0
            else "command failed or is unavailable"
        ),
        command=list(command),
        returncode=result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
    )
    return result


def check_platform(report: Report, policy: dict[str, Any]) -> None:
    target = policy["target"]
    report.add(
        "host:root-execution",
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "admission runs as root so protected host facts are readable",
        effective_uid=os.geteuid() if hasattr(os, "geteuid") else None,
    )
    release_error = ""
    try:
        release = parse_os_release(Path("/etc/os-release").read_text(encoding="utf-8"))
    except OSError as exc:
        release_error = str(exc)
        release = {}
    os_pass = (
        release.get("ID") == target["os_id"]
        and release.get("VERSION_ID") == target["os_version"]
    )
    report.add(
        "host:os",
        os_pass,
        "Ubuntu release matches policy",
        facts=release,
        error=release_error,
    )

    architecture = platform.machine().lower()
    report.add(
        "host:architecture",
        architecture in {str(value).lower() for value in target["architectures"]},
        "machine architecture matches policy",
        architecture=architecture,
        kernel=platform.release(),
    )

    lscpu = check_command(report, "host:lscpu-command", ["lscpu", "-J"])
    if lscpu.returncode == 0:
        try:
            cpu = parse_lscpu_json(lscpu.stdout)
            logical = int(cpu.get("CPU(s)", "0"))
            sockets = int(cpu.get("Socket(s)", "0"))
            cores_per_socket = int(cpu.get("Core(s) per socket", "0"))
            physical_cores = sockets * cores_per_socket
            report.add(
                "host:cpu-capacity",
                logical >= int(target["min_logical_cpus"])
                and physical_cores >= int(target["min_physical_cores"]),
                "physical and logical CPU capacity meet policy",
                logical_cpus=logical,
                physical_cores=physical_cores,
                model=cpu.get("Model name", ""),
                sockets=sockets,
                cores_per_socket=cores_per_socket,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            report.add("host:cpu-capacity", False, f"cannot parse lscpu JSON: {exc}")
    try:
        memory_bytes = parse_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8"))
        report.add(
            "host:memory-capacity",
            memory_bytes >= int(target["min_memory_bytes"]),
            "physical memory meets policy",
            memory_bytes=memory_bytes,
        )
    except (OSError, ValueError) as exc:
        report.add(
            "host:memory-capacity", False, f"cannot determine physical memory: {exc}"
        )

    findmnt = check_command(
        report,
        "host:filesystem-command",
        [
            "findmnt",
            "-J",
            "-b",
            "-T",
            "/",
            "-o",
            "TARGET,SOURCE,FSTYPE,SIZE,AVAIL,OPTIONS",
        ],
    )
    if findmnt.returncode == 0:
        try:
            filesystems = json.loads(findmnt.stdout)["filesystems"]
            root = filesystems[0]
            filesystem_pass = (
                int(root["size"]) >= int(target["min_root_filesystem_bytes"])
                and int(root["avail"]) >= int(target["min_root_available_bytes"])
                and root["fstype"] in target["allowed_root_filesystems"]
                and "rw" in str(root.get("options", "")).split(",")
            )
            report.add(
                "host:root-filesystem",
                filesystem_pass,
                "root filesystem meets capacity and type policy",
                facts=root,
            )
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            report.add(
                "host:root-filesystem",
                False,
                f"cannot parse root filesystem facts: {exc}",
            )


def check_docker(report: Report, policy: dict[str, Any]) -> None:
    server = check_command(
        report,
        "docker:server",
        ["docker", "version", "--format", "{{.Server.Version}}"],
    )
    server_major = (
        parse_version_major(server.stdout) if server.returncode == 0 else None
    )
    report.add(
        "docker:server-version",
        server_major is not None
        and server_major >= int(policy["docker"]["min_server_major"]),
        "Docker server major version meets policy",
        version=server.stdout.strip(),
    )
    compose = check_command(
        report, "docker:compose", ["docker", "compose", "version", "--short"]
    )
    compose_major = (
        parse_version_major(compose.stdout) if compose.returncode == 0 else None
    )
    report.add(
        "docker:compose-version",
        compose_major is not None
        and compose_major >= int(policy["docker"]["min_compose_major"]),
        "Docker Compose major version meets policy",
        version=compose.stdout.strip(),
    )
    logging_driver = check_command(
        report,
        "docker:logging-driver-command",
        ["docker", "info", "--format", "{{.LoggingDriver}}"],
    )
    active_driver = logging_driver.stdout.strip()
    report.add(
        "docker:logging-driver-active",
        logging_driver.returncode == 0
        and active_driver in policy["logging"]["docker_log_drivers"],
        "Docker daemon is actively using an approved bounded logging driver",
        driver=active_driver,
    )
    socket_path = Path("/var/run/docker.sock")
    try:
        status = socket_path.stat()
        socket_mode = stat.S_IMODE(status.st_mode)
        passed = (
            stat.S_ISSOCK(status.st_mode)
            and status.st_uid == 0
            and socket_mode & 0o007 == 0
        )
        report.add(
            "docker:socket-permissions",
            passed,
            "Docker socket is root-owned and inaccessible to other users",
            owner_uid=status.st_uid,
            group_gid=status.st_gid,
            mode=f"{socket_mode:04o}",
        )
    except OSError as exc:
        report.add(
            "docker:socket-permissions", False, f"cannot verify Docker socket: {exc}"
        )


def check_clock_and_network(
    report: Report, policy: dict[str, Any], require_public_listener: bool
) -> None:
    timedate = check_command(
        report,
        "clock:timedatectl",
        [
            "timedatectl",
            "show",
            "--property=NTPSynchronized",
            "--property=Timezone",
            "--property=LocalRTC",
        ],
    )
    values = parse_os_release(timedate.stdout) if timedate.returncode == 0 else {}
    report.add(
        "clock:synchronized",
        values.get("NTPSynchronized") == "yes" and values.get("LocalRTC") == "no",
        "NTP is synchronized and the RTC is UTC",
        facts=values,
    )
    check_command(report, "network:addresses", ["ip", "-j", "address", "show"])
    routes = check_command(
        report, "network:default-route", ["ip", "-j", "route", "show", "default"]
    )
    if routes.returncode == 0:
        try:
            route_facts = json.loads(routes.stdout)
            report.add(
                "network:default-route-present",
                bool(route_facts),
                "at least one default route is configured",
                facts=route_facts,
            )
        except json.JSONDecodeError as exc:
            report.add(
                "network:default-route-present",
                False,
                f"cannot parse default route JSON: {exc}",
            )
    listeners = check_command(report, "network:listeners-command", ["ss", "-H", "-lnt"])
    if listeners.returncode == 0:
        try:
            passed, evaluated, errors = evaluate_listener_boundary(
                parse_ss_listeners(listeners.stdout),
                policy["network"],
                require_public_listener=require_public_listener,
            )
            report.add(
                "network:tcp-exposure",
                passed,
                "only approved TCP listeners are externally reachable",
                listeners=evaluated,
                errors=errors,
            )
        except (KeyError, ValueError) as exc:
            report.add(
                "network:tcp-exposure",
                False,
                f"cannot evaluate listener boundary: {exc}",
            )
    firewall = check_command(report, "firewall:ufw", ["ufw", "status", "verbose"])
    firewall_pass, firewall_errors = evaluate_ufw_policy(
        firewall.stdout, policy["network"]
    )
    report.add(
        "firewall:policy",
        firewall.returncode == 0 and firewall_pass,
        "UFW is default-deny and only permits public gateway or trusted-network ingress",
        errors=firewall_errors,
    )


def check_storage_and_temperature(report: Report, policy: dict[str, Any]) -> None:
    block_devices = check_command(
        report,
        "storage:block-devices",
        ["lsblk", "-J", "-b", "-d", "-o", "PATH,TYPE,SIZE,MODEL"],
    )
    if block_devices.returncode == 0:
        try:
            parsed_devices = json.loads(block_devices.stdout).get("blockdevices", [])
            physical_disks = [
                device
                for device in parsed_devices
                if isinstance(device, dict) and device.get("type") == "disk"
            ]
            required_size = int(policy["target"]["min_physical_disk_bytes"])
            report.add(
                "storage:physical-capacity",
                bool(physical_disks)
                and max(int(device.get("size", 0)) for device in physical_disks)
                >= required_size,
                "at least one physical disk meets the nominal capacity policy",
                required_bytes=required_size,
                devices=physical_disks,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            report.add(
                "storage:physical-capacity",
                False,
                f"cannot parse physical block-device facts: {exc}",
            )

    scan = check_command(report, "storage:smart-scan", ["smartctl", "--scan-open"])
    devices: list[list[str]] = []
    for line in scan.stdout.splitlines():
        command_text = line.split("#", 1)[0].strip()
        if command_text:
            devices.append(command_text.split())
    if scan.returncode != 0 or not devices:
        report.add(
            "storage:smart-health",
            False,
            "no SMART-capable storage device could be inspected",
            devices=devices,
        )
    else:
        health: list[dict[str, Any]] = []
        healthy = True
        for device_args in devices:
            result = run(smartctl_health_command(device_args))
            try:
                facts = json.loads(result.stdout) if result.stdout else {}
            except json.JSONDecodeError:
                facts = {}
            passed = (
                result.returncode == 0
                and facts.get("smart_status", {}).get("passed") is True
            )
            healthy = healthy and passed
            health.append(
                {
                    "device": " ".join(device_args),
                    "passed": passed,
                    "returncode": result.returncode,
                }
            )
        report.add(
            "storage:smart-health",
            healthy,
            "all discovered storage devices report passing SMART health",
            devices=health,
        )

    readings: list[dict[str, Any]] = []
    sensor_paths = list(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
    sensor_paths.extend(Path("/sys/class/hwmon").glob("hwmon*/temp*_input"))
    for input_path in sorted(sensor_paths):
        try:
            millidegrees = int(input_path.read_text(encoding="utf-8").strip())
            readings.append({"path": str(input_path), "celsius": millidegrees / 1000.0})
        except (OSError, ValueError):
            continue
    maximum = float(policy["power"]["max_temperature_celsius"])
    temperature_pass = bool(readings) and all(
        0.0 <= value["celsius"] < maximum for value in readings
    )
    report.add(
        "thermal:temperature",
        temperature_pass,
        "thermal sensors are readable and below the admission limit",
        limit_celsius=maximum,
        readings=readings,
    )


def check_identity_and_directories(report: Report, policy: dict[str, Any]) -> None:
    identity = policy["identity"]
    try:
        user = pwd.getpwnam(identity["user"])
        primary_group = grp.getgrgid(user.pw_gid).gr_name
        supplemental = [
            entry.gr_name
            for entry in grp.getgrall()
            if identity["user"] in entry.gr_mem
        ]
        forbidden = sorted(set(supplemental) & set(identity["forbidden_groups"]))
        identity_pass = (
            primary_group == identity["group"]
            and user.pw_shell in identity["allowed_shells"]
            and 0 < user.pw_uid < 1000
            and not forbidden
        )
        report.add(
            "identity:service-user",
            identity_pass,
            "dedicated service identity has no login shell or Docker privilege",
            user=identity["user"],
            uid=user.pw_uid,
            primary_group=primary_group,
            supplemental_groups=supplemental,
            forbidden_groups=forbidden,
            shell=user.pw_shell,
        )
    except (KeyError, LookupError) as exc:
        report.add(
            "identity:service-user",
            False,
            f"dedicated service identity is unavailable: {exc}",
        )

    for directory in policy["directories"]:
        path = Path(directory["path"])
        try:
            status = path.stat()
            expected_uid = pwd.getpwnam(directory["owner"]).pw_uid
            expected_gid = grp.getgrnam(directory["group"]).gr_gid
            actual_mode = stat.S_IMODE(status.st_mode)
            expected_mode = int(directory["mode"], 8)
            passed = (
                path.is_dir()
                and not path.is_symlink()
                and status.st_uid == expected_uid
                and status.st_gid == expected_gid
                and actual_mode == expected_mode
            )
            report.add(
                f"directory:{path}",
                passed,
                "persistent directory ownership and mode match policy",
                owner_uid=status.st_uid,
                group_gid=status.st_gid,
                mode=f"{actual_mode:04o}",
            )
        except (OSError, KeyError, LookupError, ValueError) as exc:
            report.add(
                f"directory:{path}", False, f"cannot verify persistent directory: {exc}"
            )


def check_log_policy(report: Report, policy: dict[str, Any]) -> None:
    logging_policy = policy["logging"]
    journald_path = Path(logging_policy["journald_config"])
    try:
        journald_status = journald_path.stat()
        text = journald_path.read_text(encoding="utf-8")
        values = parse_os_release(text)
        passed = (
            journald_status.st_uid == 0
            and stat.S_IMODE(journald_status.st_mode) & 0o022 == 0
            and values.get("Storage") == "persistent"
            and bool(values.get("SystemMaxUse"))
            and bool(values.get("SystemKeepFree"))
            and bool(values.get("MaxRetentionSec"))
        )
        report.add(
            "logging:journald-policy",
            passed,
            "root-owned journald policy has persistent bounded retention",
            facts=values,
            mode=f"{stat.S_IMODE(journald_status.st_mode):04o}",
        )
    except OSError as exc:
        report.add(
            "logging:journald-policy", False, f"cannot verify journald policy: {exc}"
        )

    daemon_path = Path(logging_policy["docker_daemon_config"])
    try:
        daemon_status = daemon_path.stat()
        daemon = load_json(daemon_path)
        driver = daemon.get("log-driver")
        options = daemon.get("log-opts", {})
        passed = (
            daemon_status.st_uid == 0
            and stat.S_IMODE(daemon_status.st_mode) & 0o022 == 0
            and driver in logging_policy["docker_log_drivers"]
            and isinstance(options, dict)
            and bool(options.get("max-size"))
            and bool(options.get("max-file"))
        )
        report.add(
            "logging:docker-policy",
            passed,
            "Docker default log driver has bounded rotation",
            driver=driver,
            log_options=options,
            mode=f"{stat.S_IMODE(daemon_status.st_mode):04o}",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report.add(
            "logging:docker-policy", False, f"cannot verify Docker log policy: {exc}"
        )


def check_power_and_unit(report: Report, policy: dict[str, Any], host_id: str) -> None:
    for target in policy["power"]["masked_targets"]:
        result = run(["systemctl", "is-enabled", target])
        masked = result.stdout.strip() == "masked"
        report.add(
            f"power:masked:{target}",
            masked,
            "sleep-related systemd target is masked",
            value=result.stdout.strip(),
        )

    attestation_path = Path(policy["power"]["restart_attestation"])
    try:
        status = attestation_path.stat()
        attestation = load_json(attestation_path)
        secure_mode = status.st_uid == 0 and stat.S_IMODE(status.st_mode) & 0o022 == 0
        required_fields = all(
            bool(attestation.get(key)) for key in ["verified_at", "method", "operator"]
        )
        passed = (
            secure_mode
            and attestation.get("host_id_sha256") == host_id
            and attestation.get("restart_on_power_loss") is True
            and required_fields
        )
        report.add(
            "power:restart-on-power-loss",
            passed,
            "root-owned firmware or out-of-band power recovery attestation matches this host",
            attestation={
                "host_id_sha256": attestation.get("host_id_sha256"),
                "restart_on_power_loss": attestation.get("restart_on_power_loss"),
                "verified_at": attestation.get("verified_at"),
                "method": attestation.get("method"),
                "operator": attestation.get("operator"),
            },
            mode=f"{stat.S_IMODE(status.st_mode):04o}",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report.add(
            "power:restart-on-power-loss",
            False,
            f"cannot verify power recovery attestation: {exc}",
        )

    unit_path = Path(policy["systemd"]["unit_path"])
    try:
        status = unit_path.stat()
        installed_digest = hashlib.sha256(unit_path.read_bytes()).hexdigest()
        reference_digest = str(policy["systemd"]["unit_sha256"])
        unit_secure = (
            status.st_uid == 0
            and stat.S_IMODE(status.st_mode) & 0o022 == 0
            and installed_digest == reference_digest
        )
        report.add(
            "systemd:compose-unit-file",
            unit_secure,
            "Compose lifecycle unit is root-owned, read-only and matches the governed unit",
            path=str(unit_path),
            mode=f"{stat.S_IMODE(status.st_mode):04o}",
            sha256=installed_digest,
            expected_sha256=reference_digest,
        )
    except OSError as exc:
        report.add(
            "systemd:compose-unit-file",
            False,
            f"cannot verify Compose lifecycle unit: {exc}",
        )


def check_runtime(report: Report, policy: dict[str, Any]) -> None:
    unit = str(policy["systemd"]["compose_unit"])
    for verb in ["is-enabled", "is-active"]:
        result = run(["systemctl", verb, unit])
        expected = "enabled" if verb == "is-enabled" else "active"
        report.add(
            f"systemd:compose-unit-{verb}",
            result.returncode == 0 and result.stdout.strip() == expected,
            f"Compose lifecycle unit is {expected}",
            value=result.stdout.strip(),
        )

    containers = run(["docker", "ps", "--format", "{{json .}}"])
    discovered: dict[str, dict[str, Any]] = {}
    parse_error = ""
    if containers.returncode == 0:
        for line in containers.stdout.splitlines():
            try:
                row = json.loads(line)
                discovered[str(row.get("Names", ""))] = row
            except json.JSONDecodeError as exc:
                parse_error = str(exc)
                break
    required = [str(value) for value in policy["runtime"]["required_containers"]]
    missing = [name for name in required if name not in discovered]
    unhealthy: list[str] = []
    for name in required:
        if name not in discovered:
            continue
        status = str(discovered[name].get("Status", "")).lower()
        if (
            not status.startswith("up")
            or "unhealthy" in status
            or "health: starting" in status
        ):
            unhealthy.append(name)
    report.add(
        "runtime:container-topology",
        containers.returncode == 0
        and not parse_error
        and not missing
        and not unhealthy,
        "all required service and monitoring containers are running",
        required=required,
        missing=missing,
        unhealthy=unhealthy,
        parse_error=parse_error,
    )
    for endpoint in policy["runtime"]["local_endpoints"]:
        result = run(
            [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "5",
                str(endpoint),
            ]
        )
        report.add(
            f"runtime:endpoint:{endpoint}",
            result.returncode == 0,
            "local health endpoint is reachable",
            returncode=result.returncode,
        )


def write_summary(
    path: Path,
    phase: str,
    policy_path: Path,
    report: Report,
    host_id: str,
    current_boot_id: str,
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed = report.failed
    try:
        policy_sha256 = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    except OSError:
        policy_sha256 = ""
    summary = {
        "summary_version": 2,
        "generated_at": now(),
        "phase": phase,
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "operations_host_admission" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "host": {
            "hostname": socket.gethostname(),
            "host_id_sha256": host_id,
            "boot_id": current_boot_id,
        },
        "policy": {
            "path": str(policy_path),
            "sha256": policy_sha256,
        },
        "checks": report.checks,
        "artifacts": {"summary_path": str(path), **(artifacts or {})},
    }
    atomic_write_json(path, summary)
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["admit", "prepare-reboot", "verify-reboot"])
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--reboot-marker", type=Path, default=DEFAULT_REBOOT_MARKER)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    policy_path = args.policy if args.policy.is_absolute() else ROOT / args.policy
    summary_path = (
        args.summary_path
        if args.summary_path.is_absolute()
        else ROOT / args.summary_path
    )
    reboot_marker = (
        args.reboot_marker
        if args.reboot_marker.is_absolute()
        else ROOT / args.reboot_marker
    )
    report = Report()
    try:
        policy = load_json(policy_path)
        if policy.get("schema_version") != 1:
            raise ValueError("operations host policy schema_version must be 1")
        current_host_id = machine_id_sha256()
        current_boot_id = boot_id()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report.add(
            "admission:initialization", False, f"cannot initialize admission: {exc}"
        )
        current_host_id = ""
        current_boot_id = ""
        policy = {}
        summary = write_summary(
            summary_path,
            args.phase,
            policy_path,
            report,
            current_host_id,
            current_boot_id,
        )
        print(f"operations host {args.phase}: FAIL", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 1

    policy_status = policy_path.stat()
    report.add(
        "admission:policy-file-security",
        policy_status.st_uid == 0 and stat.S_IMODE(policy_status.st_mode) & 0o022 == 0,
        "host policy is root-owned and not group/world writable",
        owner_uid=policy_status.st_uid,
        mode=f"{stat.S_IMODE(policy_status.st_mode):04o}",
    )

    try:
        check_platform(report, policy)
        check_docker(report, policy)
        check_clock_and_network(
            report, policy, require_public_listener=args.phase != "admit"
        )
        check_storage_and_temperature(report, policy)
        check_identity_and_directories(report, policy)
        check_log_policy(report, policy)
        check_power_and_unit(report, policy, current_host_id)
    except (KeyError, TypeError, ValueError) as exc:
        report.add(
            "admission:policy-execution",
            False,
            f"policy is incomplete or contains an invalid value: {exc}",
        )

    artifacts: dict[str, Any] = {}
    if args.phase in {"prepare-reboot", "verify-reboot"}:
        try:
            check_runtime(report, policy)
        except (KeyError, TypeError, ValueError) as exc:
            report.add(
                "runtime:policy-execution",
                False,
                f"runtime policy is incomplete or contains an invalid value: {exc}",
            )

    if args.phase == "prepare-reboot" and not report.failed:
        marker = {
            "schema_version": 1,
            "created_at": now(),
            "hostname": socket.gethostname(),
            "host_id_sha256": current_host_id,
            "boot_id_before": current_boot_id,
        }
        atomic_write_json(reboot_marker, marker)
        artifacts["reboot_marker"] = str(reboot_marker)
    elif args.phase == "verify-reboot":
        try:
            marker_status = reboot_marker.stat()
            marker = load_json(reboot_marker)
            secure_marker = (
                marker_status.st_uid == 0
                and stat.S_IMODE(marker_status.st_mode) & 0o022 == 0
            )
            marker_pass = secure_marker and evaluate_reboot_marker(
                marker, current_host_id, current_boot_id
            )
            report.add(
                "reboot:boot-id-changed",
                marker_pass,
                "boot ID changed on the same admitted host",
                boot_id_before=marker.get("boot_id_before"),
                boot_id_after=current_boot_id,
                marker_mode=f"{stat.S_IMODE(marker_status.st_mode):04o}",
            )
            artifacts["reboot_marker"] = str(reboot_marker)
            if marker_pass and not report.failed:
                marker["verified_at"] = now()
                marker["boot_id_after"] = current_boot_id
                atomic_write_json(reboot_marker, marker)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            report.add(
                "reboot:boot-id-changed", False, f"cannot verify reboot marker: {exc}"
            )

    summary = write_summary(
        summary_path,
        args.phase,
        policy_path,
        report,
        current_host_id,
        current_boot_id,
        artifacts,
    )
    print(
        f"operations host {args.phase}: {'PASS' if summary['overall_pass'] else 'FAIL'}"
    )
    print(f"summary: {summary_path}")
    if args.phase == "prepare-reboot" and summary["overall_pass"]:
        print(f"reboot marker: {reboot_marker}")
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
