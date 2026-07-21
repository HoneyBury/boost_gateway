import copy
import unittest

from scripts.producers.collect_v2_perf_baseline import (
    aggregate_case_runs,
    analyze_resources,
    battle_p99_limit_ms,
    build_case_resource_evidence,
    build_case_manifest,
    build_run_cases,
    build_saturation_analysis,
    evaluate_release_gates,
)


def pressure_run(clients: int, *, scenario: str = "echo") -> dict:
    natural = scenario == "battle"
    return {
        "target_clients": clients,
        "started_clients": clients,
        "tcp_connected_clients": clients,
        "authenticated_clients": clients,
        "active_clients": 0,
        "peak_active_clients": clients,
        "cancelled_clients": 0,
        "cancelled_before_connect": 0,
        "connected_clients": clients,
        "failed_clients": 0,
        "rejected_clients": 0,
        "total_messages": 7000 if natural else 1000,
        "response_messages": 7000 if natural else 1000,
        "push_messages": 0,
        "throughput_msg_per_sec": 1000.0,
        "latency_p50_ms": 1.0,
        "latency_p90_ms": 2.0,
        "latency_p99_ms": 3.0,
        "ramp_up_seconds": 2.0,
        "ramp_timeout_seconds": 60.0,
        "ramp_completed": True,
        "measurement_started": True,
        "steady_state_target_seconds": 30.0,
        "steady_state_elapsed_seconds": 12.0 if natural else 30.0,
        "steady_state_completed": True,
        "termination_reason": "natural_completion" if natural else "steady_duration_elapsed",
        "bench_exit_code": 0,
        "forced_timeout": False,
        "load_model": "closed_loop_one_in_flight_per_client",
        "configured_request_rate_is_bounded": True,
        "configured_request_rate_ceiling_ops_per_sec": 10_000.0,
        "business_send_attempts": 300_000,
        "business_send_successes": 300_000,
        "achieved_send_rate_ops_per_sec": 10_000.0,
        "achieved_response_rate_ops_per_sec": 10_000.0,
    }


class PerfLoadEvidenceTest(unittest.TestCase):
    def evaluate(self, case_name: str, run: dict) -> dict:
        aggregate = aggregate_case_runs(case_name, [run])
        return evaluate_release_gates([aggregate])["checks"][0]

    def test_echo_requires_real_connections_and_full_steady_window(self) -> None:
        valid = pressure_run(100)
        self.assertTrue(self.evaluate("echo-100-30s", valid)["passed"])

        invalid_mutations = {
            "not all started": lambda run: run.update(started_clients=99),
            "not all tcp connected": lambda run: run.update(tcp_connected_clients=99),
            "not all authenticated": lambda run: run.update(authenticated_clients=99),
            "target was never active": lambda run: run.update(peak_active_clients=99),
            "cancelled before connect": lambda run: run.update(
                cancelled_clients=1, cancelled_before_connect=1
            ),
            "ramp incomplete": lambda run: run.update(ramp_completed=False),
            "measurement missing": lambda run: run.update(measurement_started=False),
            "steady window short": lambda run: run.update(
                steady_state_elapsed_seconds=20.0
            ),
            "bench failed": lambda run: run.update(bench_exit_code=1),
        }
        for name, mutate in invalid_mutations.items():
            with self.subTest(name=name):
                run = dict(valid)
                mutate(run)
                self.assertFalse(self.evaluate("echo-100-30s", run)["passed"])

    def test_echo_count_consistency_allows_inflight_sends_but_rejects_mismatch(self) -> None:
        valid = pressure_run(100)
        valid["business_send_successes"] = valid["response_messages"] + 100
        valid["business_send_attempts"] = valid["business_send_successes"] + 10
        self.assertTrue(self.evaluate("echo-100-30s", valid)["passed"])

        inconsistent = dict(valid)
        inconsistent["total_messages"] += 1
        check = self.evaluate("echo-100-30s", inconsistent)
        self.assertFalse(check["passed"])
        self.assertFalse(check["observed"]["message_count_consistent"])

    def test_within_ten_percent_warning_excludes_values_above_gate(self) -> None:
        echo_near = pressure_run(100)
        echo_near["latency_p99_ms"] = 45.0
        echo_near_gates = evaluate_release_gates([
            aggregate_case_runs("echo-100-30s", [echo_near])
        ])
        self.assertEqual(len(echo_near_gates["warnings"]), 1)

        echo_over = dict(echo_near)
        echo_over["latency_p99_ms"] = 75.0
        echo_over_gates = evaluate_release_gates([
            aggregate_case_runs("echo-100-30s", [echo_over])
        ])
        self.assertEqual(echo_over_gates["warnings"], [])

        battle_limit = battle_p99_limit_ms("battle-100-30s")
        battle_near = pressure_run(100, scenario="battle")
        battle_near["latency_p99_ms"] = battle_limit * 0.9
        battle_near_gates = evaluate_release_gates([
            aggregate_case_runs("battle-100-30s", [battle_near])
        ])
        self.assertEqual(len(battle_near_gates["warnings"]), 1)

        battle_over = dict(battle_near)
        battle_over["latency_p99_ms"] = battle_limit + 1.0
        battle_over_gates = evaluate_release_gates([
            aggregate_case_runs("battle-100-30s", [battle_over])
        ])
        self.assertEqual(battle_over_gates["warnings"], [])

    def test_battle_natural_completion_is_explicitly_valid(self) -> None:
        run = pressure_run(100, scenario="battle")
        run["response_messages"] = 2000
        run["push_messages"] = 5000
        check = self.evaluate("battle-100-30s", run)
        self.assertTrue(check["passed"])
        self.assertEqual(check["observed"]["termination_reasons"], ["natural_completion"])
        self.assertTrue(check["observed"]["message_count_consistent"])

        run["steady_state_completed"] = False
        self.assertFalse(self.evaluate("battle-100-30s", run)["passed"])

        run["steady_state_completed"] = True
        run["push_messages"] -= 1
        self.assertFalse(self.evaluate("battle-100-30s", run)["passed"])

    def test_capacity_preset_uses_bounded_high_rate_ramp(self) -> None:
        cases = build_run_cases("capacity")
        ten_thousand = next(case for case in cases if case["clients"] == 10_000)
        self.assertEqual(ten_thousand["ramp_clients_per_second"], 2000)
        self.assertEqual(ten_thousand["ramp_timeout_seconds"], 90)
        self.assertLess(
            ten_thousand["clients"] / ten_thousand["ramp_clients_per_second"],
            ten_thousand["ramp_timeout_seconds"],
        )

    def test_saturation_manifest_keeps_comparison_identity(self) -> None:
        cases = build_run_cases("saturation")
        manifest = build_case_manifest(
            cases,
            service_cpu_set="0-1",
            service_cpu_count=2,
            io_cores=4,
        )
        self.assertGreaterEqual(len(manifest), 3)
        ceilings = [item["configured_request_rate_ceiling_ops_per_sec"] for item in manifest]
        self.assertEqual(ceilings, sorted(ceilings))
        self.assertEqual(manifest[0]["comparison_axes"], ["service_cpu_count", "io_cores"])
        self.assertIn("service_cpu_count=2|io_cores=4", manifest[0]["comparison_identity"])

    def test_saturation_analysis_finds_cpu_knee_or_is_inconclusive(self) -> None:
        case_names = ["echo-sat-low", "echo-sat-mid", "echo-sat-high"]
        ceilings = [5_000.0, 10_000.0, 20_000.0]
        responses = [4_500.0, 9_000.0, 9_200.0]
        cpu_values = [50.0, 90.0, 95.0]
        aggregates = []
        cases = []
        snapshots = {}
        per_run = []
        resource_aggregates = []
        manifest = []
        for name, ceiling, response, cpu in zip(
            case_names, ceilings, responses, cpu_values, strict=True
        ):
            run = pressure_run(100)
            run.update({
                "configured_request_rate_ceiling_ops_per_sec": ceiling,
                "achieved_send_rate_ops_per_sec": response,
                "achieved_response_rate_ops_per_sec": response,
            })
            aggregate = aggregate_case_runs(name, [run])
            identity = {
                "case_id": name,
                "comparison_identity": f"{name}|service_cpu_count=1|io_cores=4",
            }
            aggregate["case_identity"] = identity
            aggregates.append(aggregate)
            run_name = f"{name}.run1"
            cases.append({"case_name": run_name})
            snapshots[run_name] = {
                "measurement_boundary": "loadgen_process_exit",
                "quiescence": {"quiesced": True},
            }
            per_run.append({
                "case_name": run_name,
                "services": {
                    "v2_gateway_demo": {"cpu_percent_from_cpu_seconds": cpu},
                },
                "loadgen": {"cpu_percent_from_cpu_seconds": 75.0},
            })
            resource_aggregates.append({
                "case_name": name,
                "services": {
                    "v2_gateway_demo": {
                        "cpu_percent_from_cpu_seconds": {
                            "min": cpu,
                            "median": cpu,
                            "max": cpu,
                        },
                    },
                },
            })
            manifest.append(identity)

        summary = {
            "repetitions": 1,
            "case_manifest": manifest,
            "case_aggregates": aggregates,
            "cases": cases,
            "process_snapshots": snapshots,
            "service_resource_constraint": {
                "type": "linux_cpu_affinity",
                "applied": True,
                "effective_cpu_set": "0",
                "cpu_count": 1,
            },
            "loadgen_resource_constraint": {
                "type": "linux_cpu_affinity",
                "applied": True,
                "effective_cpu_set": "2-3",
                "cpu_count": 2,
            },
            "resource_analysis": {
                "per_run": per_run,
                "case_aggregates": resource_aggregates,
            },
        }
        analysis = build_saturation_analysis(summary)
        self.assertTrue(analysis["collection_pass"])
        self.assertTrue(analysis["saturation_found"])
        self.assertEqual(analysis["conclusion"], "knee_found")
        self.assertEqual(analysis["cpu_saturation_case"], aggregates[1]["case_identity"])
        self.assertEqual(analysis["throughput_knee_case"], aggregates[2]["case_identity"])

        inconsistent_summary = copy.deepcopy(summary)
        inconsistent_summary["case_aggregates"][0]["message_count_consistent"] = False
        inconsistent = build_saturation_analysis(inconsistent_summary)
        self.assertFalse(inconsistent["collection_pass"])
        self.assertFalse(inconsistent["points"][0]["evidence_valid"])
        self.assertIn(
            "inconsistent total/response/push message counts",
            inconsistent["inconclusive_reason"],
        )

        comparison_summary = {
            **summary,
            "case_manifest": manifest[1:2],
            "case_aggregates": aggregates[1:2],
            "cases": cases[1:2],
            "process_snapshots": {cases[1]["case_name"]: snapshots[cases[1]["case_name"]]},
            "resource_analysis": {
                "per_run": per_run[1:2],
                "case_aggregates": resource_aggregates[1:2],
            },
        }
        comparison = build_saturation_analysis(comparison_summary)
        self.assertTrue(comparison["collection_pass"])
        self.assertEqual(comparison["analysis_mode"], "comparison_point")
        self.assertFalse(comparison["saturation_found"])

        for item in per_run:
            item["loadgen"]["cpu_percent_from_cpu_seconds"] = 180.0
        no_headroom = build_saturation_analysis(summary)
        self.assertTrue(no_headroom["collection_pass"])
        self.assertFalse(no_headroom["saturation_found"])
        self.assertIsNotNone(no_headroom["gateway_cpu_threshold_case"])
        self.assertIsNone(no_headroom["cpu_saturation_case"])
        self.assertIn("load generator lacked required headroom", no_headroom["inconclusive_reason"])
        for item in per_run:
            item["loadgen"]["cpu_percent_from_cpu_seconds"] = 75.0

        for item, cpu in zip(resource_aggregates, [20.0, 30.0, 40.0], strict=True):
            item["services"]["v2_gateway_demo"]["cpu_percent_from_cpu_seconds"].update(
                min=cpu, median=cpu, max=cpu
            )
        inconclusive = build_saturation_analysis(summary)
        self.assertTrue(inconclusive["collection_pass"])
        self.assertFalse(inconclusive["overall_pass"])
        self.assertEqual(inconclusive["conclusion"], "inconclusive")

    def test_resource_cpu_window_excludes_post_run_quiescence(self) -> None:
        before = [{
            "service_name": "v2_gateway_demo",
            "cpu_seconds": 0.0,
            "working_set_mb": 10.0,
            "private_memory_mb": 10.0,
            "virtual_memory_mb": 20.0,
            "handles": 5,
            "threads": 2,
            "cpu_percent": 0.0,
        }]
        load_end = [{**before[0], "cpu_seconds": 60.0}]
        after_quiescence = [{**before[0], "cpu_seconds": 65.0}]
        window = build_case_resource_evidence(
            service_before=before,
            service_at_load_end=load_end,
            loadgen={"cpu_percent_from_cpu_seconds": 50.0},
            load_window_elapsed_seconds=60.0,
            quiescence={"quiesced": True, "wait_seconds": 5.0},
            service_after_quiescence=after_quiescence,
        )
        summary = {
            "cases": [{"case_name": "echo-sat.run1", "connected_clients": 100}],
            "process_snapshots": {"echo-sat.run1": window},
        }

        analyzed = analyze_resources(summary)["per_run"][0]
        gateway = analyzed["services"]["v2_gateway_demo"]
        self.assertEqual(gateway["cpu_seconds_delta"], 60.0)
        self.assertEqual(gateway["cpu_percent_from_cpu_seconds"], 100.0)
        self.assertEqual(window["measurement_boundary"], "loadgen_process_exit")
        self.assertEqual(
            window["post_quiescence"]["after"][0]["cpu_seconds"], 65.0
        )


if __name__ == "__main__":
    unittest.main()
