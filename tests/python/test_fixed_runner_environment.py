from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts/gates/infrastructure/check_fixed_runner_environment.py"
)
SPEC = importlib.util.spec_from_file_location("check_fixed_runner_environment", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OrbStackPortIsolationTests(unittest.TestCase):
    def test_passes_when_forwarding_is_disabled(self) -> None:
        errors: list[str] = []
        with (
            mock.patch.object(MODULE.platform, "system", return_value="Darwin"),
            mock.patch.object(
                MODULE.shutil, "which", return_value="/usr/local/bin/orbctl"
            ),
            mock.patch.object(
                MODULE.subprocess,
                "check_output",
                side_effect=["boost-linux-arm64  running\n", "false\n"],
            ),
        ):
            result = MODULE.check_orbstack_port_isolation(errors)

        self.assertEqual(result["status"], "passed")
        self.assertIs(result["required"], True)
        self.assertEqual(errors, [])

    def test_rejects_forwarding_on_shared_host(self) -> None:
        errors: list[str] = []
        with (
            mock.patch.object(MODULE.platform, "system", return_value="Darwin"),
            mock.patch.object(
                MODULE.shutil, "which", return_value="/usr/local/bin/orbctl"
            ),
            mock.patch.object(
                MODULE.subprocess,
                "check_output",
                side_effect=["boost-linux-arm64  running\n", "true\n"],
            ),
        ):
            result = MODULE.check_orbstack_port_isolation(errors)

        self.assertEqual(result["status"], "failed")
        self.assertIs(result["required"], True)
        self.assertEqual(len(errors), 1)

    def test_skips_unrelated_macos_runner(self) -> None:
        errors: list[str] = []
        with (
            mock.patch.object(MODULE.platform, "system", return_value="Darwin"),
            mock.patch.object(
                MODULE.shutil, "which", return_value="/usr/local/bin/orbctl"
            ),
            mock.patch.object(
                MODULE.subprocess, "check_output", return_value="other-vm\n"
            ),
        ):
            result = MODULE.check_orbstack_port_isolation(errors)

        self.assertEqual(result["status"], "skipped")
        self.assertIs(result["required"], False)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
