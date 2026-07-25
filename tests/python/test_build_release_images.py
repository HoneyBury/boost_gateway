"""Unit tests for verified release image provenance."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.tools import build_release_images as module

SHA = "a" * 64
COMMIT = "b" * 40


def staging_tree(root: Path) -> dict[str, object]:
    (root / "bin").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config/gateway.json").write_text("{}", encoding="utf-8")
    entries = []
    for _variable, (_service, binary, _dockerfile) in module.SERVICE_IMAGES.items():
        path = root / "bin" / binary
        path.write_bytes(binary.encode())
        entries.append({"name": binary, "sha256": module.sha256_file(path)})
    manifest = {
        "schema_version": 1,
        "tag": "v3.6.2",
        "commit": COMMIT,
        "platform": "linux-x64",
        "source_build_performed": False,
        "assets": {"boost-gateway-v3.6.2-linux-x64.tar.gz": SHA},
        "configuration": {"sha256": module.sha256_tree(root / "config")},
        "binaries": entries,
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class BuildReleaseImagesTest(unittest.TestCase):
    def test_verified_staging_accepts_exact_binary_and_config_digests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = staging_tree(root)
            self.assertEqual(module.verify_staging(root, manifest)["asset"], SHA)

    def test_verified_staging_rejects_changed_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = staging_tree(root)
            (root / "bin/v2_gateway_demo").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "binary digest"):
                module.verify_staging(root, manifest)

    def test_image_inspection_requires_platform_and_provenance_labels(self) -> None:
        expected = {"tag": "v3.6.2", "commit": COMMIT, "asset": SHA, "config": "c" * 64}
        labels = {label: expected[field] for field, label in module.LABELS.items()}
        document = [{"Id": "sha256:" + "d" * 64, "Os": "linux", "Architecture": "amd64", "Config": {"Labels": labels}}]
        completed = type("Result", (), {"stdout": json.dumps(document)})()
        with patch.object(module, "run", return_value=completed):
            item = module.inspect_image("boost-gateway/gateway:test", expected)
        self.assertEqual(item["architecture"], "amd64")
        document[0]["Architecture"] = "arm64"
        completed.stdout = json.dumps(document)
        with patch.object(module, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "linux/amd64"):
                module.inspect_image("boost-gateway/gateway:test", expected)

    def test_compose_environment_contains_only_immutable_project_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path = root / "compose-images.env"
            summary = root / "summary.json"
            environment = {
                key: "sha256:" + format(index, "064x")
                for index, key in enumerate(module.SERVICE_IMAGES, 1)
            }
            module.write_outputs(environment, [], env_path, summary)
            text = env_path.read_text(encoding="utf-8")
            self.assertNotIn("PASSWORD", text)
            self.assertEqual(len(text.splitlines()), 6)
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o640)


if __name__ == "__main__":
    unittest.main()
