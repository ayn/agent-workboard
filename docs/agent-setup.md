# Agent setup

These instructions work with Codex, Claude Code, other coding agents, and humans.
The board reads files; it is not an agent controller or execution environment.

1. Initialize a state directory with `python3 workboard.py --init --state-dir PATH`.
2. Keep it ignored by Git. Run the board with that same directory and a project title.
3. Add the instructions below to the consuming project's `AGENTS.md`, `CLAUDE.md`,
   or equivalent. Do not replace that project's existing instructions.

## Suggested project instructions

- The coordinator owns the shared status files. Helpers report progress to the
  coordinator instead of concurrently replacing the same JSON file.
- At session start, read both files and reconcile them with actual work. Preserve
  unrelated tasks. Add a task when work starts; update it on progress, blockers,
  completion, and meaningful scope changes. Keep completed work visible.
- Allowed task states are `working`, `background`, `waiting`, `queued`, and `done`.
  Distinguish a process running in the background from a plan to run it later.
- Team members represent actual agent sessions. Give each a stable ID, accurate
  name/role, parent if relevant, status and current task. Do not invent helpers.
- Set each member's `updated_at` when that member actually starts or reports.
  Do not refresh every timestamp to make inactive agents look current. Mark
  completed agents `done`; an old card is not proof of a live agent.
- Update top-level `updated_at` on a saved change. Use timezone-aware ISO 8601.
- Write a complete temporary file in the same directory, then atomically replace
  the target. Read before editing. Serialize writers through the coordinator.
- Keep supported display fields free of secrets, raw logs/payloads, personal
  identifiers and private capability links. The dashboard can display whatever
  you put there. Only publish synthetic screenshots.
- Before ending a turn, reconcile outcomes and the coordinator's status. A durable
  background task can stay `background`; an ended agent should not stay `working`.

See [state-format.md](state-format.md) for the schema and
[`examples/update.py`](../examples/update.py) for atomic writes.

## Keeping the server up

Start it in your existing process manager or tmux session. A minimal tmux example:

```sh
tmux new-session -d -s workboard 'python3 /path/to/workboard.py --state-dir /path/to/.workboard'
```

Use a supervisor if you want process restarts. tmux survives terminal disconnects
but not container removal or host reboots. Hook startup into the workspace's
launcher or devcontainer lifecycle rather than relying on an agent tool process.
The board does not install background services or change your agent configuration.
