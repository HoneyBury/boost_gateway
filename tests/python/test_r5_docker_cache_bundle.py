"""Unit coverage for R5 offline Docker cache bundle provenance checks."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts/tools/r5_docker_cache_bundle.py"
SPEC = importlib.util.spec_from_file_location("r5_docker_cache_bundle", SCRIPT_PATH)
assert SPEC and SPEC.loader
BUNDLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUNDLE)

REVISION = "a" * 40
SHA256 = "b" * 64


def manifest(
    *,
    candidate_revision: str = REVISION,
    registry_digests: object = None,
    target_platform: str = "linux/amd64",
) -> dict[str, object]:
    if registry_digests is None:
        registry_digests = ["docker.io/library/redis@sha256:" + "c" * 64]
    return {
        "schema_version": 2,
        "candidate_revision": candidate_revision,
        "target_platform": target_platform,
        "compose_sha256": SHA256,
        "bundle": {"sha256": SHA256},
        "requirements": [
            {"service": "gateway", "image": "boost-gateway", "source": "build"},
            {"service": "redis", "image": "redis:7", "source": "registry"},
        ],
        "image_inventory": [
            {
                "image": "boost-gateway",
                "image_id": "sha256:" + "d" * 64,
                "repo_digests": [],
                "os": "linux",
                "architecture": target_platform.split("/", 1)[1],
            },
            {
                "image": "redis:7",
                "image_id": "sha256:" + "e" * 64,
                "repo_digests": registry_digests,
                "os": "linux",
                "architecture": target_platform.split("/", 1)[1],
            },
        ],
    }


class R5DockerCacheBundleTest(unittest.TestCase):
    def test_manifest_accepts_complete_linux_amd64_inventory(self) -> None:
        requirements, inventory = BUNDLE.validate_manifest(manifest())

        self.assertEqual(
            {item["image"] for item in requirements}, {"boost-gateway", "redis:7"}
        )
        self.assertEqual(set(inventory), {"boost-gateway", "redis:7"})

    def test_manifest_accepts_complete_linux_arm64_inventory(self) -> None:
        requirements, inventory = BUNDLE.validate_manifest(
            manifest(target_platform="linux/arm64"), "linux/arm64"
        )

        self.assertEqual(
            {item["image"] for item in requirements}, {"boost-gateway", "redis:7"}
        )
        self.assertEqual(
            {item["architecture"] for item in inventory.values()}, {"arm64"}
        )

    def test_manifest_rejects_requested_platform_mismatch(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "differs from requested"):
            BUNDLE.validate_manifest(manifest(), "linux/arm64")

    def test_manifest_rejects_registry_image_without_repo_digest(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no registry digest"):
            BUNDLE.validate_manifest(manifest(registry_digests=[]))

    def test_manifest_rejects_missing_candidate_revision(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "candidate_revision"):
            BUNDLE.validate_manifest(manifest(candidate_revision=""))

    def test_checkout_revision_rejects_candidate_environment_mismatch(self) -> None:
        completed = subprocess.CompletedProcess(
            ["git"], 0, stdout=REVISION + "\n", stderr=""
        )
        with patch.dict(os.environ, {"BOOST_GATEWAY_CANDIDATE_REVISION": "f" * 40}):
            with patch.object(BUNDLE, "run", return_value=completed):
                with self.assertRaisesRegex(RuntimeError, "differs"):
                    BUNDLE.checkout_revision()

    def test_clean_checkout_rejects_uncommitted_source(self) -> None:
        completed = subprocess.CompletedProcess(
            ["git"], 0, stdout=" M conanfile.py\n", stderr=""
        )
        with patch.object(BUNDLE, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "uncommitted changes"):
                BUNDLE.require_clean_checkout()


if __name__ == "__main__":
    unittest.main()
