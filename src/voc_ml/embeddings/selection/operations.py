"""Pin and validate shared dataset selections for embedding work."""

from voc import collector
from voc.datasets.shared.manifest import frame_hash
from voc.embeddings.shared.contracts import DatasetIdentity, InputSelection

from .contracts import RESERVED_COLUMNS, PinnedInput, _rows_digest, validate_reviews


def select_snapshot(snapshot, *, limit=None):
    """Verify an unmodified shared Snapshot, then copy a full or explicit first-N selection."""
    try:
        dataset = DatasetIdentity(
            **{
                field: snapshot.info[field]
                for field in DatasetIdentity.__dataclass_fields__
            }
        )
    except KeyError as exc:
        raise ValueError(
            f"Snapshot is missing required shared metadata: {exc.args[0]}"
        ) from exc
    frame = snapshot.data
    validate_reviews(frame)
    if len(frame) != dataset.row_count or frame_hash(frame) != dataset.content_sha256:
        raise ValueError(
            "Snapshot content/row count differs from its pinned dataset identity"
        )
    if RESERVED_COLUMNS.intersection(frame.columns):
        raise ValueError("Snapshot contains reserved embedding output columns")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= len(frame)):
        raise ValueError(
            "limit must be a positive integer no larger than the source dataset"
        )
    selected = frame.iloc[:limit].copy(deep=True).reset_index(drop=True)
    selected.insert(0, "source_row", range(len(selected)))
    selection = InputSelection(dataset, len(selected), _rows_digest(selected), limit)
    return PinnedInput(selection, selected)


def pin_input(*, version="latest", source="s3", limit=None):
    """One dataset read/resolve per call; later stages use the returned pin, never latest."""
    if source != "s3":
        raise ValueError("The initial embedding producer requires a shared S3 dataset")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("limit must be a positive integer")
    snapshot = collector.get_dataset(stage="prepared", version=version, source=source)
    return select_snapshot(snapshot, limit=limit)
