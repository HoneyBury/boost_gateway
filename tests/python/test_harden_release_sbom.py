from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from copy import deepcopy
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.tools.harden_release_sbom import (
    SbomSemanticError,
    collect_archive_files,
    collect_package_files,
    enrich_sbom_document,
    load_policy,
    load_runtime_dependencies,
    main,
    verify_attested_sbom_predicate,
    verify_sbom_document,
)


class HardenReleaseSbomTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.package_root = self.directory / "boost-gateway-v3.5.4-linux-x64"
        (self.package_root / "bin").mkdir(parents=True)
        (self.package_root / "share").mkdir()
        (self.package_root / "bin" / "gateway").write_bytes(b"gateway-binary\n")
        (self.package_root / "share" / "README.md").write_text(
            "release docs\n", encoding="utf-8"
        )
        self.policy_path = self.directory / "policy.json"
        self.policy_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "exclude_build_requires": True,
                    "excluded_conan_requires": ["gtest"],
                }
            ),
            encoding="utf-8",
        )
        self.lockfile = self.directory / "conan.lock"
        self.lockfile.write_text(
            json.dumps(
                {
                    "version": "0.5",
                    "requires": [
                        "fmt/11.2.0#579bb2cdf4a7607621beea4eb4651e0f%1",
                        "gtest/1.15.0#9eba70e54373fb7325151ad3375934a1%1",
                        "boost/1.86.0#aa6d9ddaf19c636a44f44be7ce4165f2%1",
                    ],
                    "build_requires": ["b2/5.4.2#ffd6084a119587e70f11cd45d1a386e2%1"],
                    "python_requires": [],
                    "config_requires": [],
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def base_sbom() -> dict[str, object]:
        return {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": "release",
            "packages": [
                {
                    "name": "release",
                    "SPDXID": "SPDXRef-DocumentRoot",
                    "filesAnalyzed": False,
                }
            ],
            "files": [
                {
                    "fileName": "bin/gateway",
                    "SPDXID": "SPDXRef-OldFile",
                    "checksums": [{"algorithm": "SHA1", "checksumValue": "0" * 40}],
                }
            ],
            "relationships": [
                {
                    "spdxElementId": "SPDXRef-DOCUMENT",
                    "relatedSpdxElement": "SPDXRef-DocumentRoot",
                    "relationshipType": "DESCRIBES",
                }
            ],
        }

    def inputs(
        self,
    ) -> tuple[
        dict[str, object], dict[str, str], list[dict[str, str]], dict[str, object]
    ]:
        policy = load_policy(self.policy_path)
        dependencies = load_runtime_dependencies(self.lockfile, policy)
        files = collect_package_files(self.package_root)
        return self.base_sbom(), files, dependencies, policy

    def test_enriches_all_files_and_runtime_dependencies_deterministically(
        self,
    ) -> None:
        document, files, dependencies, policy = self.inputs()
        enriched = enrich_sbom_document(document, files, dependencies)
        summary = verify_sbom_document(enriched, files, dependencies, policy)

        self.assertTrue(summary["overall_pass"])
        self.assertEqual(summary["sbom"]["sha256_covered_file_count"], 2)
        self.assertEqual(
            [
                dependency["name"]
                for dependency in summary["conan"]["runtime_dependencies"]
            ],
            ["boost", "fmt"],
        )
        package_names = {package.get("name") for package in enriched["packages"]}
        self.assertNotIn("gtest", package_names)
        self.assertNotIn("b2", package_names)
        self.assertTrue(
            all(
                file["checksums"][0]["algorithm"] == "SHA256"
                for file in enriched["files"]
            )
        )
        self.assertEqual(
            enrich_sbom_document(deepcopy(enriched), files, dependencies),
            enriched,
        )

    def test_rejects_zero_checksum_and_incomplete_file_coverage(self) -> None:
        document, files, dependencies, policy = self.inputs()
        summary = verify_sbom_document(document, files, dependencies, policy)

        self.assertFalse(summary["overall_pass"])
        self.assertTrue(
            any("lowercase SHA256" in failure for failure in summary["failures"])
        )
        self.assertTrue(
            any("share/README.md" in failure for failure in summary["failures"])
        )

    def test_rejects_file_digest_mismatch_and_missing_dependency(self) -> None:
        document, files, dependencies, policy = self.inputs()
        enriched = enrich_sbom_document(document, files, dependencies)
        enriched["files"][0]["checksums"][0]["checksumValue"] = "1" * 64
        enriched["packages"] = [
            package for package in enriched["packages"] if package.get("name") != "fmt"
        ]
        summary = verify_sbom_document(enriched, files, dependencies, policy)

        self.assertFalse(summary["overall_pass"])
        self.assertTrue(
            any("SHA256 mismatch" in failure for failure in summary["failures"])
        )
        self.assertIn("fmt", summary["conan"]["missing_dependencies"])

    def test_rejects_non_runtime_conan_package(self) -> None:
        document, files, dependencies, policy = self.inputs()
        enriched = enrich_sbom_document(document, files, dependencies)
        enriched["packages"].append(
            {
                "name": "gtest",
                "SPDXID": "SPDXRef-Package-Gtest",
                "externalRefs": [
                    {
                        "referenceType": "purl",
                        "referenceLocator": (
                            "pkg:conan/gtest@1.15.0?rrev=9eba70e54373fb7325151ad3375934a1"
                        ),
                    }
                ],
            }
        )
        summary = verify_sbom_document(enriched, files, dependencies, policy)

        self.assertFalse(summary["overall_pass"])
        self.assertIn("gtest", summary["conan"]["unexpected_dependencies"])

    def test_archive_verification_rejects_unsafe_member(self) -> None:
        archive = self.directory / "unsafe.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            payload = b"escape\n"
            info = tarfile.TarInfo("../escape")
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))

        with self.assertRaisesRegex(SbomSemanticError, "unsafe archive member"):
            collect_archive_files(archive, "boost-gateway-v3.5.4-linux-x64")

    def test_archive_verification_hashes_regular_files(self) -> None:
        archive = self.directory / "release.tar.gz"
        root = "boost-gateway-v3.5.4-linux-x64"
        with tarfile.open(archive, "w:gz") as bundle:
            for relative, payload in (
                ("bin/gateway", b"gateway\n"),
                ("README.md", b"docs\n"),
            ):
                info = tarfile.TarInfo(f"{root}/{relative}")
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))

        files = collect_archive_files(archive, root)
        self.assertEqual(set(files), {"bin/gateway", "README.md"})
        self.assertTrue(
            all(len(digest) == 64 and digest != "0" * 64 for digest in files.values())
        )

    def test_cli_enriches_directory_then_verifies_archive(self) -> None:
        sbom = self.directory / "release.spdx.json"
        sbom.write_text(json.dumps(self.base_sbom()), encoding="utf-8")
        enrich_summary = self.directory / "enrich-summary.json"
        with (
            patch(
                "sys.argv",
                [
                    "harden_release_sbom.py",
                    "enrich",
                    "--sbom",
                    str(sbom),
                    "--package-root",
                    str(self.package_root),
                    "--lockfile",
                    str(self.lockfile),
                    "--policy",
                    str(self.policy_path),
                    "--summary-path",
                    str(enrich_summary),
                ],
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(), 0)
        self.assertTrue(
            json.loads(enrich_summary.read_text(encoding="utf-8"))["overall_pass"]
        )

        archive = self.directory / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for path in sorted(self.package_root.rglob("*")):
                if path.is_file():
                    relative = path.relative_to(self.package_root)
                    bundle.add(path, arcname=f"{self.package_root.name}/{relative}")
        verify_summary = self.directory / "verify-summary.json"
        with (
            patch(
                "sys.argv",
                [
                    "harden_release_sbom.py",
                    "verify",
                    "--sbom",
                    str(sbom),
                    "--archive",
                    str(archive),
                    "--expected-root",
                    self.package_root.name,
                    "--lockfile",
                    str(self.lockfile),
                    "--policy",
                    str(self.policy_path),
                    "--summary-path",
                    str(verify_summary),
                ],
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(), 0)
        self.assertTrue(
            json.loads(verify_summary.read_text(encoding="utf-8"))["overall_pass"]
        )

    def test_rejects_unknown_lockfile_schema_and_malformed_reference(self) -> None:
        policy = load_policy(self.policy_path)
        self.lockfile.write_text(
            json.dumps({"version": "0.6", "requires": [], "build_requires": []}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            SbomSemanticError, "unsupported Conan lockfile version"
        ):
            load_runtime_dependencies(self.lockfile, policy)

        self.lockfile.write_text(
            json.dumps(
                {"version": "0.5", "requires": ["fmt/11.2.0"], "build_requires": []}
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            SbomSemanticError, "unsupported Conan lock reference"
        ):
            load_runtime_dependencies(self.lockfile, policy)

    @staticmethod
    def attestation_result(predicate: object) -> dict[str, object]:
        return {
            "verificationResult": {
                "statement": {
                    "predicateType": "https://spdx.dev/Document/v2.3",
                    "predicate": predicate,
                }
            }
        }

    def test_attested_predicate_matches_standalone_sbom_structurally(self) -> None:
        standalone = self.base_sbom()
        predicate = json.loads(json.dumps(standalone, sort_keys=True))
        summary = verify_attested_sbom_predicate(
            standalone, [self.attestation_result(predicate)]
        )

        self.assertTrue(summary["overall_pass"])
        self.assertTrue(summary["predicate_matches_published_sbom"])
        self.assertEqual(summary["matching_predicate_count"], 1)

    def test_attested_predicate_mismatch_fails_closed(self) -> None:
        standalone = self.base_sbom()
        mismatched = deepcopy(standalone)
        mismatched["name"] = "different-release"
        summary = verify_attested_sbom_predicate(
            standalone, [self.attestation_result(mismatched)]
        )

        self.assertFalse(summary["overall_pass"])
        self.assertFalse(summary["predicate_matches_published_sbom"])
        self.assertTrue(
            any("does not match" in failure for failure in summary["failures"])
        )

    def test_all_verified_spdx_predicates_must_match(self) -> None:
        standalone = self.base_sbom()
        mismatched = deepcopy(standalone)
        mismatched["files"] = []
        summary = verify_attested_sbom_predicate(
            standalone,
            [
                self.attestation_result(deepcopy(standalone)),
                self.attestation_result(mismatched),
            ],
        )

        self.assertFalse(summary["overall_pass"])
        self.assertEqual(summary["spdx_predicate_count"], 2)
        self.assertEqual(summary["matching_predicate_count"], 1)

    def test_attested_predicate_preserves_json_boolean_and_number_types(self) -> None:
        standalone = self.base_sbom()
        standalone["packages"][0]["filesAnalyzed"] = True
        mismatched = deepcopy(standalone)
        mismatched["packages"][0]["filesAnalyzed"] = 1

        summary = verify_attested_sbom_predicate(
            standalone, [self.attestation_result(mismatched)]
        )

        self.assertFalse(summary["overall_pass"])
        self.assertFalse(summary["predicate_matches_published_sbom"])

    def test_attestation_cli_records_match_and_returns_nonzero_on_mismatch(
        self,
    ) -> None:
        sbom = self.directory / "published.spdx.json"
        sbom.write_text(json.dumps(self.base_sbom()), encoding="utf-8")
        verification = self.directory / "attestation.json"
        verification.write_text(
            json.dumps([self.attestation_result({"spdxVersion": "SPDX-2.3"})]),
            encoding="utf-8",
        )
        summary_path = self.directory / "attestation-summary.json"
        with (
            patch(
                "sys.argv",
                [
                    "harden_release_sbom.py",
                    "verify-attestation",
                    "--sbom",
                    str(sbom),
                    "--attestation-verification",
                    str(verification),
                    "--summary-path",
                    str(summary_path),
                ],
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(), 1)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertFalse(summary["overall_pass"])
        self.assertFalse(summary["predicate_matches_published_sbom"])


if __name__ == "__main__":
    unittest.main()
