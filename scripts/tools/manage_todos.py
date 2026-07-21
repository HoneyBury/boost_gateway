#!/usr/bin/env python3
"""Manage repository TODOs and explicitly synchronize them with GitHub Issues."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE = ROOT / "docs/todos/tasks.json"
DEFAULT_BOARD = ROOT / "docs/todos/BOARD.md"
DEFAULT_REPOSITORY = "HoneyBury/boost_gateway"
VALID_STATUSES = {"open", "completed"}
VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
TASK_ID_PATTERN = re.compile(r"TODO-(\d{4,})$")


class TodoError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_store(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TodoError(f"cannot read TODO store {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TodoError(f"TODO store is invalid JSON: {exc}") from exc
    validate_store(document)
    return document


def validate_store(document: Any) -> None:
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise TodoError("TODO store schema_version must be 1")
    if not isinstance(document.get("repository"), str) or not document["repository"]:
        raise TodoError("TODO store repository must be non-empty")
    tasks = document.get("tasks")
    if not isinstance(tasks, list):
        raise TodoError("TODO store tasks must be an array")
    identifiers: set[str] = set()
    largest_number = 0
    for task in tasks:
        if not isinstance(task, dict):
            raise TodoError("every TODO must be an object")
        task_id = task.get("id")
        match = TASK_ID_PATTERN.fullmatch(str(task_id or ""))
        if not match or task_id in identifiers:
            raise TodoError(f"invalid or duplicate TODO id: {task_id!r}")
        identifiers.add(task_id)
        largest_number = max(largest_number, int(match.group(1)))
        if task.get("status") not in VALID_STATUSES:
            raise TodoError(f"{task_id}: status must be open or completed")
        if task.get("priority") not in VALID_PRIORITIES:
            raise TodoError(f"{task_id}: priority must be P0, P1, P2, or P3")
        for field in ("title", "category", "description", "created_at"):
            if not isinstance(task.get(field), str) or not task[field].strip():
                raise TodoError(f"{task_id}: {field} must be non-empty")
        criteria = task.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria or not all(
            isinstance(item, str) and item.strip() for item in criteria
        ):
            raise TodoError(f"{task_id}: acceptance_criteria must contain non-empty strings")
        completed_at = task.get("completed_at")
        if task["status"] == "completed" and not isinstance(completed_at, str):
            raise TodoError(f"{task_id}: completed task requires completed_at")
        if task["status"] == "open" and completed_at is not None:
            raise TodoError(f"{task_id}: open task must not have completed_at")
        github = task.get("github")
        if not isinstance(github, dict):
            raise TodoError(f"{task_id}: github metadata must be an object")
        issue_number = github.get("issue_number")
        if issue_number is not None and (not isinstance(issue_number, int) or issue_number <= 0):
            raise TodoError(f"{task_id}: GitHub issue number must be a positive integer")
        issue_url = github.get("issue_url")
        if issue_url is not None and not isinstance(issue_url, str):
            raise TodoError(f"{task_id}: GitHub issue URL must be a string or null")
    next_id = document.get("next_id")
    if not isinstance(next_id, int) or next_id <= largest_number:
        raise TodoError("TODO store next_id must be greater than every existing task number")


def save_store(path: Path, document: dict[str, Any]) -> None:
    validate_store(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def task_sort_key(task: dict[str, Any]) -> tuple[int, int, str]:
    return (0 if task["status"] == "open" else 1, int(task["priority"][1]), task["id"])


def markdown_text(value: str) -> str:
    return " ".join(value.replace("|", "\\|").splitlines()).strip()


def render_board(document: dict[str, Any]) -> str:
    tasks = sorted(document["tasks"], key=task_sort_key)
    open_count = sum(task["status"] == "open" for task in tasks)
    completed_count = len(tasks) - open_count
    lines = [
        "# Project TODO Board",
        "",
        "> Generated from `docs/todos/tasks.json`. Use `python3 scripts/manage_todos.py`; do not edit this file manually.",
        "",
        f"Open: **{open_count}** | Completed: **{completed_count}** | Total: **{len(tasks)}**",
        "",
        "| Done | ID | Priority | Category | Task | GitHub Issue |",
        "|---|---|---|---|---|---|",
    ]
    for task in tasks:
        github = task["github"]
        issue = "Not linked"
        if github.get("issue_number"):
            issue = f"[#{github['issue_number']}]({github['issue_url']})"
        lines.append(
            "| {done} | `{task_id}` | `{priority}` | {category} | {title} | {issue} |".format(
                done="[x]" if task["status"] == "completed" else "[ ]",
                task_id=task["id"],
                priority=task["priority"],
                category=markdown_text(task["category"]),
                title=markdown_text(task["title"]),
                issue=issue,
            )
        )
    for task in tasks:
        lines.extend(["", f"## {task['id']}: {task['title']}", "", task["description"], ""])
        checked = "x" if task["status"] == "completed" else " "
        lines.extend(f"- [{checked}] {criterion}" for criterion in task["acceptance_criteria"])
        lines.extend(["", f"Status: `{task['status']}`. Created: `{task['created_at']}`."])
        if task["completed_at"]:
            lines[-1] += f" Completed: `{task['completed_at']}`."
    return "\n".join(lines) + "\n"


def write_board(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_board(document), encoding="utf-8")


def find_task(document: dict[str, Any], task_id: str) -> dict[str, Any]:
    normalized = task_id.upper()
    for task in document["tasks"]:
        if task["id"] == normalized:
            return task
    raise TodoError(f"unknown TODO id: {task_id}")


def issue_body(task: dict[str, Any]) -> str:
    checked = "x" if task["status"] == "completed" else " "
    criteria = "\n".join(f"- [{checked}] {item}" for item in task["acceptance_criteria"])
    completed = f"\nCompleted: `{task['completed_at']}`" if task["completed_at"] else ""
    return f"""<!-- boost-gateway-todo:{task['id']} -->
## Local TODO

- ID: `{task['id']}`
- Priority: `{task['priority']}`
- Category: `{task['category']}`
- Status: `{task['status']}`
- Created: `{task['created_at']}`{completed}

## Description

{task['description']}

## Acceptance Criteria

{criteria}

---
Managed by `python3 scripts/manage_todos.py`. The repository TODO store is the source of truth.
"""


def run_gh(arguments: list[str], *, stdin: str | None = None) -> str:
    gh = shutil.which("gh")
    if gh is None:
        raise TodoError("GitHub CLI 'gh' is not installed")
    try:
        result = subprocess.run(
            [gh, *arguments],
            input=stdin,
            text=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise TodoError("GitHub CLI timed out; check GH_TOKEN or gh auth/Keychain access") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise TodoError(f"GitHub CLI failed: {detail}")
    return result.stdout.strip()


def synchronize_task(task: dict[str, Any], repository: str) -> None:
    title = f"[{task['id']}] {task['title']}"
    body = issue_body(task)
    issue_number = task["github"].get("issue_number")
    if issue_number is None:
        output = run_gh(
            ["issue", "create", "--repo", repository, "--title", title, "--body-file", "-"],
            stdin=body,
        )
        issue_url = next((line for line in reversed(output.splitlines()) if "/issues/" in line), "")
        match = re.search(r"/issues/(\d+)(?:\s*)$", issue_url)
        if not match:
            raise TodoError(f"cannot parse issue URL from gh output: {output!r}")
        issue_number = int(match.group(1))
        task["github"] = {"issue_number": issue_number, "issue_url": issue_url.strip()}
    else:
        run_gh(
            ["issue", "edit", str(issue_number), "--repo", repository, "--title", title, "--body-file", "-"],
            stdin=body,
        )
    state_document = json.loads(
        run_gh(["issue", "view", str(issue_number), "--repo", repository, "--json", "state"])
    )
    state = str(state_document.get("state", "")).upper()
    if task["status"] == "completed" and state != "CLOSED":
        run_gh(
            ["issue", "close", str(issue_number), "--repo", repository, "--reason", "completed", "--comment", f"Completed via {task['id']} in the repository TODO board."],
        )
    elif task["status"] == "open" and state == "CLOSED":
        run_gh(["issue", "reopen", str(issue_number), "--repo", repository, "--comment", f"Reopened via {task['id']} in the repository TODO board."])


def update_files(store_path: Path, board_path: Path, document: dict[str, Any]) -> None:
    save_store(store_path, document)
    write_board(board_path, document)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--board", type=Path, default=DEFAULT_BOARD)
    parser.add_argument("--repo", help="GitHub OWNER/REPO override")
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="List TODOs")
    list_parser.add_argument("--status", choices=["all", *sorted(VALID_STATUSES)], default="open")
    list_parser.add_argument("--json", action="store_true")

    show_parser = commands.add_parser("show", help="Show one TODO")
    show_parser.add_argument("task_id")

    add_parser = commands.add_parser("add", help="Add a local TODO")
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--priority", choices=sorted(VALID_PRIORITIES), default="P2")
    add_parser.add_argument("--category", required=True)
    add_parser.add_argument("--description", required=True)
    add_parser.add_argument("--acceptance", action="append", required=True)
    add_parser.add_argument("--sync-github", action="store_true")

    for name in ("complete", "reopen"):
        state_parser = commands.add_parser(name, help=f"Mark a TODO {name}")
        state_parser.add_argument("task_id")
        state_parser.add_argument("--sync-github", action="store_true")

    link_parser = commands.add_parser("link", help="Link an existing GitHub Issue")
    link_parser.add_argument("task_id")
    link_parser.add_argument("issue_number", type=int)

    sync_parser = commands.add_parser("sync", help="Explicitly synchronize GitHub Issues")
    target = sync_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("task_id", nargs="?")
    target.add_argument("--all", action="store_true")

    commands.add_parser("render", help="Regenerate the Markdown board")
    commands.add_parser("check", help="Validate schema and generated board consistency")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store_path = args.store.resolve()
    board_path = args.board.resolve()
    try:
        document = load_store(store_path)
        repository = args.repo or document.get("repository") or DEFAULT_REPOSITORY
        if args.command == "list":
            tasks = sorted(document["tasks"], key=task_sort_key)
            if args.status != "all":
                tasks = [task for task in tasks if task["status"] == args.status]
            if args.json:
                print(json.dumps(tasks, indent=2, ensure_ascii=False))
            else:
                for task in tasks:
                    issue = f"#{task['github']['issue_number']}" if task["github"].get("issue_number") else "no-issue"
                    print(f"{task['id']} {task['priority']} {task['status']:<9} {issue:<9} {task['title']}")
            return 0
        if args.command == "show":
            print(json.dumps(find_task(document, args.task_id), indent=2, ensure_ascii=False))
            return 0
        if args.command == "add":
            task_id = f"TODO-{document['next_id']:04d}"
            task = {
                "id": task_id,
                "title": args.title.strip(),
                "status": "open",
                "priority": args.priority,
                "category": args.category.strip(),
                "description": args.description.strip(),
                "acceptance_criteria": [item.strip() for item in args.acceptance],
                "created_at": utc_now(),
                "completed_at": None,
                "github": {"issue_number": None, "issue_url": None},
            }
            if args.sync_github:
                synchronize_task(task, repository)
            document["tasks"].append(task)
            document["next_id"] += 1
            update_files(store_path, board_path, document)
            print(task_id)
            return 0
        if args.command in {"complete", "reopen"}:
            task = find_task(document, args.task_id)
            desired_status = "completed" if args.command == "complete" else "open"
            task["status"] = desired_status
            task["completed_at"] = utc_now() if desired_status == "completed" else None
            if args.sync_github:
                synchronize_task(task, repository)
            update_files(store_path, board_path, document)
            print(f"{task['id']} -> {desired_status}")
            return 0
        if args.command == "link":
            if args.issue_number <= 0:
                raise TodoError("issue number must be positive")
            task = find_task(document, args.task_id)
            task["github"] = {
                "issue_number": args.issue_number,
                "issue_url": f"https://github.com/{repository}/issues/{args.issue_number}",
            }
            update_files(store_path, board_path, document)
            print(f"{task['id']} -> #{args.issue_number}")
            return 0
        if args.command == "sync":
            tasks = document["tasks"] if args.all else [find_task(document, args.task_id)]
            updated = copy.deepcopy(document)
            selected_ids = {task["id"] for task in tasks}
            for task in updated["tasks"]:
                if task["id"] in selected_ids:
                    synchronize_task(task, repository)
                    print(f"synced {task['id']} -> #{task['github']['issue_number']}")
            update_files(store_path, board_path, updated)
            return 0
        if args.command == "render":
            write_board(board_path, document)
            print(board_path)
            return 0
        if args.command == "check":
            expected = render_board(document)
            actual = board_path.read_text(encoding="utf-8") if board_path.exists() else ""
            if actual != expected:
                raise TodoError(f"TODO board is stale; run: python3 scripts/manage_todos.py render")
            print(f"TODO board: PASS ({len(document['tasks'])} tasks)")
            return 0
        raise TodoError(f"unsupported command: {args.command}")
    except TodoError as exc:
        print(f"todo manager: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
