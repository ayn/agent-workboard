# State format

All data is optional. Unknown fields are ignored; malformed top-level files retain
their last valid contents. Keep files small. The server limits a snapshot to 5,000
tasks, 500 team members, 50 cards, and 20 links or sources.

`tasks.json`:

```json
{
  "updated_at": "2030-01-01T12:00:00Z",
  "tasks": [
    {"id": "review", "title": "Review changes", "status": "waiting", "detail": "Waiting for feedback.", "url": "https://example.com/review"}
  ],
  "cards": [
    {"id": "checks", "label": "Checks passed", "value": 12, "note": "Example metric", "progress": {"value": 12, "max": 16}},
    {"label": "Processed", "value": "120 / 200", "estimate": "About 20 minutes remaining", "note": "Estimate from the producer, not a deadline."}
  ],
  "links": [{"label": "Project docs", "url": "https://example.com/docs"}]
}
```

A card value can be a string or a finite number. Numeric values use locale-aware
grouping. Optional `precision` is 0–3, and optional `unit` follows the value.
Progress requires numeric `0 <= value <= max` and `max > 0`. Invalid progress is
omitted. The producer calculates estimates and explains their limitations in notes.

`team.json`:

```json
{
  "updated_at": "2030-01-01T12:00:00Z",
  "members": [
    {"id": "coordinator", "name": "Coordinator", "role": "Codex, Claude, another agent, or a human", "status": "working", "task": "Review the current changes.", "updated_at": "2030-01-01T12:00:00Z"},
    {"id": "helper", "name": "Test reviewer", "role": "Helper", "parent": "Coordinator", "status": "done", "task": "Finished the focused checks.", "updated_at": "2030-01-01T11:55:00Z"}
  ]
}
```

The same five states work for tasks and members. Unknown states display as
`queued`. Missing member timestamps display as unknown; a team's global timestamp
is not substituted for an individual agent's last report. Staleness is advisory,
not a claim that an agent is disconnected. Completed members are hidden initially.

An adapter callback returns the same fields as `tasks.json` with an additional
`team` object matching `team.json`. `/api/status` returns these allowlisted fields
plus `served_at`. `/api/workspaces` and SSE may also include `workspaces`, each with
`id`, `title`, `available` and projected `data`. Consumers must not treat status
notes as authenticated proof that a task ran or a side effect succeeded.
