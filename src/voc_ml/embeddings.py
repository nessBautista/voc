"""Pin existing shared datasets for embedding work; no model or orchestration yet."""

from dataclasses import dataclass

import pandas as pd

from voc import collector
from voc.datasets.manifest import frame_hash
from voc.embeddings.contracts import (
    DatasetIdentity,
    InputSelection,
    ordered_rows_hash,
    text_hash,
)

# Refuse generated columns rather than interpreting an old run as source input.
RESERVED_COLUMNS = {
    "source_row",
    "vector_row",
    "embedding_text",
    "source_text_hash",
    "feature_text_hash",
}


class InputValidationError(ValueError):
    def __init__(self, report):
        self.report = report
        # Aggregate diagnostics only; callers may choose a bounded ID report separately.
        super().__init__("Review input is invalid: " + str(report))


def validate_reviews(frame):
    """Fail before selection; a test limit must not hide errors later in the source."""
    missing = sorted({"record_id", "model_text"} - set(frame.columns))
    report = {"rows": len(frame), "missing_columns": missing}
    if not frame.columns.is_unique:
        report["duplicate_columns"] = True
    if missing or not frame.columns.is_unique:
        raise InputValidationError(report)
    ids = frame["record_id"]
    valid_ids = ids.map(lambda value: isinstance(value, str) and bool(value.strip()))
    valid_text = frame["model_text"].map(
        lambda value: isinstance(value, str) and bool(value.strip())
    )
    report.update(
        invalid_id_rows=int((~valid_ids).sum()),
        duplicate_id_rows=int(ids.loc[valid_ids].duplicated(keep=False).sum()),
        invalid_text_rows=int((~valid_text).sum()),
    )
    if not len(frame) or any(
        report[key]
        for key in ("invalid_id_rows", "duplicate_id_rows", "invalid_text_rows")
    ):
        raise InputValidationError(report)


def _rows_digest(frame):
    return ordered_rows_hash(
        (int(position), record_id, text_hash(text))
        for position, record_id, text in frame[
            ["source_row", "record_id", "model_text"]
        ].itertuples(index=False, name=None)
    )


@dataclass(frozen=True)
class PinnedInput:
    selection: InputSelection
    reviews: pd.DataFrame

    def validate(self):
        """Recheck mutable dataframe content before a later stage uses the pin."""
        validate_reviews(self.reviews)
        if (
            "source_row" not in self.reviews
            or self.reviews["source_row"].tolist()
            != list(range(self.selection.row_count))
            or len(self.reviews) != self.selection.row_count
            or _rows_digest(self.reviews) != self.selection.rows_sha256
        ):
            raise ValueError("Pinned review selection was changed or reordered")


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
