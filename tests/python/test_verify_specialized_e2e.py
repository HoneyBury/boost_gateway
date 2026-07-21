import importlib.util
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "e2e" / "verify_specialized_e2e.py"
SPEC = importlib.util.spec_from_file_location("verify_specialized_e2e", SCRIPT_PATH)
assert SPEC and SPEC.loader
verify_specialized_e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_specialized_e2e)


GTEST_LIST_OUTPUT = """Running main() from gtest_main.cc
RaftClusterTest.
  ThreeNodeClusterElectsSingleLeader
  LeaderStepDownOnHigherTerm
RedisClientTest.
  SetAndGet
  DelRemovesKey
RedisLeaderboardLiveTest.
  SubmitReturnsRank
  TopKReturnsDescending
"""


class SpecializedE2ETests(unittest.TestCase):
    def test_parse_gtest_list_output_returns_fully_qualified_names(self) -> None:
        self.assertEqual(verify_specialized_e2e.parse_gtest_list_output(GTEST_LIST_OUTPUT), [
            "RaftClusterTest.ThreeNodeClusterElectsSingleLeader",
            "RaftClusterTest.LeaderStepDownOnHigherTerm",
            "RedisClientTest.SetAndGet",
            "RedisClientTest.DelRemovesKey",
            "RedisLeaderboardLiveTest.SubmitReturnsRank",
            "RedisLeaderboardLiveTest.TopKReturnsDescending",
        ])

    def test_match_gtest_filter_requires_every_positive_pattern_to_match(self) -> None:
        tests = verify_specialized_e2e.parse_gtest_list_output(GTEST_LIST_OUTPUT)
        matched, missing = verify_specialized_e2e.match_gtest_filter(
            "RedisClientTest.SetAndGet:RedisClientTest.SetGetDel:RedisLeaderboardLiveTest.*",
            tests,
        )

        self.assertEqual(matched, [
            "RedisClientTest.SetAndGet",
            "RedisLeaderboardLiveTest.SubmitReturnsRank",
            "RedisLeaderboardLiveTest.TopKReturnsDescending",
        ])
        self.assertEqual(missing, ["RedisClientTest.SetGetDel"])

    def test_match_gtest_filter_applies_negative_patterns(self) -> None:
        tests = verify_specialized_e2e.parse_gtest_list_output(GTEST_LIST_OUTPUT)
        matched, missing = verify_specialized_e2e.match_gtest_filter(
            "RedisLeaderboardLiveTest.*-*.TopKReturnsDescending",
            tests,
        )

        self.assertEqual(missing, [])
        self.assertEqual(matched, ["RedisLeaderboardLiveTest.SubmitReturnsRank"])

    def test_parse_gtest_run_count_uses_summary_line(self) -> None:
        output = "[==========] Running 3 tests from 2 test suites.\n[==========] 3 tests ran."
        self.assertEqual(verify_specialized_e2e.parse_gtest_run_count(output), 3)
        self.assertIsNone(verify_specialized_e2e.parse_gtest_run_count("no gtest summary"))

    @mock.patch.object(verify_specialized_e2e.subprocess, "run")
    def test_run_step_rejects_a_partially_unmatched_filter(self, run: mock.Mock) -> None:
        run.return_value = SimpleNamespace(returncode=0, stdout=GTEST_LIST_OUTPUT, stderr="")

        result = verify_specialized_e2e.run_step(
            "filter validation",
            "test",
            ["fake-tests", "--gtest_filter=RedisClientTest.SetAndGet:RedisClientTest.SetGetDel"],
            Path("."),
            10,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("RedisClientTest.SetGetDel", result["stderr_tail"])
        run.assert_called_once()

    @mock.patch.object(verify_specialized_e2e.subprocess, "run")
    def test_run_step_records_validated_and_executed_counts(self, run: mock.Mock) -> None:
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout=GTEST_LIST_OUTPUT, stderr=""),
            SimpleNamespace(
                returncode=0,
                stdout="[==========] Running 2 tests from 1 test suite.\n[==========] 2 tests ran.\n",
                stderr="",
            ),
        ]

        result = verify_specialized_e2e.run_step(
            "filter validation",
            "test",
            ["fake-tests", "--gtest_filter=RedisLeaderboardLiveTest.*"],
            Path("."),
            10,
        )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["expected_test_count"], 2)
        self.assertEqual(result["executed_test_count"], 2)
        self.assertEqual(run.call_count, 2)

    def test_repository_filters_only_reference_registered_test_names(self) -> None:
        source_root = Path(__file__).resolve().parents[2] / "tests" / "v2"
        registered = []
        test_pattern = re.compile(r"TEST(?:_F)?\(\s*([^,\s]+)\s*,\s*([^\)\s]+)\s*\)")
        for source in source_root.rglob("*.cpp"):
            registered.extend(f"{suite}.{test}" for suite, test in test_pattern.findall(source.read_text(encoding="utf-8")))

        for filter_expression in (
            verify_specialized_e2e.RAFT_UNIT_FILTER,
            verify_specialized_e2e.RAFT_INTEGRATION_FILTER,
            verify_specialized_e2e.REDIS_DEGRADED_FILTER,
            verify_specialized_e2e.REDIS_LIVE_FILTER,
            verify_specialized_e2e.REDIS_EVENT_STORE_LIVE_FILTER,
        ):
            matched, missing = verify_specialized_e2e.match_gtest_filter(filter_expression, registered)
            self.assertTrue(matched)
            self.assertEqual(missing, [])

    def test_specialized_summary_provenance_matches_checkout(self) -> None:
        root = Path(__file__).resolve().parents[2]
        provenance = verify_specialized_e2e.build_evidence_provenance(
            root, build_configuration="Release"
        )
        self.assertTrue(provenance["revision_matches_checkout"])
        self.assertEqual(provenance["candidate_revision"], provenance["git_commit"])


if __name__ == "__main__":
    unittest.main()
