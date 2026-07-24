"""Unit tests for immutable release runtime verification."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/tools/prepare_release_runtime.py"
SPEC = importlib.util.spec_from_file_location("prepare_release_runtime", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

SHA = "a" * 64
COMMIT = "b" * 40


class PrepareReleaseRuntimeTest(unittest.TestCase):
    def test_validate_inputs_requires_exact_forms(self) -> None:
        MODULE.validate_inputs("HoneyBury/boost_gateway", "v3.6.2", COMMIT)
        for repository, tag, commit in (
            ("missing-owner", "v3.6.2", COMMIT),
            ("HoneyBury/boost_gateway", "3.6.2", COMMIT),
            ("HoneyBury/boost_gateway", "v3.6.2", "abc"),
        ):
            with self.assertRaises(RuntimeError):
                MODULE.validate_inputs(repository, tag, commit)

    def test_checksum_manifest_accepts_strict_unique_basenames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SHA256SUMS.txt"
            path.write_text(f"{SHA}  asset.tar.gz\n", encoding="utf-8")
            self.assertEqual(MODULE.parse_checksum_manifest(path), {"asset.tar.gz": SHA})

    def test_checksum_manifest_rejects_malformed_and_duplicate_entries(self) -> None:
        cases = (
            f"{SHA} asset.tar.gz\n",
            f"{SHA}  ../asset.tar.gz\n",
            f"{SHA}  asset.tar.gz\n{SHA}  asset.tar.gz\n",
            "",
        )
        for content in cases:
            with self.subTest(content=content):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "SHA256SUMS.txt"
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(RuntimeError):
                        MODULE.parse_checksum_manifest(path)

    def test_release_asset_digests_requires_every_exact_asset(self) -> None:
        metadata = {
            "assets": [
                {"name": "runtime.tar.gz", "digest": "sha256:" + SHA},
                {"name": "runtime.spdx.json", "digest": "sha256:" + "c" * 64},
            ]
        }
        self.assertEqual(
            MODULE.release_asset_digests(
                metadata, {"runtime.tar.gz", "runtime.spdx.json"}
            )["runtime.tar.gz"],
            SHA,
        )
        with self.assertRaisesRegex(RuntimeError, "missing required"):
            MODULE.release_asset_digests(metadata, {"SHA256SUMS.txt"})

    def test_release_asset_urls_require_exact_https_downloads(self) -> None:
        metadata = {
            "assets": [
                {
                    "name": "runtime.tar.gz",
                    "browser_download_url": "https://github.com/o/r/releases/download/v1/runtime.tar.gz",
                }
            ]
        }
        self.assertEqual(
            MODULE.release_asset_urls(metadata, {"runtime.tar.gz"})["runtime.tar.gz"],
            metadata["assets"][0]["browser_download_url"],
        )
        metadata["assets"][0]["browser_download_url"] = "http://example.test/runtime"
        with self.assertRaisesRegex(RuntimeError, "invalid download URL"):
            MODULE.release_asset_urls(metadata, {"runtime.tar.gz"})

    def test_attestation_subject_requires_expected_digest_and_predicate(self) -> None:
        results = [
            {
                "verificationResult": {
                    "statement": {
                        "predicateType": MODULE.SPDX_PREDICATE_TYPE,
                        "subject": [{"digest": {"sha256": SHA}}],
                    }
                }
            }
        ]
        MODULE.verify_attestation_subject(results, SHA, MODULE.SPDX_PREDICATE_TYPE)
        with self.assertRaisesRegex(RuntimeError, "does not bind"):
            MODULE.verify_attestation_subject(results, "d" * 64, MODULE.SPDX_PREDICATE_TYPE)
        with self.assertRaisesRegex(RuntimeError, "predicate"):
            MODULE.verify_attestation_subject(results, SHA, "wrong")

    def test_sbom_attestation_binding_rejects_changed_document(self) -> None:
        standalone = {"spdxVersion": "SPDX-2.3", "name": "release"}
        results = [
            {
                "verificationResult": {
                    "statement": {
                        "predicateType": MODULE.SPDX_PREDICATE_TYPE,
                        "predicate": {"spdxVersion": "SPDX-2.3", "name": "other"},
                    }
                }
            }
        ]
        summary = MODULE.verify_attested_sbom_predicate(standalone, results)
        self.assertFalse(summary["passed"])

    def test_tree_digest_is_deterministic_and_path_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").mkdir()
            (root / "a/value.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
            first = MODULE.sha256_tree(root)
            self.assertEqual(first, MODULE.sha256_tree(root))
            (root / "a/value.json").rename(root / "value.json")
            self.assertNotEqual(first, MODULE.sha256_tree(root))


if __name__ == "__main__":
    unittest.main()
