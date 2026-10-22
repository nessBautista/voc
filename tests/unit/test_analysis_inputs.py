"""No-network fixtures prove selection reproducibility and review/vector alignment."""

import hashlib
import io
import json
from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from voc.datasets.manifest import frame_hash
from voc.embeddings.contracts import (
    REVIEW_COLUMNS,
    DatasetIdentity,
    EmbeddingBundle,
    InputSelection,
    encoder_identity,
    feature_identity,
    ordered_rows_hash,
    text_hash,
)
from voc.embeddings.profiles import resolve_profile
from voc.models import Snapshot
from voc_ml.analysis import (
    AnalysisConfig,
    AnalysisInput,
    SelectionConfig,
    load_config,
    pin_inputs,
    select_inputs,
)

DATASET_ID = "b7a61a8b-27d8-46a7-b2ce-67d97268b741"
EMBEDDING_ID = "23243c6e-7787-463c-ac3b-bfe950060d6e"


def fixture_inputs(count=48, available=None):
    """Build a real portable manifest around synthetic rows and unit vectors."""
    available = count if available is None else available
    frame = pd.DataFrame(
        {
            "record_id": [f"review-{i:03d}" for i in range(count)],
            "model_text": [f"Reseña {i // 2}" for i in range(count)],
            "platform": ["google_play"] * (count // 2)
            + ["app_store"] * (count - count // 2),
            "record_date": [
                None
                if i % 5 == 0
                else ("not-a-date" if i % 7 == 0 else f"2026-0{1 + i % 2}-10")
                for i in range(count)
            ],
            "rating": [1 + i % 5 for i in range(count)],
        }
    )
    dataset = DatasetIdentity(
        DATASET_ID, frame_hash(frame), "a" * 64, "s3://test-bucket/voc/datasets", count
    )
    reviews = frame.iloc[:available][["record_id", "model_text"]].copy()
    reviews["source_row"] = range(available)
    reviews["vector_row"] = range(available)
    reviews["embedding_text"] = reviews.model_text
    reviews["source_text_hash"] = reviews.model_text.map(text_hash)
    reviews["feature_text_hash"] = reviews.source_text_hash
    reviews = reviews[list(REVIEW_COLUMNS)]
    vectors = np.zeros((available, 384), dtype="<f4")
    vectors[np.arange(available), np.arange(available) // 2] = 1
    vector_bytes = io.BytesIO()
    np.save(vector_bytes, vectors, allow_pickle=False)
    parquet = reviews.to_parquet(index=False)
    selected = InputSelection(
        dataset,
        available,
        ordered_rows_hash(
            reviews[["source_row", "record_id", "source_text_hash"]].itertuples(
                index=False, name=None
            )
        ),
        None if count == available else available,
    )
    profile = resolve_profile()
    descriptor = {
        "profile_id": profile.profile_id,
        "adapter": "synthetic-v1",
        "device": "cpu",
        "dtype": "<f4",
        "software": {
            "torch": "test",
            "transformers": "test",
            "sentence-transformers": "test",
        },
    }
    feature_hash = ordered_rows_hash(
        reviews[["record_id", "source_text_hash", "feature_text_hash"]].itertuples(
            index=False, name=None
        )
    )
    info = {
        "schema_version": "voc-embeddings-v1",
        "embedding_release_id": EMBEDDING_ID,
        "dataset": dataset.as_dict(),
        "selection": selected.as_dict(),
        "profile": profile.as_dict(),
        "feature": {
            "policy": "verbatim-v1",
            "rows_sha256": feature_hash,
            "feature_id": feature_identity(
                selected.selection_id, "verbatim-v1", feature_hash
            ),
        },
        "encoder": {
            "descriptor": descriptor,
            "encoder_id": encoder_identity(descriptor),
        },
        "coverage": {
            "source_rows": count,
            "selected_rows": available,
            "unique_texts": reviews.model_text.nunique(),
        },
        "artifacts": {
            "reviews": {
                "key": f"releases/{EMBEDDING_ID}/reviews.parquet",
                "sha256": hashlib.sha256(parquet).hexdigest(),
                "size_bytes": len(parquet),
            },
            "vectors": {
                "key": f"releases/{EMBEDDING_ID}/vectors.npy",
                "sha256": hashlib.sha256(vector_bytes.getvalue()).hexdigest(),
                "size_bytes": vector_bytes.tell(),
                "shape": [available, 384],
                "dtype": "<f4",
                "order": "C",
            },
        },
        "provenance": {
            "producer_run_id": DATASET_ID,
            "zenml_run_id": DATASET_ID,
            "mlflow_run_id": "b" * 32,
            "completed_at": "2026-10-10T00:00:00+00:00",
            "status": "completed",
        },
    }
    return Snapshot(frame, dataset.as_dict()), EmbeddingBundle(reviews, vectors, info)


@pytest.fixture
def inputs():
    return fixture_inputs()


def test_full_and_first_n_preserve_vectors_text_and_metadata(inputs):
    snapshot, bundle = inputs
    original_frame, original_reviews = (
        snapshot.data.copy(deep=True),
        bundle.reviews.copy(deep=True),
    )
    full = select_inputs(snapshot, bundle)
    small = select_inputs(snapshot, bundle, selection=SelectionConfig("first-n", 10))
    for result, n in ((full, 48), (small, 10)):
        result.validate()
        np.testing.assert_array_equal(result.vectors, bundle.vectors[:n])
        assert (
            result.reviews.record_id.tolist() == bundle.reviews.record_id[:n].tolist()
        )
        assert result.reviews.rating.tolist() == snapshot.data.rating[:n].tolist()
        assert len(result.reviews) == n  # Duplicate text never drops review identities.
        assert result.info["dataset"]["release_id"] == DATASET_ID
        assert result.info["embedding"]["release_id"] == EMBEDDING_ID
        assert not result.vectors.flags.writeable
    small.reviews.loc[0, "model_text"] = "changed locally"
    pd.testing.assert_frame_equal(snapshot.data, original_frame)
    pd.testing.assert_frame_equal(bundle.reviews, original_reviews)


def test_reordered_metadata_joins_by_id_without_changing_identity(inputs):
    snapshot, bundle = inputs
    expected = select_inputs(snapshot, bundle)
    reordered = Snapshot(snapshot.data.sample(frac=1, random_state=8), snapshot.info)
    actual = select_inputs(reordered, bundle)
    assert actual.info == expected.info
    pd.testing.assert_frame_equal(actual.reviews, expected.reviews)
    np.testing.assert_array_equal(actual.vectors, expected.vectors)


def test_stratified_sampling_is_proportional_reproducible_and_ordered(inputs):
    snapshot, bundle = inputs
    config = SelectionConfig("stratified", 19, 42)
    first = select_inputs(snapshot, bundle, selection=config)
    repeated = select_inputs(snapshot, bundle, selection=config)
    other_seed = select_inputs(snapshot, bundle, selection=replace(config, seed=43))
    assert first.info == repeated.info
    assert first.info["input_id"] != other_seed.info["input_id"]
    selected = first.info["selection"]
    assert selected["positions"] == sorted(selected["positions"])
    assert (
        selected["allocation_policy"]
        == "platform-month-proportional-largest-remainder-v1"
    )
    assert sum(s["selected"] for s in selected["strata"]) == 19
    assert sum(s["available"] for s in selected["strata"]) == 48
    assert any(s["month"] is None for s in selected["strata"])
    for row in selected["strata"]:
        quota = 19 * row["available"] / 48
        assert row["selected"] in (int(np.floor(quota)), int(np.ceil(quota)))
    np.testing.assert_array_equal(first.vectors, bundle.vectors[selected["positions"]])


def test_small_stratified_sample_records_unrepresented_strata(inputs):
    sample = select_inputs(*inputs, selection=SelectionConfig("stratified", 1, 0))
    assert sum(row["selected"] == 0 for row in sample.info["selection"]["strata"]) >= 1


@pytest.mark.parametrize("kind", ["first-n", "stratified"])
def test_oversized_sample_fails(inputs, kind):
    with pytest.raises(ValueError, match="exceeds"):
        select_inputs(
            *inputs,
            selection=SelectionConfig(kind, 49, 0 if kind == "stratified" else None),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-id",
        "duplicate-id",
        "changed-text",
        "changed-rating",
        "duplicate-columns",
        "identity",
        "bundle-order",
        "vector-order",
        "nan",
    ],
)
def test_invalid_original_alignment_or_metadata_is_rejected(inputs, mutation):
    snapshot, bundle = inputs
    if mutation == "missing-id":
        snapshot.data.loc[0, "record_id"] = "missing"
    elif mutation == "duplicate-id":
        snapshot.data.loc[0, "record_id"] = snapshot.data.record_id[1]
    elif mutation == "changed-text":
        snapshot.data.loc[0, "model_text"] = "changed"
    elif mutation == "changed-rating":
        snapshot.data.loc[0, "rating"] = 2
    elif mutation == "duplicate-columns":
        snapshot.data.columns = [
            "record_id",
            "model_text",
            "platform",
            "record_date",
            "platform",
        ]
    elif mutation == "identity":
        snapshot.info["file_sha256"] = "c" * 64
    elif mutation == "bundle-order":
        bundle = replace(bundle, reviews=bundle.reviews.iloc[::-1])
    elif mutation == "vector-order":
        bundle.vectors[[0, 2]] = bundle.vectors[[2, 0]]
    elif mutation == "nan":
        bundle.vectors[0, 0] = np.nan
    with pytest.raises(ValueError):
        select_inputs(snapshot, bundle)


def test_persisted_pin_detects_swapped_vectors_and_changed_review(tmp_path, inputs):
    selected = select_inputs(*inputs, selection=SelectionConfig("first-n", 10))
    selected.reviews.to_parquet(tmp_path / "reviews.parquet", index=False)
    np.save(tmp_path / "vectors.npy", selected.vectors, allow_pickle=False)
    (tmp_path / "input.json").write_text(json.dumps(selected.info))
    reloaded = AnalysisInput(
        pd.read_parquet(tmp_path / "reviews.parquet"),
        np.load(tmp_path / "vectors.npy"),
        json.loads((tmp_path / "input.json").read_text()),
    )
    reloaded.validate()
    reloaded.vectors[[0, 2]] = reloaded.vectors[[2, 0]]
    with pytest.raises(ValueError, match="vectors changed"):
        reloaded.validate()
    reloaded.vectors[[0, 2]] = reloaded.vectors[[2, 0]]
    reloaded.reviews.loc[0, "model_text"] = "changed"
    with pytest.raises(ValueError, match="reviews changed"):
        reloaded.validate()


def test_saved_descriptor_tampering_rejected(inputs):
    selected = select_inputs(*inputs)
    selected.info["selection"]["record_ids"][0] = "forged"
    with pytest.raises(ValueError):
        selected.validate()


def test_subset_release_and_analysis_selection_are_distinct():
    snapshot, bundle = fixture_inputs(48, 12)
    selected = select_inputs(snapshot, bundle, selection=SelectionConfig("first-n", 5))
    assert selected.info["dataset"]["row_count"] == 48
    assert selected.info["embedding"]["row_count"] == 12
    assert selected.info["embedding"]["selection_kind"] == "first-n"
    assert selected.info["selection"]["size"] == 5


def test_pin_resolves_each_reader_once_and_keeps_snapshot_whole(monkeypatch, inputs):
    from voc import collector, embeddings

    snapshot, bundle = inputs
    read_dataset, read_embeddings = (
        Mock(return_value=snapshot),
        Mock(return_value=bundle),
    )
    monkeypatch.setattr(collector, "get_dataset", read_dataset)
    monkeypatch.setattr(embeddings, "get_embeddings", read_embeddings)
    selected = pin_inputs(selection=SelectionConfig("first-n", 10))
    read_dataset.assert_called_once_with(
        stage="prepared", source="s3", version="latest"
    )
    read_embeddings.assert_called_once_with(
        dataset=snapshot,
        profile="minilm-verbatim-v1",
        version="latest",
        source="s3",
        allow_subset=False,
    )
    assert len(snapshot.data) == 48
    assert selected.info["dataset"]["release_id"] == DATASET_ID
    assert "latest" not in json.dumps(selected.info)


@pytest.mark.parametrize(
    "value",
    [
        {"unexpected": 1},
        {"topics": {"backend": "manual"}},
        {"topics": {"oops": 1}},
        {"clustering": {"min_clustr_size": 10}},
        {"clustering": {"min_cluster_size": True}},
        {"reduction": {"n_neighbors": 1}},
        {"reduction": {"min_dist": float("nan")}},
        {"reduction": {"random_state": -1}},
        {"reduction": {"metric": "bad"}},
        {"selection": {"kind": "first-n", "size": 0}},
        {"selection": {"kind": "stratified", "size": 10}},
        {"selection": {"kind": "full", "size": 10}},
        {"selection": {"seed": 42}},
        {"selection": {"kind": "stratified", "size": 10, "seed": True}},
    ],
)
def test_configuration_rejects_unknown_or_invalid_settings(value):
    with pytest.raises(ValueError):
        AnalysisConfig.from_dict(value)


def test_config_defaults_toml_roundtrip_and_small_input(tmp_path):
    config = AnalysisConfig()
    assert AnalysisConfig.from_dict(config.as_dict()).config_id == config.config_id
    path = tmp_path / "analysis.toml"
    path.write_text(
        '[topics]\nbackend = "bertopic"\n[selection]\nkind="stratified"\nsize=1000\nseed=42\n'
    )
    loaded = load_config(path)
    assert loaded.selection == SelectionConfig("stratified", 1000, 42)
    loaded.validate_row_count(1000)
    with pytest.raises(ValueError, match="too small"):
        loaded.validate_row_count(10)
    assert loaded.config_id != config.config_id


def test_bad_selection_fails_before_readers(monkeypatch):
    from voc import collector

    read = Mock(side_effect=AssertionError("No I/O expected"))
    monkeypatch.setattr(collector, "get_dataset", read)
    with pytest.raises(ValueError):
        pin_inputs(selection={})
    with pytest.raises(ValueError):
        pin_inputs(source="personal")
    read.assert_not_called()


def test_equal_source_and_embedding_text_with_different_pandas_dtypes(inputs):
    snapshot, bundle = inputs
    # Parquet can restore equivalent text columns with different string dtypes.
    bundle.reviews["embedding_text"] = bundle.reviews.embedding_text.astype("string")
    selected = select_inputs(snapshot, bundle)
    assert (
        selected.reviews.model_text.tolist() == selected.reviews.embedding_text.tolist()
    )
    selected.validate()


def test_input_identity_stable_across_python_hash_seeds():
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = """
from tests.unit.test_analysis_inputs import fixture_inputs
from voc_ml.analysis import SelectionConfig, select_inputs
result = select_inputs(*fixture_inputs(), selection=SelectionConfig('stratified', 10, 42))
print(result.info['input_id'])
"""
    root = Path(__file__).resolve().parents[2]
    identities = []
    for seed in ("1", "2"):
        env = dict(
            os.environ,
            PYTHONHASHSEED=seed,
            PYTHONPATH=os.pathsep.join((str(root / "src"), str(root))),
        )
        identities.append(
            subprocess.check_output(
                [sys.executable, "-c", code], env=env, text=True
            ).strip()
        )
    assert identities[0] == identities[1]
