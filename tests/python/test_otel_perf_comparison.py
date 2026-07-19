import http.client
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from scripts.gates.release.verify_fixed_runner_release_capacity import (
    validate_otel_comparison,
)
from scripts.producers.collect_v2_perf_baseline import (
    LoopbackOtelCollector,
    aggregate_otel_mode,
    build_otel_comparison,
    otel_exporter_metrics,
    wait_for_otel_mode_quiescence,
)


def performance() -> dict:
    return {
        "case_name": "battle-100-30s",
        "runs": 3,
        "throughput_msg_per_sec": {"min": 1000, "median": 1100, "max": 1200},
        "latency_p50_ms": {"min": 1, "median": 2, "max": 3},
        "latency_p90_ms": {"min": 2, "median": 3, "max": 4},
        "latency_p99_ms": {"min": 3, "median": 4, "max": 5},
        "total_messages": {"min": 6000, "median": 6500, "max": 7000},
        "connected_clients": {"min": 100, "median": 100, "max": 100},
        "rejected_clients": {"min": 0, "median": 0, "max": 0},
        "failed_clients": {"min": 0, "median": 0, "max": 0},
        "forced_timeout": False,
    }


def mode(name: str) -> dict:
    return {
        "mode": name,
        "runs": 3,
        "performance": performance(),
        "gateway_cpu_seconds": {"min": 1, "median": 2, "max": 3},
        "gateway_rss_mb": {"min": 10, "median": 11, "max": 12},
        "backend_routed_requests": 768,
        "gateway_cpu_affinities": ["0-1"],
        "gateway_pid": 100 if name == "off" else 200,
        "runs_detail": [],
    }


class OtelPerfComparisonTest(unittest.TestCase):
    def test_mode_quiescence_waits_for_route_and_exporter_counters_to_settle(self) -> None:
        def diagnostics(routed: int, enqueued: int) -> dict:
            return {
                "backend_metrics": {"battle": {"total_requests": routed}},
                "otel_exporter_metrics": {
                    "configured": True,
                    "enqueued_spans": enqueued,
                },
            }

        snapshots = [diagnostics(2, 1), diagnostics(2, 2), diagnostics(2, 2)]
        with (
            patch(
                "scripts.producers.collect_v2_perf_baseline.fetch_json",
                side_effect=snapshots,
            ) as fetch,
            patch("scripts.producers.collect_v2_perf_baseline.time.sleep"),
        ):
            result = wait_for_otel_mode_quiescence(
                "http://collector.test/diagnostics",
                mode="on",
                initial_backend_requests=0,
            )

        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(otel_exporter_metrics(result)["enqueued_spans"], 2)

    def test_mode_level_routed_delta_keeps_requests_between_runs(self) -> None:
        runs = []
        for _ in range(3):
            runs.append({
                "throughput_msg_per_sec": 1000,
                "latency_p50_ms": 1,
                "latency_p90_ms": 2,
                "latency_p99_ms": 3,
                "total_messages": 6000,
                "connected_clients": 100,
                "rejected_clients": 0,
                "failed_clients": 0,
                "forced_timeout": False,
                "backend_routed_requests": 10,
                "gateway_resources": {
                    "cpu_seconds_delta": 1,
                    "rss_mb_after": 10,
                    "cpu_affinity": "0-1",
                    "pid": 123,
                },
            })

        aggregate = aggregate_otel_mode(
            "on",
            runs,
            mode_backend_routed_requests=35,
        )

        self.assertEqual(aggregate["backend_routed_requests"], 35)
        self.assertEqual(aggregate["per_run_backend_routed_requests"], 30)

    def test_loopback_collector_counts_valid_and_invalid_payloads(self) -> None:
        collector = LoopbackOtelCollector()
        collector.start()
        self.addCleanup(collector.stop)
        endpoint = urlparse(collector.endpoint)
        connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=1)
        payload = json.dumps({"spans": [{"status": "ok"}, {"status": "error"}]})
        connection.request("POST", endpoint.path, payload, {"Content-Type": "application/json"})
        self.assertEqual(connection.getresponse().status, 200)
        connection.close()
        connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=1)
        connection.request("POST", endpoint.path, "{}", {"Content-Type": "application/json"})
        self.assertEqual(connection.getresponse().status, 400)
        connection.close()

        self.assertEqual(collector.snapshot(), {
            "requests": 2,
            "spans": 2,
            "invalid_payloads": 1,
            "http_status_errors": 1,
            "span_status_errors": 1,
        })

    def test_comparison_requires_counter_route_and_collector_agreement(self) -> None:
        comparison = build_otel_comparison(
            mode("off"),
            mode("on"),
            repetitions=3,
            off_log_verified=True,
            on_log_verified=True,
            collector_off={
                "requests": 0,
                "spans": 0,
                "invalid_payloads": 0,
                "http_status_errors": 0,
                "span_status_errors": 0,
            },
            collector_on={
                "requests": 3,
                "spans": 768,
                "invalid_payloads": 0,
                "http_status_errors": 0,
                "span_status_errors": 0,
            },
            off_exporter={
                "configured": False,
                "enqueued_spans": 0,
                "exported_spans": 0,
                "successful_batches": 0,
                "failed_batches": 0,
                "buffered_spans": 0,
            },
            on_exporter={
                "configured": True,
                "enqueued_spans": 768,
                "exported_spans": 768,
                "successful_batches": 3,
                "failed_batches": 0,
                "buffered_spans": 0,
            },
        )
        self.assertTrue(comparison["verified"])
        self.assertEqual(comparison["performance_regression_policy"], "observed_not_thresholded")
        self.assertIn("latency_p99_ms", comparison["deltas"])

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "summary.json"
            path.write_text(json.dumps({"otel_comparison": comparison}), encoding="utf-8")
            gate = validate_otel_comparison(path, True, 3)
        self.assertTrue(gate["passed"])

        mutations = {
            "off collector is not zero": lambda value: value["proof"]["off"]["collector"].update(requests=1),
            "off exporter configured": lambda value: value["proof"]["off"]["exporter"].update(configured=True),
            "routed mismatch": lambda value: value["modes"]["on"].update(backend_routed_requests=767),
            "buffered mismatch": lambda value: value["proof"]["on"]["exporter"].update(buffered_spans=1),
            "collector status error": lambda value: value["proof"]["on"]["collector"].update(span_status_errors=1),
            "missing delta": lambda value: value["deltas"].pop("gateway_rss_mb"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                invalid = json.loads(json.dumps(comparison))
                mutate(invalid)
                path = Path(temp) / "summary.json"
                path.write_text(json.dumps({"otel_comparison": invalid}), encoding="utf-8")
                gate = validate_otel_comparison(path, True, 3)
                self.assertFalse(gate["passed"])

    def test_diagnostics_exporter_metrics_are_normalized(self) -> None:
        metrics = otel_exporter_metrics({
            "otel_exporter_metrics": {
                "configured": True,
                "enqueued_spans": 10,
                "exported_spans": 8,
                "successful_batches": 1,
                "failed_batches": 0,
                "buffered_spans": 2,
            }
        })
        self.assertTrue(metrics["configured"])
        self.assertEqual(metrics["buffered_spans"], 2)


if __name__ == "__main__":
    unittest.main()
