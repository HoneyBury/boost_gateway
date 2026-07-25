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


ROOT = Path(__file__).resolve().parents[3]
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


class MixedBinaryGate:
    def __init__(
        self,
        *,
        legacy_binary: Path,
        candidate_binary: Path,
        state_tool: Path,
        work_dir: Path,
        timeout: float,
    ) -> None:
        self.legacy_binary = legacy_binary
        self.candidate_binary = candidate_binary
        self.state_tool = state_tool
        self.work_dir = work_dir
        self.timeout = timeout
        self.nodes: list[Node] = []
        self.stages: list[dict[str, Any]] = []
        self.command_number = 0

    def prepare(self) -> None:
        ports = reserve_ports(3)
        peers = [(f"lb-{index + 1}", port) for index, port in enumerate(ports)]
        for index, (node_id, port) in enumerate(peers):
            node_root = self.work_dir / node_id
            config_path = node_root / "config.json"
            storage_dir = node_root / "state"
            write_node_config(config_path, node_id, port, peers, storage_dir)
            self.nodes.append(Node(node_id, port, config_path, storage_dir, node_root / "backend.log"))

    def start_all_legacy(self) -> None:
        for node in self.nodes:
            node.start(self.legacy_binary, "legacy", self.timeout)

    def submit(self, stage_name: str) -> tuple[Node, dict[str, Any], str, int]:
        self.command_number += 1
        user_id = f"mixed-user-{self.command_number}"
        score = 1000 + self.command_number
        deadline = time.monotonic() + self.timeout
        attempts: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            for node in self.nodes:
                if node.process is None:
                    continue
                try:
                    response = backend_request(
                        node.port,
                        "leaderboard_submit",
                        {
                            "user_id": user_id,
                            "display_name": stage_name,
                            "score": score,
                        },
                    )
                    attempts.append({"node_id": node.node_id, "kind": response.get("kind"), "error_code": response.get("error_code")})
                    body = response.get("decoded_payload")
                    if response.get("kind") == "response" and isinstance(body, dict) and body.get("status") == "ok":
                        return node, response, user_id, score
                except (OSError, RuntimeError, json.JSONDecodeError) as exc:
                    attempts.append({"node_id": node.node_id, "error": str(exc)})
            time.sleep(0.1)
        raise RuntimeError(f"{stage_name}: no leader accepted command; attempts={attempts[-9:]}")

    def wait_for_replication(self, user_id: str, score: int) -> dict[str, dict[str, Any]]:
        pending = {node.node_id: node for node in self.nodes if node.process is not None}
        responses: dict[str, dict[str, Any]] = {}
        deadline = time.monotonic() + self.timeout
        while pending and time.monotonic() < deadline:
            for node_id, node in list(pending.items()):
                try:
                    response = backend_request(node.port, "leaderboard_rank", {"user_id": user_id})
                    body = response.get("decoded_payload")
                    if response.get("kind") == "response" and isinstance(body, dict) and body.get("score") == score:
                        responses[node_id] = response
                        del pending[node_id]
                except (OSError, RuntimeError, json.JSONDecodeError):
                    pass
            if pending:
                time.sleep(0.1)
        if pending:
            raise RuntimeError(f"committed command {user_id} was not readable on: {sorted(pending)}")
        return responses

    def wait_for_disk(self, minimum_commit_index: int) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                if all(node.state_path.is_file() and state_commit_index(node.state_path) >= minimum_commit_index for node in self.nodes):
                    return
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            time.sleep(0.1)
        indexes = {
            node.node_id: state_commit_index(node.state_path) if node.state_path.is_file() else None
            for node in self.nodes
        }
        raise RuntimeError(f"disk commit index did not reach {minimum_commit_index}: {indexes}")

    def capture_stage(self, name: str, expected_schemas: dict[str, int]) -> Node:
        leader, write_response, user_id, score = self.submit(name)
        reads = self.wait_for_replication(user_id, score)
        self.wait_for_disk(self.command_number)
        states: dict[str, Any] = {}
        for node in self.nodes:
            schema = state_schema(node.state_path)
            expected = expected_schemas[node.node_id]
            if schema != expected:
                raise RuntimeError(f"{name}: {node.node_id} schema={schema}, expected={expected}")
            states[node.node_id] = {
                "binary_kind": node.binary_kind,
                "schema_version": schema,
                "commit_index": state_commit_index(node.state_path),
                "state_sha256": sha256(node.state_path),
            }
        self.stages.append(
            {
                "name": name,
                "leader": leader.node_id,
                "command": {"user_id": user_id, "score": score},
                "write_response": write_response,
                "read_responses": reads,
                "nodes": states,
            }
        )
        return leader

    def replace(self, node: Node, binary: Path, kind: str, *, downgrade: bool = False) -> dict[str, Any] | None:
        node.stop()
        downgrade_summary: dict[str, Any] | None = None
        if downgrade:
            summary_path = node.storage_dir / "downgrade-summary.json"
            completed = subprocess.run(
                [
                    str(self.state_tool),
                    "downgrade",
                    "--state",
                    str(node.state_path),
                    "--node-id",
                    node.node_id,
                    "--summary",
                    str(summary_path),
                ],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"{node.node_id}: state downgrade failed ({completed.returncode}): "
                    f"{completed.stderr or completed.stdout}"
                )
            downgrade_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if downgrade_summary.get("overall_pass") is not True or state_schema(node.state_path) != 0:
                raise RuntimeError(f"{node.node_id}: downgrade did not produce a validated v0 state")
        node.start(binary, kind, self.timeout)
        return downgrade_summary

    def run(self) -> list[dict[str, Any]]:
        self.prepare()
        self.start_all_legacy()
        expected = {node.node_id: 0 for node in self.nodes}
        leader = self.capture_stage("all-legacy", expected)

        followers = [node for node in self.nodes if node is not leader]
        for sequence, node in enumerate(followers, start=1):
            self.replace(node, self.candidate_binary, "candidate")
            expected[node.node_id] = 1
            self.capture_stage(f"upgrade-follower-{sequence}", expected)

        self.replace(leader, self.candidate_binary, "candidate")
        expected[leader.node_id] = 1
        leader = self.capture_stage("all-candidate", expected)

        rollback_followers = [node for node in self.nodes if node is not leader]
        for sequence, node in enumerate(rollback_followers, start=1):
            downgrade = self.replace(node, self.legacy_binary, "legacy", downgrade=True)
            expected[node.node_id] = 0
            self.capture_stage(f"rollback-follower-{sequence}", expected)
            self.stages[-1]["downgrade"] = downgrade

        downgrade = self.replace(leader, self.legacy_binary, "legacy", downgrade=True)
        expected[leader.node_id] = 0
        leader = self.capture_stage("all-legacy-after-rollback", expected)
        self.stages[-1]["downgrade"] = downgrade

        retry_followers = [node for node in self.nodes if node is not leader]
        for sequence, node in enumerate(retry_followers, start=1):
            self.replace(node, self.candidate_binary, "candidate")
            expected[node.node_id] = 1
            self.capture_stage(f"retry-upgrade-follower-{sequence}", expected)

        self.replace(leader, self.candidate_binary, "candidate")
        expected[leader.node_id] = 1
        leader = self.capture_stage("all-candidate-after-retry", expected)

        retry_rollback_followers = [node for node in self.nodes if node is not leader]
        for sequence, node in enumerate(retry_rollback_followers, start=1):
            downgrade = self.replace(node, self.legacy_binary, "legacy", downgrade=True)
            expected[node.node_id] = 0
            self.capture_stage(f"retry-rollback-follower-{sequence}", expected)
            self.stages[-1]["downgrade"] = downgrade

        downgrade = self.replace(leader, self.legacy_binary, "legacy", downgrade=True)
        expected[leader.node_id] = 0
        self.capture_stage("all-legacy-after-second-rollback", expected)
        self.stages[-1]["downgrade"] = downgrade
        return self.stages

    def stop_all(self) -> None:
        for node in self.nodes:
            node.stop()


def validate_executable(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RuntimeError(f"{label} does not exist: {resolved}")
    if not os.access(resolved, os.X_OK):
        raise RuntimeError(f"{label} is not executable: {resolved}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-binary", type=Path, required=True)
    parser.add_argument("--legacy-revision", required=True)
    parser.add_argument("--expected-legacy-sha256")
    parser.add_argument("--candidate-binary", type=Path, required=True)
    parser.add_argument("--state-tool", type=Path, required=True)
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--summary-path", type=Path, default=ROOT / "runtime/validation/raft-mixed-binary-summary.json")
    args = parser.parse_args()

    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    generated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    run_suffix = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
    work_dir = args.work_dir or ROOT / "runtime/validation/raft-mixed-binary" / run_suffix
    if not work_dir.is_absolute():
        work_dir = ROOT / work_dir
    gate: MixedBinaryGate | None = None
    error = ""
    stages: list[dict[str, Any]] = []
    binaries: dict[str, Any] = {}
    try:
        legacy = validate_executable(args.legacy_binary, "legacy binary")
        candidate = validate_executable(args.candidate_binary, "candidate binary")
        state_tool = validate_executable(args.state_tool, "Raft state tool")
        legacy_hash = sha256(legacy)
        candidate_hash = sha256(candidate)
        expected_legacy_hash = (args.expected_legacy_sha256 or "").strip().lower()
        if expected_legacy_hash and legacy_hash != expected_legacy_hash:
            raise RuntimeError(
                f"legacy binary SHA-256 mismatch: actual={legacy_hash}, expected={expected_legacy_hash}"
            )
        if legacy_hash == candidate_hash:
            raise RuntimeError("legacy and candidate binaries have the same SHA-256")
        if not args.legacy_revision.strip():
            raise RuntimeError("legacy revision must be non-empty")
        work_dir.mkdir(parents=True, exist_ok=False)
        binaries = {
            "legacy": {
                "path": str(legacy),
                "revision": args.legacy_revision,
                "sha256": legacy_hash,
                "expected_sha256": expected_legacy_hash or legacy_hash,
            },
            "candidate": {"path": str(candidate), "sha256": candidate_hash},
            "raft_state_tool": {"path": str(state_tool), "sha256": sha256(state_tool)},
        }
        gate = MixedBinaryGate(
            legacy_binary=legacy,
            candidate_binary=candidate,
            state_tool=state_tool,
            work_dir=work_dir,
            timeout=args.timeout_seconds,
        )
        stages = gate.run()
    except Exception as exc:  # Evidence must retain the first operational failure.
        error = str(exc)
    finally:
        if gate is not None:
            gate.stop_all()

    provenance = build_evidence_provenance(ROOT, build_configuration=args.configuration)
    if "candidate" in binaries:
        binaries["candidate"]["revision"] = provenance["candidate_revision"]
    passed = not error and len(stages) == 13
    summary = {
        "summary_version": 2,
        "generated_at": generated_at,
        "overall_pass": passed,
        "passed": passed,
        "failed_category": "raft_mixed_binary" if error else "",
        "failed_step": error,
        "provenance": provenance,
        "binaries": binaries,
        "cycle_count": 2,
        "expected_stage_count": 13,
        "stage_count": len(stages),
        "stages": stages,
        "artifacts": {"work_dir": str(work_dir), "summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Raft mixed-binary gate: {'PASS' if passed else 'FAIL'} ({len(stages)}/13 stages)")
    print(f"summary: {summary_path}")
    if error:
        print(error, file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
