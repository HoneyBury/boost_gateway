#!/usr/bin/env python3
"""Unit coverage for the pinned Conan virtual environment helper."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts/tools/ensure_conan_venv.py"
SPEC = importlib.util.spec_from_file_location("ensure_conan_venv", SCRIPT_PATH)
assert SPEC and SPEC.loader
VENV_TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VENV_TOOL)


class EnsureConanVenvTests(unittest.TestCase):
    def test_offline_accepts_matching_precreated_virtual_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            venv_path = Path(temp_dir) / "conan-venv"
            python_path = venv_path / "bin" / "python"
            python_path.parent.mkdir(parents=True)
            python_path.write_text(
                "#!/bin/sh\nprintf 'Python 3.12.4\\n'\n",
                encoding="utf-8",
            )
            python_path.chmod(0o755)
            conan_path = venv_path / "bin" / "conan"
            conan_path.write_text("#!/bin/sh\nprintf 'Conan version 2.8.1\\n'\n", encoding="utf-8")
            conan_path.chmod(0o755)

            self.assertEqual(VENV_TOOL.ensure_conan_venv(venv_path, "2.8.1", "3.12", offline=True), ("3.12", "2.8.1"))

    def test_offline_rejects_missing_virtual_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeError, "pre-created"):
                VENV_TOOL.ensure_conan_venv(Path(temp_dir) / "missing", "2.8.1", "3.12", offline=True)

    def test_offline_rejects_partial_virtual_environment_without_conan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            venv_path = Path(temp_dir) / "conan-venv"
            python_path = venv_path / "bin" / "python"
            python_path.parent.mkdir(parents=True)
            python_path.write_text("#!/bin/sh\nprintf 'Python 3.12.4\\n'\n", encoding="utf-8")
            python_path.chmod(0o755)

            with self.assertRaisesRegex(RuntimeError, "requires Conan 2.8.1"):
                VENV_TOOL.ensure_conan_venv(venv_path, "2.8.1", "3.12", offline=True)

    def test_online_can_recreate_python_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            venv_path = Path(temp_dir) / "conan-venv"
            python_path = venv_path / "bin" / "python"
            python_path.parent.mkdir(parents=True)
            python_path.write_text("stale", encoding="utf-8")
            with (
                mock.patch.object(VENV_TOOL, "installed_python_version", side_effect=["3.10", "3.12"]),
                mock.patch.object(VENV_TOOL, "installed_conan_version", return_value="2.8.1"),
                mock.patch.object(VENV_TOOL.venv.EnvBuilder, "create") as create,
            ):
                result = VENV_TOOL.ensure_conan_venv(
                    venv_path,
                    "2.8.1",
                    "3.12",
                    offline=False,
                    recreate_if_python_mismatch=True,
                )

            self.assertEqual(result, ("3.12", "2.8.1"))
            create.assert_called_once_with(venv_path)
            self.assertFalse(python_path.exists())


if __name__ == "__main__":
    unittest.main()
