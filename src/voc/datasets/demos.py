"""Immutable demo snapshots with verified local caches. No model execution.

Only prototypeV0 is enabled. Dataset identity participates in the content ID,
so identical files cannot silently be relabelled as a different release.
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

import boto3

from voc.storage import load_storage
from voc.storage.objects import PublicationConflict, ReleaseObjects

from .manifest import (
    MAX_FILE_BYTES,
    MAX_JSON_BYTES,
    canonical_id,
    file_hash,
    json_bytes,
)

PROTOTYPE_FILES = {
    "prototypeV0": (
        "sample.parquet",
        "embeddings.npy",
        "embeddings.json",
        "reduced.npz",
        "reduced.json",
        "clusters.npz",
        "clusters.json",
        "representations.json",
        "labels.json",
        "sentiment.parquet",
        "sentiment.json",
    ),
}
SCHEMA = "voc-demo-snapshot-v1"


class DemoReleaseMismatch(ValueError):
    """A valid checkpoint belongs to a different dataset release."""


def snapshot_id(release_id, files):
    pairs = sorted((item["name"], item["sha256"]) for item in files)
    return hashlib.sha256(json_bytes([release_id, pairs])).hexdigest()[:16]


def checked_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{16}", value):
        raise ValueError("Expected a 16-character demo snapshot ID")
    return value


def validate_manifest(value, prototype, version, release_id):
    """Validate before constructing local paths or downloading artifact files."""
    try:
        if (
            value["schema_version"] != SCHEMA
            or value["prototype"] != prototype
            or value["snapshot_id"] != version
        ):
            raise ValueError("Demo manifest identity differs")
        canonical_id(value["dataset_release_id"])
        if value["dataset_release_id"] != release_id:
            raise DemoReleaseMismatch(
                "Demo dataset release differs from the notebook release"
            )
        files = value["files"]
        names = [item["name"] for item in files]
        if len(names) != len(set(names)) or set(names) != set(
            PROTOTYPE_FILES[prototype]
        ):
            raise ValueError("Demo must contain exactly the expected prototype files")
        for item in files:
            if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                raise ValueError("Invalid demo file checksum")
            if (
                type(item["size_bytes"]) is not int
                or not 0 < item["size_bytes"] <= MAX_FILE_BYTES
            ):
                raise ValueError("Invalid demo file size")
        if sum(item["size_bytes"] for item in files) > MAX_FILE_BYTES:
            raise ValueError("Demo exceeds the 1 GiB total limit")
        if snapshot_id(release_id, files) != version:
            raise ValueError("Demo content does not match its snapshot ID")
        if not isinstance(value["keys"], dict):
            raise TypeError("Invalid demo stage metadata")
        datetime.fromisoformat(value["created_at"])
        if not isinstance(value["publisher_id"], str) or not value["publisher_id"]:
            raise ValueError("Demo publisher is missing")
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Invalid demo manifest") from error
    return value


class DemoObjects(ReleaseObjects):
    """Use the existing conditional-write operations with the demo pointer format."""

    def latest(self):
        value, etag = self.get_json("latest.json")
        if value is not None:
            try:
                version = checked_id(value["snapshot_id"])
                if (
                    value["schema_version"] != SCHEMA
                    or value["manifest_key"]
                    != self.key(f"snapshots/{version}/manifest.json")
                    or not re.fullmatch(r"[0-9a-f]{64}", value["manifest_sha256"])
                ):
                    raise ValueError("Invalid demo latest pointer")
            except (KeyError, TypeError) as error:
                raise ValueError("Invalid demo latest pointer") from error
        return value, etag


@contextmanager
def folder_lock(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder / "cache.db", timeout=120)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield
            db.commit()
        except BaseException:
            db.rollback()
            raise


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def file_matches(path, item):
    return (
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_size == item["size_bytes"]
        and file_hash(path) == item["sha256"]
    )


class DemoSnapshots:
    def __init__(self, prototype, storage=None, *, client=None):
        if prototype not in PROTOTYPE_FILES:
            raise ValueError("Only prototypeV0 demo snapshots are supported")
        self.prototype = prototype
        self.storage = storage or load_storage(destination="local")
        if not self.storage.bucket or not self.storage.region:
            raise ValueError("Demo snapshots require AWS_BUCKET and AWS_REGION")
        self._client = client
        self.objects = DemoObjects(
            self, self.storage.bucket, f"{self.storage.demos_prefix}/{prototype}"
        )
        namespace = hashlib.sha256(
            json_bytes([self.storage.bucket, self.storage.demos_prefix])
        ).hexdigest()
        self.cache = self.storage.runtime_root / "cache/demos" / namespace / prototype

    def client(self):
        # Pinned verified reads can work without network access or credentials.
        if self._client is None:
            self._client = boto3.client("s3", region_name=self.storage.region)
        return self._client

    def get_object(self, **kwargs):
        return self.client().get_object(**kwargs)

    def put_object(self, **kwargs):
        return self.client().put_object(**kwargs)

    def fetch(self, version="latest", *, release_id):
        canonical_id(release_id)
        expected = None
        if version == "latest":
            pointer, _ = (
                self.objects.latest()
            )  # Never silently use stale latest offline.
            if pointer is None:
                raise LookupError("No demo snapshot has been published")
            version, expected = pointer["snapshot_id"], pointer["manifest_sha256"]
        checked_id(version)
        folder = self.cache / version
        with folder_lock(self.cache):
            folder.mkdir(exist_ok=True)
            manifest_path = folder / "manifest.json"
            manifest = None
            if manifest_path.exists():
                try:
                    envelope = json.loads(manifest_path.read_bytes())
                    payload = envelope["text"].encode()
                    digest = hashlib.sha256(payload).hexdigest()
                    if (
                        len(payload) > MAX_JSON_BYTES
                        or digest != envelope["sha256"]
                        or (expected and digest != expected)
                    ):
                        raise ValueError("Cached demo manifest checksum differs")
                    manifest = validate_manifest(
                        json.loads(payload), self.prototype, version, release_id
                    )
                except DemoReleaseMismatch:
                    raise
                except (ValueError, KeyError, TypeError):
                    pass  # Invalid cache metadata is repaired from S3, never trusted.
            if manifest is None:
                manifest, _ = self.objects.get_json(
                    f"snapshots/{version}/manifest.json"
                )
                if manifest is None:
                    raise LookupError("Demo snapshot does not exist: " + version)
                payload = json_bytes(manifest)
                digest = hashlib.sha256(payload).hexdigest()
                if expected and digest != expected:
                    raise ValueError("Demo manifest checksum differs from latest")
                validate_manifest(manifest, self.prototype, version, release_id)
                atomic_bytes(
                    manifest_path,
                    json_bytes({"text": payload.decode(), "sha256": digest}),
                )
            for item in manifest["files"]:
                target = folder / item["name"]
                if file_matches(target, item):
                    continue
                response = self.get_object(
                    Bucket=self.storage.bucket,
                    Key=self.objects.key(f"snapshots/{version}/{item['name']}"),
                )
                temporary = None
                try:
                    with (
                        closing(response["Body"]) as body,
                        tempfile.NamedTemporaryFile(dir=folder, delete=False) as stream,
                    ):
                        temporary = Path(stream.name)
                        count = 0
                        while block := body.read(1024**2):
                            count += len(block)
                            if count > item["size_bytes"]:
                                raise ValueError("Demo download exceeds declared size")
                            stream.write(block)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if not file_matches(temporary, item):
                        raise ValueError("Demo file checksum or size differs")
                    temporary.replace(target)
                finally:
                    if temporary:
                        temporary.unlink(missing_ok=True)
        return folder

    def publish(self, folder, *, release_id, keys=None):
        """Freeze files, upload immutably, then promote with a durable retry token.

        A retry of an older successful snapshot cannot roll back a newer latest.
        The local attempt journal also preserves the first ETag across failures.
        """
        canonical_id(release_id)
        if not self.storage.member_id:
            raise ValueError("Set VOC_MEMBER_ID before publishing a demo")
        folder = Path(folder)
        with (
            folder_lock(self.cache),
            tempfile.TemporaryDirectory(dir=self.cache) as temporary,
        ):
            frozen = Path(temporary)
            files = []
            for name in PROTOTYPE_FILES[self.prototype]:
                source = folder / name
                if not source.is_file() or source.is_symlink():
                    raise ValueError(
                        "Complete the prototype first; missing regular file: " + name
                    )
                if source.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError("Demo file exceeds size limit")
                shutil.copyfile(source, frozen / name)
                files.append(
                    {
                        "name": name,
                        "sha256": file_hash(frozen / name),
                        "size_bytes": (frozen / name).stat().st_size,
                    }
                )
            version = snapshot_id(release_id, files)
            current, etag = self.objects.latest()
            remote, _ = self.objects.get_json(f"snapshots/{version}/manifest.json")
            attempt_path = self.cache / (version + ".attempt.json")
            if attempt_path.exists():
                attempt = json.loads(attempt_path.read_bytes())
                manifest, expected_etag = attempt["manifest"], attempt["expected_etag"]
                if manifest["files"] != files or manifest["keys"] != (keys or {}):
                    raise ValueError(
                        "Saved demo attempt differs; restore its original metadata"
                    )
            elif remote is not None:
                manifest, expected_etag = remote, etag
                if current is None or current["snapshot_id"] != version:
                    raise PublicationConflict(
                        "This snapshot was already published; refusing to move latest backwards"
                    )
                if manifest["files"] != files or manifest["keys"] != (keys or {}):
                    raise ValueError("Existing demo metadata differs")
            else:
                expected_etag = etag
                manifest = {
                    "schema_version": SCHEMA,
                    "prototype": self.prototype,
                    "snapshot_id": version,
                    "dataset_release_id": release_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "publisher_id": self.storage.member_id,
                    "notebook": "notebooks/demos/workflow01.py",
                    "files": files,
                    "keys": keys or {},
                }
                atomic_bytes(
                    attempt_path,
                    json_bytes({"manifest": manifest, "expected_etag": etag}),
                )
            validate_manifest(manifest, self.prototype, version, release_id)
            payload = json_bytes(manifest)
            if len(payload) > MAX_JSON_BYTES:
                raise ValueError("Demo manifest exceeds size limit")
            pointer = {
                "schema_version": SCHEMA,
                "snapshot_id": version,
                "manifest_key": self.objects.key(f"snapshots/{version}/manifest.json"),
                "manifest_sha256": hashlib.sha256(payload).hexdigest(),
            }
            if current != pointer and etag != expected_etag:
                raise PublicationConflict(
                    "Demo latest changed since this attempt; refusing a stale retry"
                )
            for item in files:
                self.objects.put_immutable(
                    f"snapshots/{version}/{item['name']}", frozen / item["name"]
                )
            atomic_bytes(frozen / "manifest.json", payload)
            self.objects.put_immutable(
                f"snapshots/{version}/manifest.json", frozen / "manifest.json"
            )
            self.objects.promote(pointer, expected_etag)
            return version
