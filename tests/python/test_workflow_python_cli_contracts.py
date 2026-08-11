from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.gates.governance import check_workflow_python_cli_contracts as contracts


class WorkflowPythonCliContractsTest(unittest.TestCase):
    def test_extracts_multiline_python_invocation_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workflow = Path(temporary) / "ci.yml"
            script_path = "scripts/" + "tool.py"
            workflow.write_text(
                "jobs:\n"
                "  test:\n"
                "    steps:\n"
                "      - name: contract\n"
                "        run: |\n"
                f"          python3 {script_path} \\\n"
                "            --build-dir build/release --skip-build\n",
                encoding="utf-8",
            )

            with mock.patch.object(contracts, "ROOT", Path(temporary)):
                invocations = contracts.extract_invocations(workflow)

        self.assertEqual(1, len(invocations))
        self.assertEqual(script_path, invocations[0]["script"])
        self.assertEqual(["--build-dir", "--skip-build"], invocations[0]["options"])

    def test_collects_only_declared_long_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "tool.py"
            script.write_text(
                "import argparse\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--build-dir')\n"
                "p.add_argument('-v', '--verbose', action='store_true')\n",
                encoding="utf-8",
            )

            options, error = contracts.collect_declared_options(script)

        self.assertEqual("", error)
        self.assertEqual({"--help", "--build-dir", "--verbose"}, options)


if __name__ == "__main__":
    unittest.main()
