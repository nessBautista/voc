"""Publish validated embedding exports without importing encoders or orchestration."""

import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3

from voc.datasets.manifest import file_hash
from voc.storage.objects import PublicationConflict, ReleaseObjects
from voc.storage.settings import validate_prefixes

from .manifest import require_promotable
from .namespace import scope_prefix
from .validation import load_release


def _now():
    return datetime.now(UTC).isoformat()


def _save(db, run_id, record):
    db.execute(
        "INSERT OR REPLACE INTO attempts VALUES (?, ?)",
        (run_id, json.dumps(record, sort_keys=True)),
    )
    db.commit()


def _upload(objects, key, path, digest, size):
    # The exported bytes must still match the pinned intent immediately before
    # upload. Recheck remote bytes against that intent, not a newly computed hash.
    if path.stat().st_size != size or file_hash(path) != digest:
        raise ValueError("Local export changed after validation: " + path.name)
    objects.put_immutable(key, path)
    if not objects.verify_file(key, digest, size):
        raise ValueError("Uploaded release object is missing: " + key)


def publish_release(reference, storage, *, promote=False, client=None):
    """Upload an immutable release; optionally promote a full selection.

    A durable SQLite record pins the destination and export for this producer.
    Upload and promotion have separate outcomes. A later promotion gets its own
    precondition; retries of that attempt never refresh it. Local SQLite locking
    serializes callers sharing this runtime, while S3 conditions arbitrate across
    installations. The runtime record must survive restarts for safe retries.
    """
    directory = Path(reference["directory"])
    manifest = load_release(
        directory, manifest_sha256=reference["manifest_sha256"]
    ).info
    expected = {
        "schema_version": "voc-embedding-export-v1",
        "state": "exported",
        "producer_run_id": manifest["provenance"]["producer_run_id"],
        "embedding_release_id": manifest["embedding_release_id"],
        "profile_id": manifest["profile"]["profile_id"],
        "dataset_release_id": manifest["dataset"]["release_id"],
        "coverage": manifest["coverage"],
    }
    if any(reference.get(key) != value for key, value in expected.items()):
        raise ValueError("Export reference differs from validated release")
    if promote:
        require_promotable(manifest)
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", storage.bucket):
        raise ValueError("Set AWS_BUCKET to a valid bucket name for publication")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", storage.region):
        raise ValueError("Set AWS_REGION for publication")
    shared, _, _ = validate_prefixes(
        storage.shared_prefix, storage.members_prefix, storage.embeddings_prefix
    )
    if manifest["dataset"]["dataset_uri"] != f"s3://{storage.bucket}/{shared}":
        raise ValueError("Release must originate from the configured shared dataset")
    scope = scope_prefix(
        storage, reference["profile_id"], reference["dataset_release_id"]
    )
    objects = ReleaseObjects(
        client
        if client is not None
        else boto3.client("s3", region_name=storage.region),
        storage.bucket,
        scope,
    )
    release_id, run_id = reference["embedding_release_id"], reference["producer_run_id"]
    pointer = {
        "format_version": 1,
        "release_id": release_id,
        "manifest_key": objects.key(f"releases/{release_id}.json"),
        "manifest_sha256": reference["manifest_sha256"],
    }
    inputs = {
        "bucket": storage.bucket,
        "region": storage.region,
        "scope": scope,
        "export": reference,
        "artifacts": manifest["artifacts"],
        "manifest_size": (directory / "manifest.json").stat().st_size,
    }
    folder = storage.runtime_root / "shared-publications" / "embeddings"
    folder.mkdir(parents=True, exist_ok=True)
    database = folder / "attempts.db"
    with closing(sqlite3.connect(database, timeout=300)) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS attempts (producer_id TEXT PRIMARY KEY, record TEXT)"
        )
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT record FROM attempts WHERE producer_id=?", (run_id,)
        ).fetchone()
        if row is None:
            _, etag = objects.latest()
            record = {
                "inputs": inputs,
                "publication_id": str(uuid4()),
                "created_at": _now(),
                "observed_etag": etag,
                "upload_state": "pending",
                "promotion": None,
            }
        else:
            record = json.loads(row[0])
            if record["inputs"] != inputs:
                raise ValueError(
                    "Saved publication intent differs; refusing changed destination or export"
                )
        if promote and record["promotion"] is None:
            if row is not None and record["upload_state"] != "published":
                raise ValueError(
                    "Finish the saved upload before starting a separate promotion"
                )
            # On initial publication capture before any upload; a later promotion
            # has a new, independently durable precondition.
            etag = record["observed_etag"] if row is None else objects.latest()[1]
            record["promotion"] = {
                "attempt_id": str(uuid4()),
                "expected_etag": etag,
                "created_at": _now(),
                "state": "pending",
            }
        elif (
            not promote
            and record["promotion"]
            and record["promotion"]["state"] != "promoted"
        ):
            raise ValueError("Retry the saved promotion with --promote")
        _save(db, run_id, record)  # Durable intent precedes every S3 mutation.

        db.execute("BEGIN IMMEDIATE")
        # Another local caller may have completed between the two transactions.
        record = json.loads(
            db.execute(
                "SELECT record FROM attempts WHERE producer_id=?", (run_id,)
            ).fetchone()[0]
        )
        phase = "upload"
        try:
            for name, filename in (
                ("vectors", "vectors.npy"),
                ("reviews", "reviews.parquet"),
            ):
                artifact = manifest["artifacts"][name]
                _upload(
                    objects,
                    artifact["key"],
                    directory / filename,
                    artifact["sha256"],
                    artifact["size_bytes"],
                )
            _upload(
                objects,
                f"releases/{release_id}.json",
                directory / "manifest.json",
                reference["manifest_sha256"],
                inputs["manifest_size"],
            )
            record["upload_state"] = "published"
            record.pop("upload_error", None)
            if promote:
                phase = "promotion"
                objects.promote(pointer, record["promotion"]["expected_etag"])
                record["promotion"]["state"] = "promoted"
                record["promotion"].pop("error", None)
                record["promotion"].setdefault("completed_at", _now())
            record.setdefault("published_at", _now())
        except Exception as error:
            if phase == "upload":
                record.update(upload_state="failed", upload_error=str(error))
            else:
                record["promotion"].update(
                    state="conflict"
                    if isinstance(error, PublicationConflict)
                    else "failed",
                    error=str(error),
                )
            record["updated_at"] = _now()
            _save(db, run_id, record)
            raise
        record["updated_at"] = _now()
        _save(db, run_id, record)
    return {
        "schema_version": "voc-embedding-publication-v1",
        "state": "promoted" if promote else "published",
        "publication_id": record["publication_id"],
        "producer_run_id": run_id,
        "embedding_release_id": release_id,
        "dataset_release_id": reference["dataset_release_id"],
        "profile_id": reference["profile_id"],
        "manifest_sha256": reference["manifest_sha256"],
        "manifest_uri": f"s3://{storage.bucket}/{pointer['manifest_key']}",
        "scope_uri": f"s3://{storage.bucket}/{scope}",
        "latest_uri": f"s3://{storage.bucket}/{scope}/latest.json",
        "coverage": manifest["coverage"],
        "promotion": record["promotion"],
        "attempts_database": str(database),
    }
