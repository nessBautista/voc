"""Input invariants for reading embeddings aligned to a full dataset snapshot."""

from voc.datasets.manifest import frame_hash

from ..shared.contracts import REVIEW_COLUMNS, DatasetIdentity


def validate_snapshot(dataset):
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
