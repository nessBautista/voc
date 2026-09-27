"""Portable shared Parquet reads; no ZenML, MLflow or raw database required."""

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

import boto3
import pandas as pd
import pyarrow.parquet as pq
from botocore.exceptions import ClientError

from .shared import (
    MAX_JSON_BYTES,
    ReleaseObjects,
    canonical_id,
    file_hash,
    frame_hash,
    json_bytes,
    validate_manifest,
)
from .storage import load_storage
from .store import Snapshot


class SharedDatasetReader:
    def __init__(self, storage=None, *, client=None):
        # Reading does not need a personal installation identity.
        self.storage = storage or load_storage(destination="local")
        if not self.storage.bucket or not self.storage.region:
            raise ValueError("Shared reads require AWS_BUCKET and AWS_REGION")
        self._client = client
        self.objects = ReleaseObjects(
            self, self.storage.bucket, self.storage.shared_prefix
        )
        namespace = hashlib.sha256(
            json_bytes([self.storage.bucket, self.storage.shared_prefix])
        ).hexdigest()
        self.cache = self.storage.runtime_root / "cache/datasets" / namespace

    def get_object(self, **kwargs):
        # Laziness lets a pinned cached read work without credentials/network.
        if self._client is None:
            self._client = boto3.client("s3", region_name=self.storage.region)
        return self._client.get_object(**kwargs)

    @contextmanager
    def locked(self):
        self.cache.mkdir(parents=True, exist_ok=True)
        # Serialize downloads across notebook/dashboard processes on this runtime.
        with closing(sqlite3.connect(self.cache / "cache.db", timeout=120)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def atomic_bytes(self, target, payload):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.cache, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def checked_manifest(self, payload, release, expected_hash=None):
        digest = hashlib.sha256(payload).hexdigest()
        if expected_hash is not None and digest != expected_hash:
            raise ValueError("Release manifest checksum differs")
        try:
            manifest = json.loads(payload)
            validate_manifest(manifest, self.objects)
            if manifest["release_id"] != release:
                raise ValueError("Release manifest identity differs")
            if manifest["workable"]["schema_version"] != "workable-v1":
                raise ValueError("Unsupported shared workable schema")
            for field in ("project_identity", "mlflow_run_id"):
                manifest["lineage"][field]
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("Invalid shared release manifest") from error
        return manifest

    def manifest(self, version="latest"):
        expected_hash = None
        if version == "latest":
            # Always resolve online. Never quietly use yesterday's latest pointer.
            pointer, _ = self.objects.latest()
            if pointer is None:
                raise LookupError("No shared dataset release has been published")
            version = pointer["release_id"]
            expected_hash = pointer["manifest_sha256"]
        release = canonical_id(version)
        path = self.cache / (release + ".json")
        with self.locked():
            if path.exists():
                try:
                    envelope = json.loads(path.read_bytes())
                    payload = envelope["text"].encode()
                    if len(payload) > MAX_JSON_BYTES:
                        raise ValueError("Cached manifest exceeds size limit")
                    manifest = self.checked_manifest(
                        payload, release, envelope["sha256"]
                    )
                    if (
                        expected_hash
                        and hashlib.sha256(payload).hexdigest() != expected_hash
                    ):
                        raise ValueError("Cached manifest differs from latest")
                    return manifest
                except (ValueError, KeyError, TypeError, AttributeError):
                    pass  # Repair corrupt metadata from S3; never trust it silently.
            try:
                response = self.get_object(
                    Bucket=self.storage.bucket,
                    Key=self.objects.key(f"releases/{release}.json"),
                )
            except ClientError as error:
                if error.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    raise LookupError(
                        "Shared release does not exist: " + release
                    ) from error
                raise
            with closing(response["Body"]) as stream:
                payload = stream.read(MAX_JSON_BYTES + 1)
            if len(payload) > MAX_JSON_BYTES:
                raise ValueError("Release manifest exceeds size limit")
            manifest = self.checked_manifest(payload, release, expected_hash)
            self.atomic_bytes(
                path,
                json_bytes(
                    {
                        "text": payload.decode(),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ),
            )
            return manifest

    def info(self, manifest, stage="prepared"):
        if stage not in ("raw", "prepared"):
            raise ValueError("stage must be raw or prepared")
        item = manifest["raw" if stage == "raw" else "workable"]
        return {
            "source": "s3",
            "stage": stage,
            "release_id": manifest["release_id"],
            "publisher_id": manifest["publisher_id"],
            "created_at": manifest["created_at"],
            "dataset_uri": self.storage.shared_dataset_uri,
            "raw_revision_id": manifest["raw"]["revision_id"],
            "artifact_id": manifest["workable"]["snapshot_id"],
            "schema_version": "raw-unified"
            if stage == "raw"
            else manifest["workable"]["schema_version"],
            "row_count": item["row_count"],
            "file_sha256": item["sha256"],
            "size_bytes": item["size_bytes"],
            "columns": item["columns"],
            **{
                k: manifest["lineage"][k]
                for k in ("run_id", "mlflow_run_id", "rules", "project_identity")
            },
            **(
                {"content_sha256": item["content_sha256"]}
                if stage == "prepared"
                else {}
            ),
        }

    def resolve(self, version="latest", *, stage="prepared"):
        return self.info(self.manifest(version), stage)

    def validate_frame(self, path, item, stage):
        if (
            path.stat().st_size != item["size_bytes"]
            or file_hash(path) != item["sha256"]
        ):
            raise ValueError("Dataset file checksum or size differs")
        schema = pq.read_schema(path)
        if [(f.name, str(f.type)) for f in schema] != [
            (c["name"], c["arrow_type"]) for c in item["columns"]
        ]:
            raise ValueError("Dataset Arrow schema differs")
        frame = pd.read_parquet(path)
        if len(frame) != item["row_count"] or [str(d) for d in frame.dtypes] != [
            c["pandas_dtype"] for c in item["columns"]
        ]:
            raise ValueError("Dataset row count or pandas schema differs")
        if stage == "prepared" and frame_hash(frame) != item["content_sha256"]:
            raise ValueError("Workable content checksum differs")
        return frame

    def read(self, version="latest", *, stage="prepared"):
        if stage not in ("raw", "prepared"):
            raise ValueError("stage must be raw or prepared")
        manifest = self.manifest(version)
        item = manifest["raw" if stage == "raw" else "workable"]
        identity = hashlib.sha256(json_bytes([item["key"], item["sha256"]])).hexdigest()
        path = self.cache / (identity + ".parquet")
        with self.locked():
            frame = None
            if path.exists():
                try:
                    frame = self.validate_frame(path, item, stage)
                except (ValueError, OSError):
                    pass
            if frame is None:
                temporary = None
                try:
                    response = self.get_object(
                        Bucket=self.storage.bucket, Key=item["key"]
                    )
                    with (
                        closing(response["Body"]) as body,
                        tempfile.NamedTemporaryFile(
                            dir=self.cache, delete=False
                        ) as stream,
                    ):
                        temporary = Path(stream.name)
                        size = 0
                        while block := body.read(1024**2):
                            size += len(block)
                            if size > item["size_bytes"]:
                                raise ValueError(
                                    "Dataset download exceeds declared size"
                                )
                            stream.write(block)
                        stream.flush()
                        os.fsync(stream.fileno())
                    frame = self.validate_frame(temporary, item, stage)
                    temporary.replace(path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        return Snapshot(frame, self.info(manifest, stage))
