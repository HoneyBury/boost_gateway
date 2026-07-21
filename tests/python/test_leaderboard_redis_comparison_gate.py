import json
import tempfile
import unittest
from pathlib import Path

from scripts.gates.release.verify_fixed_runner_release_capacity import (
    validate_leaderboard_redis_comparison,
)


def mode_summary(runs: int = 3) -> dict:
    return {
        "scenario_aggregates": [{
            "scenario": "leaderboard",
            "runs": runs,
            "passed_runs": runs,
            "passed": True,
            "operations": [
                {"operation": operation, "failed": 0}
                for operation in ("leaderboard_submit", "leaderboard_top", "leaderboard_rank")
            ],
        }],
    }


class LeaderboardRedisComparisonGateTest(unittest.TestCase):
    def write_summary(self, root: Path, comparison: dict) -> Path:
        path = root / "summary.json"
        path.write_text(json.dumps({"leaderboard_persistence_comparison": comparison}), encoding="utf-8")
        return path

    def valid_comparison(self) -> dict:
        return {
            "requested": True,
            "verified": True,
            "repetitions_per_mode": 3,
            "modes": {
                "in_memory_only": {"log_verified": True, "summary": mode_summary()},
                "redis_primary_with_memory_shadow": {"log_verified": True, "summary": mode_summary()},
            },
            "redis_proof": {
                "verified": True,
                "ping_before": True,
                "ping_after": True,
                "key": "lb:perf:unique",
                "zcard": 48,
                "expected_min_zcard": 48,
            },
        }

    def test_accepts_complete_three_run_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_summary(Path(temp), self.valid_comparison())
            result = validate_leaderboard_redis_comparison(path, True, 3)
        self.assertTrue(result["passed"])

    def test_rejects_missing_log_or_insufficient_zcard(self) -> None:
        for mutation in ("log", "zcard"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                comparison = self.valid_comparison()
                if mutation == "log":
                    comparison["modes"]["in_memory_only"]["log_verified"] = False
                else:
                    comparison["redis_proof"]["zcard"] = 47
                path = self.write_summary(Path(temp), comparison)
                result = validate_leaderboard_redis_comparison(path, True, 3)
                self.assertFalse(result["passed"])

    def test_optional_missing_comparison_does_not_fail_existing_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "summary.json"
            path.write_text("{}", encoding="utf-8")
            result = validate_leaderboard_redis_comparison(path, False, 3)
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
