import unittest

from scripts.producers.collect_v2_arch_baseline import (
    MAILBOX_CASE,
    merge_benchmark_results,
)


def result(name: str) -> dict[str, object]:
    return {
        "name": name,
        "samples": 100,
        "p99_us": 1.0,
        "throughput_ops_per_sec": 1000.0,
    }


class MergeV2ArchitectureBaselinesTest(unittest.TestCase):
    def test_merges_isolated_mailbox_result_after_architecture_results(self) -> None:
        merged = merge_benchmark_results(
            {"results": [result("actor"), result("battle")]},
            {"results": [result(MAILBOX_CASE)]},
        )

        self.assertEqual(["actor", "battle", MAILBOX_CASE], [item["name"] for item in merged])

    def test_rejects_mailbox_case_in_primary_architecture_binary(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain"):
            merge_benchmark_results(
                {"results": [result("actor"), result(MAILBOX_CASE)]},
                {"results": [result(MAILBOX_CASE)]},
            )

    def test_rejects_wrong_standalone_case(self) -> None:
        with self.assertRaisesRegex(ValueError, "must contain only"):
            merge_benchmark_results(
                {"results": [result("actor")]},
                {"results": [result("other")]},
            )

    def test_rejects_duplicate_primary_results(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            merge_benchmark_results(
                {"results": [result("actor"), result("actor")]},
                {"results": [result(MAILBOX_CASE)]},
            )

    def test_rejects_missing_results_array(self) -> None:
        with self.assertRaisesRegex(ValueError, "results arrays"):
            merge_benchmark_results({}, {"results": [result(MAILBOX_CASE)]})


if __name__ == "__main__":
    unittest.main()
