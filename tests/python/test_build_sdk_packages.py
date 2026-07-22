from __future__ import annotations

import unittest

from scripts.tools import build_sdk_packages


class BuildSdkPackagesTest(unittest.TestCase):
    def test_native_platform_accepts_all_production_rids(self) -> None:
        build_sdk_packages.validate_native_platform("linux-x64", "Linux", "x86_64")
        build_sdk_packages.validate_native_platform("linux-arm64", "Linux", "aarch64")
        build_sdk_packages.validate_native_platform("osx-arm64", "Darwin", "arm64")

    def test_native_platform_rejects_cross_platform_packaging(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "RID linux-arm64"):
            build_sdk_packages.validate_native_platform("linux-arm64", "Darwin", "arm64")


if __name__ == "__main__":
    unittest.main()
