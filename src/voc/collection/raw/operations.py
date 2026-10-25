"""Initialize and read committed raw datasets."""

import hashlib
import sqlite3
from pathlib import Path

import pandas as pd

from ...paths import raw_path
from .store import RawStore


def initialize_raw_store(store=None, *, seed_path):
    """Import the full unified seed once. The input is opened read-only."""
    path = Path(seed_path).resolve()
    if path.is_dir():
        path = path / "dataset.db"
    if path.suffix in (".db", ".sqlite"):
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as c:
            frame = pd.read_sql_query("SELECT * FROM dataset", c)
    elif path.suffix == ".parquet":
        frame = pd.read_parquet(path)
    else:
        raise ValueError("Use the unified SQLite or Parquet seed")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return RawStore(raw_path(store)).initialize(
        frame, {"path": str(path), "sha256": h.hexdigest()}
    )


def get_raw_dataset(store=None, revision="latest"):
    """Resolve a committed revision and return Snapshot(data, info)."""
    return RawStore(raw_path(store)).read(revision)
