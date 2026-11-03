"""G/R lineage, study budgets and comparisons with no real grouping fits."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from voc.datasets.shared.manifest import file_hash
from voc.embeddings.shared.contracts import (
    canonical_json,
    fingerprint,
    snapshot_identity,
)
from voc.embeddings.shared.profiles import resolve_profile
from voc.topics import read_topics
from voc.topics.comparison import adjusted_rand, compare_assignments, review_sample
from voc.topics.contracts import selection_hash, validate_results
from voc_ml.topics.config import TopicConfig
from voc_ml.topics.models import representation_policy
from voc_ml.topics.production import (
    create_run,
    create_variant,
    execute_local,
)
from voc_ml.topics.representation import RepresentationConfig
from voc_ml.topics.representation_artifacts import export_representation
from voc_ml.topics.selection import TopicInput
from voc_ml.topics.store import TopicRunStore
from voc_ml.topics.studies import (
    create_study,
    inspect_study,
    load_study,
    run_variant,
    select_candidates,
    select_representation_parent,
)


def synthetic_worker(folder, config):
    reviews = pd.read_parquet(folder / "input.parquet")
    assignments = pd.DataFrame(
        {
            "record_id": reviews.record_id,
            "topic_id": [0] * 400 + [1] * 500 + [-1] * 100,
            "membership_strength": [0.8] * 900 + [0.0] * 100,
        }
    )
    coordinates = np.zeros((len(reviews), config.n_components), dtype="float32")
    topics = [
        {
            "topic_id": tid,
            "size": size,
            "keywords": [{"term": term, "weight": 0.2}],
            "evidence_ids": [reviews.record_id.iloc[index]],
        }
        for tid, size, term, index in [
            (0, 400, "acceso", 0),
            (1, 500, "pago", 400),
            (-1, 100, "general", 900),
        ]
    ]
    counts = validate_results(reviews, assignments, topics, coordinates)
    assignments.to_parquet(folder / "assignments.parquet", index=False)
    np.save(folder / "coordinates.npy", coordinates)
    (folder / "topics.json").write_bytes(canonical_json(topics))
    (folder / "fit.json").write_bytes(
        canonical_json(
            {
                "counts": counts,
                "peak_worker_rss_bytes": 1024,
                "software": {"numpy": np.__version__},
                "representation": representation_policy(config),
            }
        )
    )


def synthetic_execute(run_id, *, store):
    return execute_local(run_id, store=store, worker=synthetic_worker)


@pytest.fixture
def baseline(tmp_path):
    from voc.models import Snapshot

    store = TopicRunStore(tmp_path)
    frame = pd.DataFrame(
        {
            "record_id": [f"r{i:04}" for i in range(1000)],
            "model_text": [
                f"problema acceso cuenta pago número aplicación {i}" for i in range(1000)
            ],
            "platform": ["google_play"] * 1000,
            "record_date": ["2026-09-01"] * 1000,
            "source_row": list(range(1000)),
            "period": ["2026-09"] * 1000,
        }
    )
    vectors = np.zeros((1000, 384), dtype="float32")
    vectors[:, 0] = 1
    identity = {
        "artifact_id": str(uuid4()),
        "raw_revision_id": str(uuid4()),
        "run_id": str(uuid4()),
        "row_count": 1000,
        "schema_version": "workable-v1",
        "content_sha256": "a" * 64,
    }
    info = {
        "dataset": snapshot_identity(identity, source="personal").as_dict(),
        "embedding_release_id": str(uuid4()),
        "embedding_manifest_sha256": "b" * 64,
        "profile": resolve_profile().as_dict(),
        "sample_seed": 42,
        "sample_size": 1000,
        "selection_policy": "platform-month-proportional-largest-remainder-v1",
        "coverage": [
            {
                "platform": "google_play",
                "period": "2026-09",
                "population": 1000,
                "selected": 1000,
            }
        ],
        "selection_sha256": selection_hash(frame),
        "vectors_sha256": fingerprint(vectors.tolist()),
    }
    inputs = TopicInput(frame, vectors, info)
    run = create_run(
        identity["artifact_id"],
        tmp_path / "embeddings",
        "b" * 64,
        store=store,
        provenance=lambda: {
            "code_identity": "c" * 64,
            "git_commit": None,
            "git_status": None,
        },
    )
    state = execute_local(
        run["run_id"],
        store=store,
        snapshot=Snapshot(frame, identity),
        select=lambda *args: inputs,
        worker=synthetic_worker,
    )
    return store, state


def read(state):
    result = state["result"]
    return read_topics(result["directory"], manifest_sha256=result["manifest_sha256"])


def test_ari_label_permutation_and_empty_intersection():
    assert adjusted_rand([0, 0, 1, 1], [9, 9, 3, 3]) == 1
    assert adjusted_rand([0, 0, 1, 1], [0, 1, 0, 1]) == pytest.approx(-0.5)
    assert adjusted_rand([], []) is None
    assert adjusted_rand([0], [1]) is None


def test_comparison_rejects_different_inputs_and_handles_outliers(baseline):
    _, state = baseline
    a, b = read(state), read(state)
    b.assignments["topic_id"] = -1
    result = compare_assignments(a, b)
    assert result["assigned_intersection_rows"] == 0
    assert result["ari_assigned_intersection"] is None
    assert result["became_unassigned"] == 900
    b.info["input"]["sample_seed"] = 1
    with pytest.raises(ValueError, match="identical"):
        compare_assignments(a, b)


def test_review_template_is_deterministic_and_unscored(baseline):
    _, state = baseline
    sample = review_sample(read(state))
    assert sample == review_sample(read(state))
    assert sample["review_status"] == "pending"
    assert len(sample["unassigned_ids"]) == 5
    assert all(t["scores"] is None and t["reviewer"] is None for t in sample["topics"])


def test_group_variant_uses_parent_input_without_dataset_reader(baseline):
    store, parent = baseline
    source = Path(parent["result"]["directory"])
    before = {p.name: file_hash(p) for p in source.iterdir()}
    run = create_variant(
        parent["run_id"],
        parent["result"]["manifest_sha256"],
        replace(TopicConfig(), n_components=4),
        store=store,
    )
    result = synthetic_execute(run["run_id"], store=store)
    assert read(result).coordinates.shape == (1000, 4)
    assert read(result).info["input"] == read(parent).info["input"]
    assert before == {p.name: file_hash(p) for p in source.iterdir()}
    with pytest.raises(ValueError, match="selection"):
        create_variant(
            parent["run_id"],
            parent["result"]["manifest_sha256"],
            replace(TopicConfig(), sample_seed=2),
            store=store,
        )


def test_corrupt_parent_work_fails_before_creating_variant(baseline):
    store, parent = baseline
    folder = store.directory(parent["run_id"]) / "work"
    vectors = np.load(folder / "vectors.npy")
    vectors[0, 0] = 0.9
    np.save(folder / "vectors.npy", vectors)
    before = list((store.root / "topics/runs").iterdir())
    with pytest.raises(ValueError, match="vectors changed"):
        create_variant(
            parent["run_id"],
            parent["result"]["manifest_sha256"],
            TopicConfig(),
            store=store,
        )
    assert before == list((store.root / "topics/runs").iterdir())


def test_study_failures_consume_slot_and_seeds_require_decision(baseline):
    store, parent = baseline
    study = create_study(
        parent["run_id"], parent["result"]["manifest_sha256"], store=store
    )
    sid = study["study_id"]
    with pytest.raises(ValueError, match="Freeze sensitivity"):
        run_variant(sid, "G1", seed=13, store=store)

    def fail(*args, **kwargs):
        raise RuntimeError("tracking unavailable")

    with pytest.raises(RuntimeError, match="tracking"):
        run_variant(sid, "G1", store=store, execute=fail)
    entry = load_study(sid, store=store)["entries"]["G1-42"]
    assert entry["state"] == "failed" and entry["run_id"]
    with pytest.raises(ValueError, match="No automatic replay"):
        run_variant(sid, "G1", store=store, execute=synthetic_execute)
    assert len(load_study(sid, store=store)["entries"]) == 2


def test_study_full_g_budget_and_frozen_decisions(baseline):
    store, parent = baseline
    sid = create_study(
        parent["run_id"], parent["result"]["manifest_sha256"], store=store
    )["study_id"]
    for name in ("G1", "G2", "G3"):
        run_variant(sid, name, store=store, execute=synthetic_execute)
    select_candidates(
        sid, ["G0", "G1"], "test reviewer", "synthetic test only", store=store
    )
    with pytest.raises(ValueError, match="already frozen"):
        select_candidates(sid, ["G2"], "test", "change", store=store)
    with pytest.raises(ValueError, match="Freeze sensitivity"):
        run_variant(sid, "G2", seed=13, store=store, execute=synthetic_execute)
    for name in ("G0", "G1"):
        for seed in (13, 73):
            run_variant(sid, name, seed=seed, store=store, execute=synthetic_execute)
    select_representation_parent(
        sid,
        "G0",
        "test",
        "no real selection",
        store=store,
        encoder_metadata=lambda: {"test": True},
    )
    result = inspect_study(sid, store=store)
    assert (
        len([e for e in result["entries"].values() if e["state"] == "completed"]) == 7
    )
    assert result["entries"]["R0"]["run_id"] == parent["run_id"]
    assert result["review_templates"]["G1-42"]["review_status"] == "pending"


def test_edited_protocol_rejected(baseline):
    store, parent = baseline
    study = create_study(
        parent["run_id"], parent["result"]["manifest_sha256"], store=store
    )
    p = store.root / "topics/studies" / study["study_id"] / "study.json"
    study["protocol"]["max_new_fits"] = 900
    p.write_bytes(canonical_json(study))
    with pytest.raises(ValueError, match="protocol changed"):
        load_study(study["study_id"], store=store)


def test_derived_export_preserves_files_and_is_self_contained(baseline, tmp_path):
    _, state = baseline
    parent = read(state)
    config = RepresentationConfig()
    execution = {
        **parent.execution,
        "run_id": str(uuid4()),
        "representation_config": config.as_dict(),
        "fit": {"configuration": config.as_dict()},
    }
    topics = [
        {**t, "keywords": [{"term": "cuenta", "weight": 0.4}]} for t in parent.topics
    ]
    result = export_representation(
        state["result"]["directory"],
        state["result"]["manifest_sha256"],
        topics,
        execution,
        tmp_path / "derived",
    )
    child = read_topics(result["directory"], manifest_sha256=result["manifest_sha256"])
    assert child.info["schema_version"] == "voc-local-topic-representation-v1"
    for name in ("selection.parquet", "assignments.parquet", "coordinates.npy"):
        assert parent.info["files"][name] == child.info["files"][name]
    assert [t["evidence_ids"] for t in child.topics] == [
        t["evidence_ids"] for t in parent.topics
    ]
    bad = [{**t, "evidence_ids": []} for t in topics]
    with pytest.raises(ValueError, match="evidence"):
        export_representation(
            state["result"]["directory"],
            state["result"]["manifest_sha256"],
            bad,
            execution,
            tmp_path / "bad",
        )
    # Portable read must not consult the working parent directory.
    Path(state["result"]["directory"]).rename(
        Path(state["result"]["directory"]).with_name("hidden")
    )
    assert (
        read_topics(
            result["directory"], manifest_sha256=result["manifest_sha256"]
        ).topics
        == topics
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"diversity": float("nan")},
        {"candidate_words": 2},
        {"nr_samples": True},
        {"batch_size": 0},
        {"method": "llm"},
    ],
)
def test_invalid_refinement_config(kwargs):
    with pytest.raises(ValueError):
        RepresentationConfig(**kwargs)


def test_refine_uses_update_only_and_keeps_evidence(baseline, monkeypatch):
    bertopic = pytest.importorskip("bertopic")
    from bertopic.backend import BaseEmbedder

    from voc_ml.topics import models
    from voc_ml.topics.representation import refine

    def forbidden(*args, **kwargs):
        raise AssertionError("No cluster fit or factory during representation")

    monkeypatch.setattr(bertopic.BERTopic, "fit", forbidden)
    monkeypatch.setattr(bertopic.BERTopic, "fit_transform", forbidden)
    monkeypatch.setattr(models, "build_reducer", forbidden)
    monkeypatch.setattr(models, "build_clusterer", forbidden)

    class Embedder(BaseEmbedder):
        report = {"synthetic": True}

        def embed(self, documents, verbose=False):
            # Deterministic local substitute, never download weights in a unit test.
            values = np.ones((len(documents), 4), dtype="float32")
            for i, d in enumerate(documents):
                values[i, 0] = len(d) % 13 + 1
            return values / np.linalg.norm(values, axis=1, keepdims=True)

    _, state = baseline
    parent = read(state)
    for method in ("ctfidf", "keybert", "keybert_mmr"):
        topics, report = refine(
            parent,
            RepresentationConfig(method=method),
            Path("missing"),
            embedder_factory=lambda *args: Embedder(),
        )
        assert [(t["topic_id"], t["evidence_ids"]) for t in topics] == [
            (t["topic_id"], t["evidence_ids"]) for t in parent.topics
        ]
        assert report["counts"] == parent.info["counts"]
        if method == "ctfidf":
            assert any("aplicación" in k["term"] for t in topics for k in t["keywords"])


def test_cli_study_inspection_does_not_fit(baseline, monkeypatch):
    from voc_ml.topics import store as module
    from voc_ml.topics.cli import topics

    store, parent = baseline
    sid = create_study(
        parent["run_id"], parent["result"]["manifest_sha256"], store=store
    )["study_id"]
    monkeypatch.setattr(module, "local_store", lambda: store)
    response = CliRunner().invoke(topics, ["study", "inspect", sid])
    assert response.exit_code == 0, response.output
    assert json.loads(response.output)["summaries"][0]["rows"] == 1000


def test_variant_files_match_protocol():
    from voc_ml.topics.config import load_config
    from voc_ml.topics.studies import VARIANTS

    root = Path(__file__).resolve().parents[2]
    for name, filename in (
        ("G1", "g1-dimensions"),
        ("G2", "g2-cluster-size"),
        ("G3", "g3-leaf"),
    ):
        assert load_config(root / f"config/topics/{filename}.toml") == replace(
            TopicConfig(), **VARIANTS[name]
        )


def test_concurrent_owner_cannot_start_attempt(baseline):
    from voc_ml.topics.studies import _locked

    store, parent = baseline
    sid = create_study(
        parent["run_id"], parent["result"]["manifest_sha256"], store=store
    )["study_id"]
    with _locked(sid, store):
        with pytest.raises(ValueError, match="Another process"):
            run_variant(sid, "G1", store=store, execute=synthetic_execute)
    assert len(load_study(sid, store=store)["entries"]) == 1


def test_refinement_lifecycle_and_budget(baseline):
    store, parent = baseline
    sid = create_study(
        parent["run_id"], parent["result"]["manifest_sha256"], store=store
    )["study_id"]
    for name in ("G1", "G2", "G3"):
        run_variant(sid, name, store=store, execute=synthetic_execute)
    select_candidates(sid, ["G0"], "test reviewer", "synthetic test", store=store)
    for seed in (13, 73):
        run_variant(sid, "G0", seed=seed, store=store, execute=synthetic_execute)
    select_representation_parent(
        sid,
        "G0",
        "test reviewer",
        "synthetic test",
        store=store,
        encoder_metadata=lambda: {"test": True},
    )

    def worker(folder, config):
        synthetic_worker(folder, config)
        request = json.loads((folder / "request.json").read_bytes())
        report = json.loads((folder / "fit.json").read_bytes())
        report["configuration"] = request["representation_config"]
        (folder / "fit.json").write_bytes(canonical_json(report))

    def execute(run_id, *, store):
        return execute_local(run_id, store=store, worker=worker)

    for name in ("R1", "R2"):
        run_variant(sid, name, store=store, execute=execute)
        with pytest.raises(ValueError, match="No automatic replay"):
            run_variant(sid, name, store=store, execute=execute)
    result = inspect_study(sid, store=store)
    assert result["entries"]["R1"]["run_id"] != result["entries"]["R2"]["run_id"]
    assert (
        result["review_templates"]["R0"]["topics"][0]["evidence_ids"]
        == result["review_templates"]["R2"]["topics"][0]["evidence_ids"]
    )


def test_worker_rejects_software_drift_before_fit(baseline):
    from voc_ml.topics.production import fit_run, pin_run

    store, parent = baseline
    run = create_variant(
        parent["run_id"],
        parent["result"]["manifest_sha256"],
        TopicConfig(),
        store=store,
    )
    store.update(run["run_id"], expected_software={"numpy": "0.invalid"})
    pin_run(run["run_id"], store=store)
    with pytest.raises(RuntimeError, match="worker exited"):
        fit_run(run["run_id"], store=store)
    folder = store.directory(run["run_id"]) / "work"
    assert "Study environment changed: numpy" in (folder / "worker.log").read_text()
    assert not (folder / "fit.json").exists()
    with pytest.raises(ValueError, match="replayed"):
        fit_run(run["run_id"], store=store)
