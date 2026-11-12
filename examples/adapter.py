"""A trusted local producer with calculated, fictional metrics."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workboard import Config, serve


def snapshot():
    return {"tasks": [{"id": "example", "title": "Process example items", "status": "background",
                       "detail": "This is fictional data; replace this function with your producer."}],
            "cards": [{"label": "Items processed", "value": "12 / 20",
                       "progress": {"value": 12, "max": 20}}]}


if __name__ == "__main__":
    serve(Config(title="Example Project"), snapshot_fn=snapshot)
