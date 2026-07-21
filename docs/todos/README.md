# Project TODO Module

`tasks.json` is the versioned source of truth. `BOARD.md` is generated for quick
review. Local operations never contact GitHub unless `--sync-github` or `sync`
is explicitly requested.

```bash
python3 scripts/manage_todos.py list --status all
python3 scripts/manage_todos.py show TODO-0001
python3 scripts/manage_todos.py add \
  --title "Task title" --priority P1 --category release \
  --description "Why this is needed" \
  --acceptance "First verifiable outcome" \
  --acceptance "Second verifiable outcome"
python3 scripts/manage_todos.py sync TODO-0001
python3 scripts/manage_todos.py complete TODO-0001 --sync-github
python3 scripts/manage_todos.py reopen TODO-0001 --sync-github
python3 scripts/manage_todos.py link TODO-0001 123
python3 scripts/manage_todos.py check
```

`sync` creates an Issue when none is linked, otherwise it updates the title and
body. Completed tasks close linked Issues; reopened tasks reopen them. The Issue
contains `<!-- boost-gateway-todo:TODO-NNNN -->` so its repository identity is
auditable. Set `GH_TOKEN` or configure `gh auth`; credentials are never stored in
the TODO files.

Completion is a factual assertion. Check every acceptance criterion before using
`complete`; do not close environment or release evidence tasks based only on local
simulation.
