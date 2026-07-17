from __future__ import annotations

import unittest

from scripts.tools.render_release_notes import extract_release_notes


class RenderReleaseNotesTest(unittest.TestCase):
    def test_extracts_only_requested_version(self) -> None:
        changelog = """# Log

## v3.5.1 - Patch

Scope.

- Fixed release metadata.

## v3.5.0 - Base

- Base release.
"""
        notes = extract_release_notes(changelog, "3.5.1")
        self.assertIn("## v3.5.1", notes)
        self.assertIn("Fixed release metadata", notes)
        self.assertNotIn("## v3.5.0", notes)

    def test_rejects_missing_or_unsafe_version(self) -> None:
        with self.assertRaises(ValueError):
            extract_release_notes("# Log\n", "3.5.1")
        with self.assertRaises(ValueError):
            extract_release_notes("# Log\n", "3.5.1/../../main")


if __name__ == "__main__":
    unittest.main()
