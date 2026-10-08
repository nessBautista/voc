"""Encode prepared features with cached reuse and save a local checkpoint."""

from datetime import UTC

import pandas as pd


def _peak_process_rss():
    """Process-lifetime high water, not a per-run memory delta; unavailable off Unix."""
    import sys

    try:
        import resource
    except ImportError:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def encode_features(
    feature_reference,
    *,
    output_dir,
    cache_path,
    model_dir,
    profile_name="minilm-verbatim-v1",
    batch_size=32,
    local_files_only=False,
):
    """Encode verified features and save a local checkpoint; never publish a release.

    Completed cache batches survive failure. output_dir must be a new directory.
    A warm run resolves installed version metadata but does not load a model/tokenizer.
    """
    import json
    import os
    import tempfile
    import time
    from datetime import datetime
    from pathlib import Path
    from uuid import uuid4

    import numpy as np

    from voc.datasets.manifest import file_hash
    from voc.embeddings.contracts import (
        REVIEW_COLUMNS,
        canonical_json,
        encoder_identity,
    )
    from voc.embeddings.profiles import resolve_profile

    from .cache import EmbeddingCache, validate_vectors
    from .features import read_features
    from .sentence_encoder import SentenceEncoder, encoder_descriptor

    started = time.perf_counter()
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise FileExistsError(f"Encoding output already exists: {destination}")
    profile = resolve_profile(profile_name)
    frame = read_features(feature_reference)
    feature_report = json.loads(
        (Path(feature_reference["directory"]) / "feature-report.json").read_bytes()
    )
    if feature_report["feature"]["policy"] != profile.preparation_policy:
        raise ValueError("Features were prepared for a different profile policy")
    descriptor = encoder_descriptor(profile)
    encoder_id = encoder_identity(descriptor)
    unique = {}
    positions = {}
    for row, (key, text) in enumerate(
        zip(frame.feature_text_hash, frame.embedding_text, strict=True)
    ):
        if key in unique and unique[key] != text:
            raise ValueError("Different prepared texts share a hash")
        unique[key] = text
        positions.setdefault(key, []).append(row)
    matrix = np.empty((len(frame), profile.dimensions), dtype="<f4")
    tokens = {}
    missing = []
    encoding_calls = 0
    model = None
    with EmbeddingCache(cache_path, descriptor, profile) as cache:
        for key in unique:
            cached = cache.get(key)
            if cached is None:
                missing.append(key)
            else:
                vector, tokens[key] = cached
                matrix[positions[key]] = vector
        if missing:
            model = SentenceEncoder(
                profile, model_dir, local_files_only=local_files_only
            )
            if model.resolved_revision != profile.revision:
                raise ValueError("Loaded encoder revision differs from profile")
        for start in range(0, len(missing), batch_size):
            keys = missing[start : start + batch_size]
            vectors, token_counts = model.encode(
                [unique[key] for key in keys], batch_size
            )
            encoding_calls += 1
            cache.put_batch(keys, vectors, token_counts)
            for key, vector, count in zip(keys, vectors, token_counts, strict=True):
                matrix[positions[key]] = vector
                tokens[key] = count
    validate_vectors(matrix, len(frame), profile.dimensions, profile.normalize)
    mapping = frame.assign(vector_row=np.arange(len(frame)))[list(REVIEW_COLUMNS)]
    report = {
        "schema_version": "voc-local-encoding-v1",
        "encoding_run_id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "status": "encoded",
        "dataset": feature_report["dataset"],
        "selection": feature_report["selection"],
        "feature": feature_report["feature"],
        "feature_reference": dict(feature_reference),
        "profile": profile.as_dict(),
        "encoder_id": encoder_id,
        "encoder": descriptor,
        "rows": len(frame),
        "unique_texts": len(unique),
        "encoded_unique": len(missing),
        "reused_unique": len(unique) - len(missing),
        "encoding_calls": encoding_calls,
        "model_loaded": model is not None,
        "batch_size": batch_size,
        "truncated_unique": sum(
            count > profile.max_length for count in tokens.values()
        ),
        "truncated_rows": sum(
            len(positions[key])
            for key, count in tokens.items()
            if count > profile.max_length
        ),
        "max_tokens_observed": max(tokens.values()),
        "token_lengths_include_special_tokens": True,
        "cache_path": str(Path(cache_path).resolve()),
        "cache_bytes": Path(cache_path).stat().st_size,
        "peak_process_rss_bytes": _peak_process_rss(),
        "memory_measurement": "process-lifetime high-water RSS",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".encoding-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        np.save(staging / "vectors.npy", matrix, allow_pickle=False)
        mapping.to_parquet(staging / "reviews.parquet", index=False)
        stored = np.load(staging / "vectors.npy", allow_pickle=False, mmap_mode="r")
        validate_vectors(stored, len(frame), profile.dimensions, profile.normalize)
        if not np.array_equal(stored, matrix) or not pd.read_parquet(
            staging / "reviews.parquet"
        ).equals(mapping):
            raise ValueError("Encoding checkpoint round-trip differs")
        del stored
        report["artifacts"] = {}
        for name in ("vectors.npy", "reviews.parquet"):
            path = staging / name
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
            report["artifacts"][name] = {
                "sha256": file_hash(path),
                "size_bytes": path.stat().st_size,
            }
        report["duration_seconds"] = time.perf_counter() - started
        report["peak_process_rss_bytes"] = _peak_process_rss()
        path = staging / "encoding-report.json"
        with path.open("wb") as stream:
            stream.write(canonical_json(report))
            stream.flush()
            os.fsync(stream.fileno())
        reference = {
            "directory": str(destination),
            "report_sha256": file_hash(path),
            "encoding_run_id": report["encoding_run_id"],
        }
        if destination.exists():
            raise FileExistsError(f"Encoding output already exists: {destination}")
        staging.rename(destination)
    return reference
