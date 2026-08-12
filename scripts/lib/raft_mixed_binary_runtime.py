#!/usr/bin/env python3
"""Verify a three-node Raft rolling upgrade and rollback with real binaries."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any



"""Shared implementation extracted from verify_raft_mixed_binary.py."""

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.evidence_provenance import build_evidence_provenance


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_exactly(stream: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.recv(remaining)
        if not chunk:
            raise RuntimeError("backend closed the connection before completing a frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def backend_request(port: int, message_type: str, payload: dict[str, Any], timeout: float = 3.0) -> dict[str, Any]:
    envelope = {
        "correlation_id": time.time_ns() & ((1 << 63) - 1),
        "source_service": "gateway",
        "target_service": "leaderboard",
        "kind": "request",
        "timeout_ms": int(timeout * 1000),
        "error_code": 0,
        "payload": json.dumps(payload, separators=(",", ":"), sort_keys=True),
        "message_type": message_type,
        "trace_id": 0,
        "span_id": 0,
    }
    encoded = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as stream:
        stream.settimeout(timeout)
        stream.sendall(struct.pack("<I", len(encoded)) + encoded)
        length = struct.unpack("<I", read_exactly(stream, 4))[0]
        if length <= 0 or length > 1024 * 1024:
            raise RuntimeError(f"invalid backend frame length: {length}")
        response = json.loads(read_exactly(stream, length))
    if not isinstance(response, dict):
        raise RuntimeError("backend response is not a JSON object")
    raw_payload = response.get("payload", "")
    try:
        response["decoded_payload"] = json.loads(raw_payload)
    except (TypeError, json.JSONDecodeError):
        response["decoded_payload"] = raw_payload
    return response


def reserve_ports(count: int) -> list[int]:
    reservations: list[socket.socket] = []
    try:
        for _ in range(count):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            reservations.append(listener)
        return [int(listener.getsockname()[1]) for listener in reservations]
    finally:
        for listener in reservations:
            listener.close()


def state_schema(path: Path) -> int:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError(f"{path}: Raft state must be an object")
    return int(document.get("schema_version", 0))


def state_commit_index(path: Path) -> int:
    document = json.loads(path.read_text(encoding="utf-8"))
    return int(document.get("commit_index", 0))


def write_node_config(path: Path, node_id: str, port: int, peers: list[tuple[str, int]], storage_dir: Path) -> None:
    document = {
        "service": {"name": "leaderboard", "port": port, "config_version": "raft-mixed-binary-v1"},
        "raft": {
            "node_id": node_id,
            "peers": [{"id": peer_id, "host": "127.0.0.1", "port": peer_port} for peer_id, peer_port in peers],
            "storage_dir": str(storage_dir),
            "election_timeout_min_ms": 250,
            "election_timeout_max_ms": 500,
            "heartbeat_interval_ms": 75,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")


@dataclass
class Node:
    node_id: str
    port: int
    config_path: Path
    storage_dir: Path
    log_path: Path
    binary_kind: str = "legacy"
    process: subprocess.Popen[bytes] | None = None
    log_stream: Any = None

    @property
    def state_path(self) -> Path:
        return self.storage_dir / f"{self.node_id}.raft.json"

    def start(self, binary: Path, binary_kind: str, timeout: float) -> None:
        if self.process is not None:
            raise RuntimeError(f"{self.node_id}: process is already running")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_stream = self.log_path.open("ab")
        environment = os.environ.copy()
        environment["BOOST_DISABLE_TLS"] = "1"
        environment["BOOST_DISABLE_REDIS_AUTO_CONNECT"] = "1"
        self.process = subprocess.Popen(
            [str(binary), "--config", str(self.config_path)],
            cwd=ROOT,
            env=environment,
            stdout=self.log_stream,
            stderr=subprocess.STDOUT,
        )
        self.binary_kind = binary_kind
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"{self.node_id}: {binary_kind} process exited with {self.process.returncode}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(f"{self.node_id}: {binary_kind} process did not listen on port {self.port}")

    def stop(self, timeout: float = 5.0) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=timeout)
        self.process = None
        if self.log_stream is not None:
            self.log_stream.close()
            self.log_stream = None
