"""Physical-host checks and digest-bound cloud hardware substitutes."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any

from scripts.lib.operations_host import Report, check_command, run, smartctl_health_command


def verify_cloud_deviation_evidence(
    contract: dict[str, Any], host_check: str
) -> tuple[bool, dict[str, Any]]:
    rows = contract.get("cloud_deviations", [])
    matches = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("host_check") == host_check
    ] if isinstance(rows, list) else []
    if len(matches) != 1:
        return False, {"error": "cloud deviation is missing or duplicated"}
    row = matches[0]
    reference = row.get("evidence_reference")
    expected_digest = str(row.get("evidence_sha256", "")).lower()
    if (
        row.get("evidence_status") != "accepted"
        or not isinstance(reference, str)
        or not Path(reference).is_absolute()
        or len(expected_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_digest)
    ):
        return False, {"error": "cloud deviation evidence is not accepted and digest-bound"}
    path = Path(reference)
    try:
        status = path.stat()
        content = path.read_bytes()
        evidence = json.loads(content)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, {"error": f"cannot read cloud deviation evidence: {exc}"}
    actual_digest = hashlib.sha256(content).hexdigest()
    secure_file = (
        path.is_file()
        and not path.is_symlink()
        and status.st_uid == 0
        and stat.S_IMODE(status.st_mode) & 0o022 == 0
    )
    provider = contract.get("provider", {})
    bound = (
        isinstance(evidence, dict)
        and evidence.get("schema_version") == 1
        and evidence.get("passed") is True
        and evidence.get("host_check") == host_check
        and evidence.get("target_id") == contract.get("target_id")
        and evidence.get("provider") == provider.get("name")
        and evidence.get("region_id") == provider.get("region_id")
        and evidence.get("zone_id") == provider.get("zone_id")
        and bool(evidence.get("instance_id"))
        and bool(evidence.get("observed_at"))
        and bool(evidence.get("source"))
    )
    return (
        secure_file and actual_digest == expected_digest and bound,
        {
            "path": str(path),
            "expected_sha256": expected_digest,
            "actual_sha256": actual_digest,
            "secure_file": secure_file,
            "bound": bound,
        },
    )


def check_storage_and_temperature(
    report: Report,
    policy: dict[str, Any],
    cloud_contract: dict[str, Any] | None = None,
) -> None:
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

    if cloud_contract is not None:
        passed, facts = verify_cloud_deviation_evidence(
            cloud_contract, "storage:smart-health"
        )
        report.add(
            "storage:smart-health",
            passed,
            "provider volume health evidence substitutes for unavailable guest SMART",
            cloud_evidence=facts,
        )
    else:
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

    if cloud_contract is not None:
        passed, facts = verify_cloud_deviation_evidence(
            cloud_contract, "thermal:temperature"
        )
        report.add(
            "thermal:temperature",
            passed,
            "provider instance health evidence substitutes for unavailable guest thermal sensors",
            cloud_evidence=facts,
        )
    else:
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
