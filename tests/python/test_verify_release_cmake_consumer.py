from __future__ import annotations

from pathlib import Path

from scripts.tools.verify_release_cmake_consumer import (
    docker_command,
    write_consumer_project,
)


def test_docker_command_enforces_offline_read_only_inputs(tmp_path: Path) -> None:
    package = tmp_path / "package"
    source = tmp_path / "source"
    build = tmp_path / "build"
    for path in (package, source, build):
        path.mkdir()
    command = docker_command(
        "consumer:test", package, source, build, ["cmake", "--version"]
    )
    assert "--pull=never" in command
    assert "--network=none" in command
    assert command[command.index("--platform") + 1] == "linux/amd64"
    assert f"{package.resolve()}:/opt/boost-gateway:ro" in command
    assert f"{source.resolve()}:/work/src:ro" in command


def test_docker_command_supports_linux_arm64(tmp_path: Path) -> None:
    paths = [tmp_path / name for name in ("package", "source", "build")]
    for path in paths:
        path.mkdir()
    command = docker_command(
        "consumer:test", *paths, ["cmake", "--version"], "linux/arm64"
    )
    assert command[command.index("--platform") + 1] == "linux/arm64"


def test_consumer_project_uses_installed_cmake_package(tmp_path: Path) -> None:
    write_consumer_project(tmp_path)
    cmake = (tmp_path / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "find_package(boost_gateway_sdk 4.2.0 CONFIG REQUIRED)" in cmake
    assert "boost_gateway::sdk" in cmake


def test_toolchain_probe_is_strict_and_uses_versioned_compiler() -> None:
    script = Path("scripts/tools/verify_release_cmake_consumer.py").read_text(
        encoding="utf-8"
    )
    assert "set -euo pipefail; gcc-13 --version" in script
