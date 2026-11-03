"""Read-only topic preparation. Does not create runs, fit models or export results."""

import numpy as np
import pandas as pd

from voc.embeddings import get_local_embeddings
from voc.embeddings.shared.contracts import require_digest, require_uuid
from voc.topics.contracts import SELECTION_COLUMNS, selection_hash, validate_selection
from voc.topics.scope import (
    FULL_POLICY,
    INPUT_SCHEMA,
    MAX_ROWS,
    SAMPLE_POLICY,
    SCHEMA_V2,
    VECTOR_HASH,
    TopicSelection,
    vector_hash,
)

from .selection import TopicInput, sample_rows


def select_corpus_input(
    snapshot, directory, manifest_sha256, selection, *, read_embeddings=None
):
    """Resolve the public embedding contract before selecting aligned positions."""
    selection.__post_init__()
    require_digest(manifest_sha256, "manifest_sha256")
    if not 1 <= len(snapshot.data) <= MAX_ROWS:
        raise ValueError("Population exceeds topic scope")
    reader = read_embeddings if read_embeddings is not None else get_local_embeddings
    bundle = reader(
        dataset=snapshot,
        directory=directory,
        manifest_sha256=manifest_sha256,
        allow_subset=False,
    )
    source = snapshot.data.reset_index(drop=True)
    if bundle.reviews.record_id.tolist() != source.record_id.tolist():
        raise ValueError("Dataset and embedding order differ")
    frame = source[["record_id", "model_text", "platform", "record_date"]].copy()
    frame["source_row"] = np.arange(len(frame))
    dates = pd.to_datetime(frame.record_date, format="mixed", errors="coerce", utc=True)
    frame["period"] = dates.dt.strftime("%Y-%m").fillna("unknown")
    frame = frame.sort_values("record_id").reset_index(drop=True)[
        list(SELECTION_COLUMNS)
    ]
    validate_selection(frame, schema=SCHEMA_V2)
    if selection.mode == "sample":
        selected, coverage = sample_rows(frame, selection)
        selected = selected[list(SELECTION_COLUMNS)]
    else:
        selected = frame
        coverage = [
            {
                "platform": platform,
                "period": period,
                "population": int(size),
                "selected": int(size),
            }
            for (platform, period), size in frame.groupby(
                ["platform", "period"], sort=True
            )
            .size()
            .items()
        ]
    # Advanced indexing already creates the required ordered copy.
    vectors = bundle.vectors[selected.source_row.to_numpy()]
    info = {
        "schema_version": INPUT_SCHEMA,
        "dataset": bundle.info["dataset"],
        "embedding_release_id": bundle.info["embedding_release_id"],
        "embedding_manifest_sha256": manifest_sha256,
        "profile": bundle.info["profile"],
        "selection": selection.as_dict(),
        "selection_policy": FULL_POLICY if selection.mode == "full" else SAMPLE_POLICY,
        "selected_rows": len(selected),
        "population_rows": len(frame),
        "population_selection_sha256": selection_hash(frame),
        "coverage": coverage,
        "selection_sha256": selection_hash(selected),
        "vectors_hash_algorithm": VECTOR_HASH,
        "vectors_sha256": vector_hash(vectors),
    }
    result = TopicInput(selected, vectors, info)
    result.validate()
    return result


def preflight(
    dataset_version,
    directory,
    manifest_sha256,
    selection=None,
    *,
    read_dataset=None,
    read_embeddings=None,
):
    """Check inputs for a requested scope. Success does not authorize model execution."""
    require_uuid(dataset_version, "dataset_version")
    require_digest(manifest_sha256, "manifest_sha256")
    selection = selection if selection is not None else TopicSelection()
    selection.__post_init__()
    if read_dataset is None:
        from voc.datasets import get_dataset

        read_dataset = get_dataset
    snapshot = read_dataset(version=dataset_version, source="personal")
    if snapshot.info["artifact_id"] != dataset_version:
        raise ValueError("Resolved dataset differs from request")
    inputs = select_corpus_input(
        snapshot, directory, manifest_sha256, selection, read_embeddings=read_embeddings
    )
    return {
        "schema_version": "voc-topic-preflight-v1",
        "state": "inputs_verified",
        "input": inputs.info,
        "vectors": {
            "shape": list(inputs.vectors.shape),
            "dtype": str(inputs.vectors.dtype),
            "bytes": int(inputs.vectors.nbytes),
        },
        "execution_ready": False,
        "pending": ["resource_preflight", "frozen_pilot_protocol"],
        "model_requests": 0,
        "fits": 0,
        "run_created": False,
    }
