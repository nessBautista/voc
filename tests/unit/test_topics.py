"""Local topic contracts, deterministic sampling, corruption and process boundaries."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from voc.datasets.shared.manifest import file_hash, frame_hash
from voc.embeddings.shared.contracts import (
    canonical_json,
    fingerprint,
    snapshot_identity,
)
from voc.embeddings.shared.profiles import resolve_profile
from voc.models import Snapshot
from voc.topics import read_topics
from voc.topics.contracts import selection_hash
from voc_ml.topics import TopicConfig, executor, production
from voc_ml.topics.artifacts import export_topics
from voc_ml.topics.selection import TopicInput, sample_rows, select_input
from voc_ml.topics.store import local_store


@pytest.fixture
def population():
    frame = pd.DataFrame(
        {
            "record_id": [f"r{i:03}" for i in range(100)],
            "model_text": [f"Text {i % 50}" for i in range(100)],
            "platform": ["google_play"] * 61
            + ["app_store"] * 27
            + ["x"] * 11
            + ["youtube"],
            "record_date": ["2026-09-03"] * 51 + ["2026-10-20"] * 48 + [None],
        }
    )
    return Snapshot(
        frame,
        {
            "artifact_id": str(uuid4()),
            "raw_revision_id": str(uuid4()),
            "run_id": str(uuid4()),
            "row_count": 100,
            "schema_version": "workable-v1",
            "content_sha256": frame_hash(frame),
        },
    )


@pytest.fixture
def inputs(population):
    config = TopicConfig(sample_size=40)
    frame = population.data.copy()
    frame["source_row"] = np.arange(len(frame))
    selected, coverage = sample_rows(frame, config)
    from voc.topics.contracts import SELECTION_COLUMNS

    selected = selected[list(SELECTION_COLUMNS)]
    vectors = np.zeros((40, 384), dtype="float32")
    vectors[:, 0] = 1
    info = {
        "dataset": snapshot_identity(population.info, source="personal").as_dict(),
        "embedding_release_id": str(uuid4()),
        "embedding_manifest_sha256": "b" * 64,
        "profile": resolve_profile().as_dict(),
        "sample_seed": 42,
        "sample_size": 40,
        "selection_policy": "platform-month-proportional-largest-remainder-v1",
        "coverage": coverage,
        "selection_sha256": selection_hash(selected),
        "vectors_sha256": fingerprint(vectors.tolist()),
    }
    return TopicInput(selected, vectors, info)


@pytest.fixture
def artifact(inputs, tmp_path):
    assignments = pd.DataFrame(
        {
            "record_id": inputs.reviews.record_id,
            "topic_id": [0] * 35 + [-1] * 5,
            "membership_strength": [0.8] * 35 + [0.0] * 5,
        }
    )
    topics = [
        {
            "topic_id": tid,
            "size": count,
            "keywords": [{"term": "word", "weight": 0.3}],
            "evidence_ids": [inputs.reviews.record_id.iloc[index]],
        }
        for tid, count, index in [(0, 35, 0), (-1, 5, 35)]
    ]
    coordinates = np.zeros((40, 5), dtype="float32")
    execution = {
        "run_id": str(uuid4()),
        "input": inputs.info,
        "configuration": TopicConfig(sample_size=40).as_dict(),
    }
    reference = export_topics(
        inputs, assignments, topics, coordinates, execution, tmp_path / "release"
    )
    return reference


def read(reference):
    return read_topics(
        reference["directory"], manifest_sha256=reference["manifest_sha256"]
    )


def rewrite(reference, name, transform):
    folder = Path(reference["directory"])
    path = folder / name
    if name.endswith("parquet"):
        frame = pd.read_parquet(path)
        transform(frame).to_parquet(path, index=False)
    else:
        value = json.loads(path.read_bytes())
        path.write_bytes(canonical_json(transform(value)))
    m = json.loads((folder / "manifest.json").read_bytes())
    m["files"][name] = {"sha256": file_hash(path), "size_bytes": path.stat().st_size}
    (folder / "manifest.json").write_bytes(canonical_json(m))
    return {**reference, "manifest_sha256": file_hash(folder / "manifest.json")}


def test_sample_reproducible_after_population_reordering(population):
    frame = population.data.assign(source_row=range(100))
    config = TopicConfig(sample_size=40)
    a, ca = sample_rows(frame, config)
    b, cb = sample_rows(frame.sample(frac=1, random_state=8), config)
    pd.testing.assert_frame_equal(a, b)
    assert ca == cb
    assert len(a) == a.record_id.nunique() == 40
    assert sum(row["selected"] for row in ca) == 40
    assert sum(row["population"] for row in ca) == 100
    assert any(row["selected"] == 0 for row in ca)
    c, _ = sample_rows(frame, replace(config, sample_seed=7))
    assert c.record_id.tolist() != a.record_id.tolist()


@pytest.mark.parametrize(
    "values",
    [
        {"sample_size": 1001},
        {"sample_size": True},
        {"sample_seed": -1},
        {"min_dist": float("nan")},
        {"n_components": 19, "sample_size": 20},
        {"threads": 5},
        {"timeout_seconds": 601},
        {"cluster_selection_method": "unknown"},
    ],
)
def test_config_rejects_unbounded_or_invalid_work(values):
    with pytest.raises(ValueError):
        TopicConfig(**values)


def test_select_uses_verified_vector_positions(population, monkeypatch):
    vectors = np.zeros((100, 384), dtype="float32")
    vectors[np.arange(100), np.arange(100)] = 1
    bundle = SimpleNamespace(
        reviews=population.data,
        vectors=vectors,
        info={
            "dataset": snapshot_identity(population.info, source="personal").as_dict(),
            "embedding_release_id": str(uuid4()),
            "profile": resolve_profile().as_dict(),
        },
    )
    calls = []

    def reader(**kwargs):
        calls.append(kwargs)
        return bundle

    monkeypatch.setattr("voc_ml.topics.selection.get_local_embeddings", reader)
    selected = select_input(population, "source", "b" * 64, TopicConfig(sample_size=40))
    assert calls[0]["allow_subset"] is False
    assert np.array_equal(selected.vectors.argmax(axis=1), selected.reviews.source_row)
    bundle.reviews = bundle.reviews.iloc[::-1]
    with pytest.raises(ValueError, match="order"):
        select_input(population, "source", "b" * 64, TopicConfig(sample_size=40))


@pytest.mark.parametrize("kind", ["text", "order", "vector"])
def test_inputs_reject_mutation(inputs, kind):
    if kind == "text":
        inputs.reviews.loc[0, "model_text"] = "changed"
    elif kind == "order":
        inputs.reviews = inputs.reviews.iloc[::-1]
    else:
        inputs.vectors[0, 0] = 0
    with pytest.raises(ValueError):
        inputs.validate()


def test_roundtrip_and_outlier_evidence(artifact):
    bundle = read(artifact)
    assert bundle.info["counts"] == {
        "rows": 40,
        "topic_count": 1,
        "unassigned_rows": 5,
        "unassigned_fraction": 0.125,
    }
    assert bundle.topic(-1)["size"] == 5
    assert (
        bundle.topic(0)["evidence"][0]["record_id"]
        == bundle.topics[0]["evidence_ids"][0]
    )
    with pytest.raises(LookupError):
        bundle.topic(999)


@pytest.mark.parametrize(
    "name",
    [
        "selection.parquet",
        "assignments.parquet",
        "topics.json",
        "coordinates.npy",
        "execution.json",
    ],
)
def test_file_corruption_is_rejected(artifact, name):
    with (Path(artifact["directory"]) / name).open("ab") as f:
        f.write(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        read(artifact)


def test_invalid_membership_even_with_updated_hash(artifact):
    wrong = read(artifact).assignments.record_id.iloc[-1]

    def change(topics):
        topics[0]["evidence_ids"] = [wrong]
        return topics

    with pytest.raises(ValueError, match="belong"):
        read(rewrite(artifact, "topics.json", change))


def test_reordered_assignments_even_with_updated_hash(artifact):
    with pytest.raises(ValueError, match="order"):
        read(rewrite(artifact, "assignments.parquet", lambda f: f.iloc[::-1]))


def test_wrong_execution_identity(artifact):
    with pytest.raises(ValueError, match="Execution/counts"):
        read(
            rewrite(artifact, "execution.json", lambda x: {**x, "run_id": str(uuid4())})
        )


def test_no_overwrite(inputs, artifact):
    bundle = read(artifact)
    with pytest.raises(FileExistsError):
        export_topics(
            inputs,
            bundle.assignments,
            bundle.topics,
            bundle.coordinates,
            bundle.execution,
            artifact["directory"],
        )


def test_reader_process_without_ml_modules(artifact):
    script = """import sys, importlib.abc, json
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in {'torch','bertopic','umap','hdbscan','zenml','mlflow','voc_ml'}:
   raise AssertionError('Unexpected model/workflow import: ' + fullname)
sys.meta_path.insert(0, Block())
from voc.topics import read_topics
b=read_topics(sys.argv[1], manifest_sha256=sys.argv[2])
print(json.dumps(b.info['counts']))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            artifact["directory"],
            artifact["manifest_sha256"],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["rows"] == 40


def test_public_cli(artifact):
    from voc.cli import main

    result = CliRunner().invoke(
        main,
        [
            "topics",
            "read",
            artifact["directory"],
            "--manifest-sha256",
            artifact["manifest_sha256"],
            "--topic-id",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["size"] == 35


def test_worker_timeout_stops_only_owned_process(tmp_path, monkeypatch):
    class Process:
        pid = 99

        def wait(self, timeout=None):
            if timeout != 5:
                raise subprocess.TimeoutExpired("worker", timeout)
            self.stopped = True
            return -15

        def poll(self):
            return None

    process = Process()
    signals = []
    monkeypatch.setattr(executor.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(
        executor.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )
    with pytest.raises(TimeoutError):
        executor.bounded_worker(tmp_path, TopicConfig(timeout_seconds=1))
    assert process.stopped and len(signals) == 1 and signals[0][0] == 99


def test_failed_producer_is_persisted_and_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path))
    state = production.create_run(str(uuid4()), tmp_path, "a" * 64)

    def fail(**kwargs):
        raise ValueError("invalid input")

    with pytest.raises(ValueError, match="invalid input"):
        production.execute_local(state["run_id"], read_dataset=fail)
    assert production.inspect_run(state["run_id"])["state"] == "failed"
    with pytest.raises(ValueError, match="already launched"):
        production.execute_local(state["run_id"])


def test_infinite_library_defaults_are_recorded_not_lost():
    from voc_ml.topics.discovery import portable_parameters

    result = portable_parameters(
        {
            "disconnection_distance": np.float64(np.inf),
            "n_neighbors": np.int64(15),
            "min_dist": 0.0,
        }
    )
    assert json.loads(canonical_json(result)) == {
        "disconnection_distance": "inf",
        "n_neighbors": 15,
        "min_dist": 0.0,
    }


def test_workflow_requires_successful_matching_tracking(tmp_path, monkeypatch):
    from types import ModuleType

    from voc_ml.topics.workflow import execute_workflow

    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path))
    state = production.create_run(str(uuid4()), tmp_path, "a" * 64)
    rid = state["run_id"]
    zid = str(uuid4())
    mid = uuid4().hex
    storage = SimpleNamespace(
        artifact_destination="local",
        zenml_artifact_uri="/tmp/artifacts",
        mlflow_artifact_uri="/tmp/ml-artifacts",
    )
    monkeypatch.setattr("voc.storage.load_storage", lambda: storage)
    monkeypatch.setattr("voc_ml.tracking.tracking_uri", lambda _: "local-test")
    stack = SimpleNamespace(
        orchestrator=SimpleNamespace(flavor="local"),
        experiment_tracker=SimpleNamespace(
            name="voc-mlflow", config=SimpleNamespace(tracking_uri="local-test")
        ),
        artifact_store=SimpleNamespace(path="/tmp/artifacts"),
    )
    receipt = {"run_id": rid, "mlflow_run_id": mid}
    zrun = SimpleNamespace(
        id=zid,
        status=SimpleNamespace(value="completed"),
        steps={
            "track_topics": SimpleNamespace(
                outputs={"topic_tracking": [SimpleNamespace(load=lambda: receipt)]}
            )
        },
    )

    class ZClient:
        active_stack = stack

        def get_pipeline_run(self, _):
            return zrun

    class MClient:
        def __init__(self, **kwargs):
            pass

        def get_run(self, _):
            return SimpleNamespace(
                info=SimpleNamespace(status="FAILED", experiment_id="1"),
                data=SimpleNamespace(tags={"topic_run_id": rid, "zenml_run_id": zid}),
            )

    class Pipeline:
        def with_options(self, **kwargs):
            return self

        def __call__(self, **kwargs):
            local_store().update(
                rid, state="running", zenml_run_id=zid, mlflow_run_id=mid
            )
            return SimpleNamespace(id=zid)

    for name, attrs in {
        "mlflow": {},
        "mlflow.tracking": {"MlflowClient": MClient},
        "zenml": {},
        "zenml.client": {"Client": ZClient},
        "voc_ml.pipelines.topics": {"topics_pipeline": Pipeline()},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr("voc_ml.tracking.ensure_topic_experiment", lambda *a: "1")
    with pytest.raises(ValueError, match="Tracking lineage"):
        execute_workflow(rid)
    assert local_store().load(rid)["state"] == "failed"
    assert not (local_store().directory(rid) / "release").exists()


def test_all_unassigned_is_a_valid_result(inputs, tmp_path):
    assignments = pd.DataFrame(
        {
            "record_id": inputs.reviews.record_id,
            "topic_id": [-1] * 40,
            "membership_strength": [0.0] * 40,
        }
    )
    topics = [{"topic_id": -1, "size": 40, "keywords": [], "evidence_ids": []}]
    execution = {
        "run_id": str(uuid4()),
        "input": inputs.info,
        "configuration": TopicConfig(sample_size=40).as_dict(),
    }
    ref = export_topics(
        inputs,
        assignments,
        topics,
        np.zeros((40, 5), dtype="float32"),
        execution,
        tmp_path / "all-outliers",
    )
    assert read(ref).info["counts"]["topic_count"] == 0
    assert read(ref).info["counts"]["unassigned_fraction"] == 1


@pytest.fixture
def explicit_run(tmp_path, population):
    from voc_ml.topics.store import TopicRunStore

    store = TopicRunStore(tmp_path / "owned")
    state = production.create_run(
        population.info["artifact_id"],
        tmp_path / "embeddings",
        "b" * 64,
        TopicConfig(sample_size=40),
        store=store,
        provenance=lambda: {
            "code_identity": "a" * 64,
            "git_commit": None,
            "git_status": None,
        },
    )
    return store, state["run_id"]


def synthetic_worker(artifact):
    """Supply saved synthetic fit files, never invoke a model or a process."""
    import shutil

    def execute(folder, config):
        source = Path(artifact["directory"])
        for name in ("assignments.parquet", "topics.json", "coordinates.npy"):
            shutil.copyfile(source / name, folder / name)
        counts = read(artifact).info["counts"]
        (folder / "fit.json").write_bytes(
            canonical_json({"counts": counts, "peak_worker_rss_bytes": 1024})
        )

    return execute


def test_explicit_store_ignores_environment_and_preserves_local_contract(
    explicit_run, population, inputs, artifact, tmp_path, monkeypatch
):
    store, run_id = explicit_run
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path / "unrelated"))
    state = production.execute_local(
        run_id,
        store=store,
        snapshot=population,
        select=lambda *args: inputs,
        worker=synthetic_worker(artifact),
    )
    assert state["state"] == "completed"
    assert state["zenml_run_id"] is state["mlflow_run_id"] is None
    assert not (tmp_path / "unrelated").exists()
    assert production.inspect_run(run_id, store=store) == state
    recovered = production.read_run_input(run_id, store=store)
    pd.testing.assert_frame_equal(recovered.reviews, inputs.reviews)
    np.testing.assert_array_equal(recovered.vectors, inputs.vectors)
    with pytest.raises(ValueError, match="already launched"):
        production.execute_local(run_id, store=store)


def test_input_reader_rejects_modified_vectors(explicit_run, population, inputs):
    store, run_id = explicit_run
    production.pin_run(run_id, population, store=store, select=lambda *args: inputs)
    changed = inputs.vectors.copy()
    changed[0] *= -1  # Still normalized, but no longer the pinned vector.
    np.save(store.directory(run_id) / "work/vectors.npy", changed, allow_pickle=False)
    with pytest.raises(ValueError, match="vectors changed"):
        production.read_run_input(run_id, store=store)


def test_invalid_request_has_no_storage_or_provenance_effects(tmp_path):
    from voc_ml.topics.store import TopicRunStore

    def forbidden():
        raise AssertionError("Invalid input reached provenance adapter")

    with pytest.raises(ValueError):
        production.create_run(
            "latest",
            tmp_path,
            "a" * 64,
            store=TopicRunStore(tmp_path / "unused"),
            provenance=forbidden,
        )
    assert not (tmp_path / "unused").exists()


def test_worker_failure_is_durable_without_replay(explicit_run, population, inputs):
    store, run_id = explicit_run
    calls = []

    def fail(folder, config):
        calls.append(folder)
        raise TimeoutError("synthetic timeout")

    with pytest.raises(TimeoutError):
        production.execute_local(
            run_id,
            store=store,
            snapshot=population,
            select=lambda *args: inputs,
            worker=fail,
        )
    assert store.load(run_id)["state"] == "failed"
    assert store.load(run_id)["error"]["type"] == "TimeoutError"
    assert not (store.directory(run_id) / "release").exists()
    with pytest.raises(ValueError):
        production.fit_run(run_id, store=store, worker=fail)
    assert len(calls) == 1


def test_tracked_operation_accepts_verified_receipt(
    explicit_run, population, inputs, artifact
):
    from voc_ml.topics.workflow import execute_workflow

    store, run_id = explicit_run
    zid, mid = str(uuid4()), uuid4().hex

    def workflow(rid, owned_store):
        production.pin_run(
            rid, population, store=owned_store, select=lambda *args: inputs
        )
        production.fit_run(rid, store=owned_store, worker=synthetic_worker(artifact))
        owned_store.update(rid, zenml_run_id=zid, mlflow_run_id=mid)
        return {"run_id": rid, "zenml_run_id": zid, "mlflow_run_id": mid}

    result = execute_workflow(run_id, store=store, run_workflow=workflow)
    assert result["state"] == "completed"
    assert read(result["result"]).execution["mlflow_run_id"] == mid


def test_tracked_operation_rejects_wrong_receipt(explicit_run):
    from voc_ml.topics.workflow import execute_workflow

    store, run_id = explicit_run
    with pytest.raises(ValueError, match="Tracking lineage"):
        execute_workflow(
            run_id,
            store=store,
            run_workflow=lambda rid, _: {
                "run_id": rid,
                "zenml_run_id": "other",
                "mlflow_run_id": "other",
            },
        )
    assert store.load(run_id)["state"] == "failed"
    assert not (store.directory(run_id) / "release").exists()


def test_export_sync_failure_leaves_no_partial_release(
    inputs, artifact, tmp_path, monkeypatch
):
    from voc_ml.topics import artifacts

    source = read(artifact)
    before = {p.name: file_hash(p) for p in Path(artifact["directory"]).iterdir()}
    destination = tmp_path / "new-release"

    def failed_sync(_):
        raise OSError("simulated flush failure")

    monkeypatch.setattr(artifacts.os, "fsync", failed_sync)
    with pytest.raises(OSError, match="flush failure"):
        export_topics(
            inputs,
            source.assignments,
            source.topics,
            source.coordinates,
            source.execution,
            destination,
        )
    assert not destination.exists()
    assert not list(tmp_path.glob(".topics-*"))
    assert before == {
        p.name: file_hash(p) for p in Path(artifact["directory"]).iterdir()
    }


def test_export_syncs_before_and_after_visibility(
    inputs, artifact, tmp_path, monkeypatch
):
    from voc_ml.topics import artifacts

    source = read(artifact)
    destination = tmp_path / "durable-release"
    events = []
    real_sync = artifacts.sync_directory

    def sync(path):
        events.append((Path(path), destination.exists()))
        real_sync(path)

    monkeypatch.setattr(artifacts, "sync_directory", sync)
    result = export_topics(
        inputs,
        source.assignments,
        source.topics,
        source.coordinates,
        source.execution,
        destination,
    )
    assert [visible for _, visible in events] == [False, True]
    assert events[1][0] == destination.parent
    assert read(result).info == source.info
    assert result["manifest_sha256"] == artifact["manifest_sha256"]


def test_state_sync_failure_preserves_previous_record(explicit_run, monkeypatch):
    from voc_ml.topics import store as storage

    store, run_id = explicit_run
    previous = store.load(run_id)

    def failed_sync(_):
        raise OSError("simulated record flush failure")

    monkeypatch.setattr(storage.os, "fsync", failed_sync)
    with pytest.raises(OSError):
        store.update(run_id, state="running")
    assert store.load(run_id) == previous
    assert [p.name for p in store.directory(run_id).iterdir()] == ["run.json"]


def test_steps_keep_explicit_root_and_tracking_identity(
    explicit_run, population, inputs, artifact, tmp_path, monkeypatch
):
    pytest.importorskip("zenml")
    mlflow = pytest.importorskip("mlflow")
    from voc_ml.steps import topics as steps

    store, run_id = explicit_run
    zid, mid = str(uuid4()), uuid4().hex
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path / "wrong-root"))
    monkeypatch.setattr("voc.datasets.get_dataset", lambda **kwargs: population)
    monkeypatch.setattr(production, "select_input", lambda *args: inputs)
    monkeypatch.setattr(production, "bounded_worker", synthetic_worker(artifact))
    monkeypatch.setattr(
        steps,
        "get_step_context",
        lambda: SimpleNamespace(pipeline_run=SimpleNamespace(id=zid)),
    )
    monkeypatch.setattr(
        mlflow, "active_run", lambda: SimpleNamespace(info=SimpleNamespace(run_id=mid))
    )
    logged = {}
    monkeypatch.setattr(mlflow, "set_tags", lambda values: logged.update(tags=values))
    monkeypatch.setattr(
        mlflow, "log_params", lambda values: logged.update(params=values)
    )
    monkeypatch.setattr(
        mlflow, "log_metrics", lambda values: logged.update(metrics=values)
    )
    monkeypatch.setattr(
        mlflow, "log_dict", lambda value, name: logged.update({name: value})
    )
    root = str(store.root)
    pinned = steps.pin_topics.entrypoint(run_id, root)
    fitted = steps.fit_topics.entrypoint(pinned, root)
    receipt = steps.track_topics.entrypoint(fitted, root)
    assert receipt == {"run_id": run_id, "mlflow_run_id": mid}
    assert logged["tags"]["zenml_run_id"] == zid
    assert logged["params"]["dataset_artifact_id"] == population.info["artifact_id"]
    assert logged["metrics"]["rows"] == 40
    assert not (tmp_path / "wrong-root").exists()
    assert store.load(run_id)["mlflow_run_id"] == mid
    # Step execution alone cannot claim completion before tracking is verified.
    assert store.load(run_id)["state"] == "running"
