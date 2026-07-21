#!/usr/bin/env python3
"""Unit coverage for control-plane Go state isolation."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts/gates/production/verify_control_plane_gate.py"
SPEC = importlib.util.spec_from_file_location("verify_control_plane_gate", SCRIPT_PATH)
assert SPEC and SPEC.loader
CONTROL_PLANE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL_PLANE)


class VerifyControlPlaneGateTests(unittest.TestCase):
    def test_runner_default_is_job_isolated_and_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as runner_temp, mock.patch.dict(
            os.environ,
            {"RUNNER_TEMP": runner_temp, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"},
            clear=True,
        ):
            state_root = CONTROL_PLANE.default_go_state_root()

        self.assertEqual(state_root, Path(runner_temp) / "boost-gateway-go-123-2")
        self.assertFalse(state_root.is_relative_to(REPO_ROOT))

    def test_explicit_environment_root_wins(self) -> None:
        with tempfile.TemporaryDirectory() as configured, mock.patch.dict(
            os.environ,
            {"BOOST_GATEWAY_GO_STATE_ROOT": configured, "RUNNER_TEMP": "/ignored"},
            clear=True,
        ):
            self.assertEqual(CONTROL_PLANE.default_go_state_root(), Path(configured))

    def test_go_environment_keeps_all_writable_state_under_state_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, {}, clear=True):
            state_root = Path(temp_dir) / "go-state"
            env = CONTROL_PLANE.go_environment(state_root)

            for key in ("GOCACHE", "GOMODCACHE", "GOTELEMETRYDIR", "APPDATA", "LOCALAPPDATA", "USERPROFILE"):
                self.assertTrue(Path(env[key]).is_relative_to(state_root), key)
            self.assertEqual(env["GOTELEMETRY"], "off")
            self.assertTrue(Path(env["GOCACHE"]).is_dir())
            self.assertTrue(Path(env["GOMODCACHE"]).is_dir())


if __name__ == "__main__":
    unittest.main()
