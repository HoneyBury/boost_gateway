from __future__ import annotations

import unittest

from scripts.tools.resolve_release_version import resolve_project_version, validate_tag


class ResolveReleaseVersionTest(unittest.TestCase):
    def test_resolves_multiline_project_version(self) -> None:
        text = "project(boost_gateway\n    VERSION 3.5.1\n    LANGUAGES CXX\n)"
        self.assertEqual(resolve_project_version(text), "3.5.1")

    def test_allows_branch_and_matching_tag(self) -> None:
        validate_tag("3.5.1", "refs/heads/release/3.5", "release/3.5")
        validate_tag("3.5.1", "refs/tags/v3.5.1", "v3.5.1")

    def test_rejects_mismatched_tag(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_tag("3.5.1", "refs/tags/v3.5.0", "v3.5.0")


if __name__ == "__main__":
    unittest.main()
