"""One coordinator updates a task atomically. Do not run competing writers."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile


def update(folder):
    path = Path(folder) / "tasks.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    task = {"id": "example-check", "title": "Run the example check", "status": "done",
            "detail": "An example script reported completion.", "updated_at": now}
    tasks = state.setdefault("tasks", [])
    state["tasks"] = [task if t.get("id") == task["id"] else t for t in tasks]
    if not any(t.get("id") == task["id"] for t in tasks):
        state["tasks"].append(task)
    state["updated_at"] = now
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as f:
            temporary = Path(f.name)
            json.dump(state, f, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 examples/update.py PATH_TO_STATE_DIRECTORY")
    update(sys.argv[1])
