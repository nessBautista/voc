"""SQLite connection ownership for the personal publication catalog."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from voc.paths import publication_path


@contextmanager
def connection(path=None):
    path = Path(path or publication_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=30)
    c.execute(
        "CREATE TABLE IF NOT EXISTS publications(seq INTEGER PRIMARY KEY, run_id TEXT UNIQUE NOT NULL, artifact_id TEXT UNIQUE NOT NULL, info TEXT NOT NULL)"
    )
    try:
        with c:
            yield c
    finally:
        c.close()
