"""Versioned text preparation and verified local feature artifacts, without inference."""

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from voc.datasets.manifest import MAX_FILE_BYTES, MAX_JSON_BYTES, file_hash
from voc.embeddings.contracts import (
    DatasetIdentity,
    InputSelection,
    canonical_json,
    feature_identity,
    ordered_rows_hash,
    require_digest,
    require_fields,
    text_hash,
)

from .selection import InputValidationError, PinnedInput, validate_reviews

SCHEMA_VERSION = "voc-features-v1"
FEATURE_COLUMNS = ("embedding_text", "source_text_hash", "feature_text_hash")


@dataclass(frozen=True)
class FeatureConfig:
    policy: str = "verbatim-v1"

    def __post_init__(self):
        if self.policy != "verbatim-v1":
            raise ValueError(f"Unsupported preparation policy: {self.policy!r}")


def _prepare_text(text, policy):
    if policy == "verbatim-v1":
        return text
    raise ValueError(f"Unsupported preparation policy: {policy!r}")


def _validate_input(reviews):
    try:
        validate_reviews(reviews)
    except InputValidationError as exc:
        report = dict(exc.report)
        if {"record_id", "model_text"} <= set(reviews) and reviews.columns.is_unique:
            ids = reviews["record_id"]
            valid_ids = ids.map(
                lambda value: isinstance(value, str) and bool(value.strip())
            )
            duplicate_ids = ids.loc[valid_ids].duplicated(keep=False)
            repeated = set(ids.loc[valid_ids].loc[duplicate_ids])
            examples = []
            for position, (record_id, text) in enumerate(
                reviews[["record_id", "model_text"]].itertuples(index=False, name=None)
            ):
                valid_id = isinstance(record_id, str) and bool(record_id.strip())
                if (
                    not valid_id
                    or record_id in repeated
                    or not isinstance(text, str)
                    or not text.strip()
                ):
                    examples.append(
                        record_id[:120]
                        if valid_id
                        else f"<invalid ID at row {position}>"
                    )
                    if len(examples) == 10:
                        break
            report["example_problem_ids"] = examples
        raise InputValidationError(report) from exc
    if set(FEATURE_COLUMNS).intersection(reviews.columns) or "vector_row" in reviews:
        raise ValueError(
            "Source input already contains reserved feature/vector columns"
        )
    if "source_row" in reviews:
        positions = reviews["source_row"]
        if (
            not pd.api.types.is_integer_dtype(positions.dtype)
            or positions.isna().any()
            or (positions < 0).any()
            or positions.duplicated().any()
        ):
            raise ValueError(
                "source_row must contain unique nonnegative integer positions"
            )


def prepare_frame(reviews: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
    """Copy source columns and append exact encoder text and both text fingerprints."""
    _validate_input(reviews)
    result = reviews.copy(deep=True).reset_index(drop=True)
    result["embedding_text"] = result["model_text"].map(
        lambda text: _prepare_text(text, config.policy)
    )
    result["source_text_hash"] = result["model_text"].map(text_hash)
    result["feature_text_hash"] = result["embedding_text"].map(text_hash)
    return result


def _feature_metadata(frame, selection, config):
    rows_sha256 = ordered_rows_hash(
        frame[["record_id", "source_text_hash", "feature_text_hash"]].itertuples(
            index=False, name=None
        )
    )
    return {
        "policy": config.policy,
        "rows_sha256": rows_sha256,
        "feature_id": feature_identity(
            selection.selection_id, config.policy, rows_sha256
        ),
    }


def _counts(frame):
    return {
        "rows": len(frame),
        "changed_rows": int(frame["model_text"].ne(frame["embedding_text"]).sum()),
        "source_unique_texts": int(frame["model_text"].nunique()),
        "prepared_unique_texts": int(frame["embedding_text"].nunique()),
    }


def prepare_features(pinned: PinnedInput, config: FeatureConfig, output_dir) -> dict:
    """Publish a complete local feature directory and return a small trusted reference.

    output_dir must be new, normally a producer-run directory's 'features' child.
    One producer owns it. Preparation/report files become visible together via a
    sibling-directory rename, after successful round-trip verification.
    """
    # Provide useful bounded diagnostics before checking pinned row identity.
    _validate_input(pinned.reviews)
    pinned.validate()
    frame = prepare_frame(pinned.reviews, config)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise FileExistsError(f"Feature output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".features-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        artifact = staging / "features.parquet"
        frame.to_parquet(artifact, index=False)
        if not 0 < artifact.stat().st_size <= MAX_FILE_BYTES:
            raise ValueError("Feature parquet exceeds file size limit")
        with artifact.open("rb") as stream:
            os.fsync(stream.fileno())
        report = {
            "schema_version": SCHEMA_VERSION,
            "dataset": pinned.selection.dataset.as_dict(),
            "selection": pinned.selection.as_dict(),
            "feature": _feature_metadata(frame, pinned.selection, config),
            "counts": _counts(frame),
            "artifact": {
                "key": artifact.name,
                "sha256": file_hash(artifact),
                "size_bytes": artifact.stat().st_size,
            },
        }
        payload = canonical_json(report)
        if len(payload) > MAX_JSON_BYTES:
            raise ValueError("Feature report exceeds metadata size limit")
        report_path = staging / "feature-report.json"
        with report_path.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        reference = {
            "directory": str(staging),
            "report_sha256": file_hash(report_path),
            "feature_id": report["feature"]["feature_id"],
        }
        # Same checks as subsequent local consumers, before making output visible.
        restored = read_features(reference, expected_selection=pinned.selection)
        if not frame.equals(restored):
            raise ValueError("Feature parquet did not preserve source values/dtypes")
        if destination.exists():
            raise FileExistsError(f"Feature output already exists: {destination}")
        staging.rename(destination)
    return {**reference, "directory": str(destination)}


def read_features(
    reference: dict, *, expected_selection: InputSelection | None = None
) -> pd.DataFrame:
    """Verify stored bytes, preparation and selection before returning feature rows.

    Preserve the returned reference outside the artifact directory. Its report hash
    anchors metadata; this local checksum contract is not a signature/trust service.
    """
    require_fields(
        reference, {"directory", "report_sha256", "feature_id"}, "feature reference"
    )
    require_digest(reference["report_sha256"], "reference.report_sha256")
    require_digest(reference["feature_id"], "reference.feature_id")
    directory = Path(reference["directory"])
    report_path = directory / "feature-report.json"
    if not 0 < report_path.stat().st_size <= MAX_JSON_BYTES:
        raise ValueError("Feature report exceeds metadata size limit")
    if file_hash(report_path) != reference["report_sha256"]:
        raise ValueError("Feature report checksum mismatch")
    report = json.loads(report_path.read_bytes())
    require_fields(
        report,
        {"schema_version", "dataset", "selection", "feature", "counts", "artifact"},
        "feature report",
    )
    if report["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported feature schema")
    require_fields(
        report["dataset"], DatasetIdentity.__dataclass_fields__, "feature dataset"
    )
    dataset = DatasetIdentity(**report["dataset"])
    selection_meta = report["selection"]
    require_fields(
        selection_meta,
        {"kind", "limit", "row_count", "rows_sha256", "selection_id"},
        "feature selection",
    )
    selection = InputSelection(
        dataset,
        selection_meta["row_count"],
        selection_meta["rows_sha256"],
        selection_meta["limit"],
    )
    if canonical_json(selection.as_dict()) != canonical_json(selection_meta):
        raise ValueError("Feature selection metadata mismatch")
    if expected_selection is not None and selection != expected_selection:
        raise ValueError("Feature artifact belongs to a different requested selection")
    require_fields(
        report["feature"], {"policy", "rows_sha256", "feature_id"}, "feature identity"
    )
    config = FeatureConfig(policy=report["feature"]["policy"])
    artifact = report["artifact"]
    require_fields(artifact, {"key", "sha256", "size_bytes"}, "feature artifact")
    require_digest(artifact["sha256"], "artifact.sha256")
    if artifact["key"] != "features.parquet":
        raise ValueError("Unexpected feature artifact key")
    if (
        type(artifact["size_bytes"]) is not int
        or not 0 < artifact["size_bytes"] <= MAX_FILE_BYTES
    ):
        raise ValueError("Invalid feature artifact size")
    path = directory / artifact["key"]
    if (
        path.stat().st_size != artifact["size_bytes"]
        or file_hash(path) != artifact["sha256"]
    ):
        raise ValueError("Feature parquet checksum or size mismatch")
    frame = pd.read_parquet(path)
    if not frame.columns.is_unique or not set(FEATURE_COLUMNS) <= set(frame):
        raise ValueError("Feature artifact is missing required unique columns")
    original = frame.drop(columns=list(FEATURE_COLUMNS))
    _validate_input(original)
    PinnedInput(selection, original).validate()
    expected = prepare_frame(original, config)
    if not frame.equals(expected):
        raise ValueError(
            "Stored feature text/hashes differ from the preparation policy"
        )
    feature = _feature_metadata(frame, selection, config)
    if (
        canonical_json(feature) != canonical_json(report["feature"])
        or feature["feature_id"] != reference["feature_id"]
    ):
        raise ValueError("Feature identity mismatch")
    if canonical_json(_counts(frame)) != canonical_json(report["counts"]):
        raise ValueError("Feature counts mismatch")
    return frame
