from __future__ import annotations

import unittest

from scripts.tools import verify_sdk_distribution_packages


class VerifySdkDistributionPackagesTest(unittest.TestCase):
    def test_offline_consumer_keeps_rid_without_requiring_apphost_pack(self) -> None:
        project = verify_sdk_distribution_packages.nuget_consumer_project("linux-arm64")

        self.assertIn("<RuntimeIdentifier>linux-arm64</RuntimeIdentifier>", project)
        self.assertIn("<UseAppHost>false</UseAppHost>", project)


if __name__ == "__main__":
    unittest.main()
