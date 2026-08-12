"""Secret-free operations identity and side-effect-free host policy contracts."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import platform
import re
import socket
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


from scripts.lib.evidence_provenance import (  # noqa: E402,F401
    EvidenceReport as Report,
    OperationsIdentityError,
    collect_operations_identity,
    operations_admission_summary as admission_summary,
)


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


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


run = run_host_command


def check_command(report: Report, name: str, command: Sequence[str]) -> CommandResult:
    result = run_host_command(command)
    detail = "command completed" if result.returncode == 0 else "command failed or is unavailable"
    report.add(name, result.returncode == 0, detail, command=list(command),
               returncode=result.returncode, stdout=result.stdout.strip(),
               stderr=result.stderr.strip())
    return result


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


def build_reboot_marker(host_id: str, current_boot_id: str) -> dict[str, Any]:
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


def host_resource_snapshot() -> dict[str, object]:
    """Collect best-effort, secret-free host resource facts on Linux or Darwin."""
    snapshot: dict[str, object] = {
        "captured_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
    }
    try:
        snapshot["load_average"] = list(os.getloadavg())
    except (AttributeError, OSError):
        pass
    if platform.system() == "Darwin":
        return _darwin_host_resource_snapshot(snapshot)

    try:
        snapshot["proc_loadavg"] = Path("/proc/loadavg").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    try:
        fields = [
            int(value)
            for value in Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
        ]
        snapshot["cpu_ticks"] = {"total": sum(fields), "idle": sum(fields[3:5])}
    except (OSError, ValueError, IndexError):
        pass
    try:
        wanted = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
        snapshot["memory_kib"] = {
            key: int(value.split()[0])
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
            for key, value in [line.split(":", 1)] if key in wanted
        }
    except (OSError, ValueError):
        pass

    frequencies: list[int] = []
    for path in Path("/sys/devices/system/cpu").glob("cpu*/cpufreq/scaling_cur_freq"):
        try:
            frequencies.append(int(path.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            continue
    if frequencies:
        snapshot["cpu_frequency_khz"] = {
            "minimum": min(frequencies), "maximum": max(frequencies),
            "average": round(sum(frequencies) / len(frequencies), 3),
            "sampled_cpus": len(frequencies),
        }
    temperatures: dict[str, int] = {}
    for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            temperatures[path.parent.name] = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
    if temperatures:
        snapshot["thermal_millicelsius"] = temperatures
    return snapshot


def _darwin_host_resource_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    try:
        completed = subprocess.run(
            ["top", "-l", "1", "-n", "0", "-stats", "pid"], check=False,
            capture_output=True, text=True, timeout=10,
        )
        match = re.search(r"CPU usage:\s*([0-9.]+)% user,\s*([0-9.]+)% sys", completed.stdout)
        if completed.returncode == 0 and match:
            snapshot["cpu_percent"] = round(float(match.group(1)) + float(match.group(2)), 3)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    try:
        completed = subprocess.run(
            ["vm_stat"], check=False, capture_output=True, text=True, timeout=10,
        )
        page_match = re.search(r"page size of (\d+) bytes", completed.stdout)
        pages = {
            key: int(value)
            for key, value in re.findall(
                r"^Pages ([^:]+):\s+(\d+)\.", completed.stdout, flags=re.MULTILINE
            )
        }
        total = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], check=False,
            capture_output=True, text=True, timeout=10,
        )
        if completed.returncode == 0 and total.returncode == 0 and page_match:
            page_size = int(page_match.group(1))
            available = sum(pages.get(name, 0) for name in ("free", "inactive", "speculative"))
            snapshot["memory_kib"] = {
                "MemTotal": int(total.stdout.strip()) // 1024,
                "MemAvailable": available * page_size // 1024,
            }
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return snapshot


def process_tree_resource_snapshot(root_pid: int) -> dict[str, object]:
    """Aggregate RSS, file descriptor, and thread counts for a process tree."""
    if platform.system() == "Darwin":
        return darwin_process_tree_resource_snapshot(root_pid)
    processes: dict[int, dict[str, object]] = {}
    for status_path in Path("/proc").glob("[0-9]*/status"):
        try:
            fields = dict(
                line.split(":", 1) for line in status_path.read_text(encoding="utf-8").splitlines()
                if ":" in line
            )
            pid = int(status_path.parent.name)
            processes[pid] = {
                "pid": pid, "ppid": int(fields.get("PPid", "0")),
                "name": fields.get("Name", "unknown").strip(),
                "rss_kib": int(fields.get("VmRSS", "0 kB").split()[0]),
                "threads": int(fields.get("Threads", "0")),
            }
        except (OSError, ValueError, IndexError):
            continue
    return _aggregate_process_tree(root_pid, processes, linux=True)


def darwin_process_tree_resource_snapshot(root_pid: int) -> dict[str, object]:
    processes: dict[int, dict[str, object]] = {}
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss=,comm="], check=False,
            capture_output=True, text=True, timeout=10,
        )
        if completed.returncode == 0:
            for line in completed.stdout.splitlines():
                fields = line.strip().split(maxsplit=3)
                if len(fields) < 3:
                    continue
                pid, ppid, rss = (int(value) for value in fields[:3])
                processes[pid] = {
                    "pid": pid, "ppid": ppid, "rss_kib": rss,
                    "name": fields[3] if len(fields) == 4 else "unknown",
                }
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return _aggregate_process_tree(root_pid, processes, linux=False)


def _aggregate_process_tree(
    root_pid: int, processes: dict[int, dict[str, object]], *, linux: bool
) -> dict[str, object]:
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, process in processes.items():
            if pid not in selected and int(process["ppid"]) in selected:
                selected.add(pid)
                changed = True
    samples: list[dict[str, object]] = []
    for pid in sorted(selected):
        process = processes.get(pid)
        if process is None:
            continue
        fd_count = 0
        threads = int(process.get("threads", 0))
        try:
            if linux:
                fd_count = sum(1 for _ in (Path("/proc") / str(pid) / "fd").iterdir())
            else:
                descriptors = subprocess.run(
                    ["lsof", "-a", "-p", str(pid), "-Fn"], check=False,
                    capture_output=True, text=True, timeout=10,
                )
                if descriptors.returncode == 0:
                    fd_count = sum(1 for line in descriptors.stdout.splitlines() if line.startswith("f"))
                thread_list = subprocess.run(
                    ["ps", "-M", "-p", str(pid)], check=False,
                    capture_output=True, text=True, timeout=10,
                )
                if thread_list.returncode == 0:
                    threads = max(1, len(thread_list.stdout.splitlines()) - 1)
        except (OSError, subprocess.SubprocessError):
            pass
        samples.append({**process, "fd_count": fd_count, "threads": threads})
    return {
        "root_pid": root_pid, "process_count": len(samples),
        "rss_kib": sum(int(item["rss_kib"]) for item in samples),
        "fd_count": sum(int(item["fd_count"]) for item in samples),
        "thread_count": sum(int(item["threads"]) for item in samples),
        "processes": samples,
    }
