"""Encoding orchestration: fake model counters establish reuse and interruption recovery."""

import json
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from voc.datasets.manifest import frame_hash
from voc.embeddings.contracts import text_hash
from voc.models import Snapshot
from voc_ml.embeddings import encode_features, select_snapshot
from voc_ml.embeddings.features import FeatureConfig, prepare_features


@pytest.fixture
def features(tmp_path):
    def create(texts=None, name="features"):
        texts = texts or ["repeat-long", "two", "three", "repeat-long"]
        frame = pd.DataFrame(
            {"record_id": [f"id-{i}" for i in range(len(texts))], "model_text": texts}
        )
        info = {
            "release_id": "b7a61a8b-27d8-46a7-b2ce-67d97268b741",
            "source": "s3",
            "stage": "prepared",
            "schema_version": "workable-v1",
            "dataset_uri": "s3://voc-test/voc/datasets",
            "row_count": len(frame),
            "file_sha256": "a" * 64,
            "content_sha256": frame_hash(frame),
        }
        return prepare_features(
            select_snapshot(Snapshot(frame, info)), FeatureConfig(), tmp_path / name
        )

    return create


@pytest.fixture
def fake(monkeypatch):
    import voc_ml.embeddings.sentence_encoder as adapter

    state = {"loads": 0, "batches": [], "fail_at": None, "software_version": "test"}

    class Encoder:
        def __init__(self, profile, model_dir, **kwargs):
            state["loads"] += 1
            self.resolved_revision = profile.revision

        def encode(self, texts, batch_size):
            state["batches"].append(list(texts))
            if len(state["batches"]) == state["fail_at"]:
                raise RuntimeError("injected interruption")
            vectors = np.zeros((len(texts), 384), dtype="<f4")
            for i, text in enumerate(texts):
                vectors[i, int(text_hash(text)[:8], 16) % 384] = 1
            return vectors, [200 if text == "repeat-long" else 6 for text in texts]

    monkeypatch.setattr(adapter, "SentenceEncoder", Encoder)
    monkeypatch.setattr(
        adapter,
        "encoder_descriptor",
        lambda profile: {
            "profile_id": profile.profile_id,
            "adapter": "fake-test-v1",
            "device": "cpu",
            "dtype": "<f4",
            "software": {
                "torch": state["software_version"],
                "sentence-transformers": "test",
                "transformers": "test",
            },
        },
    )
    return state


def run(features, root, name="encoding", **kwargs):
    reference = encode_features(
        features,
        output_dir=root / name,
        cache_path=root / "cache.db",
        model_dir=root / "models",
        batch_size=2,
        **kwargs,
    )
    return reference, json.loads(
        (Path(reference["directory"]) / "encoding-report.json").read_bytes()
    )


def test_cold_then_warm_preserves_all_reviews_without_model_load(
    features, fake, tmp_path
):
    source = features()
    cold, report = run(source, tmp_path)
    assert (
        report["rows"],
        report["unique_texts"],
        report["encoded_unique"],
        report["reused_unique"],
    ) == (4, 3, 3, 0)
    assert report["status"] == "encoded"
    assert report["encoding_calls"] == 2
    assert report["truncated_unique"] == 1 and report["truncated_rows"] == 2
    warm, repeated = run(source, tmp_path, "warm")
    assert not repeated["model_loaded"] and repeated["encoding_calls"] == 0
    assert repeated["encoded_unique"] == 0 and repeated["reused_unique"] == 3
    assert repeated["truncated_rows"] == 2
    assert fake["loads"] == 1
    assert report["encoding_run_id"] != repeated["encoding_run_id"]
    np.testing.assert_array_equal(
        np.load(Path(cold["directory"]) / "vectors.npy"),
        np.load(Path(warm["directory"]) / "vectors.npy"),
    )
    mapping = pd.read_parquet(Path(cold["directory"]) / "reviews.parquet")
    assert mapping.record_id.tolist() == ["id-0", "id-1", "id-2", "id-3"]
    assert mapping.vector_row.tolist() == [0, 1, 2, 3]
    vectors = np.load(Path(cold["directory"]) / "vectors.npy")
    np.testing.assert_array_equal(vectors[0], vectors[3])
    assert not list(tmp_path.glob(".encoding-*"))


def test_one_new_text_and_changed_encoder_are_separate(features, fake, tmp_path):
    source = features()
    _, original = run(source, tmp_path)
    changed = features(
        ["repeat-long", "new", "three", "repeat-long"], name="changed-features"
    )
    _, report = run(changed, tmp_path, "changed")
    assert (report["encoded_unique"], report["reused_unique"]) == (1, 2)
    assert fake["batches"][-1] == ["new"]
    fake["software_version"] = "updated"
    _, new_encoder = run(changed, tmp_path, "new-encoder")
    assert new_encoder["encoder_id"] != original["encoder_id"]
    assert (new_encoder["encoded_unique"], new_encoder["reused_unique"]) == (3, 0)


def test_interrupted_batch_keeps_prior_commits(features, fake, tmp_path):
    source = features()
    fake["fail_at"] = 2
    with pytest.raises(RuntimeError, match="interruption"):
        run(source, tmp_path)
    assert not (tmp_path / "encoding").exists()
    fake["fail_at"] = None
    _, resumed = run(source, tmp_path, "resumed")
    assert (resumed["encoded_unique"], resumed["reused_unique"]) == (1, 2)
    assert fake["batches"][-1] == ["three"]


def test_existing_output_rejected_before_loading_model(features, fake, tmp_path):
    source = features()
    run(source, tmp_path)
    with pytest.raises(FileExistsError):
        run(source, tmp_path)
    assert fake["loads"] == 1


def test_failed_checkpoint_keeps_cache_but_no_final_output(
    features, fake, tmp_path, monkeypatch
):
    source = features()
    original = np.save
    monkeypatch.setattr(np, "save", Mock(side_effect=OSError("injected disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        run(source, tmp_path)
    assert not (tmp_path / "encoding").exists()
    assert not list(tmp_path.glob(".encoding-*"))
    monkeypatch.setattr(np, "save", original)
    _, retry = run(source, tmp_path)
    assert retry["encoded_unique"] == 0
    assert fake["loads"] == 1


def test_bad_feature_bytes_fail_before_encoder_load(features, fake, tmp_path):
    source = features()
    artifact = Path(source["directory"]) / "features.parquet"
    artifact.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum or size"):
        run(source, tmp_path)
    assert fake["loads"] == 0


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_invalid_batch_size_rejected(features, fake, tmp_path, batch_size):
    with pytest.raises(ValueError, match="batch_size"):
        encode_features(
            features(),
            output_dir=tmp_path / "out",
            cache_path=tmp_path / "cache.db",
            model_dir=tmp_path / "models",
            batch_size=batch_size,
        )
