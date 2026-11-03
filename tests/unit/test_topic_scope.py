"""Full input identity, bounded v2 decoding, and legacy compatibility boundaries."""

import json
import shutil
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from voc.datasets.shared.manifest import file_hash, frame_hash
from voc.embeddings.shared.contracts import canonical_json, snapshot_identity
from voc.embeddings.shared.profiles import resolve_profile
from voc.models import Snapshot
from voc.topics import read_topics
from voc.topics.contracts import selection_hash, validate_selection
from voc.topics.scope import SCHEMA_V2, TopicSelection, vector_hash
from voc_ml.topics.artifacts import export_topics
from voc_ml.topics.config import TopicConfig
from voc_ml.topics.preflight import preflight, select_corpus_input
from voc_ml.topics.representation_artifacts import export_representation


@pytest.fixture
def population():
    count = 2000
    frame = pd.DataFrame(
        {
            "record_id": [f"r{i:05}" for i in reversed(range(count))],
            "model_text": [f"comentario {i % 600}" for i in range(count)],
            "platform": ["google_play"] * 1200
            + ["app_store"] * 600
            + ["x"] * 199
            + ["youtube"],
            "record_date": ["2026-09-01"] * 1500 + ["2026-10-01"] * 499 + [None],
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
            "content_sha256": frame_hash(frame),
        },
    )
    vectors = np.zeros((count, 384), dtype="float32")
    vectors[np.arange(count), np.arange(count) % 384] = 1
    bundle = SimpleNamespace(
        reviews=frame,
        vectors=vectors,
        info={
            "dataset": snapshot_identity(snapshot.info, source="personal").as_dict(),
            "embedding_release_id": str(uuid4()),
            "profile": resolve_profile().as_dict(),
        },
    )
    return snapshot, bundle


def select(population, plan=None):
    snapshot, bundle = population
    return select_corpus_input(
        snapshot,
        "unused",
        "a" * 64,
        plan or TopicSelection("full", None, None),
        read_embeddings=lambda **_: bundle,
    )


@pytest.fixture
def full_input(population):
    return select(population)


def export(inputs, folder):
    count = len(inputs.reviews)
    assignments = pd.DataFrame(
        {
            "record_id": inputs.reviews.record_id,
            "topic_id": [0] * (count - 1) + [-1],
            "membership_strength": [0.7] * (count - 1) + [0.0],
        }
    )
    topics = [
        {
            "topic_id": 0,
            "size": count - 1,
            "keywords": [{"term": "acceso", "weight": 0.5}],
            "evidence_ids": [inputs.reviews.record_id.iloc[0]],
        },
        {"topic_id": -1, "size": 1, "keywords": [], "evidence_ids": []},
    ]
    execution = {
        "run_id": str(uuid4()),
        "input": inputs.info,
        "configuration": {
            "selection": inputs.info["selection"],
            "model": {"n_components": 5},
        },
    }
    return export_topics(
        inputs,
        assignments,
        topics,
        np.zeros((count, 5), dtype="float32"),
        execution,
        folder,
    )


def read(ref):
    return read_topics(ref["directory"], manifest_sha256=ref["manifest_sha256"])


def test_full_selection_preserves_positions_and_unknown_dates(population, full_input):
    snapshot, bundle = population
    assert len(full_input.reviews) == 2000
    assert full_input.reviews.record_id.is_monotonic_increasing
    assert full_input.info["selection"]["sample_seed"] is None
    assert (
        full_input.info["population_selection_sha256"]
        == full_input.info["selection_sha256"]
    )
    positions = full_input.reviews.source_row.to_numpy()
    assert np.array_equal(full_input.vectors, bundle.vectors[positions])
    assert (
        full_input.reviews.model_text.tolist()
        == snapshot.data.iloc[positions].model_text.tolist()
    )
    assert sum(x["selected"] for x in full_input.info["coverage"]) == 2000
    assert full_input.info["coverage"][-1]["period"] == "unknown"
    with pytest.raises(ValueError, match="size"):
        validate_selection(full_input.reviews)
    with pytest.raises(ValueError):
        TopicConfig(sample_size=2000)


def test_pilot_is_reproducible_and_preserves_population_identity(population):
    plan = TopicSelection("sample", 1500, 42)
    a, b = select(population, plan), select(population, plan)
    assert a.info == b.info
    assert len(a.reviews) == 1500
    assert (
        a.info["population_selection_sha256"]
        == select(population).info["selection_sha256"]
    )
    assert a.info["vectors_sha256"] != select(population).info["vectors_sha256"]
    assert np.array_equal(a.vectors, b.vectors)


@pytest.mark.parametrize(
    "args",
    [
        ("full", 1000, None),
        ("full", None, 42),
        ("unknown", None, None),
        ("sample", 100001, 42),
        ("sample", True, 42),
        ("sample", 5000, True),
        ("sample", 5000, -1),
        ("sample", None, 42),
    ],
)
def test_selection_rejects_ambiguous_or_unbounded_options(args):
    with pytest.raises(ValueError):
        TopicSelection(*args)


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "text", "position", "vectors"]
)
def test_full_input_rejects_incomplete_or_changed_rows(full_input, mutation):
    if mutation == "missing":
        full_input.reviews = full_input.reviews.iloc[:-1].copy()
        full_input.vectors = full_input.vectors[:-1]
        full_input.info["selected_rows"] -= 1
    elif mutation == "duplicate":
        full_input.reviews.loc[1, "record_id"] = full_input.reviews.loc[0, "record_id"]
    elif mutation == "text":
        full_input.reviews.loc[0, "model_text"] = "different"
    elif mutation == "position":
        full_input.reviews.loc[0, "source_row"] = 9999
    else:
        full_input.vectors[[0, 1]] = full_input.vectors[[1, 0]]
    full_input.info["selection_sha256"] = selection_hash(full_input.reviews)
    with pytest.raises(ValueError):
        full_input.validate()


def test_hash_has_no_whole_array_list_and_is_layout_independent(full_input):
    class NoList(np.ndarray):
        def tolist(self):
            raise AssertionError("Whole matrix list conversion")

    vectors = full_input.vectors.view(NoList)
    expected = vector_hash(vectors)
    assert expected == vector_hash(np.asfortranarray(vectors))
    assert expected == vector_hash(vectors.astype(">f4"))
    assert expected != vector_hash(vectors[::-1])
    with pytest.raises(ValueError):
        vector_hash(vectors.astype("float64"))
    vectors[0, 0] = np.nan
    with pytest.raises(ValueError):
        vector_hash(vectors)


def test_v2_roundtrip_and_independent_consumer(full_input, tmp_path):
    ref = export(full_input, tmp_path / "release")
    bundle = read(ref)
    assert bundle.info["schema_version"] == SCHEMA_V2
    assert bundle.info["counts"]["rows"] == 2000
    script = """import sys, importlib.abc
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in {'voc_ml', 'bertopic', 'umap', 'hdbscan', 'torch', 'zenml', 'mlflow'}:
   raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from voc.topics import read_topics
assert read_topics(sys.argv[1], manifest_sha256=sys.argv[2]).info['counts']['rows'] == 2000
"""
    p = subprocess.run(
        [sys.executable, "-c", script, ref["directory"], ref["manifest_sha256"]],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr


def test_v2_representation_reads_without_parent(full_input, tmp_path):
    ref = export(full_input, tmp_path / "parent")
    parent = read(ref)
    config = {"method": "keybert_mmr"}
    execution = {
        **parent.execution,
        "run_id": str(uuid4()),
        "representation_config": config,
        "fit": {"configuration": config},
    }
    result = export_representation(
        ref["directory"],
        ref["manifest_sha256"],
        parent.topics,
        execution,
        tmp_path / "child",
    )
    shutil.rmtree(ref["directory"])
    assert read(result).info["schema_version"] == "voc-local-topic-representation-v2"
    assert read(result).info["counts"]["rows"] == 2000


def test_v2_bounds_before_loading_table(full_input, tmp_path, monkeypatch):
    from voc.topics import files

    ref = export(full_input, tmp_path / "release")
    monkeypatch.setattr(files, "MAX_ROWS", 1000)
    with pytest.raises(ValueError, match="parquet shape"):
        read(ref)


def test_v2_corruption_and_full_membership_rejected(full_input, tmp_path):
    ref = export(full_input, tmp_path / "release")
    folder = tmp_path / "release"
    # Recompute checksums to reach semantic verification of a false population hash.
    execution = json.loads((folder / "execution.json").read_text())
    execution["input"]["population_selection_sha256"] = "f" * 64
    (folder / "execution.json").write_bytes(canonical_json(execution))
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["input"] = execution["input"]
    manifest["files"]["execution.json"] = {
        "sha256": file_hash(folder / "execution.json"),
        "size_bytes": (folder / "execution.json").stat().st_size,
    }
    (folder / "manifest.json").write_bytes(canonical_json(manifest))
    ref["manifest_sha256"] = file_hash(folder / "manifest.json")
    with pytest.raises(ValueError, match="population rows"):
        read(ref)


def test_preflight_read_only_and_explicitly_not_execution_ready(
    population, tmp_path, monkeypatch
):
    snapshot, bundle = population
    monkeypatch.chdir(tmp_path)
    calls = []

    def reader(**kwargs):
        calls.append(kwargs)
        return bundle

    result = preflight(
        snapshot.info["artifact_id"],
        "unused",
        "a" * 64,
        TopicSelection("full", None, None),
        read_dataset=lambda *, version, source: snapshot,
        read_embeddings=reader,
    )
    assert result["state"] == "inputs_verified"
    assert result["execution_ready"] is False
    assert result["fits"] == result["model_requests"] == 0
    assert result["vectors"]["bytes"] == 2000 * 384 * 4
    assert calls[0]["allow_subset"] is False
    assert not list(tmp_path.iterdir())


def test_preflight_cli_forwards_full_scope(tmp_path, monkeypatch):
    from voc_ml.cli import main

    def check(dataset, directory, digest, selection):
        assert selection == TopicSelection("full", None, None)
        return {"state": "inputs_verified", "execution_ready": False}

    monkeypatch.setattr("voc_ml.topics.preflight.preflight", check)
    result = CliRunner().invoke(
        main,
        [
            "topics",
            "preflight",
            "--dataset-version",
            str(uuid4()),
            "--embeddings",
            str(tmp_path),
            "--manifest-sha256",
            "a" * 64,
            "--scope",
            "full",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["execution_ready"] is False


@pytest.mark.parametrize("kind", ["missing", "count", "duplicate"])
def test_scope_rejects_bad_coverage(full_input, kind):
    rows = full_input.info["coverage"]
    if kind == "missing":
        rows.pop()
    elif kind == "count":
        rows[0]["selected"] -= 1
    else:
        rows.append(rows[0].copy())
    with pytest.raises(ValueError):
        full_input.validate()


def test_v2_file_bound_and_coordinate_shape(full_input, tmp_path, monkeypatch):
    from voc.topics.files import FILE_LIMITS

    ref = export(full_input, tmp_path / "release")
    monkeypatch.setitem(FILE_LIMITS, "selection.parquet", 1)
    with pytest.raises(ValueError, match="size mismatch"):
        read(ref)


def test_preflight_rejects_wrong_dataset_before_embedding_reads(population):
    snapshot, _ = population

    def forbidden(**kwargs):
        raise AssertionError("Embeddings must not be read")

    with pytest.raises(ValueError, match="differs from request"):
        preflight(
            str(uuid4()),
            "unused",
            "a" * 64,
            read_dataset=lambda **_: snapshot,
            read_embeddings=forbidden,
        )
