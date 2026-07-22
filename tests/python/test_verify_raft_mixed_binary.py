from __future__ import annotations

import json
import socket
import struct
import threading
import unittest
from pathlib import Path

from scripts.gates.e2e.verify_raft_mixed_binary import (
    backend_request,
    read_exactly,
    state_commit_index,
    state_schema,
    validate_executable,
    write_node_config,
)
from scripts.tools.verify_release_package_consumer import REQUIRED_BINARIES


class MixedBinaryGateTests(unittest.TestCase):
    def test_release_package_requires_downgrade_tool(self) -> None:
        self.assertIn("raft_state_tool", REQUIRED_BINARIES)

    def test_backend_request_uses_legacy_length_prefixed_envelope(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = int(listener.getsockname()[1])
        captured: dict[str, object] = {}

        def serve() -> None:
            connection, _ = listener.accept()
            with connection:
                size = struct.unpack("<I", read_exactly(connection, 4))[0]
                request = json.loads(read_exactly(connection, size))
                captured.update(request)
                response = {
                    "correlation_id": request["correlation_id"],
                    "source_service": "leaderboard",
                    "target_service": "gateway",
                    "kind": "response",
                    "timeout_ms": 0,
                    "error_code": 0,
                    "payload": json.dumps({"status": "ok", "score": 42}),
                    "message_type": "leaderboard_rank",
                    "trace_id": 0,
                    "span_id": 0,
                }
                encoded = json.dumps(response).encode("utf-8")
                connection.sendall(struct.pack("<I", len(encoded)) + encoded)
            listener.close()

        server = threading.Thread(target=serve)
        server.start()
        response = backend_request(port, "leaderboard_rank", {"user_id": "u-1"})
        server.join(timeout=2)

        self.assertFalse(server.is_alive())
        self.assertEqual(captured["message_type"], "leaderboard_rank")
        self.assertEqual(json.loads(str(captured["payload"])), {"user_id": "u-1"})
        self.assertEqual(response["decoded_payload"], {"status": "ok", "score": 42})

    def test_write_node_config_binds_local_three_node_cluster(self) -> None:
        with self.subTest("three-node config"):
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / "node.json"
                storage = root / "state"
                peers = [("lb-1", 12001), ("lb-2", 12002), ("lb-3", 12003)]
                write_node_config(config, "lb-2", 12002, peers, storage)
                document = json.loads(config.read_text(encoding="utf-8"))

                self.assertEqual(document["service"]["port"], 12002)
                self.assertEqual(document["raft"]["node_id"], "lb-2")
                self.assertEqual(document["raft"]["storage_dir"], str(storage))
                self.assertEqual(document["raft"]["peers"], [
                    {"host": "127.0.0.1", "id": node_id, "port": port}
                    for node_id, port in peers
                ])
                self.assertNotIn("redis", document)

    def test_state_inspection_distinguishes_v0_and_v1(self) -> None:
        import tempfile

        cases = [
            ({"current_term": 2, "commit_index": 4}, 0, 4),
            ({"schema_version": 1, "current_term": 3, "commit_index": 7}, 1, 7),
        ]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "raft.json"
            for document, schema, commit_index in cases:
                with self.subTest(schema=schema):
                    state.write_text(json.dumps(document), encoding="utf-8")
                    self.assertEqual(state_schema(state), schema)
                    self.assertEqual(state_commit_index(state), commit_index)

    def test_validate_executable_rejects_non_executable(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "backend"
            binary.write_bytes(b"binary")
            binary.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "not executable"):
                validate_executable(binary, "test binary")


if __name__ == "__main__":
    unittest.main()
