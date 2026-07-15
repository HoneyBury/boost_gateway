from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/tools/prepare_docker_runtime_context.py"
SPEC = importlib.util.spec_from_file_location("prepare_docker_runtime_context", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_resolve_binary_supports_single_config_layout(tmp_path: Path) -> None:
    relative = Path("examples/service/service")
    binary = tmp_path / relative
    binary.parent.mkdir(parents=True)
    binary.touch()
    assert MODULE.resolve_binary(tmp_path, relative, None) == binary


def test_validate_runtime_dependencies_accepts_base_ubuntu_libraries() -> None:
    output = """
        linux-vdso.so.1 (0x00007fff)
        libstdc++.so.6 => /lib/x86_64-linux-gnu/libstdc++.so.6 (0x1)
        libm.so.6 => /lib/x86_64-linux-gnu/libm.so.6 (0x2)
        libgcc_s.so.1 => /lib/x86_64-linux-gnu/libgcc_s.so.1 (0x3)
        libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x4)
        /lib64/ld-linux-x86-64.so.2 (0x5)
    """
    assert MODULE.validate_runtime_dependencies(Path("service"), output) == [
        "libc.so.6",
        "libgcc_s.so.1",
        "libm.so.6",
        "libstdc++.so.6",
    ]


def test_validate_runtime_dependencies_rejects_third_party_shared_library() -> None:
    output = "libhiredis.so.1.1.0 => /usr/local/lib/libhiredis.so.1.1.0 (0x1)"
    with pytest.raises(RuntimeError, match="libhiredis"):
        MODULE.validate_runtime_dependencies(Path("service"), output)


def test_validate_runtime_dependencies_rejects_missing_library() -> None:
    with pytest.raises(RuntimeError, match="unresolved"):
        MODULE.validate_runtime_dependencies(Path("service"), "libfoo.so => not found")
