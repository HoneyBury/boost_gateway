from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.gates.governance import check_repository_governance as governance


class RepositoryGovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write_valid_fixture()

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_valid_fixture(self) -> None:
        codeowners = "\n".join(
            f"{pattern} {governance.PRIMARY_OWNER}"
            for pattern in governance.REQUIRED_CODEOWNER_PATTERNS
        )
        self.write("CODEOWNERS", codeowners + "\n")
        for relative, sections in governance.REQUIRED_SECTIONS.items():
            text = "\n\n".join(sections) + "\n"
            if relative == "GOVERNANCE.md":
                text += (
                    "\nNo silent administrator bypass is permitted.\n"
                    "Repository files do not prove that these settings are active.\n"
                )
            if relative == "SECURITY.md":
                text += (
                    "\nPrivate vulnerability reporting is not yet an active repository control.\n"
                    "GitHub private vulnerability reporting is external repository state "
                    "and must be verified before use.\n"
                    "zoujiahe389+boost-gateway-security@gmail.com\n"
                )
            self.write(relative, text)
        for relative, links in governance.REQUIRED_DOCUMENT_LINKS.items():
            path = self.root / relative
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            self.write(relative, existing + "\n" + "\n".join(links) + "\n")

    def failed_names(self) -> set[str]:
        return {
            str(check["name"])
            for check in governance.evaluate_repository(self.root)
            if not check["passed"]
        }

    def test_complete_governance_contract_passes(self) -> None:
        self.assertEqual(set(), self.failed_names())

    def test_missing_sensitive_codeowner_fails(self) -> None:
        codeowners = self.root / "CODEOWNERS"
        codeowners.write_text(
            codeowners.read_text(encoding="utf-8").replace(
                "/.github/ @HoneyBury\n", ""
            ),
            encoding="utf-8",
        )

        self.assertIn("codeowners:/.github/", self.failed_names())

    def test_silent_bypass_or_missing_external_boundary_fails(self) -> None:
        governance_path = self.root / "GOVERNANCE.md"
        governance_path.write_text(
            governance_path.read_text(encoding="utf-8")
            .replace("No silent administrator bypass is permitted.", "")
            .replace("Repository files do not prove that these settings are active.", ""),
            encoding="utf-8",
        )

        failures = self.failed_names()
        self.assertIn("governance:no-silent-bypass", failures)
        self.assertIn("governance:external-state-boundary", failures)

    def test_security_policy_requires_external_state_boundary(self) -> None:
        security_path = self.root / "SECURITY.md"
        security_path.write_text(
            security_path.read_text(encoding="utf-8").replace(
                "GitHub private vulnerability reporting is external repository state "
                "and must be verified before use.",
                "",
            ),
            encoding="utf-8",
        )

        self.assertIn(
            "security:external-private-reporting-boundary", self.failed_names()
        )


if __name__ == "__main__":
    unittest.main()
