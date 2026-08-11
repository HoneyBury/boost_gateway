from __future__ import annotations

from scripts.lib.perf_statistics import (
    distribution,
    latency_percentile,
    linear_slope,
    metric_distribution,
)


def test_latency_percentile_uses_nearest_rank_and_rounds() -> None:
    values = [8.1239, 1.0, 3.5, 2.0]

    assert latency_percentile(values, 0.50) == 2.0
    assert latency_percentile(values, 0.99) == 8.124
    assert latency_percentile([], 0.50) is None


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
