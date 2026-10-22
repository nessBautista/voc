"""Real BERTopic/HDBSCAN fitting over synthetic embeddings, without downloads."""

from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest

from tests.unit.test_analysis_inputs import fixture_inputs
from voc.datasets.manifest import frame_hash
from voc.embeddings.contracts import fingerprint
from voc_ml.analysis import AnalysisConfig, AnalysisInput, load_config, select_inputs
from voc_ml.analysis.config import ClusteringConfig, ReductionConfig, SelectionConfig
from voc_ml.analysis.inputs import _npy_identity
from voc_ml.topics import bertopic as b
from voc_ml.topics import reduction as r


def topic_inputs(texts=None):
    # Three separated synthetic semantic groups. Duplicate texts keep distinct IDs.
    inputs = select_inputs(*fixture_inputs(48))
    reviews = inputs.reviews.copy()
    reviews["model_text"] = texts or [
        "aplicación contraseña acceso sesión"
        if i < 16
        else "tarjeta crédito comisión cobro"
        if i < 32
        else "transferencia dinero envío depósito"
        for i in range(48)
    ]
    reviews["embedding_text"] = reviews.model_text
    vectors = np.random.default_rng(42).normal(0, 0.01, (48, 384)).astype("<f4")
    vectors[np.arange(48), np.arange(48) // 16] += 1
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    info = dict(
        inputs.info,
        reviews_sha256=frame_hash(reviews),
        vectors_sha256=_npy_identity(vectors)[0],
    )
    info["input_id"] = fingerprint({k: v for k, v in info.items() if k != "input_id"})
    return AnalysisInput(reviews, vectors, info)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    inputs = topic_inputs()
    # Test real clustering/representation against known coordinates. Separate
    # tests and the real-data smoke run exercise the actual UMAP fit.
    coords = np.random.default_rng(42).normal(0, 0.01, (48, 5)).astype("<f4")
    coords[:, 0] += np.repeat([0, 10, 20], 16)
    monkeypatch.setattr(r, "_fit", Mock(return_value=coords))
    return inputs, AnalysisConfig(), tmp_path


def test_real_bertopic_preserves_ids_accents_evidence_and_mapping(
    prepared, monkeypatch
):
    from bertopic import BERTopic

    inputs, config, cache = prepared
    monkeypatch.setattr(
        BERTopic,
        "_extract_embeddings",
        Mock(side_effect=AssertionError("Must use existing vectors")),
    )
    result = b.discover(inputs, config, cache_dir=cache)
    result.validate(inputs)
    assert len(result.assignments) == 48
    assert result.report["backend"] == "bertopic"
    assert result.report["topic_count"] >= 2
    assert any(
        "contraseña" in k.term or "crédito" in k.term or "depósito" in k.term
        for t in result.topics
        for k in t.keywords
    )
    assigned = {a.record_id: a.topic_id for a in result.assignments}
    for topic in result.topics:
        assert all(assigned[rid] == topic.topic_id for rid in topic.evidence_ids)
        members = inputs.reviews.loc[
            inputs.reviews.record_id.map(assigned).eq(topic.topic_id)
        ]
        for rid in topic.evidence_ids:
            text = inputs.reviews.set_index("record_id").loc[rid, "model_text"]
            assert members.loc[members.model_text.eq(text), "record_id"].iloc[0] == rid
    assert len(result.report["cluster_to_topic"]) == len(result.topics)
    changed = b.discover(
        inputs,
        replace(config, clustering=replace(config.clustering, min_cluster_size=12)),
        cache_dir=cache,
    )
    assert changed.report["reduction_reused"]
    assert (
        changed.report["reduction"]["reduction_id"]
        == result.report["reduction"]["reduction_id"]
    )
    assert r._fit.call_count == 1


def test_all_outliers_keep_every_review(prepared):
    inputs, config, cache = prepared
    config = replace(
        config, clustering=ClusteringConfig(min_cluster_size=48, min_samples=3)
    )
    result = b.discover(inputs, config, cache_dir=cache)
    assert len(result.assignments) == 48
    assert {a.topic_id for a in result.assignments} == {-1}
    assert result.report["topic_count"] == 0
    assert result.report["cluster_to_topic"] == [{"cluster_id": -1, "topic_id": -1}]


def test_absent_representatives_stay_empty(prepared, monkeypatch):
    from bertopic import BERTopic

    monkeypatch.setattr(BERTopic, "get_representative_docs", lambda *args: None)
    inputs, config, cache = prepared
    result = b.discover(inputs, config, cache_dir=cache)
    assert all(t.evidence_ids == () for t in result.topics)


def test_nonmember_representative_rejected(prepared, monkeypatch):
    from bertopic import BERTopic

    monkeypatch.setattr(
        BERTopic, "get_representative_docs", lambda *args: ["unknown text"]
    )
    inputs, config, cache = prepared
    with pytest.raises(ValueError, match="not a member"):
        b.discover(inputs, config, cache_dir=cache)


def test_empty_vocabulary_fails_explicitly(prepared):
    _, config, cache = prepared
    inputs = topic_inputs(["de la que"] * 48)
    with pytest.raises(ValueError, match="no usable vocabulary"):
        b.discover(inputs, config, cache_dir=cache)


def test_bad_configuration_fails_before_model_or_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(
        b, "_model", Mock(side_effect=AssertionError("model initialized"))
    )
    with pytest.raises(ValueError, match="Unknown clustering"):
        AnalysisConfig.from_dict({"clustering": {"min_cluser_size": 10}})
    inputs = topic_inputs()
    with pytest.raises(ValueError, match="too small"):
        b.discover(
            inputs,
            replace(AnalysisConfig(), reduction=ReductionConfig(n_neighbors=100)),
            cache_dir=tmp_path,
        )
    with pytest.raises(ValueError, match="selection differs"):
        b.discover(
            inputs,
            replace(AnalysisConfig(), selection=SelectionConfig("first-n", 48)),
            cache_dir=tmp_path,
        )
    assert not list(tmp_path.iterdir())


def test_shipped_configuration_is_small_bertopic_baseline():
    config = load_config("config/analysis/bertopic.toml")
    assert config.backend == "bertopic"
    assert config.selection == SelectionConfig("first-n", 1000)
    assert config.clustering.min_cluster_size == 10


def test_actual_umap_produces_verified_reusable_coordinates(tmp_path):
    inputs = topic_inputs()
    config = ReductionConfig(n_neighbors=5, n_components=3)
    fresh = r.reduce_inputs(inputs, config, cache_dir=tmp_path)
    cached = r.reduce_inputs(inputs, config, cache_dir=tmp_path)
    assert not fresh.reused and cached.reused
    assert fresh.coordinates.shape == (48, 3)
    np.testing.assert_array_equal(fresh.coordinates, cached.coordinates)


def test_nonfinite_keyword_weights_fail_explicitly(prepared, monkeypatch):
    from bertopic import BERTopic

    inputs, config, cache = prepared
    original = BERTopic.get_topic

    def broken(self, topic, *args, **kwargs):
        terms = original(self, topic, *args, **kwargs)
        return [("invalid", float("nan"))] if terms else terms

    monkeypatch.setattr(BERTopic, "get_topic", broken)
    with pytest.raises(ValueError, match="keyword weights"):
        b.discover(inputs, config, cache_dir=cache)
