#!/usr/bin/env python3
"""Plan or apply the idempotent Ubuntu operations-host security baseline."""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY = ROOT / "deploy/operations/operations-host-policy.json"
DEFAULT_SUMMARY = Path("runtime/validation/operations-host-baseline-summary.json")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def atomic_write(path: Path, content: bytes, mode: int, uid: int, gid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary_path, uid, gid)
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: dict[str, Any], mode: int = 0o640) -> None:
    content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write(path, content, mode, os.geteuid(), os.getegid())


def run(command: Sequence[str], actions: list[dict[str, Any]]) -> None:
    environment = dict(os.environ)
    environment.update({"LANG": "C", "LC_ALL": "C"})
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    action = {
        "command": list(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "passed": completed.returncode == 0,
    }
    actions.append(action)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}: "
            f"{completed.stderr.strip()}"
        )


def merge_docker_config(document: dict[str, Any]) -> dict[str, Any]:
    merged = dict(document)
    options = merged.get("log-opts")
    log_options = dict(options) if isinstance(options, dict) else {}
    log_options.update({"max-file": "5", "max-size": "10m"})
    merged["log-driver"] = "json-file"
    merged["log-opts"] = log_options
    return merged


def ufw_commands(policy: dict[str, Any]) -> list[list[str]]:
    network = policy["network"]
    commands = [
        ["ufw", "default", "deny", "incoming"],
        ["ufw", "default", "allow", "outgoing"],
    ]
    for port in network["public_tcp_ports"]:
        commands.append(["ufw", "allow", f"{int(port)}/tcp"])
    for port in network["required_trusted_tcp_ports"]:
        for cidr in network["trusted_cidrs"]:
            if str(cidr) in {"100.64.0.0/10", "fd7a:115c:a1e0::/48"}:
                commands.append(
                    [
                        "ufw",
                        "allow",
                        "from",
                        str(cidr),
                        "to",
                        "any",
                        "port",
                        str(int(port)),
                        "proto",
                        "tcp",
                    ]
                )
    commands.append(["ufw", "--force", "enable"])
    return commands


def ensure_service_identity(
    policy: dict[str, Any], actions: list[dict[str, Any]]
) -> None:
    identity = policy["identity"]
    try:
        pwd.getpwnam(str(identity["user"]))
    except KeyError:
        run(
            [
                "useradd",
                "--system",
                "--home-dir",
                "/var/lib/boost-gateway",
                "--shell",
                "/usr/sbin/nologin",
                "--user-group",
                str(identity["user"]),
            ],
            actions,
        )


def ensure_directories(policy: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    for directory in policy["directories"]:
        path = Path(directory["path"])
        uid = pwd.getpwnam(str(directory["owner"])).pw_uid
        gid = grp.getgrnam(str(directory["group"])).gr_gid
        mode = int(str(directory["mode"]), 8)
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, uid, gid)
        os.chmod(path, mode)
        actions.append(
            {
                "name": f"directory:{path}",
                "passed": True,
                "owner_uid": uid,
                "group_gid": gid,
                "mode": f"{mode:04o}",
            }
        )


def install_governed_file(
    source: Path,
    destination: Path,
    mode: int,
    actions: list[dict[str, Any]],
) -> None:
    content = source.read_bytes()
    changed = True
    if destination.exists():
        status = destination.stat()
        changed = not (
            destination.read_bytes() == content
            and status.st_uid == 0
            and status.st_gid == 0
            and stat.S_IMODE(status.st_mode) == mode
        )
    if changed:
        atomic_write(destination, content, mode, 0, 0)
    actions.append(
        {
            "name": f"install:{destination}",
            "passed": True,
            "changed": changed,
            "source": str(source),
        }
    )


def configure_docker_logging(actions: list[dict[str, Any]]) -> bool:
    path = Path("/etc/docker/daemon.json")
    existing = load_json(path) if path.exists() else {}
    desired = merge_docker_config(existing)
    changed = desired != existing
    if changed:
        desired_content = (json.dumps(desired, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        descriptor, temporary = tempfile.mkstemp(
            prefix=".boost-gateway-daemon.", suffix=".json", dir=path.parent
        )
        validation_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(desired_content)
            run(
                [
                    "dockerd",
                    "--validate",
                    f"--config-file={validation_path}",
                ],
                actions,
            )
        finally:
            validation_path.unlink(missing_ok=True)
        backup = path.with_name("daemon.json.pre-boost-gateway")
        if path.exists() and not backup.exists():
            shutil.copy2(path, backup)
        atomic_write(
            path,
            desired_content,
            0o644,
            0,
            0,
        )
    actions.append(
        {
            "name": "docker:bounded-log-policy",
            "passed": True,
            "changed": changed,
            "path": str(path),
        }
    )
    return changed


def apply(
    policy: dict[str, Any], restart_docker: bool, actions: list[dict[str, Any]]
) -> None:
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        raise RuntimeError("apply requires root on Linux")
    for command in ("dockerd", "systemctl", "ufw", "useradd"):
        if shutil.which(command) is None:
            raise RuntimeError(f"required command is unavailable: {command}")
    for source in (
        ROOT / "deploy/operations/operations-host-policy.json",
        ROOT / "deploy/operations/boost-gateway-journald.conf",
        ROOT / "deploy/systemd/boost-gateway-compose.service",
    ):
        if not source.is_file():
            raise RuntimeError(f"governed baseline source is unavailable: {source}")
    ensure_service_identity(policy, actions)
    ensure_directories(policy, actions)

    install_governed_file(
        ROOT / "deploy/operations/operations-host-policy.json",
        Path("/etc/boost-gateway/operations-host-policy.json"),
        0o644,
        actions,
    )
    install_governed_file(
        ROOT / "deploy/operations/boost-gateway-journald.conf",
        Path("/etc/systemd/journald.conf.d/boost-gateway.conf"),
        0o644,
        actions,
    )
    install_governed_file(
        ROOT / "deploy/systemd/boost-gateway-compose.service",
        Path("/etc/systemd/system/boost-gateway-compose.service"),
        0o644,
        actions,
    )
    atomic_write(
        Path("/etc/modules-load.d/boost-gateway.conf"),
        b"k10temp\n",
        0o644,
        0,
        0,
    )
    actions.append(
        {
            "name": "thermal:k10temp-module",
            "passed": True,
            "path": "/etc/modules-load.d/boost-gateway.conf",
        }
    )

    for target in policy["power"]["masked_targets"]:
        run(["systemctl", "mask", str(target)], actions)
    for command in ufw_commands(policy):
        run(command, actions)

    docker_changed = configure_docker_logging(actions)
    run(["systemctl", "daemon-reload"], actions)
    run(["systemctl", "restart", "systemd-journald.service"], actions)
    if restart_docker and docker_changed:
        run(["systemctl", "restart", "docker.service"], actions)
    elif docker_changed:
        actions.append(
            {
                "name": "docker:restart-required",
                "passed": False,
                "detail": "rerun with --restart-docker in a maintenance window",
            }
        )


def plan(policy: dict[str, Any], restart_docker: bool) -> list[dict[str, Any]]:
    return [
        {"name": "identity", "value": policy["identity"]},
        {"name": "directories", "value": policy["directories"]},
        {"name": "masked_targets", "value": policy["power"]["masked_targets"]},
        {"name": "ufw_commands", "value": ufw_commands(policy)},
        {
            "name": "governed_files",
            "value": [
                "/etc/boost-gateway/operations-host-policy.json",
                "/etc/systemd/journald.conf.d/boost-gateway.conf",
                "/etc/systemd/system/boost-gateway-compose.service",
                "/etc/modules-load.d/boost-gateway.conf",
                "/etc/docker/daemon.json",
            ],
        },
        {"name": "restart_docker", "value": restart_docker},
    ]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["plan", "apply"])
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--restart-docker", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    policy_path = args.policy if args.policy.is_absolute() else ROOT / args.policy
    summary_path = (
        args.summary_path
        if args.summary_path.is_absolute()
        else ROOT / args.summary_path
    )
    errors: list[str] = []
    actions: list[dict[str, Any]] = []
    try:
        policy = load_json(policy_path)
        if policy.get("schema_version") != 1:
            raise ValueError("operations host policy schema_version must be 1")
        if args.mode == "apply":
            apply(policy, args.restart_docker, actions)
        else:
            actions = plan(policy, args.restart_docker)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    failed_actions = [action for action in actions if action.get("passed") is False]
    passed = not errors and not failed_actions
    summary = {
        "summary_version": 1,
        "generated_at": now(),
        "mode": args.mode,
        "overall_pass": passed,
        "passed": passed,
        "failed_category": "operations_host_baseline" if not passed else "",
        "failed_step": (
            failed_actions[0].get("name", "")
            if failed_actions
            else ("baseline:exception" if errors else "")
        ),
        "restart_docker": args.restart_docker,
        "actions": actions,
        "errors": errors,
        "artifacts": {
            "summary_path": str(summary_path),
            "policy_path": str(policy_path),
        },
    }
    atomic_write_json(summary_path, summary)
    print(f"operations host baseline {args.mode}: {'PASS' if passed else 'FAIL'}")
    print(f"summary: {summary_path}")
    if errors:
        for error in errors:
            print(f"- {error}", file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
