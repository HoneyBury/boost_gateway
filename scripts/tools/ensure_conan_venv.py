#!/usr/bin/env python3
"""Create or verify an isolated, pinned Conan virtual environment on Linux."""

from __future__ import annotations

import argparse
import re
import subprocess
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONAN_VERSION = "2.8.1"
DEFAULT_PYTHON_VERSION = "3.12"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def venv_python(venv_path: Path) -> Path:
    return venv_path / "bin" / "python"


def venv_conan(venv_path: Path) -> Path:
    return venv_path / "bin" / "conan"


def installed_python_version(venv_path: Path) -> str:
    completed = run([str(venv_python(venv_path)), "--version"])
    match = re.search(r"Python\s+([0-9]+\.[0-9]+)", f"{completed.stdout}\n{completed.stderr}")
    if not match:
        raise RuntimeError(f"unable to parse Python version from: {completed.stdout!r} {completed.stderr!r}")
    return match.group(1)


def installed_conan_version(venv_path: Path) -> str:
    completed = run([str(venv_conan(venv_path)), "--version"])
    match = re.search(r"([0-9]+(?:\.[0-9]+)+)", completed.stdout)
    if not match:
        raise RuntimeError(f"unable to parse Conan version from: {completed.stdout!r}")
    return match.group(1)


def ensure_conan_venv(venv_path: Path, conan_version: str, python_version: str, offline: bool) -> tuple[str, str]:
    python_path = venv_python(venv_path)
    if not python_path.is_file():
        if offline:
            raise RuntimeError(f"offline mode requires a pre-created Conan virtual environment: {venv_path}")
        venv.EnvBuilder(with_pip=True).create(venv_path)

    installed_python = installed_python_version(venv_path)
    if installed_python != python_version:
        raise RuntimeError(
            f"expected Python {python_version}, found {installed_python} in {venv_path}; recreate the virtual environment with Python {python_version}"
        )

    try:
        installed_version = installed_conan_version(venv_path)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        if offline:
            raise RuntimeError(f"offline mode requires Conan {conan_version} in {venv_path}") from error
        run([str(python_path), "-m", "pip", "install", "--upgrade", "pip"])
        run([str(python_path), "-m", "pip", "install", f"conan=={conan_version}"])
        installed_version = installed_conan_version(venv_path)

    if installed_version != conan_version:
        if offline:
            raise RuntimeError(
                f"offline mode requires Conan {conan_version}, found {installed_version} in {venv_path}"
            )
        run([str(python_path), "-m", "pip", "install", "--upgrade", f"conan=={conan_version}"])
        installed_version = installed_conan_version(venv_path)

    if installed_version != conan_version:
        raise RuntimeError(f"expected Conan {conan_version}, found {installed_version} in {venv_path}")
    return installed_python, installed_version


def append_line(path: Path | None, line: str) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conan-version", default=DEFAULT_CONAN_VERSION)
    parser.add_argument("--python-version", default=DEFAULT_PYTHON_VERSION, help="Required Python major.minor version inside the virtual environment.")
    parser.add_argument(
        "--venv",
        type=Path,
        default=ROOT / ".venv" / f"conan-{DEFAULT_CONAN_VERSION}",
        help="Virtual environment path; default: .venv/conan-2.8.1 in this checkout.",
    )
    parser.add_argument("--offline", action="store_true", help="Fail unless the requested virtual environment already exists and matches.")
    parser.add_argument("--github-env", type=Path, help="Append BOOST_GATEWAY_CONAN_VENV to this GitHub Actions environment file.")
    parser.add_argument("--github-path", type=Path, help="Append the virtual environment bin directory to this GitHub Actions path file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    venv_path = args.venv if args.venv.is_absolute() else ROOT / args.venv
    python_version, conan_version = ensure_conan_venv(venv_path, args.conan_version, args.python_version, args.offline)
    bin_path = venv_path / "bin"
    append_line(args.github_env, f"BOOST_GATEWAY_CONAN_VENV={venv_path}")
    append_line(args.github_path, str(bin_path))
    print(f"Conan virtual environment: {venv_path}")
    print(f"Python version: {python_version}")
    print(f"Conan version: {conan_version}")
    print(f"Conan executable: {bin_path / 'conan'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
