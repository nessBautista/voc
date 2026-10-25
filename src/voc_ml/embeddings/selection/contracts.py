"""Pinned review selection and its existing source-input invariants."""

from dataclasses import dataclass

import pandas as pd

from voc.embeddings.shared.contracts import InputSelection, ordered_rows_hash, text_hash

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
