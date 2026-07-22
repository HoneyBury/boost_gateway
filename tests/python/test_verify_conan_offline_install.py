from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.tools import verify_conan_offline_install


SHA = "a" * 40


class VerifyConanOfflineInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.profile = self.directory / "profile"
        self.profile.write_text("[settings]\nos=Linux\n", encoding="utf-8")
        self.lockfile = self.directory / "conan.lock"
        self.summary = self.directory / "summary.json"

    def write_lock(self, packages: list[str]) -> None:
        self.lockfile.write_text(
            json.dumps(
                {
                    "version": "0.5",
                    "requires": [f"{name}/1.0#{index:040x}%1" for index, name in enumerate(packages, 1)],
                    "build_requires": [],
                    "python_requires": [],
                    "config_requires": [],
                }
            ),
            encoding="utf-8",
        )

    def run_main(self) -> tuple[int, dict[str, object]]:
        argv = [
            "verify_conan_offline_install.py",
            "--profile",
            str(self.profile),
            "--lockfile",
            str(self.lockfile),
            "--output-folder",
            str(self.directory / "output"),
            "--build-type",
            "Release",
            "--candidate-revision",
            SHA,
            "--summary-path",
            str(self.summary),
        ]

        def fake_run(command: list[str], **_: object) -> SimpleNamespace:
            if command[0] == "git":
                return SimpleNamespace(returncode=0, stdout=SHA + "\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="offline install complete\n", stderr="")

        with patch("sys.argv", argv), patch.object(
            verify_conan_offline_install.subprocess, "run", side_effect=fake_run
        ):
            result = verify_conan_offline_install.main()
        return result, json.loads(self.summary.read_text(encoding="utf-8"))

    def test_records_strict_offline_raft_graph(self) -> None:
        self.write_lock(["abseil", "protobuf", "fmt"])
        result, summary = self.run_main()

        self.assertEqual(result, 0)
        self.assertTrue(summary["overall_pass"])
        self.assertIn("--build=never", summary["command"])
        self.assertIn("--no-remote", summary["command"])
        self.assertTrue(summary["offline_contract"]["with_raft_protobuf"])
        self.assertFalse(summary["offline_contract"]["with_grpc"])
        self.assertEqual(summary["provenance"]["candidate_revision"], SHA)

    def test_rejects_missing_runtime_dependency_and_grpc_graph(self) -> None:
        self.write_lock(["protobuf", "grpc"])
        result, summary = self.run_main()

        self.assertEqual(result, 1)
        self.assertFalse(summary["overall_pass"])
        self.assertTrue(any("abseil" in failure for failure in summary["failures"]))
        self.assertTrue(any("grpc" in failure for failure in summary["failures"]))


if __name__ == "__main__":
    unittest.main()
