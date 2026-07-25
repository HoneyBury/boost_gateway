"""Static safety contract for the one-command Ubuntu release deployment."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/operations/deploy_verified_release.sh"


class DeployVerifiedReleaseScriptTest(unittest.TestCase):
    def test_host_guard_precedes_every_mutating_stage(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        main = text[text.index("main() {") :]
        guard = main.index("guard_target_host")
        validate = main.index("validate_inputs")
        for stage in (
            "install_gh",
            "install_release",
            "configure_docker_proxy",
            "pull_runtime_images",
            "build_images",
            "prepare_secrets",
            "start_and_verify",
        ):
            self.assertLess(guard, main.index(stage))
            self.assertLess(validate, main.index(stage))

    def test_script_is_initial_deploy_only_and_never_reboots(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('RELEASE_DIR="/opt/boost-gateway/releases/${TAG}-deploy-r3"', text)
        self.assertIn("FAILED_INITIAL_MANIFEST_SHA256", text)
        self.assertIn("TODO-0010 upgrade is required", text)
        self.assertIn("this script does not reboot automatically", text)
        self.assertNotIn("systemctl reboot", text)
        self.assertNotIn("shutdown -r", text)

    def test_docker_daemon_uses_the_local_mihomo_proxy(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("/etc/systemd/system/docker.service.d/boost-gateway-proxy.conf", text)
        self.assertIn('HTTPS_PROXY=${DOCKER_PROXY_URL}', text)
        self.assertIn("systemctl restart docker.service", text)
        main = text[text.index("main() {") :]
        self.assertLess(main.index("configure_docker_proxy"), main.index("pull_runtime_images"))

    def test_script_preserves_existing_secret(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("if [[ ! -e /etc/boost-gateway/compose.env ]]", text)
        self.assertNotIn("set -x", text)


if __name__ == "__main__":
    unittest.main()
