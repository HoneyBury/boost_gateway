#!/usr/bin/env python3
"""Send one validated restore bundle through a pinned forced-command SSH key."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from scripts.tools import restore_bundle_ssh_receiver as transport  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    import restore_bundle_ssh_receiver as transport  # type: ignore[no-redef]  # noqa: E402


Runner = Callable[..., subprocess.CompletedProcess[Any]]


def ssh_command(
    *,
    ssh: str,
    identity_file: Path,
    known_hosts: Path,
    remote_host: str,
    original_command: str,
) -> list[str]:
    if re.fullmatch(
        r"[A-Za-z0-9_.@:%\[\]-]+", remote_host
    ) is None or remote_host.startswith("-"):
        raise transport.RestoreTransportError("remote SSH host is invalid")
    identity = transport.require_regular(identity_file, "SSH identity file")
    hosts = transport.require_regular(known_hosts, "SSH known_hosts file")
    if os.stat(identity).st_mode & 0o077:
        raise transport.RestoreTransportError(
            "SSH identity file must not be group/world accessible"
        )
    return [
        ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        f"IdentityFile={identity}",
        "-o",
        f"UserKnownHostsFile={hosts}",
        "--",
        remote_host,
        original_command,
    ]


def parse_json_output(
    completed: subprocess.CompletedProcess[Any], label: str
) -> dict[str, Any]:
    if completed.returncode != 0:
        stderr = completed.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise transport.RestoreTransportError(
            f"{label} failed: {str(stderr or '').strip()[:300]}"
        )
    output = completed.stdout
    if isinstance(output, bytes):
        encoded = output
    elif isinstance(output, str):
        encoded = output.encode("utf-8")
    else:
        raise transport.RestoreTransportError(f"{label} returned no JSON")
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise transport.RestoreTransportError(
            f"{label} returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise transport.RestoreTransportError(f"{label} returned a non-object")
    return value


def verify_receipt(
    receipt: dict[str, Any],
    *,
    restore_id: str,
    binding: dict[str, Any],
) -> None:
    expected = {
        "schema_version": 1,
        "restore_id": restore_id,
        "backup_id": binding["backup_id"],
        "files": binding["files"],
        "bundle_sha256": binding["bundle_sha256"],
        "receiver_host_id_sha256": binding["source_host_id_sha256"],
        "remote_readback_sha256": True,
        "create_only": True,
        "secret_material_recorded": False,
    }
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise transport.RestoreTransportError(
                f"restore transport receipt field differs: {field}"
            )
    if set(receipt) != set(expected) | {
        "received_at"
    } or not transport.valid_utc_timestamp(receipt.get("received_at")):
        raise transport.RestoreTransportError(
            "restore transport receipt timestamp is invalid"
        )


def write_new_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise transport.RestoreTransportError(
            "receipt parent must be a non-symlink directory"
        )
    transport.write_new(path, transport.canonical_json(receipt), 0o600)


def send_restore_bundle(
    *,
    restore_id: str,
    bundle_dir: Path,
    remote_host: str,
    ssh_identity_file: Path,
    ssh_known_hosts: Path,
    ssh: str = "/usr/bin/ssh",
    receipt_path: Path | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    identifier = transport.validate_id(restore_id, "restore ID")
    binding = transport.validate_bundle_binding(bundle_dir)
    store_command = ssh_command(
        ssh=ssh,
        identity_file=ssh_identity_file,
        known_hosts=ssh_known_hosts,
        remote_host=remote_host,
        original_command="boost-gateway-restore store",
    )
    with tempfile.TemporaryFile() as framed:
        transport.write_frame(framed, identifier, bundle_dir)
        framed.seek(0)
        try:
            stored = runner(
                store_command,
                stdin=framed,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=3600,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise transport.RestoreTransportError(
                f"restore bundle SSH upload failed: {exc}"
            ) from exc
    store_receipt = parse_json_output(stored, "restore bundle SSH upload")
    verify_receipt(store_receipt, restore_id=identifier, binding=binding)

    receipt_command = ssh_command(
        ssh=ssh,
        identity_file=ssh_identity_file,
        known_hosts=ssh_known_hosts,
        remote_host=remote_host,
        original_command=f"boost-gateway-restore receipt {identifier}",
    )
    try:
        fetched = runner(
            receipt_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise transport.RestoreTransportError(
            f"restore receipt SSH readback failed: {exc}"
        ) from exc
    fetched_receipt = parse_json_output(fetched, "restore receipt SSH readback")
    verify_receipt(fetched_receipt, restore_id=identifier, binding=binding)
    if transport.canonical_json(fetched_receipt) != transport.canonical_json(
        store_receipt
    ):
        raise transport.RestoreTransportError(
            "stored and fetched restore receipts differ"
        )
    if receipt_path is not None:
        write_new_receipt(receipt_path, fetched_receipt)
    return fetched_receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore-id", required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--remote-host", required=True)
    parser.add_argument("--ssh-identity-file", type=Path, required=True)
    parser.add_argument("--ssh-known-hosts", type=Path, required=True)
    parser.add_argument("--ssh", default="/usr/bin/ssh")
    parser.add_argument("--receipt-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = send_restore_bundle(
            restore_id=args.restore_id,
            bundle_dir=args.bundle_dir,
            remote_host=args.remote_host,
            ssh_identity_file=args.ssh_identity_file,
            ssh_known_hosts=args.ssh_known_hosts,
            ssh=args.ssh,
            receipt_path=args.receipt_path,
        )
    except (transport.RestoreTransportError, OSError, ValueError) as exc:
        print(f"restore bundle send: FAIL: {exc}", file=sys.stderr)
        return 1
    print("restore bundle send: PASS")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
