"""Portable embedding artifact checks. No producer, model or tracking dependencies."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from voc.datasets.manifest import file_hash

from .contracts import REVIEW_COLUMNS, EmbeddingBundle, ordered_rows_hash, text_hash
from .manifest import MAX_JSON_BYTES, validate_manifest


def validate_release(directory, manifest, *, expected_reviews=None):
    """Validate bytes and row semantics; optionally reconcile pinned source rows.

    The directory holds the two artifact basenames; object keys remain scope-relative
    in the manifest. Metadata is trusted only after the caller pins its provenance or
    checksum. This detects corruption, not an attacker replacing an entire release.
    """
    validate_manifest(manifest)
    directory = Path(directory)
    for name, filename in (("vectors", "vectors.npy"), ("reviews", "reviews.parquet")):
        item = manifest["artifacts"][name]
        path = directory / filename
        if (
            path.stat().st_size != item["size_bytes"]
            or file_hash(path) != item["sha256"]
        ):
            raise ValueError(f"Release {name} checksum or size mismatch")

    reviews = pd.read_parquet(directory / "reviews.parquet")
    count = manifest["selection"]["row_count"]
    if list(reviews.columns) != list(REVIEW_COLUMNS) or len(reviews) != count:
        raise ValueError("Release review columns or row count mismatch")
    for field in ("record_id", "model_text", "embedding_text"):
        if (
            not reviews[field]
            .map(lambda v: isinstance(v, str) and bool(v.strip()))
            .all()
        ):
            raise ValueError(f"Release {field} must contain nonblank strings")
    if reviews.record_id.duplicated().any():
        raise ValueError("Release contains duplicate review IDs")
    for field in ("source_row", "vector_row"):
        if (
            not pd.api.types.is_integer_dtype(reviews[field].dtype)
            or reviews[field].isna().any()
            or reviews[field].tolist() != list(range(count))
        ):
            raise ValueError(f"Release {field} must preserve contiguous source order")
    if not reviews.model_text.map(text_hash).eq(reviews.source_text_hash).all():
        raise ValueError("Release source text hashes mismatch")
    if not reviews.embedding_text.map(text_hash).eq(reviews.feature_text_hash).all():
        raise ValueError("Release feature text hashes mismatch")
    # The manifest accepts only the registered verbatim profile. Future policies
    # must define their portable text validation here alongside their profile.
    if (
        manifest["feature"]["policy"] != "verbatim-v1"
        or not reviews.embedding_text.eq(reviews.model_text).all()
    ):
        raise ValueError("Release text differs from its preparation policy")
    source_digest = ordered_rows_hash(
        reviews[["source_row", "record_id", "source_text_hash"]].itertuples(
            index=False, name=None
        )
    )
    if source_digest != manifest["selection"]["rows_sha256"]:
        raise ValueError("Release source selection digest mismatch")
    feature_digest = ordered_rows_hash(
        reviews[["record_id", "source_text_hash", "feature_text_hash"]].itertuples(
            index=False, name=None
        )
    )
    if feature_digest != manifest["feature"]["rows_sha256"]:
        raise ValueError("Release feature row digest mismatch")
    if reviews.feature_text_hash.nunique() != manifest["coverage"]["unique_texts"]:
        raise ValueError("Release unique text count mismatch")
    if expected_reviews is not None:
        for field in ("record_id", "source_row", "model_text"):
            if reviews[field].tolist() != expected_reviews[field].tolist():
                raise ValueError(
                    f"Release {field} differs from the pinned source reviews"
                )

    # mmap avoids allocating memory from untrusted NPY dimensions before checks.
    vectors = np.load(directory / "vectors.npy", allow_pickle=False, mmap_mode="r")
    config = manifest["profile"]["config"]
    if vectors.dtype.str != config["dtype"] or vectors.shape != (
        count,
        config["dimensions"],
    ):
        raise ValueError("Release vector dtype or dimensions mismatch")
    if not vectors.flags.c_contiguous:
        raise ValueError("Release vectors must use C order")
    for start in range(0, count, 4096):
        batch = vectors[start : start + 4096]
        if not np.isfinite(batch).all():
            raise ValueError("Release contains non-finite vectors")
        if config["normalize"] and not np.allclose(
            np.linalg.norm(batch, axis=1), 1.0, atol=1e-4, rtol=0
        ):
            raise ValueError("Release vector normalization mismatch")
    return EmbeddingBundle(reviews, vectors, manifest)


def load_release(directory, *, manifest_sha256=None, expected_reviews=None):
    """Read a local portable release, optionally checking a previously pinned hash."""
    path = Path(directory) / "manifest.json"
    if not 0 < path.stat().st_size <= MAX_JSON_BYTES:
        raise ValueError("Release manifest exceeds metadata size limit")
    if manifest_sha256 is not None and file_hash(path) != manifest_sha256:
        raise ValueError("Release manifest checksum mismatch")
    manifest = json.loads(path.read_bytes())
    return validate_release(directory, manifest, expected_reviews=expected_reviews)
