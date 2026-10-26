"""Publish and resolve personal catalog references without materializing artifacts."""

import json

from voc.collection.shared.serialization import encoded

from .contracts import validate_publication
from .store import connection


def publish(info, *, path=None):
    """Publish a validated completed-run reference; repeat publication is idempotent."""
    validate_publication(info)
    with connection(path) as c:
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
    with connection(path) as c:
        row = c.execute(
            "SELECT info FROM publications ORDER BY seq DESC LIMIT 1"
            if version == "latest"
            else "SELECT info FROM publications WHERE artifact_id=?",
            () if version == "latest" else (version,),
        ).fetchone()
    if row is None:
        raise LookupError("No published dataset for " + version)
    return json.loads(row[0])
