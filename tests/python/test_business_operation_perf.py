import socket
import threading
import time
import unittest

from scripts.producers.collect_v2_perf_baseline import (
    build_leaderboard_persistence_comparison,
    encode_business_packet,
    recv_business_packet,
    BusinessOperationClient,
    run_business_operation_perf,
    redis_command,
    summarize_business_operations,
)


class FakeGateway:
    RESPONSE_IDS = {
        2001: 2002,
        6001: 6002,
        6004: 6005,
        6006: 6007,
        7001: 7002,
        7003: 7004,
        7005: 7006,
    }

    def __init__(
        self,
        *,
        response_version: int = 1,
        response_flags: int = 0,
        match_status_matched: bool = True,
    ) -> None:
        self.response_version = response_version
        self.response_flags = response_flags
        self.match_status_matched = match_status_matched
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.sock.settimeout(0.1)
        self.host, self.port = self.sock.getsockname()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self.stopped.is_set():
            try:
                connection, _ = self.sock.accept()
            except TimeoutError:
                continue
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection: socket.socket) -> None:
        with connection:
            while True:
                try:
                    request = recv_business_packet(connection)
                except (ConnectionError, OSError):
                    return
                response_id = self.RESPONSE_IDS[request["message_id"]]
                if request["message_id"] == 6001:
                    connection.sendall(encode_business_packet(6003, 0, "match_found"))
                body = (
                    '{"matched":true,"match_id":"fake"}'
                    if request["message_id"] == 6006 and self.match_status_matched
                    else '{"matched":false,"queue_size":2}'
                    if request["message_id"] == 6006
                    else "ok"
                )
                connection.sendall(encode_business_packet(
                    response_id,
                    request["request_id"],
                    body,
                    version=self.response_version,
                    flags=self.response_flags,
                ))

    def close(self) -> None:
        self.stopped.set()
        self.sock.close()
        self.thread.join(timeout=1)


class BusinessOperationPerfTest(unittest.TestCase):
    def test_concurrent_matchmaking_and_leaderboard_use_real_protocol(self) -> None:
        gateway = FakeGateway()
        self.addCleanup(gateway.close)

        summary = run_business_operation_perf(
            gateway.host,
            gateway.port,
            ["matchmaking", "leaderboard"],
            clients=2,
            iterations=2,
            timeout_seconds=1.0,
            repetitions=2,
        )

        self.assertTrue(summary["passed"])
        self.assertEqual(summary["completed_runs"], 2)
        self.assertEqual(len(summary["runs"]), 2)
        self.assertEqual([item["scenario"] for item in summary["scenario_aggregates"]], ["matchmaking", "leaderboard"])
        for aggregate in summary["scenario_aggregates"]:
            self.assertEqual(aggregate["runs"], 2)
            self.assertEqual(aggregate["passed_runs"], 2)
            for operation in aggregate["operations"]:
                self.assertEqual(operation["attempted"], 8)
                self.assertEqual(operation["succeeded"], 8)
                self.assertEqual(operation["failed"], 0)
                self.assertGreater(operation["throughput_ops_per_sec"]["median"], 0)
        matchmaking = summary["scenario_aggregates"][0]
        self.assertEqual(matchmaking["time_to_match_samples"], 8)
        self.assertIsNotNone(matchmaking["time_to_match_p50_ms"])
        self.assertIsNotNone(matchmaking["time_to_match_p99_ms"])
        self.assertEqual(summary["leaderboard_persistence"]["mode"], "in_memory_only")
        self.assertFalse(summary["leaderboard_persistence"]["redis_comparison"])

    def test_persistence_comparison_requires_both_modes_and_redis_data_proof(self) -> None:
        gateway = FakeGateway()
        self.addCleanup(gateway.close)
        in_memory = run_business_operation_perf(
            gateway.host,
            gateway.port,
            ["leaderboard"],
            clients=2,
            iterations=1,
            timeout_seconds=1.0,
            repetitions=3,
            leaderboard_persistence_mode="in_memory_only",
        )
        redis = run_business_operation_perf(
            gateway.host,
            gateway.port,
            ["leaderboard"],
            clients=2,
            iterations=1,
            timeout_seconds=1.0,
            repetitions=3,
            leaderboard_persistence_mode="redis_primary_with_memory_shadow",
        )

        comparison = build_leaderboard_persistence_comparison(
            in_memory,
            redis,
            repetitions=3,
            redis_host="127.0.0.1",
            redis_port=6380,
            redis_key="lb:test",
            in_memory_log_verified=True,
            redis_log_verified=True,
            ping_before=True,
            ping_after=True,
            redis_zcard=6,
            expected_min_zcard=6,
        )

        self.assertTrue(comparison["verified"])
        self.assertEqual(comparison["repetitions_per_mode"], 3)
        self.assertEqual(len(comparison["deltas"]), 3)
        comparison["redis_proof"]["zcard"] = 5
        failed = build_leaderboard_persistence_comparison(
            in_memory,
            redis,
            repetitions=3,
            redis_host="127.0.0.1",
            redis_port=6380,
            redis_key="lb:test",
            in_memory_log_verified=True,
            redis_log_verified=True,
            ping_before=True,
            ping_after=True,
            redis_zcard=5,
            expected_min_zcard=6,
        )
        self.assertFalse(failed["verified"])

    def test_redis_command_supports_ping_and_integer_proof(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        host, port = listener.getsockname()

        def serve() -> None:
            for response in (b"+PONG\r\n", b":9\r\n"):
                connection, _ = listener.accept()
                with connection:
                    connection.recv(1024)
                    connection.sendall(response)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.assertEqual(redis_command(host, port, "PING"), "PONG")
        self.assertEqual(redis_command(host, port, "ZCARD", "lb:test"), 9)
        listener.close()
        thread.join(timeout=1)

    def test_matchmaking_requires_even_clients(self) -> None:
        with self.assertRaisesRegex(ValueError, "even client count"):
            run_business_operation_perf(
                "127.0.0.1", 1, ["matchmaking"], clients=3, iterations=1, timeout_seconds=0.1
            )

    def test_client_rejects_unknown_version_and_flags(self) -> None:
        for kwargs, expected in (
            ({"response_version": 2}, "protocol version"),
            ({"response_flags": 2}, "response flags"),
            ({"response_flags": 1}, "invalid compressed response"),
        ):
            with self.subTest(kwargs=kwargs):
                gateway = FakeGateway(**kwargs)
                try:
                    client = BusinessOperationClient(gateway.host, gateway.port, 0.5)
                    with self.assertRaisesRegex(ValueError, expected):
                        client.request(2001, 2002, "u|token:u|u")
                    client.close()
                finally:
                    gateway.close()

    def test_matched_false_never_counts_as_success(self) -> None:
        gateway = FakeGateway(match_status_matched=False)
        self.addCleanup(gateway.close)

        summary = run_business_operation_perf(
            gateway.host,
            gateway.port,
            ["matchmaking"],
            clients=2,
            iterations=1,
            timeout_seconds=0.08,
        )

        self.assertFalse(summary["passed"])
        status = next(
            operation
            for operation in summary["scenario_aggregates"][0]["operations"]
            if operation["operation"] == "match_status"
        )
        self.assertEqual(status["succeeded"], 0)
        self.assertEqual(status["failed"], 2)

    def test_push_frames_do_not_extend_request_deadline(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        host, port = listener.getsockname()

        def send_pushes() -> None:
            connection, _ = listener.accept()
            with connection:
                recv_business_packet(connection)
                for _ in range(5):
                    time.sleep(0.03)
                    try:
                        connection.sendall(encode_business_packet(6003, 0, "push"))
                    except OSError:
                        return

        thread = threading.Thread(target=send_pushes, daemon=True)
        thread.start()
        client = BusinessOperationClient(host, port, 0.08)
        started = time.monotonic()
        with self.assertRaises((TimeoutError, socket.timeout)):
            client.request(6001, 6002, "u|1000|1v1")
        self.assertLess(time.monotonic() - started, 0.14)
        client.close()
        listener.close()
        thread.join(timeout=1)

    def test_summary_reports_operation_failures_and_percentiles(self) -> None:
        records = [
            {"operation": "match_join", "ok": True, "latency_ms": 1.0, "error": ""},
            {"operation": "match_join", "ok": False, "latency_ms": 9.0, "error": "timeout"},
        ]

        summary = summarize_business_operations(["match_join"], records, 2.0)[0]

        self.assertEqual(summary["attempted"], 2)
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["throughput_ops_per_sec"], 1.0)
        self.assertEqual(summary["latency_p50_ms"], 1.0)
        self.assertEqual(summary["latency_p99_ms"], 9.0)
        self.assertEqual(summary["errors"], {"timeout": 1})


if __name__ == "__main__":
    unittest.main()
