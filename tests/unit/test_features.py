"""Feature preparation preserves reviews and rejects invalid/corrupt intermediates."""

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from voc.datasets.manifest import file_hash, frame_hash
from voc.embeddings.contracts import text_hash
from voc.embeddings.profiles import resolve_profile
from voc.models import Snapshot
from voc_ml.embeddings import InputValidationError, select_snapshot
from voc_ml.features import (
    FeatureConfig,
    prepare_features,
    prepare_frame,
    read_features,
)


def pin(frame):
    return select_snapshot(
        Snapshot(
            frame,
            {
                "release_id": "b7a61a8b-27d8-46a7-b2ce-67d97268b741",
                "content_sha256": frame_hash(frame),
                "file_sha256": "a" * 64,
                "dataset_uri": "s3://voc-test/voc/datasets",
                "row_count": len(frame),
                "schema_version": "workable-v1",
                "source": "s3",
                "stage": "prepared",
            },
        )
    )


@pytest.fixture
def reviews():
    return pd.DataFrame(
        {
            "record_id": pd.Series(["a", "b", "c"], dtype="string"),
            "model_text": pd.Series(
                ["  ¡Útil! 👋\n", "Repetida", "Repetida"], dtype="string"
            ),
            "rating": pd.Series([5, None, 3], dtype="Int64"),
            "title": pd.Series([None, "Título", ""], dtype="string"),
        }
    )


def test_verbatim_preserves_source_and_positions_without_mutation(reviews):
    reviews.insert(0, "source_row", [10, 15, 18])
    reviews.index = [30, 31, 35]
    before = reviews.copy(deep=True)
    features = prepare_frame(reviews, FeatureConfig())
    pd.testing.assert_frame_equal(reviews, before)
    pd.testing.assert_frame_equal(
        features[reviews.columns], before.reset_index(drop=True)
    )
    assert features.source_row.tolist() == [10, 15, 18]
    assert features.embedding_text.tolist() == reviews.model_text.tolist()
    assert (
        features.source_text_hash.tolist() == reviews.model_text.map(text_hash).tolist()
    )
    assert features.feature_text_hash.tolist() == features.source_text_hash.tolist()
    assert features.record_id.tolist() == ["a", "b", "c"]
    assert features.feature_text_hash.iloc[1] == features.feature_text_hash.iloc[2]
    assert FeatureConfig().policy == resolve_profile().preparation_policy


@pytest.mark.parametrize("value", [None, pd.NA, "", " \t\n", 42, ["text"]])
def test_invalid_text_is_reported_without_coercion_or_dropping(value):
    frame = pd.DataFrame({"record_id": ["good", "bad"], "model_text": ["Good", value]})
    with pytest.raises(InputValidationError) as exc:
        prepare_frame(frame, FeatureConfig())
    assert exc.value.report["invalid_text_rows"] == 1
    assert exc.value.report["example_problem_ids"] == ["bad"]


def test_duplicate_and_invalid_ids_are_explicit_and_diagnostics_bounded():
    frame = pd.DataFrame(
        {"record_id": ["x"] * 15 + [None], "model_text": ["private text"] * 16}
    )
    with pytest.raises(InputValidationError) as exc:
        prepare_frame(frame, FeatureConfig())
    assert exc.value.report["duplicate_id_rows"] == 15
    assert exc.value.report["invalid_id_rows"] == 1
    assert len(exc.value.report["example_problem_ids"]) <= 10
    assert "private text" not in str(exc.value)


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame(),
        pd.DataFrame(columns=["record_id", "model_text"]),
        pd.DataFrame({"record_id": ["a"]}),
        pd.DataFrame(
            [["a", "text", "text"]], columns=["record_id", "model_text", "model_text"]
        ),
    ],
)
def test_missing_empty_or_duplicate_columns_rejected(frame):
    with pytest.raises(InputValidationError):
        prepare_frame(frame, FeatureConfig())


@pytest.mark.parametrize(
    "column", ["embedding_text", "source_text_hash", "feature_text_hash", "vector_row"]
)
def test_stale_outputs_rejected(reviews, column):
    reviews[column] = "stale"
    with pytest.raises(ValueError, match="reserved"):
        prepare_frame(reviews, FeatureConfig())


@pytest.mark.parametrize(
    "positions", [[0.0, 1.0, 2.0], [0, 1, None], [0, 0, 2], [-1, 0, 1]]
)
def test_invalid_source_positions_rejected(reviews, positions):
    reviews["source_row"] = positions
    with pytest.raises(ValueError, match="source_row"):
        prepare_frame(reviews, FeatureConfig())


@pytest.mark.parametrize("policy", ["strip-v1", "whitespace-v1", "verbatim-v2", None])
def test_unimplemented_policies_fail(policy):
    with pytest.raises(ValueError, match="Unsupported preparation policy"):
        FeatureConfig(policy=policy)


def test_artifact_roundtrip_and_repeat_identity(reviews, tmp_path):
    pinned = pin(reviews)
    first = prepare_features(pinned, FeatureConfig(), tmp_path / "first")
    second = prepare_features(pinned, FeatureConfig(), tmp_path / "second")
    assert first["feature_id"] == second["feature_id"]
    restored = read_features(first, expected_selection=pinned.selection)
    pd.testing.assert_frame_equal(restored[reviews.columns], reviews)
    report = json.loads((tmp_path / "first/feature-report.json").read_bytes())
    assert report["counts"] == {
        "rows": 3,
        "changed_rows": 0,
        "source_unique_texts": 2,
        "prepared_unique_texts": 2,
    }
    assert report["selection"] == pinned.selection.as_dict()
    assert report["feature"]["policy"] == "verbatim-v1"
    assert report["artifact"]["key"] == "features.parquet"
    assert str(tmp_path) not in json.dumps(report)


def test_changed_source_has_new_identity_and_old_selection_is_rejected(
    reviews, tmp_path
):
    original = pin(reviews)
    first = prepare_features(original, FeatureConfig(), tmp_path / "original")
    reviews.loc[0, "model_text"] = "changed"
    changed = pin(reviews)
    second = prepare_features(changed, FeatureConfig(), tmp_path / "changed")
    assert first["feature_id"] != second["feature_id"]
    with pytest.raises(ValueError, match="different requested selection"):
        read_features(first, expected_selection=changed.selection)


def test_pinned_mutation_rejected_before_writing(reviews, tmp_path):
    pinned = pin(reviews)
    pinned.reviews.loc[0, "model_text"] = "changed"
    with pytest.raises(ValueError, match="changed or reordered"):
        prepare_features(pinned, FeatureConfig(), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_no_overwrite_or_partial_directory_after_failed_validation(reviews, tmp_path):
    pinned = pin(reviews)
    saved = prepare_features(pinned, FeatureConfig(), tmp_path / "saved")
    original_hash = file_hash(tmp_path / "saved/features.parquet")
    with pytest.raises(FileExistsError):
        prepare_features(pinned, FeatureConfig(), tmp_path / "saved")
    assert file_hash(tmp_path / "saved/features.parquet") == original_hash
    read_features(saved)
    with (
        patch(
            "voc_ml.features.read_features",
            side_effect=ValueError("injected round-trip failure"),
        ),
        pytest.raises(ValueError, match="injected"),
    ):
        prepare_features(pinned, FeatureConfig(), tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".features-*"))


@pytest.mark.parametrize("filename", ["features.parquet", "feature-report.json"])
def test_corrupt_bytes_rejected(reviews, tmp_path, filename):
    reference = prepare_features(pin(reviews), FeatureConfig(), tmp_path / "out")
    artifact = tmp_path / "out" / filename
    artifact.write_bytes(artifact.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="checksum|size"):
        read_features(reference)


def rewrite_report(reference, mutate):
    """Update checksum deliberately to exercise semantics beyond byte corruption."""
    directory = Path(reference["directory"])
    path = directory / "feature-report.json"
    report = json.loads(path.read_bytes())
    mutate(report)
    path.write_text(json.dumps(report))
    return {**reference, "report_sha256": file_hash(path)}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(schema_version="future-v2"),
        lambda r: r["feature"].update(policy="strip-v1"),
        lambda r: r["feature"].update(feature_id="e" * 64),
        lambda r: r["counts"].update(rows=1),
        lambda r: r["counts"].update(changed_rows=False),
        lambda r: r["selection"].update(selection_id="e" * 64),
        lambda r: r["artifact"].update(key="../features.parquet"),
    ],
)
def test_changed_metadata_rejected_even_with_updated_report_hash(
    reviews, tmp_path, mutation
):
    reference = prepare_features(pin(reviews), FeatureConfig(), tmp_path / "out")
    with pytest.raises(ValueError):
        read_features(rewrite_report(reference, mutation))


@pytest.mark.parametrize(
    "change",
    [
        "prepared_text",
        "source_text",
        "hash",
        "row_order",
        "source_row",
        "column_missing",
    ],
)
def test_recomputed_artifact_checksum_does_not_hide_semantic_corruption(
    reviews, tmp_path, change
):
    reference = prepare_features(pin(reviews), FeatureConfig(), tmp_path / "out")
    path = tmp_path / "out/features.parquet"
    frame = pd.read_parquet(path)
    if change == "prepared_text":
        frame.loc[0, "embedding_text"] = "changed"
    elif change == "source_text":
        frame.loc[0, "model_text"] = "changed"
    elif change == "hash":
        frame.loc[0, "feature_text_hash"] = "e" * 64
    elif change == "row_order":
        frame = frame.iloc[::-1]
    elif change == "source_row":
        frame["source_row"] = frame.source_row.astype(float)
    else:
        frame = frame.drop(columns="feature_text_hash")
    frame.to_parquet(path, index=False)

    def update(report):
        report["artifact"].update(
            sha256=file_hash(path), size_bytes=path.stat().st_size
        )

    reference = rewrite_report(reference, update)
    with pytest.raises(ValueError):
        read_features(reference)
