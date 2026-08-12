"""Secret-free operations identity and side-effect-free host policy contracts."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import platform
import pwd
import re
import socket
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class OperationsIdentityError(ValueError):
    """Raised when required host or operator identity cannot be established."""


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
        "policy": {"path": str(policy_path), "sha256": policy_sha256},
        "checks": report.checks,
        "artifacts": {"summary_path": str(path), **(artifacts or {})},
    }
    atomic_write_json(path, summary)
    return summary


def _required_text(path: Path, label: str) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise OperationsIdentityError(f"{label} is empty")
    return value


def _os_release(path: Path) -> dict[str, str]:
    values = parse_os_release(path.read_text(encoding="utf-8"))
    return {key: value for key, value in values.items() if key in {"ID", "VERSION_ID"}}


def _operator(environment: Mapping[str, str]) -> dict[str, Any]:
    sudo_user = environment.get("SUDO_USER", "").strip()
    sudo_uid = environment.get("SUDO_UID", "").strip()
    if sudo_user or sudo_uid:
        if (
            not sudo_user
            or not sudo_uid.isdecimal()
            or any(character.isspace() or ord(character) < 32 for character in sudo_user)
            or len(sudo_user) > 128
        ):
            raise OperationsIdentityError("SUDO_USER/SUDO_UID identity is invalid")
        return {"name": sudo_user, "uid": int(sudo_uid), "source": "sudo"}

    uid = os.getuid()
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError as exc:
        raise OperationsIdentityError(f"cannot resolve process uid {uid}") from exc
    return {"name": name, "uid": uid, "source": "process"}


def collect_operations_identity(
    *,
    environment: Mapping[str, str] | None = None,
    machine_id_path: Path = Path("/etc/machine-id"),
    boot_id_path: Path = Path("/proc/sys/kernel/random/boot_id"),
    os_release_path: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    """Return only governed host and operator fields suitable for JSON evidence."""
    machine_id = machine_id_path.read_bytes()
    if not machine_id.strip():
        raise OperationsIdentityError("machine-id is empty")
    release = _os_release(os_release_path)
    if not release.get("ID") or not release.get("VERSION_ID"):
        raise OperationsIdentityError("os-release lacks ID or VERSION_ID")
    return {
        "host": {
            "hostname": socket.gethostname(),
            "host_id_sha256": hashlib.sha256(machine_id).hexdigest(),
            "boot_id": _required_text(boot_id_path, "boot_id"),
            "os": {
                "id": release["ID"],
                "version_id": release["VERSION_ID"],
                "kernel_release": platform.release(),
            },
            "architecture": platform.machine(),
        },
        "operator": _operator(environment if environment is not None else os.environ),
    }


def run_host_command(command: Sequence[str], timeout: int = 15) -> CommandResult:
    """Run a bounded host inspection command with stable C-locale output."""
    try:
        environment = dict(os.environ)
        environment.update({"LANG": "C", "LC_ALL": "C"})
        completed = subprocess.run(
            list(command), check=False, capture_output=True, text=True,
            timeout=timeout, env=environment,
        )
        return CommandResult(
            tuple(command), completed.returncode, completed.stdout, completed.stderr
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(tuple(command), 127, "", f"{type(exc).__name__}: {exc}")


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
        for row in rows if isinstance(row, dict)
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
    if any(address.version == network.version and address in network for network in trusted_networks):
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


def evaluate_ufw_policy(text: str, network_policy: dict[str, Any]) -> tuple[bool, list[str]]:
    lowered = text.lower()
    errors: list[str] = []
    if "status: active" not in lowered:
        errors.append("UFW is not active")
    if re.search(r"default:\s+deny\s+\(incoming\)", lowered) is None:
        errors.append("UFW incoming default is not deny")
    public_ports = {int(value) for value in network_policy["public_tcp_ports"]}
    trusted = [ipaddress.ip_network(str(value)) for value in network_policy["trusted_cidrs"]]
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
            source_network.version == network.version and source_network.subnet_of(network)
            for network in trusted
        ):
            errors.append(f"UFW allow source {source} for TCP {port} is outside trusted networks")
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


def evaluate_reboot_marker(marker: dict[str, Any], host_id: str, current_boot_id: str) -> bool:
    boot_id_after = marker.get("boot_id_after")
    return (
        marker.get("schema_version") == 1
        and marker.get("host_id_sha256") == host_id
        and bool(marker.get("boot_id_before"))
        and marker.get("boot_id_before") != current_boot_id
        and boot_id_after in {None, current_boot_id}
    )


def reboot_marker(host_id: str, current_boot_id: str) -> dict[str, Any]:
    """Build the minimal same-host reboot challenge without recording environment data."""
    return {
        "schema_version": 1,
        "created_at": now(),
        "hostname": socket.gethostname(),
        "host_id_sha256": host_id,
        "boot_id_before": current_boot_id,
    }


def smartctl_health_command(device_args: Sequence[str]) -> list[str]:
    if not device_args:
        raise ValueError("SMART device arguments are empty")
    return ["smartctl", "-H", "-j", *device_args[1:], device_args[0]]
