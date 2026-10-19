"""Versioned, model-free identities shared by embedding producers and consumers."""

import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd

SCHEMA_VERSION = "voc-embeddings-v1"
REVIEW_COLUMNS = (
    "record_id",
    "source_row",
    "vector_row",
    "model_text",
    "embedding_text",
    "source_text_hash",
    "feature_text_hash",
)


def canonical_json(value) -> bytes:
    """UTF-8 JSON; object keys sort, arrays preserve order, nonfinite floats fail."""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def fingerprint(value) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def text_hash(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("Text must be a string; no implicit coercion")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ordered_rows_hash(rows: Iterable[tuple]) -> str:
    """Hash a row stream with length framing; no whole-corpus JSON allocation."""
    digest = hashlib.sha256(b"voc-ordered-rows-v1\0")
    for row in rows:
        payload = canonical_json(list(row))
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def require_digest(value, field="digest"):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def require_uuid(value, field="release_id"):
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{field} must be a canonical UUID")


def require_positive_int(value, field):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer")


def require_fields(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{label} must contain exactly: {', '.join(sorted(fields))}")


def require_key(value):
    """Only portable, relative object-key segments; no URI or host paths."""
    if (
        not isinstance(value, str)
        or not value
        or any(
            part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part)
            for part in value.split("/")
        )
    ):
        raise ValueError("Artifact key must be an ordinary relative object key")


@dataclass(frozen=True)
class DatasetIdentity:
    release_id: str
    content_sha256: str
    file_sha256: str
    dataset_uri: str
    row_count: int
    schema_version: str = "workable-v1"
    source: str = "s3"
    stage: str = "prepared"

    def __post_init__(self):
        require_uuid(self.release_id, "dataset.release_id")
        require_digest(self.content_sha256, "dataset.content_sha256")
        require_digest(self.file_sha256, "dataset.file_sha256")
        require_positive_int(self.row_count, "dataset.row_count")
        if (self.schema_version, self.source, self.stage) != (
            "workable-v1",
            "s3",
            "prepared",
        ):
            raise ValueError("Embeddings require a shared workable-v1 prepared dataset")
        if not isinstance(self.dataset_uri, str):
            raise TypeError("dataset_uri must be an S3 URI")
        uri = urlsplit(self.dataset_uri)
        if (
            uri.scheme != "s3"
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", uri.netloc)
            or uri.query
            or uri.fragment
        ):
            raise ValueError(
                "dataset_uri must identify the shared S3 dataset namespace"
            )
        require_key(uri.path.removeprefix("/"))

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class InputSelection:
    dataset: DatasetIdentity
    row_count: int
    rows_sha256: str
    limit: int | None = None

    def __post_init__(self):
        if not isinstance(self.dataset, DatasetIdentity):
            raise TypeError("selection.dataset must be a DatasetIdentity")
        require_positive_int(self.row_count, "selection.row_count")
        require_digest(self.rows_sha256, "selection.rows_sha256")
        if self.limit is None:
            if self.row_count != self.dataset.row_count:
                raise ValueError("A full selection must cover every source row")
        else:
            require_positive_int(self.limit, "selection.limit")
            if self.limit > self.dataset.row_count or self.row_count != self.limit:
                raise ValueError(
                    "A subset must contain exactly limit rows within the source"
                )

    @property
    def kind(self):
        # An explicit limit is a test selection even when it equals the source count.
        return "full" if self.limit is None else "first-n"

    @property
    def promotable(self):
        return self.kind == "full"

    def descriptor(self):
        return {
            "kind": self.kind,
            "limit": self.limit,
            "row_count": self.row_count,
            "rows_sha256": self.rows_sha256,
        }

    @property
    def selection_id(self):
        return fingerprint(
            {
                "identity_schema": "voc-selection-v1",
                "dataset": self.dataset.as_dict(),
                "selection": self.descriptor(),
            }
        )

    def as_dict(self):
        return {**self.descriptor(), "selection_id": self.selection_id}


def feature_identity(selection_id, policy, rows_sha256):
    """rows_sha256 covers ordered (record_id, source hash, prepared hash) tuples."""
    require_digest(selection_id, "selection_id")
    require_digest(rows_sha256, "feature.rows_sha256")
    if not isinstance(policy, str) or not policy.strip():
        raise ValueError("A versioned preparation policy is required")
    return fingerprint(
        {
            "identity_schema": "voc-features-v1",
            "selection_id": selection_id,
            "policy": policy,
            "rows_sha256": rows_sha256,
        }
    )


def encoder_identity(descriptor):
    require_fields(
        descriptor,
        {"profile_id", "adapter", "software", "device", "dtype"},
        "encoder descriptor",
    )
    require_digest(descriptor["profile_id"], "encoder.profile_id")
    if not isinstance(descriptor["adapter"], str) or not descriptor["adapter"].strip():
        raise ValueError("A versioned encoder adapter is required")
    if descriptor["device"] != "cpu" or descriptor["dtype"] != "<f4":
        raise ValueError(
            "The initial encoder contract supports CPU little-endian float32"
        )
    software = descriptor["software"]
    if (
        not isinstance(software, dict)
        or not {"sentence-transformers", "transformers", "torch"} <= set(software)
        or any(
            not isinstance(k, str)
            or not k.strip()
            or not isinstance(v, str)
            or not v.strip()
            for k, v in software.items()
        )
    ):
        raise ValueError(
            "Record nonempty encoder software versions, including the three model libraries"
        )
    return fingerprint({"identity_schema": "voc-encoder-v1", "descriptor": descriptor})


@dataclass(frozen=True)
class EmbeddingBundle:
    """Future reader result: reviews.iloc[i] and vectors[i] refer to the same input.

    The dataclass prevents field reassignment, not mutation of a contained dataframe
    or array. Consumers must apply any filtering to both together.
    """

    reviews: "pd.DataFrame"
    vectors: "np.ndarray"
    info: dict
