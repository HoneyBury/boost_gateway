from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/tools/prepare_docker_runtime_context.py"
SPEC = importlib.util.spec_from_file_location("prepare_docker_runtime_context", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PrepareDockerRuntimeContextTest(unittest.TestCase):
    def test_resolve_binary_supports_single_config_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = Path("examples/service/service")
            binary = root / relative
            binary.parent.mkdir(parents=True)
            binary.touch()
            self.assertEqual(binary, MODULE.resolve_binary(root, relative, None))

    def test_validate_runtime_dependencies_accepts_base_ubuntu_libraries(self) -> None:
        output = """
            linux-vdso.so.1 (0x00007fff)
            libstdc++.so.6 => /lib/x86_64-linux-gnu/libstdc++.so.6 (0x1)
            libm.so.6 => /lib/x86_64-linux-gnu/libm.so.6 (0x2)
            libgcc_s.so.1 => /lib/x86_64-linux-gnu/libgcc_s.so.1 (0x3)
            libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x4)
            /lib64/ld-linux-x86-64.so.2 (0x5)
        """
        self.assertEqual(
            ["libc.so.6", "libgcc_s.so.1", "libm.so.6", "libstdc++.so.6"],
            MODULE.validate_runtime_dependencies(Path("service"), output),
        )

    def test_validate_runtime_dependencies_accepts_aarch64_loader(self) -> None:
        output = """
            linux-vdso.so.1 (0x0000ffffbca80000)
            libstdc++.so.6 => /lib/aarch64-linux-gnu/libstdc++.so.6 (0x1)
            libm.so.6 => /lib/aarch64-linux-gnu/libm.so.6 (0x2)
            libgcc_s.so.1 => /lib/aarch64-linux-gnu/libgcc_s.so.1 (0x3)
            libc.so.6 => /lib/aarch64-linux-gnu/libc.so.6 (0x4)
            /lib/ld-linux-aarch64.so.1 (0x5)
        """
        self.assertEqual(
            ["libc.so.6", "libgcc_s.so.1", "libm.so.6", "libstdc++.so.6"],
            MODULE.validate_runtime_dependencies(Path("service"), output),
        )

    def test_validate_runtime_dependencies_rejects_third_party_shared_library(self) -> None:
        output = "libhiredis.so.1.1.0 => /usr/local/lib/libhiredis.so.1.1.0 (0x1)"
        with self.assertRaisesRegex(RuntimeError, "libhiredis"):
            MODULE.validate_runtime_dependencies(Path("service"), output)

    def test_validate_runtime_dependencies_rejects_missing_library(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unresolved"):
            MODULE.validate_runtime_dependencies(
                Path("service"), "libfoo.so => not found"
            )

    def test_worktree_cleanliness_is_part_of_docker_evidence_contract(self) -> None:
        with patch.object(
            MODULE.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            ),
        ):
            self.assertTrue(MODULE.worktree_is_clean())
        with patch.object(
            MODULE.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=" M tracked-file\n", stderr=""
            ),
        ):
            self.assertFalse(MODULE.worktree_is_clean())


if __name__ == "__main__":
    unittest.main()
