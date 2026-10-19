"""Verified S3 embedding consumption with an atomic, independently usable cache."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from voc.datasets.manifest import frame_hash
from voc.storage import load_storage
from voc.storage.objects import ReleaseObjects, _missing

from .contracts import (
    REVIEW_COLUMNS,
    DatasetIdentity,
    canonical_json,
    fingerprint,
    require_digest,
    require_uuid,
)
from .export import sync_directory
from .manifest import MAX_JSON_BYTES, require_promotable, validate_manifest
from .namespace import scope_prefix
from .profiles import resolve_profile
from .validation import load_release


def _snapshot(dataset):
    """Check all source rows, not only the selected prefix, before any request."""
    try:
        identity = DatasetIdentity(
            **{key: dataset.info[key] for key in DatasetIdentity.__dataclass_fields__}
        )
        frame = dataset.data.copy(deep=True)
    except (KeyError, AttributeError, TypeError) as error:
        raise ValueError("A verified shared workable Snapshot is required") from error
    if not frame.columns.is_unique or not {"record_id", "model_text"}.issubset(
        frame.columns
    ):
        raise ValueError(
            "Snapshot requires unique columns including record_id and model_text"
        )
    if (
        set(REVIEW_COLUMNS)
        .difference({"record_id", "model_text"})
        .intersection(frame.columns)
    ):
        raise ValueError("Snapshot contains reserved embedding output columns")
    for field in ("record_id", "model_text"):
        if (
            not frame[field]
            .map(lambda value: isinstance(value, str) and bool(value.strip()))
            .all()
        ):
            raise ValueError(f"Snapshot {field} contains invalid values")
    if frame.record_id.duplicated().any():
        raise ValueError("Snapshot contains duplicate record IDs")
    if len(frame) != identity.row_count or frame_hash(frame) != identity.content_sha256:
        raise ValueError(
            "Snapshot content/row count differs from its pinned dataset identity"
        )
    return identity, frame[["record_id", "model_text"]].reset_index(drop=True)


def _bytes(path):
    with path.open("rb") as stream:
        payload = stream.read(MAX_JSON_BYTES + 1)
    if not 0 < len(payload) <= MAX_JSON_BYTES:
        raise ValueError("Embedding metadata exceeds size limit or is empty")
    return payload


def _write(path, payload):
    with path.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


class SharedEmbeddingReader:
    """A core-only consumer. Constructing it neither contacts S3 nor runs models."""

    def __init__(self, storage=None, *, client=None):
        self.storage = storage or load_storage(destination="local")
        if not self.storage.bucket or not self.storage.region:
            raise ValueError("Embedding reads require AWS_BUCKET and AWS_REGION")
        self._client = client

    def get_object(self, **kwargs):
        if self._client is None:
            self._client = boto3.client("s3", region_name=self.storage.region)
        return self._client.get_object(**kwargs)

    @contextmanager
    def _locked(self, cache):
        cache.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(cache / "cache.db", timeout=300)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _fetch_manifest(self, objects, release):
        try:
            response = self.get_object(
                Bucket=self.storage.bucket, Key=objects.key(f"releases/{release}.json")
            )
        except ClientError as error:
            if _missing(error):
                raise LookupError(
                    "Embedding release does not exist: " + release
                ) from error
            raise
        with closing(response["Body"]) as body:
            payload = body.read(MAX_JSON_BYTES + 1)
        if not 0 < len(payload) <= MAX_JSON_BYTES:
            raise ValueError("Embedding manifest exceeds size limit or is empty")
        return payload

    def _manifest(self, payload, release, expected_hash):
        if (
            expected_hash is not None
            and hashlib.sha256(payload).hexdigest() != expected_hash
        ):
            raise ValueError("Embedding manifest checksum mismatch")
        try:
            manifest = json.loads(payload)
            validate_manifest(manifest)
            if manifest["embedding_release_id"] != release:
                raise ValueError("Embedding manifest release identity mismatch")
        except (KeyError, TypeError, AttributeError, RecursionError) as error:
            raise ValueError("Invalid embedding release manifest") from error
        return manifest

    def _download(self, objects, artifact, path):
        try:
            response = self.get_object(
                Bucket=self.storage.bucket, Key=objects.key(artifact["key"])
            )
        except ClientError as error:
            if _missing(error):
                raise LookupError(
                    "Embedding artifact is missing: " + artifact["key"]
                ) from error
            raise
        digest, size = hashlib.sha256(), 0
        with closing(response["Body"]) as body, path.open("wb") as stream:
            while block := body.read(1024**2):
                size += len(block)
                if size > artifact["size_bytes"]:
                    raise ValueError("Embedding download exceeds declared size")
                digest.update(block)
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
        if size != artifact["size_bytes"] or digest.hexdigest() != artifact["sha256"]:
            raise ValueError("Embedding download checksum or size mismatch")

    def read(
        self,
        *,
        dataset,
        profile="minilm-verbatim-v1",
        version="latest",
        allow_subset=False,
    ):
        """Load a pinned or freshly resolved release reconciled to the full Snapshot.

        A valid pinned cache needs no credentials/network. Corrupt cached bytes are
        never returned: a complete replacement is downloaded and validated first.
        Failure leaves the old cache untouched. Latest always resolves online.
        """
        if type(allow_subset) is not bool:
            raise ValueError("allow_subset must be a boolean")
        if version != "latest":
            require_uuid(version, "embedding_release_id")
        selected_profile = resolve_profile(profile)
        identity, reviews = _snapshot(dataset)
        if identity.dataset_uri != self.storage.shared_dataset_uri:
            raise ValueError("Snapshot belongs to another shared dataset namespace")
        prefix = scope_prefix(
            self.storage, selected_profile.profile_id, identity.release_id
        )
        objects = ReleaseObjects(self, self.storage.bucket, prefix)
        namespace = fingerprint(
            {
                "bucket": self.storage.bucket,
                "region": self.storage.region,
                "scope": prefix,
            }
        )
        cache = self.storage.runtime_root / "cache/embeddings/releases" / namespace
        expected_hash = None
        latest = version == "latest"
        if latest:
            try:
                pointer, etag = objects.latest()
                if pointer is None:
                    if etag is not None:
                        raise ValueError("Invalid null latest embedding pointer")
                    raise LookupError(
                        "No full embedding release is published for this dataset/profile; use a pinned version and allow_subset=True for a smoke release"
                    )
                version, expected_hash = (
                    pointer["release_id"],
                    pointer["manifest_sha256"],
                )
            except (KeyError, TypeError, AttributeError) as error:
                raise ValueError("Invalid latest embedding pointer") from error
        destination = cache / version
        with self._locked(cache):
            payload = manifest = None
            if destination.exists():
                try:
                    receipt = json.loads(_bytes(destination / "verified.json"))
                    require_digest(receipt["manifest_sha256"], "cached manifest_sha256")
                    payload = _bytes(destination / "manifest.json")
                    manifest = self._manifest(
                        payload, version, receipt["manifest_sha256"]
                    )
                    if (
                        expected_hash is not None
                        and hashlib.sha256(payload).hexdigest() != expected_hash
                    ):
                        raise ValueError("Cached manifest differs from latest pointer")
                except (ValueError, OSError, KeyError, TypeError):
                    payload = manifest = None
            cached_manifest_verified = manifest is not None
            if manifest is None:
                payload = self._fetch_manifest(objects, version)
                manifest = self._manifest(payload, version, expected_hash)
            if (
                manifest["dataset"] != identity.as_dict()
                or manifest["profile"] != selected_profile.as_dict()
            ):
                raise ValueError(
                    "Embedding release differs from the supplied dataset/profile identity"
                )
            if latest:
                require_promotable(manifest)
            if manifest["selection"]["kind"] != "full" and not allow_subset:
                raise ValueError("Subset embedding release requires allow_subset=True")
            expected = reviews.iloc[: manifest["selection"]["row_count"]].copy()
            expected.insert(0, "source_row", range(len(expected)))
            digest = hashlib.sha256(payload).hexdigest()
            if cached_manifest_verified:
                try:
                    return load_release(
                        destination, manifest_sha256=digest, expected_reviews=expected
                    )
                except (ValueError, OSError):
                    pass  # Rebuild only from verified remote bytes, never stale vectors.
            with tempfile.TemporaryDirectory(
                prefix=".download-", dir=cache
            ) as temporary:
                staging = Path(temporary)
                for name, filename in (
                    ("vectors", "vectors.npy"),
                    ("reviews", "reviews.parquet"),
                ):
                    self._download(
                        objects, manifest["artifacts"][name], staging / filename
                    )
                _write(staging / "manifest.json", payload)
                checked = load_release(
                    staging, manifest_sha256=digest, expected_reviews=expected
                )
                del checked  # Close the mmap before renaming, including on Windows.
                _write(
                    staging / "verified.json",
                    canonical_json({"manifest_sha256": digest}),
                )
                sync_directory(staging)
                if destination.exists():
                    shutil.rmtree(
                        destination
                    )  # Invalid cache only; readers share this lock.
                staging.replace(destination)
                sync_directory(cache)
            return load_release(
                destination, manifest_sha256=digest, expected_reviews=expected
            )


def get_embeddings(
    *,
    dataset,
    profile="minilm-verbatim-v1",
    version="latest",
    source="s3",
    allow_subset=False,
):
    """Return verified canonical-order reviews, vectors and release metadata.

    Explicit subset releases require allow_subset=True. No inference or producer
    runtime is needed. Pass a full, unmodified shared workable Snapshot as dataset.
    """
    if source != "s3":
        raise ValueError("Embedding reads currently require source='s3'")
    return SharedEmbeddingReader().read(
        dataset=dataset, profile=profile, version=version, allow_subset=allow_subset
    )
