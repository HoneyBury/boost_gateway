from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.gates.governance import check_script_inventory as inventory_gate


class ScriptInventoryGovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts/tools").mkdir(parents=True)
        (self.root / "docs").mkdir()
        public_script = Path("scripts") / "run_tests.py"
        internal_script = Path("scripts") / "tools/impl.py"
        (self.root / public_script).write_text("# entrypoint\n", encoding="utf-8")
        (self.root / internal_script).write_text("# implementation\n", encoding="utf-8")
        self.inventory = self.root / "docs/script-inventory.json"
        self.summary = self.root / "summary.json"
        self.inventory.write_text(
            json.dumps(
                {
                    "schema_version": 5,
                    "script_growth_exceptions": {},
                    "public_entrypoints": [public_script.as_posix()],
                    "public_entrypoint_lifecycle": {
                        public_script.as_posix(): {
                            "owner": "@maintainer",
                            "support_level": "stable",
                            "execution_environment": "developer-or-ci",
                            "typical_duration": "minutes",
                            "side_effects": ["runtime-artifacts"],
                            "retirement_condition": "A replacement remains stable for one release cycle.",
                        }
                    },
                    "internal_scripts": {internal_script.as_posix(): {"category": "tool"}},
                    "scripts": {
                        public_script.as_posix(): {"category": "public_entrypoint"}
                    },
                }
            ),
            encoding="utf-8",
        )

    def run_gate(self) -> int:
        arguments = [
            "check_script_inventory.py",
            "--inventory",
            str(self.inventory),
            "--summary-path",
            str(self.summary),
        ]
        with mock.patch.object(inventory_gate, "ROOT", self.root), mock.patch(
            "sys.argv", arguments
        ):
            return inventory_gate.main()

    def test_complete_inventory_passes(self) -> None:
        self.assertEqual(0, self.run_gate())

    def test_unrepresented_script_fails(self) -> None:
        orphan = Path("scripts") / "tools/orphan.py"
        (self.root / orphan).write_text("# orphan\n", encoding="utf-8")

        self.assertEqual(1, self.run_gate())
        summary = json.loads(self.summary.read_text(encoding="utf-8"))
        failed = {item["name"] for item in summary["checks"] if not item["passed"]}
        self.assertIn("all-recursive-scripts-represented", failed)

    def test_growth_exception_registry_must_be_an_object(self) -> None:
        document = json.loads(self.inventory.read_text(encoding="utf-8"))
        document["script_growth_exceptions"] = []
        self.inventory.write_text(json.dumps(document), encoding="utf-8")

        self.assertEqual(1, self.run_gate())
        summary = json.loads(self.summary.read_text(encoding="utf-8"))
        failed = {item["name"] for item in summary["checks"] if not item["passed"]}
        self.assertIn("script-growth-exceptions-object", failed)

    def test_public_entrypoint_without_lifecycle_metadata_fails(self) -> None:
        document = json.loads(self.inventory.read_text(encoding="utf-8"))
        document["public_entrypoint_lifecycle"] = {}
        self.inventory.write_text(json.dumps(document), encoding="utf-8")

        self.assertEqual(1, self.run_gate())
        summary = json.loads(self.summary.read_text(encoding="utf-8"))
        failed = {item["name"] for item in summary["checks"] if not item["passed"]}
        self.assertIn("public-lifecycle-exact-set", failed)

    def test_unknown_lifecycle_side_effect_fails(self) -> None:
        document = json.loads(self.inventory.read_text(encoding="utf-8"))
        metadata = next(iter(document["public_entrypoint_lifecycle"].values()))
        metadata["side_effects"] = ["surprise-production-mutation"]
        self.inventory.write_text(json.dumps(document), encoding="utf-8")

        self.assertEqual(1, self.run_gate())
        summary = json.loads(self.summary.read_text(encoding="utf-8"))
        failed = {item["name"] for item in summary["checks"] if not item["passed"]}
        self.assertTrue(any(name.startswith("lifecycle-side-effects:") for name in failed))


if __name__ == "__main__":
    unittest.main()
