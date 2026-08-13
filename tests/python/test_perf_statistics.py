from __future__ import annotations

from scripts.lib.perf_statistics import (
    distribution,
    interpolated_percentile,
    latency_percentile,
    linear_slope,
    metric_distribution,
    parse_redis_benchmark_csv,
    relative_percent_change,
)


def test_latency_percentile_uses_nearest_rank_and_rounds() -> None:
    values = [8.1239, 1.0, 3.5, 2.0]

    assert latency_percentile(values, 0.50) == 2.0
    assert latency_percentile(values, 0.99) == 8.124
    assert latency_percentile([], 0.50) is None


def test_interpolated_percentile_preserves_operational_latency_contract() -> None:
    assert interpolated_percentile([], 0.50) is None
    assert interpolated_percentile([8.1239], 0.99) == 8.124
    assert interpolated_percentile([10.0, 30.0], 0.50) == 20.0
    assert interpolated_percentile([10.0, 30.0], 0.99) == 29.8
    assert interpolated_percentile([10.0, 30.0], 0.0) == 10.0
    assert interpolated_percentile([10.0, 30.0], 1.0) == 30.0


def test_interpolated_percentile_rejects_out_of_range_fraction() -> None:
    for percentile in (-0.01, 1.01):
        try:
            interpolated_percentile([1.0], percentile)
        except ValueError as exc:
            assert str(exc) == "percentile must be between 0 and 1"
        else:
            raise AssertionError("out-of-range percentile was accepted")


def test_relative_percent_change_preserves_zero_and_rounding_contract() -> None:
    assert relative_percent_change(120.0, 100.0) == 20.0
    assert relative_percent_change(90.0, 100.0) == -10.0
    assert relative_percent_change(1.0, 3.0) == -66.666667
    assert relative_percent_change(1.0, 3.0, digits=3) == -66.667
    assert relative_percent_change(1.0, 0.0) is None


def test_metric_distribution_preserves_empty_sample_contract() -> None:
    assert metric_distribution([]) == {"min": None, "median": None, "max": None}
    assert metric_distribution([3.1256, 1.0, 2.0, 4.0]) == {
        "min": 1.0,
        "median": 2.563,
        "max": 4.0,
    }
    assert distribution([2.0, 4.0]) == metric_distribution([2.0, 4.0])


def test_linear_slope_handles_short_flat_and_growing_series() -> None:
    assert linear_slope([]) == 0.0
    assert linear_slope([9.0]) == 0.0
    assert linear_slope([4.0, 4.0, 4.0]) == 0.0
    assert linear_slope([1.0, 3.0, 5.0]) == 2.0


def test_parse_redis_benchmark_csv_uses_final_complete_measurement() -> None:
    content = (
        '"command","rps","avg","min","p50","p95","p99","max"\n'
        '"invalid","not-a-number","0","0","0","0","0","0"\n'
        '"eval leaderboard","50000.0","0.30","0.10","0.25","0.60","0.90","1.20"\n'
    )

    assert parse_redis_benchmark_csv(content) == {
        "throughput_requests_per_second": 50000.0,
        "average_latency_ms": 0.3,
        "minimum_latency_ms": 0.1,
        "p50_latency_ms": 0.25,
        "p95_latency_ms": 0.6,
        "p99_latency_ms": 0.9,
        "maximum_latency_ms": 1.2,
    }


def test_parse_redis_benchmark_csv_rejects_incomplete_output() -> None:
    for content in ('"eval","1.0"\n', '"eval","invalid","0","0","0","0","0","0"\n'):
        try:
            parse_redis_benchmark_csv(content)
        except ValueError as exc:
            assert str(exc) == "redis-benchmark CSV output is invalid"
        else:
            raise AssertionError("incomplete redis-benchmark output was accepted")
