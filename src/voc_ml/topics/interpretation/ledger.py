"""Durable local study ledger, serialized for the entire provider operation."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from voc.embeddings.shared.contracts import canonical_json, fingerprint, require_digest
from voc.embeddings.shared.filesystem import sync_directory


def atomic_json(path, value):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class InterpretationStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    @contextmanager
    def lock(self, *, name=".lock"):
        if name not in (".lock", ".batch.lock"):
            raise ValueError("Unknown operation lock")
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / name).open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(
                    "Another interpretation operation owns the ledger"
                ) from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def initialize(self, protocol, catalog):
        with self.lock():
            # A single ledger per runtime prevents a new study silently resetting the allowance.
            path = self.root / "ledger.json"
            if path.exists():
                raise ValueError(
                    "Interpretation ledger already exists; inspect it without resetting"
                )
            state = {
                "schema_version": "voc-interpretation-ledger-v1",
                "protocol": protocol,
                "protocol_sha256": fingerprint(protocol),
                "initial_catalog": catalog,
                "attempts": {},
                "development_enabled": False,
            }
            self.save(state)
            return state

    def load(self):
        raw = json.loads((self.root / "ledger.json").read_bytes())
        if raw["schema_version"] != "voc-interpretation-ledger-v1":
            raise ValueError("Unsupported interpretation ledger")
        require_digest(raw["protocol_sha256"])
        if fingerprint(raw["protocol"]) != raw["protocol_sha256"]:
            raise ValueError("Frozen protocol changed")
        for key, attempt in raw["attempts"].items():
            request = raw["protocol"]["requests"][key]
            if attempt["request_sha256"] != fingerprint(request):
                raise ValueError("Attempt request changed")
        return raw

    def save(self, state):
        atomic_json(self.root / "ledger.json", state)
