"""Representation experiments must not become accidental discovery reruns."""

import json
from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest
from click.testing import CliRunner

from tests.unit.test_analysis_store import candidate as candidate  # noqa: PLC0414
from voc.analysis.queries import query
from voc.analysis.store import read_analysis, write_analysis
from voc.datasets.manifest import file_hash
from voc_ml.analysis import representation as workflow
from voc_ml.cli import main
from voc_ml.topics import representation
from voc_ml.topics.representation import RepresentationConfig, load_config, refine


@pytest.fixture
def parent(candidate):
    candidate["topics"] = [
        replace(t, name="Reviewed title") for t in candidate["topics"]
    ]
    return read_analysis(write_analysis(**candidate).analysis_id)


@pytest.fixture
def semantic_encoder(monkeypatch):
    from bertopic.backend import BaseEmbedder

    class FakeEncoder(BaseEmbedder):
        def __init__(self):
            super().__init__()
            self.report = {"encoded_unique": 0}

        def embed(self, documents, verbose=False):
            # Deterministic positive vectors support real KeyBERT/MMR code without
            # model downloads. Vary term geometry instead of mocking the rankers.
            return np.asarray(
                [[len(t) + 1, sum(map(ord, t)) % 17 + 1, 1] for t in documents],
                dtype=float,
            )

    monkeypatch.setattr(representation, "_embedder", lambda *a: FakeEncoder())


def test_config_rejects_unknown_and_invalid_values(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "untracked-model"')
    with pytest.raises(ValueError, match="Unknown"):
        load_config(path)
    for kwargs in (
        {"method": "gemini"},
        {"top_n_words": True},
        {"diversity": float("nan")},
        {"ngram_min": 3, "ngram_max": 1},
        {"candidate_words": 1},
        {"nr_repr_docs": 8, "nr_samples": 2},
    ):
        with pytest.raises(ValueError):
            RepresentationConfig(**kwargs)


@pytest.mark.parametrize("method", ["ctfidf", "keybert", "mmr", "keybert_mmr"])
def test_actual_bertopic_rankers_preserve_groups_and_titles(
    parent, semantic_encoder, monkeypatch, method
):
    from bertopic import BERTopic
    from hdbscan import HDBSCAN
    from umap import UMAP

    def forbidden(*args, **kwargs):
        raise AssertionError("Refinement must not fit a discovery model")

    monkeypatch.setattr(BERTopic, "fit_transform", forbidden)
    monkeypatch.setattr(UMAP, "fit", forbidden)
    monkeypatch.setattr(HDBSCAN, "fit", forbidden)
    reviews = parent.reviews.copy()
    reviews["model_text"] = [
        "tarjeta pago compra",
        "tarjeta pago falla",
        "cuenta saldo acceso",
        "bloqueo tarjeta",
    ]
    saved = replace(parent, reviews=reviews)
    before = workflow.preserved_content(saved)
    topics, report = refine(saved, RepresentationConfig(method=method, top_n_words=3))
    assert all(t.name == "Reviewed title" for t in topics)
    assert any(
        t.keywords != original.keywords for t, original in zip(topics, saved.topics)
    )
    assert workflow.preserved_content(replace(saved, topics=topics)) == before
    assert (
        report["encoder"] is None
        if method == "ctfidf"
        else report["encoder"] is not None
    )
    assert all(len(t.keywords) <= 3 for t in topics)


def test_empty_vocabulary_fails_before_loading_model(parent, monkeypatch):
    reviews = parent.reviews.copy()
    reviews["model_text"] = "y la el"
    encoder = Mock(side_effect=AssertionError("No model required"))
    monkeypatch.setattr(representation, "_embedder", encoder)
    with pytest.raises(ValueError, match="No usable topic vocabulary"):
        refine(replace(parent, reviews=reviews), RepresentationConfig(method="keybert"))
    encoder.assert_not_called()


def test_child_roundtrip_tracking_and_queries(parent, tmp_path, monkeypatch):
    client = Mock()
    monkeypatch.setattr(workflow, "_start_tracking", lambda *a: (client, "tracked-run"))
    finish = Mock()
    monkeypatch.setattr(workflow, "_finish_tracking", finish)
    path = tmp_path / "config.toml"
    path.write_text('method = "ctfidf"\ntop_n_words = 3\n')
    manifest_path = (
        tmp_path / "analyses" / parent.manifest.analysis_id / "manifest.json"
    )
    before = file_hash(manifest_path)
    state = workflow.execute_representation(parent.manifest.analysis_id, path)
    assert state["state"] == "completed", state
    finish.assert_called_once()
    child = read_analysis(state["analysis_id"])
    assert file_hash(manifest_path) == before
    assert child.manifest.parent_analysis_id == parent.manifest.analysis_id
    assert workflow.preserved_content(child) == workflow.preserved_content(parent)
    assert child.manifest.inputs == parent.manifest.inputs
    details = query(child.manifest.analysis_id, "diagnostics")
    assert details["available"]
    assert (
        details["data"]["preserved_content"]["parent"]
        == details["data"]["preserved_content"]["child"]
    )
    # Same groups, same memberships, same titles: only the keywords changed.
    assert child.assignments == parent.assignments
    assert [t.topic_id for t in child.topics] == [t.topic_id for t in parent.topics]
    assert [t.name for t in child.topics] == [t.name for t in parent.topics]


def test_failed_tracking_does_not_create_child(parent, tmp_path, monkeypatch):
    client = Mock()
    monkeypatch.setattr(workflow, "_start_tracking", lambda *a: (client, "tracked-run"))
    monkeypatch.setattr(
        workflow,
        "_finish_tracking",
        Mock(side_effect=ValueError("tracking unavailable")),
    )
    path = tmp_path / "config.toml"
    path.write_text('method = "ctfidf"')
    response = CliRunner().invoke(
        main,
        [
            "analysis",
            "represent",
            parent.manifest.analysis_id,
            "--config",
            str(path),
            "--json",
        ],
    )
    assert response.exit_code == 1
    state = json.loads(response.stdout)
    assert state["state"] == "failed", state
    assert "tracking unavailable" in state["error"]["message"]
    assert not (tmp_path / "analyses" / state["candidate_id"]).exists()
    assert (
        json.loads(
            (
                tmp_path / "representation-runs" / state["run_id"] / "state.json"
            ).read_text()
        )["state"]
        == "failed"
    )
    client.set_terminated.assert_called_with("tracked-run", status="FAILED")


def test_semantic_profile_mismatch_rejected_before_model_load(parent, monkeypatch):
    # Test the real adapter guard, without initializing weights.
    parent.manifest.inputs["embedding"]["profile_id"] = "0" * 64
    from voc_ml.embeddings import sentence_encoder

    model = Mock(side_effect=AssertionError("Should not load"))
    monkeypatch.setattr(sentence_encoder, "SentenceEncoder", model)
    with pytest.raises(ValueError, match="profile differs"):
        representation._embedder(parent, RepresentationConfig(method="keybert"), True)
    model.assert_not_called()


@pytest.mark.parametrize("labels", [[-1, -1, -1, -1], [7, 7, 23, -1]])
def test_single_group_and_noncontiguous_topic_ids(parent, semantic_encoder, labels):
    from voc.analysis.contracts import TopicRecord

    assignments = tuple(
        replace(a, topic_id=tid) for a, tid in zip(parent.assignments, labels)
    )
    topics = tuple(
        TopicRecord(tid, (), (), name=f"Topic {tid}") for tid in sorted(set(labels))
    )
    saved = replace(parent, assignments=assignments, topics=topics)
    result, _ = refine(saved, RepresentationConfig(method="keybert_mmr"))
    assert {t.topic_id for t in result} == set(labels)
    assert workflow.preserved_content(
        replace(saved, topics=result)
    ) == workflow.preserved_content(saved)


def test_embedding_adapter_deduplicates_and_records_cost(parent, monkeypatch):
    from voc_ml.embeddings import sentence_encoder

    encoder = Mock()
    encoder.encode.return_value = (np.ones((2, 384), dtype=np.float32), [130, 2])
    constructor = Mock(return_value=encoder)
    monkeypatch.setattr(sentence_encoder, "SentenceEncoder", constructor)
    adapter = representation._embedder(parent, RepresentationConfig(method="mmr"), True)
    first = adapter.embed(["tarjeta", "saldo", "tarjeta"])
    second = adapter.embed(["saldo"])
    assert first.shape == (3, 384) and second.shape == (1, 384)
    assert adapter.report["encoded_unique"] == 2
    assert adapter.report["requested_texts"] == 4
    assert adapter.report["reused_texts"] == 2
    assert adapter.report["truncated_unique"] == 1
    encoder.encode.assert_called_once_with(["tarjeta", "saldo"], batch_size=32)
    assert constructor.call_args.kwargs["local_files_only"] is True


def test_tracking_roundtrip_mismatch_is_rejected(tmp_path):
    client = Mock()
    path = tmp_path / "bad.json"
    path.write_text("{}")
    client.download_artifacts.return_value = str(path)
    report = {
        "configuration": {"representation": {"method": "ctfidf"}},
        "refinement": {"changed_topics": 1, "duration_seconds": 1, "encoder": None},
    }
    with pytest.raises(ValueError, match="round-trip"):
        workflow._finish_tracking(client, "run", report)
    client.set_terminated.assert_not_called()
