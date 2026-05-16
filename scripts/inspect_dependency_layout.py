#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def print_status(category: str, path: Path) -> None:
    if path.exists():
        print(f"{category}: {path}")
    else:
        print(f"{category}: missing ({path})")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    print("Dependency layout report")
    print(f"root: {root}")
    print()

    for category, rel_path in [
        ("vendored-source", "third_party/hiredis-src"),
        ("vendored-source", "third_party/openssl"),
        ("cached-archive", "third_party/fmt-11.2.0.tar.gz"),
        ("cached-archive", "third_party/googletest-1.17.0.tar.gz"),
        ("cached-archive", "third_party/spdlog-1.15.3.tar.gz"),
        ("cached-archive", "third_party/nlohmann_json-3.12.0.tar.gz"),
        ("cached-archive", "third_party/boost_1_90_0.zip"),
        ("toolchain-cache", "build/go-modcache"),
        ("toolchain-cache", "build/_deps"),
        ("toolchain-cache", "build/windows-msvc-debug/_deps"),
    ]:
        print_status(category, root / rel_path)

    print("\nSuggested restore commands")
    print("  bash third_party/download_deps.sh")
    print("  bash third_party/bootstrap_from_build_cache.sh")
    print("  cmake --preset windows-msvc-debug")
    return 0


if __name__ == "__main__":
    sys.exit(main())
