#!/usr/bin/env python3

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.tools import create_macos_dsym_package, verify_macos_dsym_package


@unittest.skipUnless(
    platform.system() == "Darwin"
    and platform.machine() == "arm64"
    and all(shutil.which(tool) for tool in ("c++", "dsymutil", "dwarfdump", "strip", "codesign")),
    "native macOS ARM64 symbol tools are required",
)
class MacosDsymPackageTest(unittest.TestCase):
    def test_manifest_paths_must_remain_relative(self) -> None:
        self.assertIsNotNone(verify_macos_dsym_package.safe_relative("bin/probe"))
        self.assertIsNone(verify_macos_dsym_package.safe_relative("../probe"))
        self.assertIsNone(verify_macos_dsym_package.safe_relative("/tmp/probe"))

    def test_creates_and_verifies_runtime_dsym_pair(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "probe.cpp"
            source.write_text(
                "int probe_value(int value) { return value + 1; }\n"
                "int main() { return probe_value(1) == 2 ? 0 : 1; }\n",
                encoding="utf-8",
            )
            install = root / "install" / "bin"
            install.mkdir(parents=True)
            object_file = root / "probe.o"
            subprocess.run([
                "c++", "-std=c++20", "-O2", "-g", "-arch", "arm64", "-c", str(source),
                "-o", str(object_file),
            ], check=True)
            subprocess.run([
                "c++", "-arch", "arm64", str(object_file), "-o", str(install / "probe"),
            ], check=True)
            assets = root / "assets"
            build_summary = root / "build-summary.json"
            with mock.patch("sys.argv", [
                "create_macos_dsym_package.py",
                "--install-root", str(root / "install"),
                "--version", "test",
                "--output-dir", str(assets),
                "--candidate-revision", "a" * 40,
                "--summary-path", str(build_summary),
            ]):
                self.assertEqual(0, create_macos_dsym_package.main())

            runtime = assets / "boost-gateway-test-macos-arm64-symbol-runtime.tar.gz"
            symbols = assets / "boost-gateway-test-macos-arm64-dsym.tar.gz"
            self.assertEqual(
                sorted([runtime.name, symbols.name]),
                sorted(path.name for path in assets.iterdir()),
            )
            verify_summary = root / "verify-summary.json"
            with mock.patch("sys.argv", [
                "verify_macos_dsym_package.py",
                "--runtime-archive", str(runtime),
                "--symbols-archive", str(symbols),
                "--candidate-revision", "a" * 40,
                "--summary-path", str(verify_summary),
            ]):
                self.assertEqual(0, verify_macos_dsym_package.main())
            summary = json.loads(verify_summary.read_text(encoding="utf-8"))
            self.assertTrue(summary["overall_pass"])
            self.assertEqual("a" * 40, summary["candidate_revision"])
            self.assertEqual(0, summary["failed_checks"])


if __name__ == "__main__":
    unittest.main()
