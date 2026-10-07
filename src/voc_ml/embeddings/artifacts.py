"""Checked local references passed between producer steps; no release publication."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from voc.datasets.manifest import MAX_FILE_BYTES, MAX_JSON_BYTES, file_hash
from voc.embeddings.contracts import (
    REVIEW_COLUMNS,
    DatasetIdentity,
    InputSelection,
    encoder_identity,
)
from voc.embeddings.profiles import resolve_profile

from .cache import validate_vectors
from .features import read_features
from .selection import PinnedInput


def write_input(pinned, directory):
    pinned.validate()
    directory = Path(directory)
    directory.mkdir()
    path = directory / "reviews.parquet"
    pinned.reviews.to_parquet(path, index=False)
    reference = {
        "directory": str(directory),
        "sha256": file_hash(path),
        "dataset": pinned.selection.dataset.as_dict(),
        "selection": pinned.selection.as_dict(),
    }
    restored = read_input(reference)
    if not restored.reviews.equals(pinned.reviews):
        raise ValueError("Pinned input round-trip differs")
    return reference


def read_input(reference):
    path = Path(reference["directory"]) / "reviews.parquet"
    if (
        not 0 < path.stat().st_size <= MAX_FILE_BYTES
        or file_hash(path) != reference["sha256"]
    ):
        raise ValueError("Pinned input checksum or size mismatch")
    meta = reference["selection"]
    selection = InputSelection(
        DatasetIdentity(**reference["dataset"]),
        meta["row_count"],
        meta["rows_sha256"],
        meta["limit"],
    )
    if selection.as_dict() != meta:
        raise ValueError("Pinned input selection mismatch")
    pinned = PinnedInput(selection, pd.read_parquet(path))
    pinned.validate()
    return pinned


def read_encoding(reference, *, expected_input=None, expected_profile=None):
    """Verify report, bytes, row mapping and feature identities before tracking/use."""
    directory = Path(reference["directory"])
    path = directory / "encoding-report.json"
    if (
        not 0 < path.stat().st_size <= MAX_JSON_BYTES
        or file_hash(path) != reference["report_sha256"]
    ):
        raise ValueError("Encoding report checksum or size mismatch")
    report = json.loads(path.read_bytes())
    if (
        report["schema_version"] != "voc-local-encoding-v1"
        or report["status"] != "encoded"
        or report["encoding_run_id"] != reference["encoding_run_id"]
    ):
        raise ValueError("Unexpected encoding report identity or state")
    profile = resolve_profile(report["profile"]["name"])
    if report["profile"] != profile.as_dict() or (
        expected_profile is not None and profile.name != expected_profile
    ):
        raise ValueError("Encoding profile mismatch")
    if (
        encoder_identity(report["encoder"]) != report["encoder_id"]
        or report["encoder"]["profile_id"] != profile.profile_id
    ):
        raise ValueError("Encoder identity mismatch")
    selection = None
    if expected_input is not None:
        selection = read_input(expected_input).selection
    frame = read_features(report["feature_reference"], expected_selection=selection)
    feature_report = json.loads(
        (
            Path(report["feature_reference"]["directory"]) / "feature-report.json"
        ).read_bytes()
    )
    if report["feature"]["policy"] != profile.preparation_policy:
        raise ValueError("Encoding preparation policy differs from profile")
    for key in ("dataset", "selection", "feature"):
        if report[key] != feature_report[key]:
            raise ValueError(f"Encoding {key} differs from prepared features")
    for name in ("vectors.npy", "reviews.parquet"):
        path = directory / name
        item = report["artifacts"][name]
        if (
            not 0 < path.stat().st_size <= MAX_FILE_BYTES
            or path.stat().st_size != item["size_bytes"]
            or file_hash(path) != item["sha256"]
        ):
            raise ValueError(f"Encoding artifact checksum or size mismatch: {name}")
    mapping = pd.read_parquet(directory / "reviews.parquet")
    expected = frame.assign(vector_row=np.arange(len(frame)))[list(REVIEW_COLUMNS)]
    if not mapping.equals(expected):
        raise ValueError("Encoding review-to-vector mapping mismatch")
    matrix = np.load(directory / "vectors.npy", allow_pickle=False, mmap_mode="r")
    validate_vectors(matrix, len(frame), profile.dimensions, profile.normalize)
    if not matrix.flags.c_contiguous:
        raise ValueError("Encoding matrix must use C order")
    unique = int(frame.feature_text_hash.nunique())
    if (
        report["rows"] != len(frame)
        or report["unique_texts"] != unique
        or any(
            type(report[k]) is not int or report[k] < 0
            for k in ("encoded_unique", "reused_unique")
        )
        or report["encoded_unique"] + report["reused_unique"] != unique
    ):
        raise ValueError("Encoding counts mismatch")
    return report
