from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/tools/manage_todos.py"
SPEC = importlib.util.spec_from_file_location("manage_todos", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
manage_todos = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage_todos)


class ManageTodosTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.store = root / "tasks.json"
        self.board = root / "BOARD.md"
        self.document = {
            "schema_version": 1,
            "repository": "example/project",
            "next_id": 2,
            "tasks": [
                {
                    "id": "TODO-0001",
                    "title": "Configure runner",
                    "status": "open",
                    "priority": "P0",
                    "category": "environment",
                    "description": "Prepare the fixed runner.",
                    "acceptance_criteria": ["Runner is online."],
                    "created_at": "2026-07-22T00:00:00Z",
                    "completed_at": None,
                    "github": {"issue_number": None, "issue_url": None},
                }
            ],
        }
        manage_todos.save_store(self.store, self.document)
        manage_todos.write_board(self.board, self.document)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def arguments(self, *command: str) -> list[str]:
        return ["--store", str(self.store), "--board", str(self.board), *command]

    def test_check_accepts_matching_store_and_board(self) -> None:
        self.assertEqual(0, manage_todos.main(self.arguments("check")))

    def test_check_rejects_stale_board(self) -> None:
        self.board.write_text("stale\n", encoding="utf-8")
        self.assertEqual(1, manage_todos.main(self.arguments("check")))

    def test_add_allocates_stable_id_and_updates_board(self) -> None:
        result = manage_todos.main(
            self.arguments(
                "add",
                "--title",
                "Package SDK",
                "--priority",
                "P1",
                "--category",
                "release",
                "--description",
                "Build candidate packages.",
                "--acceptance",
                "Wheel passes.",
            )
        )
        self.assertEqual(0, result)
        document = manage_todos.load_store(self.store)
        self.assertEqual(3, document["next_id"])
        self.assertEqual("TODO-0002", document["tasks"][1]["id"])
        self.assertIn("TODO-0002", self.board.read_text(encoding="utf-8"))

    def test_complete_and_reopen_keep_status_timestamps_consistent(self) -> None:
        self.assertEqual(0, manage_todos.main(self.arguments("complete", "TODO-0001")))
        completed = manage_todos.load_store(self.store)["tasks"][0]
        self.assertEqual("completed", completed["status"])
        self.assertIsInstance(completed["completed_at"], str)
        self.assertEqual(0, manage_todos.main(self.arguments("reopen", "TODO-0001")))
        reopened = manage_todos.load_store(self.store)["tasks"][0]
        self.assertEqual("open", reopened["status"])
        self.assertIsNone(reopened["completed_at"])

    def test_link_records_repository_issue_url(self) -> None:
        self.assertEqual(0, manage_todos.main(self.arguments("link", "TODO-0001", "42")))
        task = manage_todos.load_store(self.store)["tasks"][0]
        self.assertEqual(42, task["github"]["issue_number"])
        self.assertEqual("https://github.com/example/project/issues/42", task["github"]["issue_url"])

    def test_sync_creates_issue_and_records_stable_marker(self) -> None:
        task = self.document["tasks"][0]
        with mock.patch.object(
            manage_todos,
            "run_gh",
            side_effect=["https://github.com/example/project/issues/7", json.dumps({"state": "OPEN"})],
        ) as run_gh:
            manage_todos.synchronize_task(task, "example/project")
        self.assertEqual(7, task["github"]["issue_number"])
        create_input = run_gh.call_args_list[0].kwargs["stdin"]
        self.assertIn("<!-- boost-gateway-todo:TODO-0001 -->", create_input)

    def test_completed_sync_updates_and_closes_open_issue(self) -> None:
        task = self.document["tasks"][0]
        task["status"] = "completed"
        task["completed_at"] = "2026-07-22T01:00:00Z"
        task["github"] = {
            "issue_number": 7,
            "issue_url": "https://github.com/example/project/issues/7",
        }
        with mock.patch.object(
            manage_todos,
            "run_gh",
            side_effect=["", json.dumps({"state": "OPEN"}), ""],
        ) as run_gh:
            manage_todos.synchronize_task(task, "example/project")
        self.assertEqual("close", run_gh.call_args_list[2].args[0][1])

    def test_store_validation_rejects_status_timestamp_drift(self) -> None:
        self.document["tasks"][0]["completed_at"] = "2026-07-22T01:00:00Z"
        with self.assertRaises(manage_todos.TodoError):
            manage_todos.validate_store(self.document)


if __name__ == "__main__":
    unittest.main()
