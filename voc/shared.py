"""Immutable dataset exports with an explicit, conditional shared-latest pointer.

This module knows about dataframes and S3, not ZenML. The ML runner validates
completed pipeline outputs before passing their snapshots here.
"""

import base64
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pandas as pd
import pyarrow.parquet as pq
from botocore.exceptions import ClientError

from .storage import _prefix

MAX_FILE_BYTES = 1024**3  # Single-request uploads for this initial dataset size.
MAX_JSON_BYTES = 1024**2


class PublicationConflict(RuntimeError):
    """Another release won; an old retry must not replace it."""


def json_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def frame_hash(frame):
    return hashlib.sha256(
        frame.to_json(orient="records", force_ascii=False).encode()
    ).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Expected a canonical UUID")
    return value


def _missing(error):
    return error.response["Error"]["Code"] in ("NoSuchKey", "404", "NotFound")


class ReleaseObjects:
    """Bounded S3 operations; all keys stay under one explicitly selected root."""

    def __init__(self, client, bucket, prefix):
        self.client, self.bucket = client, bucket
        self.prefix = _prefix(prefix, "dataset prefix")

    def key(self, relative):
        return self.prefix + "/" + _prefix(relative, "object key")

    def get_json(self, relative):
        try:
            response = self.client.get_object(
                Bucket=self.bucket, Key=self.key(relative)
            )
        except ClientError as error:
            if _missing(error):
                return None, None
            raise
        with closing(response["Body"]) as stream:
            data = stream.read(MAX_JSON_BYTES + 1)
        if len(data) > MAX_JSON_BYTES:
            raise ValueError("Shared metadata exceeds size limit")
        return json.loads(data), response["ETag"]

    def latest(self):
        value, etag = self.get_json("latest.json")
        if value is not None:
            release = canonical_id(value["release_id"])
            if (
                value.get("format_version") != 1
                or value["manifest_key"] != self.key(f"releases/{release}.json")
                or not re.fullmatch(r"[0-9a-f]{64}", value["manifest_sha256"])
            ):
                raise ValueError("Invalid shared latest pointer")
        return value, etag

    def verify_file(self, relative, digest, size):
        try:
            response = self.client.get_object(
                Bucket=self.bucket, Key=self.key(relative)
            )
        except ClientError as error:
            if _missing(error):
                return False
            raise
        h, count = hashlib.sha256(), 0
        with closing(response["Body"]) as stream:
            while block := stream.read(1024**2):
                count += len(block)
                if count > size:
                    raise ValueError("Existing shared object size differs: " + relative)
                h.update(block)
        if count != size or h.hexdigest() != digest:
            raise ValueError("Existing shared object checksum differs: " + relative)
        return True

    def put_immutable(self, relative, path):
        size, digest = path.stat().st_size, file_hash(path)
        if size > MAX_FILE_BYTES:
            raise ValueError("Shared export exceeds the initial 1 GiB upload limit")
        if self.verify_file(relative, digest, size):
            return
        try:
            with path.open("rb") as stream:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=self.key(relative),
                    Body=stream,
                    IfNoneMatch="*",
                    ContentLength=size,
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
                )
        except ClientError as error:
            if error.response["Error"]["Code"] != "PreconditionFailed":
                raise
        if not self.verify_file(relative, digest, size):
            raise ValueError("Shared upload is missing: " + relative)

    def promote(self, pointer, expected_etag):
        current, _ = self.latest()
        if current == pointer:
            return  # The previous successful response may have been lost.
        condition = (
            {"IfMatch": expected_etag} if expected_etag else {"IfNoneMatch": "*"}
        )
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self.key("latest.json"),
                Body=json_bytes(pointer),
                ContentType="application/json",
                **condition,
            )
        except ClientError as error:
            if error.response["Error"]["Code"] in (
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "NoSuchKey",
                "404",
            ):
                current, _ = self.latest()
                if current == pointer:
                    return
                raise PublicationConflict(
                    "Shared latest changed. This saved attempt cannot replace it; "
                    "review the newer release before choosing a new run to share."
                ) from error
            raise


def export_frame(frame, path):
    """Retain retry bytes and prove that Parquet preserves values and dtypes."""
    if not path.exists():
        temporary = path.with_suffix(".tmp")
        frame.to_parquet(temporary, index=False)
        pd.testing.assert_frame_equal(
            frame, pd.read_parquet(temporary), check_exact=True
        )
        temporary.replace(path)
    restored = pd.read_parquet(path)
    pd.testing.assert_frame_equal(frame, restored, check_exact=True)
    schema = pq.read_schema(path)
    return {
        "row_count": len(frame),
        "size_bytes": path.stat().st_size,
        "sha256": file_hash(path),
        "columns": [
            {
                "name": field.name,
                "arrow_type": str(field.type),
                "pandas_dtype": str(frame[field.name].dtype),
            }
            for field in schema
        ],
    }


def validate_manifest(manifest, objects):
    """Check this format's required identities, hashes and exact in-root keys."""
    if manifest["format_version"] != 1:
        raise ValueError("Unsupported release manifest format")
    canonical_id(manifest["release_id"])
    canonical_id(manifest["lineage"]["run_id"])
    for stage, identity in (("raw", "revision_id"), ("workable", "snapshot_id")):
        item = manifest[stage]
        native = canonical_id(item[identity])
        if item["key"] != objects.key(f"{stage}/{native}/dataset.parquet"):
            raise ValueError("Manifest object key is outside its expected location")
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("Invalid dataset checksum")
        if (
            type(item["row_count"]) is not int
            or item["row_count"] < 0
            or type(item["size_bytes"]) is not int
            or not 0 < item["size_bytes"] <= MAX_FILE_BYTES
        ):
            raise ValueError("Invalid dataset size")
        if not item["columns"] or len({c["name"] for c in item["columns"]}) != len(
            item["columns"]
        ):
            raise ValueError("Invalid column schema")
        for column in item["columns"]:
            if not all(
                isinstance(column[k], str)
                for k in ("name", "arrow_type", "pandas_dtype")
            ):
                raise ValueError("Invalid column type")
    if manifest["workable"]["snapshot_id"] != manifest["lineage"]["artifact_id"]:
        raise ValueError("Manifest artifact identity differs")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest["workable"]["content_sha256"]):
        raise ValueError("Invalid content checksum")
    if not isinstance(manifest["lineage"]["rules"], dict):
        raise TypeError("Invalid preparation rules")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", manifest["publisher_id"]):
        raise ValueError("Invalid publisher ID")
    datetime.fromisoformat(manifest["created_at"])


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
        manifest = {
            "format_version": 1,
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
