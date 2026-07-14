#!/usr/bin/env python3
"""Regression tests for the Conan bootstrap cache-home selection."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "scripts" / "bootstrap_conan.py"


class BootstrapConanTests(unittest.TestCase):
    def test_conan_home_environment_is_used_when_argument_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_bin = temp_root / "bin"
            fake_bin.mkdir()
            fake_conan = fake_bin / "conan"
            fake_conan.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_conan.chmod(0o755)
            runner_home = temp_root / "persistent-runner-cache"
            env = os.environ.copy()
            env["CONAN_HOME"] = str(runner_home)
            env["PATH"] = f"{fake_bin}:{env['PATH']}"

            completed = subprocess.run(
                [sys.executable, str(BOOTSTRAP), "--no-remote"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertTrue(runner_home.is_dir())
            self.assertIn(f"CONAN_HOME={runner_home}", completed.stdout)
            self.assertNotIn(".conan2-local", completed.stdout)


if __name__ == "__main__":
    unittest.main()
