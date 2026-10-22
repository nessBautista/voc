"""Pin published inputs, then select reviews and vectors using one ordered index.

The public reader owns S3 downloads and release verification. This module checks
mutable in-memory inputs again and records checksums for subsequent workflow steps.
No encoder, topic model, sentiment model or orchestration service is imported.
"""

import hashlib
from copy import deepcopy
from dataclasses import dataclass

import numpy as np
import pandas as pd

from voc.analysis.contracts import INPUT_SCHEMA, validate_input_identity
from voc.datasets.manifest import frame_hash
from voc.embeddings.contracts import (
    REVIEW_COLUMNS,
    DatasetIdentity,
    fingerprint,
    ordered_rows_hash,
    text_hash,
)
from voc.embeddings.manifest import validate_manifest

from .config import SelectionConfig


class _HashWriter:
    def __init__(self):
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, payload):
        self.digest.update(payload)
        self.size += len(payload)
        return len(payload)

    def flush(self):
        pass


def _npy_identity(vectors):
    """Hash the canonical np.save encoding used by the existing release writer.

    Stream to a digest instead of allocating a second full NPY byte buffer. This
    binds vector values AND row order to the already verified release checksum.
    """
    sink = _HashWriter()
    np.save(sink, vectors, allow_pickle=False)
    return sink.digest.hexdigest(), sink.size


def _validate_bundle(snapshot, bundle):
    validate_manifest(bundle.info)
    manifest = bundle.info
    identity = DatasetIdentity(
        **{k: snapshot.info[k] for k in DatasetIdentity.__dataclass_fields__}
    )
    if identity.as_dict() != manifest["dataset"]:
        raise ValueError("Dataset and embedding release identities differ")
    frame, reviews, vectors = snapshot.data, bundle.reviews, bundle.vectors
    if (
        not frame.columns.is_unique
        or not {"record_id", "model_text", "platform", "record_date"}
        <= set(frame.columns)
        or len(frame) != identity.row_count
    ):
        raise ValueError(
            "Dataset requires complete review identity, text, platform and date metadata"
        )
    for source in (frame, reviews):
        if not source.columns.is_unique or not {"record_id", "model_text"} <= set(
            source.columns
        ):
            raise ValueError("Invalid review columns")
        for name in ("record_id", "model_text"):
            if (
                not source[name]
                .map(lambda v: isinstance(v, str) and bool(v.strip()))
                .all()
            ):
                raise ValueError(f"Invalid {name}")
        if source.record_id.duplicated().any():
            raise ValueError("Duplicate review IDs")
    count = manifest["selection"]["row_count"]
    if list(reviews.columns) != list(REVIEW_COLUMNS) or len(reviews) != count:
        raise ValueError("Embedding review schema or coverage mismatch")
    for name in ("source_row", "vector_row"):
        if not pd.api.types.is_integer_dtype(reviews[name]) or reviews[
            name
        ].tolist() != list(range(count)):
            raise ValueError("Embedding row order was changed")
    for name, hash_name in (
        ("model_text", "source_text_hash"),
        ("embedding_text", "feature_text_hash"),
    ):
        if (
            not reviews[name]
            .map(lambda v: isinstance(v, str) and bool(v.strip()))
            .all()
        ):
            raise ValueError("Invalid embedding text")
        if not reviews[name].map(text_hash).eq(reviews[hash_name]).all():
            raise ValueError("Embedding text checksum mismatch")
    if not reviews.model_text.eq(reviews.embedding_text).all():
        raise ValueError("The registered verbatim profile must preserve source text")
    for columns, expected in (
        (
            ["source_row", "record_id", "source_text_hash"],
            manifest["selection"]["rows_sha256"],
        ),
        (
            ["record_id", "source_text_hash", "feature_text_hash"],
            manifest["feature"]["rows_sha256"],
        ),
    ):
        if (
            ordered_rows_hash(reviews[columns].itertuples(index=False, name=None))
            != expected
        ):
            raise ValueError("Embedding review mapping checksum mismatch")
    if reviews.feature_text_hash.nunique() != manifest["coverage"]["unique_texts"]:
        raise ValueError("Embedding unique-text coverage mismatch")
    if not set(reviews.record_id) <= set(frame.record_id):
        raise ValueError("Embedding review is missing from dataset metadata")
    # A caller may reorder metadata after reading. For a full bundle its canonical
    # order is recoverable by ID. A subset cannot establish the omitted rows' order.
    indexed = frame.set_index("record_id", drop=False)
    canonical = (
        indexed.loc[reviews.record_id].reset_index(drop=True)
        if count == len(frame)
        else frame
    )
    if frame_hash(canonical) != identity.content_sha256:
        raise ValueError("Dataset metadata differs from its pinned content identity")
    aligned = indexed.loc[reviews.record_id].reset_index(drop=True)
    if aligned.model_text.tolist() != reviews.model_text.tolist():
        raise ValueError("Review text differs between dataset and embeddings")
    if (
        count != len(frame)
        and frame.record_id.iloc[:count].tolist() != reviews.record_id.tolist()
    ):
        raise ValueError("Subset embeddings differ from their source prefix")
    config, artifact = manifest["profile"]["config"], manifest["artifacts"]["vectors"]
    if (
        not isinstance(vectors, np.ndarray)
        or vectors.shape != (count, config["dimensions"])
        or vectors.dtype.str != config["dtype"]
        or not vectors.flags.c_contiguous
    ):
        raise ValueError("Invalid embedding vector shape, dtype or order")
    if _npy_identity(vectors) != (artifact["sha256"], artifact["size_bytes"]):
        raise ValueError(
            "Embedding vector content/order differs from published checksum"
        )
    for start in range(0, count, 4096):
        batch = vectors[start : start + 4096]
        if not np.isfinite(batch).all() or (
            config["normalize"]
            and not np.allclose(np.linalg.norm(batch, axis=1), 1, atol=1e-4, rtol=0)
        ):
            raise ValueError(
                "Embedding vectors must be finite and correctly normalized"
            )
    # Source text remains model_text for sentiment, independent of embedding policy.
    # Preserve schema order so input checksums remain stable across processes.
    for name in (c for c in REVIEW_COLUMNS if c not in {"record_id", "model_text"}):
        if name in aligned:
            raise ValueError("Dataset metadata contains a reserved embedding column")
        aligned[name] = reviews[name].to_numpy(copy=True)
    return aligned


def _strata(reviews):
    # UTC months; missing or unparseable dates have an explicit null bucket.
    dates = pd.to_datetime(
        reviews.record_date, errors="coerce", utc=True, format="mixed"
    )
    groups = {}
    for position, (platform, date) in enumerate(zip(reviews.platform, dates)):
        if pd.isna(platform) or platform == "":
            platform = None
        elif not isinstance(platform, str) or not platform.strip():
            raise ValueError("Platform must be text or missing")
        month = None if pd.isna(date) else date.strftime("%Y-%m")
        groups.setdefault((platform, month), []).append(position)
    return dict(
        sorted(groups.items(), key=lambda pair: tuple(v or "" for v in pair[0]))
    )


def _positions(groups, count, selection):
    size = count if selection.kind == "full" else selection.size
    if size > count:
        raise ValueError("Requested sample exceeds available embedding rows")
    if selection.kind != "stratified":
        return np.arange(size, dtype=np.int64)
    # Proportional largest remainder, with ties broken by sorted platform/month.
    # No minimum per stratum: a small sample can miss rare strata. Record zeros.
    keys = list(groups)
    quotients = [divmod(size * len(groups[k]), count) for k in keys]
    allocation = [q for q, _ in quotients]
    remaining = size - sum(allocation)
    for i in sorted(range(len(keys)), key=lambda i: (-quotients[i][1], i))[:remaining]:
        allocation[i] += 1
    rng = np.random.Generator(np.random.PCG64(selection.seed))
    positions = [
        int(p)
        for k, n in zip(keys, allocation)
        for p in rng.choice(groups[k], size=n, replace=False)
    ]
    # Preserve published order, rather than using randomized draw order for fitting.
    return np.array(sorted(positions), dtype=np.int64)


@dataclass(frozen=True)
class AnalysisInput:
    reviews: pd.DataFrame
    vectors: np.ndarray
    info: dict

    def validate(self):
        """Detect mutated metadata, selected texts, or reordered vectors before use.

        Saving info with the selected artifacts preserves these checks across
        steps/restarts. Checksums detect corruption, not a wholesale malicious rewrite.
        """
        validate_input_identity(self.info)
        if (
            not self.reviews.columns.is_unique
            or not {
                "record_id",
                "model_text",
                "embedding_text",
                "platform",
                "record_date",
                "vector_row",
            }
            <= set(self.reviews.columns)
            or self.reviews.record_id.tolist() != self.info["selection"]["record_ids"]
            or self.reviews.vector_row.tolist() != self.info["selection"]["positions"]
            or frame_hash(self.reviews) != self.info["reviews_sha256"]
        ):
            raise ValueError("Pinned analysis reviews changed or lost alignment")
        if (
            not isinstance(self.vectors, np.ndarray)
            or list(self.vectors.shape) != self.info["vector_shape"]
            or self.vectors.dtype.str != self.info["vector_dtype"]
            or not self.vectors.flags.c_contiguous
            or _npy_identity(self.vectors)[0] != self.info["vectors_sha256"]
        ):
            raise ValueError("Pinned analysis vectors changed or lost alignment")


def select_inputs(snapshot, bundle, *, selection=None):
    """Select only after reading the original full snapshot and verified bundle.

    Metadata joins by record_id. The same positional array slices reviews/vectors.
    All returned mutable data is copied, so selection never edits source inputs.
    """
    selection = SelectionConfig() if selection is None else selection
    if not isinstance(selection, SelectionConfig):
        raise TypeError("selection must be SelectionConfig")
    reviews = _validate_bundle(snapshot, bundle)
    groups = _strata(reviews)
    positions = _positions(groups, len(reviews), selection)
    chosen = set(positions.tolist())
    counts = [
        {
            "platform": k[0],
            "month": k[1],
            "available": len(rows),
            "selected": sum(i in chosen for i in rows),
        }
        for k, rows in groups.items()
    ]
    selected = reviews.iloc[positions].copy(deep=True).reset_index(drop=True)
    vectors = np.array(bundle.vectors[positions], dtype="<f4", order="C", copy=True)
    vectors.flags.writeable = False
    meta = bundle.info
    ids = selected.record_id.tolist()
    info = {
        "schema_version": INPUT_SCHEMA,
        "dataset": deepcopy(meta["dataset"]),
        "embedding": {
            "release_id": meta["embedding_release_id"],
            "manifest_sha256": fingerprint(meta),
            "profile_id": meta["profile"]["profile_id"],
            "profile_name": meta["profile"]["name"],
            "feature_id": meta["feature"]["feature_id"],
            "encoder_id": meta["encoder"]["encoder_id"],
            "selection_kind": meta["selection"]["kind"],
            "row_count": len(reviews),
        },
        "selection": {
            "kind": selection.kind,
            "size": len(selected),
            "seed": selection.seed,
            "allocation_policy": "platform-month-proportional-largest-remainder-v1"
            if selection.kind == "stratified"
            else "bundle-order-v1",
            "record_ids": ids,
            "positions": positions.tolist(),
            "ordered_ids_sha256": fingerprint(ids),
            "strata": counts,
        },
        "reviews_sha256": frame_hash(selected),
        "vectors_sha256": _npy_identity(vectors)[0],
        "vector_shape": list(vectors.shape),
        "vector_dtype": vectors.dtype.str,
    }
    info["input_id"] = fingerprint(info)
    result = AnalysisInput(selected, vectors, info)
    result.validate()
    return result


def sample_pinned_input(inputs, *, size, seed):
    """Subsample a verified full input without downloading or encoding again.

    Allocation is proportional by platform and UTC month, including missing
    values. The returned review/vector order remains the published order. A
    previously sampled input is refused: sampling it again requires a different
    allocation policy and explicit sampling lineage.
    """
    inputs.validate()
    if inputs.info["selection"]["kind"] != "full":
        raise ValueError("Resampling requires a full pinned input, not a prior sample")
    selection = SelectionConfig("stratified", size, seed)
    if size >= len(inputs.reviews):
        raise ValueError("Sample size must be smaller than the parent input")
    groups = _strata(inputs.reviews)
    positions = _positions(groups, len(inputs.reviews), selection)
    chosen = set(positions.tolist())
    reviews = inputs.reviews.iloc[positions].copy(deep=True).reset_index(drop=True)
    vectors = np.array(inputs.vectors[positions], dtype="<f4", order="C", copy=True)
    vectors.flags.writeable = False
    info = deepcopy(inputs.info)
    info.pop("input_id")
    info["selection"] = {
        "kind": "stratified",
        "size": size,
        "seed": seed,
        "allocation_policy": "platform-month-proportional-largest-remainder-v1",
        "record_ids": reviews.record_id.tolist(),
        "positions": reviews.vector_row.tolist(),
        "ordered_ids_sha256": fingerprint(reviews.record_id.tolist()),
        "strata": [
            {
                "platform": key[0],
                "month": key[1],
                "available": len(rows),
                "selected": sum(i in chosen for i in rows),
            }
            for key, rows in groups.items()
        ],
    }
    info.update(
        reviews_sha256=frame_hash(reviews),
        vectors_sha256=_npy_identity(vectors)[0],
        vector_shape=list(vectors.shape),
        vector_dtype=vectors.dtype.str,
    )
    info["input_id"] = fingerprint(info)
    result = AnalysisInput(reviews, vectors, info)
    result.validate()
    return result


def pin_inputs(
    *,
    dataset_version="latest",
    embedding_version="latest",
    profile="minilm-verbatim-v1",
    source="s3",
    selection=None,
    allow_subset=False,
):
    """Resolve moving releases once. No encoding, inference or publication occurs."""
    selection = SelectionConfig() if selection is None else selection
    if not isinstance(selection, SelectionConfig) or source != "s3":
        raise ValueError("Use a SelectionConfig and source=s3")
    from voc import collector
    from voc.embeddings import get_embeddings

    snapshot = collector.get_dataset(
        stage="prepared", source=source, version=dataset_version
    )
    bundle = get_embeddings(
        dataset=snapshot,
        profile=profile,
        version=embedding_version,
        source=source,
        allow_subset=allow_subset,
    )
    return select_inputs(snapshot, bundle, selection=selection)
