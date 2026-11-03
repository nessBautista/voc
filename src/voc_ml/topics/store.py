"""Local producer records. One synchronous owner updates a run sequentially."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from voc.embeddings.shared.contracts import canonical_json, require_uuid
from voc.embeddings.shared.filesystem import sync_directory
from voc.paths import data_root


def now():
    return datetime.now(UTC).isoformat()


class TopicRunStore:
    """Own a resolved runtime root, without rereading the environment per operation."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def directory(self, run_id):
        require_uuid(run_id, "run_id")
        return self.root / "topics/runs" / run_id

    def load(self, run_id):
        state = json.loads((self.directory(run_id) / "run.json").read_bytes())
        if (
            state["run_id"] != run_id
            or state["schema_version"] != "voc-topic-producer-v1"
        ):
            raise ValueError("Producer identity mismatch")
        return state

    def update(self, run_id, **changes):
        state = self.load(run_id)
        state.update(changes, updated_at=now())
        self.save(state)
        return state

    def save(self, state):
        folder = self.directory(state["run_id"])
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(canonical_json(state))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(folder / "run.json")
            sync_directory(folder)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def local_store():
    """Resolve the default only at the boundary of a Python or CLI invocation."""
    return TopicRunStore(data_root())
