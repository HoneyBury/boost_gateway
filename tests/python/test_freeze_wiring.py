import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.gates.production import verify_gateway_observability_runtime
from scripts.gates.production import verify_production_candidate_evidence


class FreezeWiringTest(unittest.TestCase):
    def test_candidate_redis_preflight_uses_supported_flag(self) -> None:
        commands: list[list[str]] = []

        def fake_run_step(
            name: str, category: str, command: list[str], timeout_seconds: int
        ) -> dict[str, object]:
            commands.append(command)
            return {
                "name": name,
                "category": category,
                "status": "passed",
                "returncode": 0,
                "duration_seconds": 0.0,
                "stdout_tail": "",
                "stderr_tail": "",
            }

        with tempfile.TemporaryDirectory() as temporary_directory, mock.patch.object(
            verify_production_candidate_evidence, "run_step", side_effect=fake_run_step
        ), mock.patch.object(
            sys,
            "argv",
            [
                "verify_production_candidate_evidence.py",
                "--include-redis-live",
                "--summary-path",
                str(Path(temporary_directory) / "summary.json"),
            ],
        ):
            result = verify_production_candidate_evidence.main()

        self.assertEqual(0, result)
        self.assertIn("--require-redis", commands[0])
        self.assertNotIn("--include-redis-live", commands[0])

    def test_runtime_observability_resolves_repository_root(self) -> None:
        expected_root = Path(__file__).resolve().parents[2]

        self.assertEqual(expected_root, verify_gateway_observability_runtime.REPO_ROOT)
        self.assertTrue(
            (verify_gateway_observability_runtime.REPO_ROOT / "scripts/verify_sdk_full_flow_client.py").is_file()
        )


if __name__ == "__main__":
    unittest.main()
