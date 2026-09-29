"""Publish an exact pair without orchestrating or collecting anything."""
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
import boto3
from voc.storage.objects import PublicationConflict, ReleaseObjects
from .manifest import MAX_JSON_BYTES, canonical_id, file_hash, frame_hash, json_bytes, validate_manifest
from .export import export_frame

def publish_release(raw, workable, storage, *, client=None):
    """Share exact snapshots; retries of a run retain the original promotion condition.

    Local intent/export files are persistent recovery state, not a team catalog.
    A SQLite write lock serializes attempts on this installation during upload.
    Conditional S3 operations arbitrate separate installations.
    """
    info = workable.info
    run_id = canonical_id(info["run_id"])
    artifact_id = canonical_id(info["artifact_id"])
    revision_id = canonical_id(info["raw_revision_id"])
    if (
        raw.info["revision_id"] != revision_id
        or raw.info["raw_store_id"] != info["raw_store_id"]
    ):
        raise ValueError("Raw snapshot does not match workable lineage")
    if (
        len(workable.data) != info["row_count"]
        or frame_hash(workable.data) != info["content_sha256"]
    ):
        raise ValueError("Workable snapshot differs from publication")
    if (
        not storage.bucket
        or not storage.region
        or not re.fullmatch(r"[A-Za-z0-9_-]+", storage.member_id)
    ):
        raise ValueError("Sharing requires bucket, region and publisher member ID")
    objects = ReleaseObjects(
        client or boto3.client("s3", region_name=storage.region),
        storage.bucket,
        storage.shared_prefix,
    )
    folder = storage.runtime_root / "shared-publications"
    folder.mkdir(parents=True, exist_ok=True)
    attempt_key = json_bytes([storage.bucket, storage.shared_prefix, run_id]).decode()
    inputs = {
        "publication": info,
        "raw_content_sha256": frame_hash(raw.data),
        "publisher_id": storage.member_id,
    }
    with closing(sqlite3.connect(folder / "attempts.db", timeout=30)) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, intent TEXT NOT NULL)"
        )
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT intent FROM attempts WHERE key=?", (attempt_key,)
        ).fetchone()
        if row:
            intent = json.loads(row[0])
            if intent["inputs"] != inputs:
                raise ValueError(
                    "Saved sharing inputs differ; refusing to alter the attempt"
                )
        else:
            _, etag = objects.latest()
            intent = {
                "release_id": str(uuid.uuid4()),
                "created_at": datetime.now(UTC).isoformat(),
                "expected_etag": etag,
                "inputs": inputs,
            }
            db.execute(
                "INSERT INTO attempts VALUES (?,?)",
                (attempt_key, json_bytes(intent).decode()),
            )
        db.commit()  # Retain identity/precondition even when a later upload fails.
        db.execute("BEGIN IMMEDIATE")
        release = canonical_id(intent["release_id"])
        exports = folder / release
        exports.mkdir(exist_ok=True)
        raw_file, workable_file = exports / "raw.parquet", exports / "workable.parquet"
        raw_meta = export_frame(raw.data, raw_file)
        workable_meta = export_frame(workable.data, workable_file)
        raw_relative = f"raw/{revision_id}/dataset.parquet"
        workable_relative = f"workable/{artifact_id}/dataset.parquet"
        # Older readers reject v2 instead of silently returning encoded strings.
        format_version = (
            2
            if any(
                "encoding" in c
                for meta in (raw_meta, workable_meta)
                for c in meta["columns"]
            )
            else 1
        )
        manifest = {
            "format_version": format_version,
            "release_id": release,
            "created_at": intent["created_at"],
            "publisher_id": storage.member_id,
            "raw": raw_meta
            | {"revision_id": revision_id, "key": objects.key(raw_relative)},
            "workable": workable_meta
            | {
                "snapshot_id": artifact_id,
                "key": objects.key(workable_relative),
                "schema_version": info["schema_version"],
                "content_sha256": info["content_sha256"],
            },
            "lineage": {
                k: info[k]
                for k in (
                    "run_id",
                    "artifact_id",
                    "mlflow_run_id",
                    "rules",
                    "project_identity",
                )
            },
        }
        validate_manifest(manifest, objects)
        manifest_path = exports / "manifest.json"
        payload = json_bytes(manifest)
        if len(payload) > MAX_JSON_BYTES:
            raise ValueError("Release manifest exceeds size limit")
        if manifest_path.exists() and manifest_path.read_bytes() != payload:
            raise ValueError("Saved manifest differs; refusing to alter the release")
        temporary_manifest = manifest_path.with_suffix(".tmp")
        temporary_manifest.write_bytes(payload)
        temporary_manifest.replace(manifest_path)
        objects.put_immutable(raw_relative, raw_file)
        objects.put_immutable(workable_relative, workable_file)
        manifest_relative = f"releases/{release}.json"
        objects.put_immutable(manifest_relative, manifest_path)
        pointer = {
            "format_version": 1,
            "release_id": release,
            "manifest_key": objects.key(manifest_relative),
            "manifest_sha256": file_hash(manifest_path),
        }
        objects.promote(pointer, intent["expected_etag"])
        db.commit()
    return {
        "status": "published",
        **pointer,
        "dataset_uri": storage.shared_dataset_uri,
        "raw_revision_id": revision_id,
        "artifact_id": artifact_id,
        "raw_row_count": len(raw.data),
        "workable_row_count": len(workable.data),
    }
