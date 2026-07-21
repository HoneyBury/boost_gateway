import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.gates.release.aggregate_saturation_axis_evidence import (
    AxisSourceSpec,
    SelectionSourceSpec,
    aggregate_axis_sources,
    parse_selection_source,
    parse_source,
)


SHA = "c" * 40
CASES = ("echo-sat-low", "echo-sat-knee", "echo-sat-high")


def cpu_set(count: int) -> str:
    return "0" if count == 1 else f"0-{count - 1}"


def constraint(requested: str, count: int, *, service: bool) -> dict:
    process = {
        "requested_cpu_set": requested,
        "effective_cpu_set": requested,
        "verified": True,
    }
    process["service_name" if service else "workload"] = "gateway" if service else "pressure"
    return {
        "type": "linux_cpu_affinity",
        "requested": requested,
        "applied": True,
        "effective_cpu_set": requested,
        "cpu_count": count,
        "processes": [process],
    }


def write_source(
    root: Path,
    axis: str,
    value: int,
    run_id: str,
    *,
    runner: str = "fixed-runner-a",
    comparison_point: bool = True,
) -> AxisSourceSpec:
    service_cpus = value if axis == "service_cpu_count" else 1
    io_cores = value if axis == "io_cores" else 4
    service_set = cpu_set(service_cpus)
    loadgen_set = "4-7"
    manifests = []
    aggregates = []
    cases = []
    snapshots = {}
    resource_runs = []
    resource_aggregates = []
    for index, name in enumerate(CASES, start=1):
        identity = {
            "case_id": name,
            "case_name": name,
            "scenario": "echo",
            "clients": index * 100,
            "interval_ms": 10,
            "duration_seconds": 30,
            "ramp_clients_per_second": 2000,
            "ramp_timeout_seconds": 60,
            "load_model": "closed_loop_one_in_flight_per_client",
            "configured_request_rate_ceiling_ops_per_sec": index * 10_000.0,
            "service_cpu_set": service_set,
            "service_cpu_count": service_cpus,
            "io_cores": io_cores,
            "comparison_identity": (
                f"{name}|service_cpu_count={service_cpus}|io_cores={io_cores}"
            ),
            "comparison_axes": ["service_cpu_count", "io_cores"],
        }
        manifests.append(identity)
        target = index * 100
        throughput = 1000.0 * index * value
        aggregates.append({
            "case_name": name,
            "case_identity": identity,
            "runs": 3,
            "target_clients": {"min": target, "median": target, "max": target},
            "started_clients": {"min": target, "median": target, "max": target},
            "tcp_connected_clients": {"min": target, "median": target, "max": target},
            "authenticated_clients": {"min": target, "median": target, "max": target},
            "peak_active_clients": {"min": target, "median": target, "max": target},
            "cancelled_clients": {"min": 0, "median": 0, "max": 0},
            "cancelled_before_connect": {"min": 0, "median": 0, "max": 0},
            "failed_clients": {"min": 0, "median": 0, "max": 0},
            "rejected_clients": {"min": 0, "median": 0, "max": 0},
            "ramp_completed": True,
            "measurement_started": True,
            "steady_state_completed": True,
            "bench_exit_code": 0,
            "forced_timeout": False,
            "throughput_msg_per_sec": {
                "min": throughput * 0.9,
                "median": throughput,
                "max": throughput * 1.1,
            },
            "latency_p99_ms": {"min": 5.0, "median": 10.0 / value, "max": 12.0},
            "achieved_send_rate_ops_per_sec": {
                "min": throughput * 0.9,
                "median": throughput,
                "max": throughput * 1.1,
            },
            "achieved_response_rate_ops_per_sec": {
                "min": throughput * 0.9,
                "median": throughput,
                "max": throughput * 1.1,
            },
        })
        resource_aggregates.append({
            "case_name": name,
            "services": {
                "v2_gateway_demo": {
                    "cpu_percent_from_cpu_seconds": {
                        "min": 80.0 * service_cpus,
                        "median": 85.0 * service_cpus,
                        "max": 90.0 * service_cpus,
                    },
                },
            },
        })
        for repetition in range(1, 4):
            run_name = f"{name}.run{repetition}"
            cases.append({"case_name": run_name})
            snapshots[run_name] = {
                "before": [{"service_name": "gateway", "cpu_seconds": 1.0}],
                "after": [{"service_name": "gateway", "cpu_seconds": 20.0}],
                "loadgen": {
                    "before": {"cpu_seconds": 1.0},
                    "after": {"cpu_seconds": 20.0},
                },
                "elapsed_seconds": 30.0,
                "quiescence": {"quiesced": True},
            }
            resource_runs.append({
                "case_name": run_name,
                "elapsed_seconds": 30.0,
                "services": {
                    "gateway": {"cpu_percent_from_cpu_seconds": 85.0 * service_cpus},
                },
                "loadgen": {"cpu_percent_from_cpu_seconds": 200.0},
            })

    fixed_identity = manifests[1]
    if comparison_point:
        fixed_name = str(fixed_identity["case_name"])
        manifests = [fixed_identity]
        aggregates = [item for item in aggregates if item["case_name"] == fixed_name]
        cases = [item for item in cases if str(item["case_name"]).startswith(f"{fixed_name}.")]
        snapshots = {
            name: value for name, value in snapshots.items() if name.startswith(f"{fixed_name}.")
        }
        resource_runs = [
            item for item in resource_runs if str(item["case_name"]).startswith(f"{fixed_name}.")
        ]
        resource_aggregates = [
            item for item in resource_aggregates if item["case_name"] == fixed_name
        ]

    perf = {
        "summary_version": 2,
        "git_commit": SHA,
        "preset": "saturation",
        "repetitions": 3,
        "case_manifest_version": 1,
        "case_manifest": manifests,
        "case_aggregates": aggregates,
        "cases": cases,
        "process_snapshots": snapshots,
        "service_resource_constraint": constraint(service_set, service_cpus, service=True),
        "loadgen_resource_constraint": constraint(loadgen_set, 4, service=False),
        "topology": {
            "io_cores": io_cores,
            "loadgen_io_threads": 4,
            "backend_connection_pool_size": 8,
            "battle_route_workers": 8,
            "battle_frame_push_every": 1,
        },
        "resource_analysis": {
            "per_run": resource_runs,
            "case_aggregates": resource_aggregates,
        },
        "saturation_analysis": {
            "collection_pass": True,
            "evidence_valid": True,
            "saturation_found": not comparison_point,
            "analysis_mode": "comparison_point" if comparison_point else "curve",
            "curve_complete": not comparison_point,
            "conclusion": "inconclusive" if comparison_point else "knee_found",
            "fixed_case_candidate": None if comparison_point else fixed_identity,
            "cpu_saturation_case": None if comparison_point else fixed_identity,
            "points": [
                {
                    "case_identity": identity,
                    "evidence_valid": True,
                    "resource_window_accepted": True,
                }
                for identity in manifests
            ],
            "failures": [],
        },
    }
    provenance = {
        "candidate_revision": SHA,
        "git_commit": SHA,
        "git_ref": "refs/heads/test",
        "workflow": "Fixed Runner Saturation",
        "run_id": run_id,
        "runner": runner,
        "runner_os": "Linux",
        "runner_arch": "X64",
        "build_configuration": "Release",
        "conan_lockfile": "conan/locks/test.lock",
        "conan_lockfile_sha256": "d" * 64,
        "revision_matches_checkout": True,
    }
    perf_dir = root / "runtime/perf/fixed-runner-saturation"
    validation_dir = root / "runtime/validation"
    perf_dir.mkdir(parents=True)
    validation_dir.mkdir(parents=True)
    (perf_dir / "summary.json").write_text(json.dumps(perf), encoding="utf-8")
    (validation_dir / "saturation-baseline-summary.json").write_text(
        json.dumps({
            "summary_version": 2,
            "provenance": provenance,
            "backend_pool_size": 8,
            "battle_route_workers": 8,
        }),
        encoding="utf-8",
    )
    return AxisSourceSpec(axis, value, run_id, root)


def write_selection_source(root: Path) -> SelectionSourceSpec:
    spec = write_source(
        root / "selection",
        "service_cpu_count",
        1,
        "900",
        comparison_point=False,
    )
    return SelectionSourceSpec("900", spec.extracted_dir)


class AggregateSaturationAxisEvidenceTest(unittest.TestCase):
    def test_top_level_shim_works_from_repo_and_arbitrary_cwd(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        shim = repo_root / "scripts/aggregate_saturation_axis_evidence.py"
        with tempfile.TemporaryDirectory() as temp:
            invocations = (
                ([sys.executable, "scripts/aggregate_saturation_axis_evidence.py", "--help"], repo_root),
                ([sys.executable, str(shim), "--help"], Path(temp)),
            )
            for command, cwd in invocations:
                completed = subprocess.run(
                    command,
                    cwd=cwd,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("--selection-source", completed.stdout)

    def test_service_cpu_axis_reports_scaling_without_changing_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selection = write_selection_source(root)
            specs = [write_source(root / str(value), "service_cpu_count", value, f"10{value}") for value in (1, 2, 4)]
            summary = aggregate_axis_sources(specs, selection_source=selection)

        self.assertTrue(summary["evidence_complete"])
        self.assertEqual(summary["comparison"]["speedup_vs_value_1"]["4"]["throughput_msg_per_sec"], 4.0)
        self.assertEqual(summary["comparison"]["scaling_efficiency"]["4"]["achieved_response_rate_ops_per_sec"], 1.0)
        self.assertEqual(summary["decision"]["observation"], "near_linear_cpu_scaling")
        self.assertFalse(summary["decision"]["automatic_default_change"])

    def test_single_cpu_io_axis_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selection = write_selection_source(root)
            specs = [write_source(root / str(value), "io_cores", value, f"20{value}") for value in (1, 2, 4)]
            summary = aggregate_axis_sources(specs, selection_source=selection)

        self.assertTrue(summary["evidence_complete"])
        self.assertEqual({source["service_cpu_count"] for source in summary["sources"]}, {1})
        self.assertEqual(summary["decision"]["observation"], "material_io_core_gain")

    def test_rejects_lifecycle_manifest_saturation_resource_and_runner_drift(self) -> None:
        mutations = {
            "lifecycle": lambda payload: payload["case_aggregates"][0].update(
                cancelled_clients={"min": 1, "median": 1, "max": 1}
            ),
            "manifest": lambda payload: payload["case_manifest"][0].update(clients=999),
            "saturation": lambda payload: payload["saturation_analysis"].update(
                collection_pass=False, evidence_valid=False
            ),
            "resource": lambda payload: payload["resource_analysis"]["per_run"][0]["services"]["gateway"].update(
                cpu_percent_from_cpu_seconds=500.0
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                selection = write_selection_source(root)
                specs = [write_source(root / str(value), "service_cpu_count", value, f"30{value}") for value in (1, 2, 4)]
                path = root / "4/runtime/perf/fixed-runner-saturation/summary.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                mutate(payload)
                path.write_text(json.dumps(payload), encoding="utf-8")
                summary = aggregate_axis_sources(specs, selection_source=selection)
                self.assertFalse(summary["evidence_complete"])
                self.assertTrue(summary["failed_step"])

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selection = write_selection_source(root)
            specs = [write_source(root / str(value), "service_cpu_count", value, f"40{value}") for value in (1, 2, 4)]
            envelope_path = root / "4/runtime/validation/saturation-baseline-summary.json"
            envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
            envelope["provenance"]["runner"] = "another-runner"
            envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
            summary = aggregate_axis_sources(specs, selection_source=selection)
            self.assertFalse(summary["evidence_complete"])
            self.assertIn("axis:same-fixed-runner-provenance", {
                check["name"] for check in summary["validation_checks"] if not check["passed"]
            })

    def test_requires_one_axis_with_exact_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selection = write_selection_source(root)
            specs = [
                write_source(root / "one", "service_cpu_count", 1, "501"),
                write_source(root / "two", "service_cpu_count", 2, "502"),
            ]
            summary = aggregate_axis_sources(specs, selection_source=selection)
        self.assertFalse(summary["evidence_complete"])
        self.assertEqual(summary["failed_step"], "axis-source-shape")

    def test_missing_selection_curve_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            specs = [
                write_source(root / str(value), "service_cpu_count", value, f"60{value}")
                for value in (1, 2, 4)
            ]
            summary = aggregate_axis_sources(specs)
        self.assertFalse(summary["evidence_complete"])
        self.assertEqual(summary["failed_step"], "selection:summary")
        self.assertEqual(summary["comparison"], {})
        self.assertEqual(summary["decision"], {})

    def test_selection_requires_bound_fixed_runner_provenance(self) -> None:
        for mutation in ("missing_provenance", "wrong_run_id", "local_forgery"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                selection = write_selection_source(root)
                specs = [
                    write_source(root / str(value), "service_cpu_count", value, f"70{value}")
                    for value in (1, 2, 4)
                ]
                envelope_path = (
                    selection.extracted_dir
                    / "runtime/validation/saturation-baseline-summary.json"
                )
                envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
                if mutation == "missing_provenance":
                    envelope.pop("provenance")
                elif mutation == "wrong_run_id":
                    envelope["provenance"]["run_id"] = "999"
                else:
                    envelope["provenance"].update(workflow="local", runner="local")
                envelope_path.write_text(json.dumps(envelope), encoding="utf-8")

                summary = aggregate_axis_sources(specs, selection_source=selection)

                self.assertFalse(summary["evidence_complete"])
                failed = {
                    check["name"]
                    for check in summary["validation_checks"]
                    if not check["passed"]
                }
                expected = (
                    "selection:run-id"
                    if mutation == "wrong_run_id"
                    else "selection:provenance"
                )
                self.assertIn(expected, failed)

    def test_parse_source_preserves_colons_in_path(self) -> None:
        source = parse_source("io_cores:2:123:/tmp/evidence:with-colon")
        self.assertEqual(source.axis, "io_cores")
        self.assertEqual(source.value, 2)
        self.assertTrue(str(source.extracted_dir).endswith("evidence:with-colon"))
        selection = parse_selection_source("456:/tmp/curve:with-colon")
        self.assertEqual(selection.run_id, "456")
        self.assertTrue(str(selection.extracted_dir).endswith("curve:with-colon"))


if __name__ == "__main__":
    unittest.main()
