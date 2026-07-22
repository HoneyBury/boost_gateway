from __future__ import annotations

import unittest

from scripts.gates.sdk import verify_sdk_full_flow_client


class SdkFullFlowIsolationTest(unittest.TestCase):
    def test_leaderboard_disables_ambient_redis(self) -> None:
        environment = verify_sdk_full_flow_client.isolated_leaderboard_environment(9305)

        self.assertEqual("9305", environment["SERVICE_PORT"])
        self.assertEqual("9305", environment["LEADERBOARD_PORT"])
        self.assertEqual("1", environment["BOOST_DISABLE_REDIS_AUTO_CONNECT"])


if __name__ == "__main__":
    unittest.main()
