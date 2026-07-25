#!/usr/bin/env python3
"""Collect a minimal, secret-free identity for operations evidence."""

from __future__ import annotations

import hashlib
import os
import platform
import pwd
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class OperationsIdentityError(ValueError):
    """Raised when required host or operator identity cannot be established."""


def _required_text(path: Path, label: str) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise OperationsIdentityError(f"{label} is empty")
    return value


def _os_release(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"ID", "VERSION_ID"}:
            values[key] = value.strip().strip('"')
    return values


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
