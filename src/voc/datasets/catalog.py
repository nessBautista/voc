import json
import sqlite3
from contextlib import contextmanager
from voc.paths import publication_path
from voc.collection.store import encoded
from pathlib import Path

@contextmanager
def _connection(path=None):
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



def publish(info, *, path=None):
    """Publish a validated completed-run reference; repeat publication is idempotent."""
    for key in (
        "run_id",
        "artifact_id",
        "raw_revision_id",
        "schema_version",
        "row_count",
    ):
        if key not in info:
            raise ValueError("Missing publication field: " + key)
    with _connection(path) as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute(
            "SELECT info FROM publications WHERE run_id=?", (info["run_id"],)
        ).fetchone()
        if existing:
            if json.loads(existing[0]) != info:
                raise ValueError("Conflicting publication for run")
            return
        c.execute(
            "INSERT INTO publications(run_id,artifact_id,info) VALUES(?,?,?)",
            (info["run_id"], info["artifact_id"], encoded(info)),
        )



def resolve_local(version="latest", *, path=None):
    with _connection(path) as c:
        row = c.execute(
            "SELECT info FROM publications ORDER BY seq DESC LIMIT 1"
            if version == "latest"
            else "SELECT info FROM publications WHERE artifact_id=?",
            () if version == "latest" else (version,),
        ).fetchone()
    if row is None:
        raise LookupError("No published dataset for " + version)
    return json.loads(row[0])
