"""Pin and validate shared dataset selections for embedding work."""

from voc import collector
from voc.datasets.shared.manifest import frame_hash
from voc.embeddings.shared.contracts import InputSelection, snapshot_identity

from .contracts import RESERVED_COLUMNS, PinnedInput, _rows_digest, validate_reviews


def select_snapshot(snapshot, *, limit=None, source="s3"):
    """Verify a full Snapshot, then copy its full or explicit first-N selection."""
    dataset = snapshot_identity(snapshot.info, source=source)
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
    if source not in ("s3", "personal"):
        raise ValueError("Embedding inputs require source=s3 or personal")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("limit must be a positive integer")
    snapshot = collector.get_dataset(stage="prepared", version=version, source=source)
    pinned = select_snapshot(snapshot, limit=limit, source=source)
    identity = pinned.selection.dataset
    resolved_version = identity.release_id if source == "s3" else identity.artifact_id
    if version != "latest" and version != resolved_version:
        raise ValueError("Resolved dataset identity differs from the requested version")
    return pinned
