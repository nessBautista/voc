"""Frozen pilot budget and the v2 lifecycle with synthetic, non-model outputs."""

import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from voc.embeddings.shared.contracts import canonical_json, snapshot_identity
from voc.embeddings.shared.profiles import resolve_profile
from voc.models import Snapshot
from voc.topics import read_topics
from voc.topics.scope import TopicSelection
from voc_ml.topics import corpus, preflight, production
from voc_ml.topics.config import TopicConfig
from voc_ml.topics.corpus_config import (
    CorpusConfig,
    resolve_config,
    tracking_parameters,
)
from voc_ml.topics.models import representation_policy
from voc_ml.topics.resources import GIB, resource_preflight
from voc_ml.topics.store import TopicRunStore


@pytest.fixture
def pilot(tmp_path, monkeypatch, request):
    count = getattr(request, "param", 5000)
    frame = pd.DataFrame(
        {
            "record_id": [f"r{i:05}" for i in range(count)],
            "model_text": [f"comentario {i}" for i in range(count)],
            "platform": ["google_play"] * count,
            "record_date": ["2026-10-01"] * count,
        }
    )
    snapshot = Snapshot(
        frame,
        {
            "artifact_id": str(uuid4()),
            "raw_revision_id": str(uuid4()),
            "run_id": str(uuid4()),
            "row_count": count,
            "schema_version": "workable-v1",
            "content_sha256": "a" * 64,
        },
    )
    vectors = np.zeros((count, 384), dtype="float32")
    vectors[:, 0] = 1
    bundle = SimpleNamespace(
        reviews=frame,
        vectors=vectors,
        info={
            "dataset": snapshot_identity(snapshot.info, source="personal").as_dict(),
            "embedding_release_id": str(uuid4()),
            "profile": resolve_profile().as_dict(),
        },
    )
    monkeypatch.setattr(preflight, "get_local_embeddings", lambda **_: bundle)
    import voc.datasets

    # The public reader accepts version/source, not a stage keyword.
    monkeypatch.setattr(
        voc.datasets, "get_dataset", lambda *, version, source: snapshot
    )
    store = TopicRunStore(tmp_path)

    def resources(root):
        return resource_preflight(
            root,
            observe=lambda: {
                "available_headroom_bytes": 8 * GIB,
                "cgroup_current_bytes": GIB,
                "effective_cpu_capacity": 4,
            },
            disk_usage=lambda _: SimpleNamespace(free=20 * GIB),
        )

    def metadata():
        return {"software": {}, "encoder": {}, "code_identity": "test"}

    def create():
        return corpus.create_pilot(
            snapshot.info["artifact_id"],
            tmp_path / "embeddings",
            "b" * 64,
            store=store,
            resources=resources,
            metadata=metadata,
        )
    return SimpleNamespace(
        store=store,
        snapshot=snapshot,
        resources=resources,
        metadata=metadata,
        create=create,
    )


def fake_worker(folder, config):
    request = json.loads((folder / "request.json").read_bytes())
    reviews = pd.read_parquet(folder / "input.parquet")
    count = len(reviews)
    if request.get("operation") == "representation":
        parent = read_topics(
            request["parent_directory"],
            manifest_sha256=request["parent"]["manifest_sha256"],
        )
        assignments, coordinates = parent.assignments, parent.coordinates
        topics = [
            {**topic, "keywords": [{"term": "transferencia", "weight": 0.4}]}
            for topic in parent.topics
        ]
    else:
        assignments = pd.DataFrame(
            {
                "record_id": reviews.record_id,
                "topic_id": [0] * count,
                "membership_strength": [1.0] * count,
            }
        )
        coordinates = np.zeros((count, 5), dtype="float32")
        topics = [
            {
                "topic_id": 0,
                "size": count,
                "keywords": [{"term": "acceso", "weight": 0.3}],
                "evidence_ids": [reviews.record_id.iloc[0]],
            }
        ]
    assignments.to_parquet(folder / "assignments.parquet", index=False)
    np.save(folder / "coordinates.npy", coordinates)
    (folder / "topics.json").write_bytes(canonical_json(topics))
    (folder / "fit.json").write_bytes(
        canonical_json(
            {
                "configuration": request.get("representation_config"),
                "representation": representation_policy(config),
                "software": {},
                "counts": {
                    "rows": count,
                    "topic_count": 1,
                    "unassigned_rows": 0,
                    "unassigned_fraction": 0.0,
                },
                "peak_worker_rss_bytes": 1,
            }
        )
    )


def fake_execute(run_id, *, store):
    return production.execute_local(run_id, store=store, worker=fake_worker)


def test_three_operation_pilot_end_to_end_without_model_libraries(pilot):
    initial = pilot.create()
    assert pilot.create()["study_id"] == initial["study_id"]
    for variant in corpus.ORDER:
        result = corpus.run_pilot(
            variant,
            store=pilot.store,
            resources=pilot.resources,
            metadata=pilot.metadata,
            execute=fake_execute,
        )
        assert result["entries"][variant]["state"] == "completed"
    assert len(result["entries"]) == 3
    assert not result["full_corpus_enabled"]
    assert len(result["summaries"]) == 3
    g3, r2 = [result["entries"][key] for key in ("G3", "R2")]
    parent = read_topics(
        pilot.store.directory(g3["run_id"]) / "release",
        manifest_sha256=g3["manifest_sha256"],
    )
    child = read_topics(
        pilot.store.directory(r2["run_id"]) / "release",
        manifest_sha256=r2["manifest_sha256"],
    )
    assert child.info["schema_version"] == "voc-local-topic-representation-v2"
    assert parent.assignments.equals(child.assignments)
    assert child.topics[0]["keywords"][0]["term"] == "transferencia"
    with pytest.raises(ValueError, match="already recorded"):
        corpus.run_pilot(
            "G0", store=pilot.store, resources=pilot.resources, metadata=pilot.metadata
        )


def test_failed_attempt_cannot_retry_or_advance(pilot):
    pilot.create()

    def fail(*args, **kwargs):
        raise TimeoutError("synthetic timeout")

    with pytest.raises(TimeoutError):
        corpus.run_pilot(
            "G0",
            store=pilot.store,
            resources=pilot.resources,
            metadata=pilot.metadata,
            execute=fail,
        )
    saved = corpus.inspect_pilot(store=pilot.store)
    assert saved["entries"]["G0"]["state"] == "failed"
    assert not list(pilot.store.root.glob("topics/runs/*/release"))
    with pytest.raises(ValueError, match="already recorded"):
        corpus.run_pilot("G0", store=pilot.store)
    with pytest.raises(ValueError, match="preceding"):
        corpus.run_pilot("G3", store=pilot.store)


def test_environment_drift_and_resource_failure_do_not_consume_attempt(pilot):
    pilot.create()
    with pytest.raises(ValueError, match="environment changed"):
        corpus.run_pilot(
            "G0", store=pilot.store, metadata=lambda: {}, resources=pilot.resources
        )

    def unavailable(root):
        return pilot.resources(root) | {
            "resource_budget_fits": False,
            "reasons": ["margin"],
        }

    with pytest.raises(ValueError, match="resource budget"):
        corpus.run_pilot(
            "G0", store=pilot.store, metadata=pilot.metadata, resources=unavailable
        )
    assert corpus.inspect_pilot(store=pilot.store)["entries"] == {}


def test_protocol_integrity_and_single_owner(pilot):
    pilot.create()
    with corpus._locked(pilot.store):
        with pytest.raises(ValueError, match="Another process"):
            corpus.run_pilot("G0", store=pilot.store)
    path = pilot.store.root / "topics/corpus/pilot-v1/pilot.json"
    state = json.loads(path.read_text())
    state["protocol"]["max_new_fits"] = 99
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="identity changed"):
        corpus.inspect_pilot(store=pilot.store)


def test_config_preserves_legacy_caps_and_separates_parameters():
    with pytest.raises(ValueError):
        TopicConfig(sample_size=5000)
    full = CorpusConfig(selection=TopicSelection("full", None, None), rows=84479)
    assert resolve_config(full.as_dict()) == full
    assert full.sample_size == 84479
    assert full.n_components == 5
    assert tracking_parameters(full.as_dict())["selection_sample_seed"] is None
    with pytest.raises(ValueError):
        replace(full, threads=5)
    with pytest.raises(ValueError):
        CorpusConfig(rows=6000)


def test_corpus_worker_cannot_bypass_supervisor(tmp_path):
    from voc_ml.topics.executor import bounded_worker

    with pytest.raises(RuntimeError, match="supervised"):
        bounded_worker(tmp_path, CorpusConfig())


def test_discovery_accepts_v2_inputs_without_refitting_test_embeddings(
    pilot, monkeypatch
):
    from voc_ml.topics import discovery

    inputs = preflight.select_corpus_input(
        pilot.snapshot, "unused", "b" * 64, TopicSelection()
    )
    count = len(inputs.reviews)

    class Model:
        umap_model = SimpleNamespace(
            embedding_=np.zeros((count, 5)), get_params=lambda: {}
        )
        hdbscan_model = SimpleNamespace(
            labels_=np.zeros(count, dtype=int), get_params=lambda: {}
        )

        def fit_transform(self, texts, embeddings):
            assert embeddings is inputs.vectors
            return [0] * count, [1.0] * count

        def get_representative_docs(self, topic):
            return [inputs.reviews.model_text.iloc[0]]

        def get_topic(self, topic):
            return [("acceso", 0.3)]

    monkeypatch.setattr(discovery, "build_topic_model", lambda _: Model())
    monkeypatch.setattr(discovery, "version", lambda _: "test")
    assignments, topics, coordinates, report = discovery.discover(
        inputs, CorpusConfig()
    )
    assert report["counts"]["rows"] == 5000
    assert topics[0]["size"] == 5000
    assert coordinates.shape == (5000, 5)
    assert len(assignments) == 5000
