"""Boundary contract for the decomposed performance baseline collector."""

from importlib import import_module
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES = {
    "scripts/lib/perf_process_affinity.py": ("parse_cpu_set", "apply_cpu_affinity"),
    "scripts/lib/perf_process_runtime.py": ("ManagedProcess", "start_perf_topology"),
    "scripts/lib/perf_otel_runtime.py": ("LoopbackOtelCollector", "wait_for_otel_mode_quiescence"),
    "scripts/lib/perf_bench_runtime.py": ("invoke_bench_case",),
    "scripts/lib/perf_business_protocol.py": ("BusinessOperationClient", "recv_business_packet"),
    "scripts/lib/perf_business_operations.py": ("run_business_operation_perf",),
    "scripts/lib/perf_stability_evidence.py": ("evaluate_resource_stability_gate",),
    "scripts/lib/perf_result_aggregation.py": ("aggregate_case_runs", "aggregate_otel_mode"),
    "scripts/lib/perf_resource_evidence.py": ("analyze_resources",),
    "scripts/lib/perf_saturation_analysis.py": ("build_saturation_analysis",),
    "scripts/lib/perf_report.py": ("render_markdown_report",),
    "scripts/lib/perf_release_contract.py": ("prepare_perf_constraints", "initial_perf_summary"),
}


def test_performance_modules_are_cli_free_and_own_expected_contracts() -> None:
    for relative, names in MODULES.items():
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "ArgumentParser(" not in source
        assert "def main(" not in source
        module = import_module(relative[:-3].replace("/", "."))
        for name in names:
            assert callable(getattr(module, name))


def test_performance_facade_stays_below_the_oversized_threshold() -> None:
    facade = REPO_ROOT / "scripts/producers/collect_v2_perf_baseline.py"
    assert len(facade.read_text(encoding="utf-8").splitlines()) <= 800
