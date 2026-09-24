"""Small publication catalog and pluggable prepared-artifact reader."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

import pandas as pd

from .paths import data_root, publication_path
from .store import Snapshot, encoded


class ArtifactReader(Protocol):
    def read(self, artifact_id: str) -> pd.DataFrame: ...


class ZenMLReader:
    """Optional adapter. ZenML handles materialization and local/S3 artifact stores."""

    def read(self, artifact_id):
        os.environ.setdefault("ZENML_CONFIG_PATH", str(data_root() / "zenml-client"))
        from zenml.client import Client

        data = Client().get_artifact_version(artifact_id).load()
        if not isinstance(data, pd.DataFrame):
            raise TypeError("Expected dataframe artifact")
        return data


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


def resolve(version="latest", *, path=None):
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


def get_dataset(version="latest", *, reader=None, path=None):
    info = resolve(version, path=path)
    frame = (reader or ZenMLReader()).read(info["artifact_id"])
    return Snapshot(frame, info)


def summary(snapshot):
    """Source/month counts shared by the CLI and dashboard; no review bodies."""
    frame = snapshot.data
    dates = pd.to_datetime(
        frame["record_date"], errors="coerce", utc=True, format="mixed"
    )
    return {
        "info": snapshot.info,
        "platform_counts": frame.platform.value_counts().to_dict(),
        "month_counts": dates.dt.strftime("%Y-%m")
        .value_counts()
        .sort_index()
        .to_dict(),
        "unknown_dates": int(dates.isna().sum()),
    }
