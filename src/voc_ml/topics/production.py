"""Reusable topic lifecycle. Storage and fitting processes are explicit collaborators."""

import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

import numpy as np
import pandas as pd

from voc.embeddings.shared.contracts import canonical_json, require_uuid
from voc.topics import read_topics

from .artifacts import export_topics
from .config import TopicConfig
from .executor import bounded_worker
from .provenance import code_provenance
from .selection import TopicInput, select_input
from .store import local_store, now


def create_run(
    dataset_version,
    embedding_directory,
    embedding_manifest_sha256,
    config=None,
    *,
    store=None,
    provenance=None,
):
    from voc.embeddings.shared.contracts import require_digest

    require_uuid(dataset_version, "dataset_version")
    require_digest(embedding_manifest_sha256, "embedding_manifest_sha256")
    config = config or TopicConfig()
    config.__post_init__()
    store = store if store is not None else local_store()
    provenance = provenance if provenance is not None else code_provenance
    identity = provenance()
    run_id = str(uuid4())
    store.directory(run_id).mkdir(parents=True, exist_ok=False)
    state = {
        "schema_version": "voc-topic-producer-v1",
        "run_id": run_id,
        "state": "created",
        "created_at": now(),
        "updated_at": now(),
        "configuration": config.as_dict(),
        "requested": {
            "dataset_version": dataset_version,
            "embedding_directory": str(Path(embedding_directory).resolve()),
            "embedding_manifest_sha256": embedding_manifest_sha256,
        },
        "code_identity": identity["code_identity"],
        "git_commit": identity["git_commit"],
        "git_status": identity["git_status"],
        "zenml_run_id": None,
        "mlflow_run_id": None,
    }
    store.save(state)
    return state


def pin_run(run_id, snapshot=None, *, store=None, read_dataset=None, select=None):
    store = store if store is not None else local_store()
    select = select if select is not None else select_input
    state = store.load(run_id)
    if state["state"] not in ("created", "running") or state.get("input"):
        raise ValueError("Input is already pinned or producer cannot be started")
    if state.get("parent"):
        parent = state["parent"]
        # Resolve from this store, never from a stale absolute path in another host.
        bundle = read_topics(
            store.directory(parent["run_id"]) / "release",
            manifest_sha256=parent["manifest_sha256"],
        )
        inputs = read_run_input(parent["run_id"], store=store)
        if inputs.info != bundle.info["input"] or not inputs.reviews.equals(
            bundle.reviews
        ):
            raise ValueError("Parent working input differs from its verified release")
    elif snapshot is None:
        if read_dataset is None:
            from voc.datasets import get_dataset

            read_dataset = get_dataset
        snapshot = read_dataset(
            stage="prepared",
            version=state["requested"]["dataset_version"],
            source="personal",
        )
    if not state.get("parent"):
        if snapshot.info["artifact_id"] != state["requested"]["dataset_version"]:
            raise ValueError("Resolved dataset differs from request")
        request = state["requested"]
        inputs = select(
            snapshot,
            request["embedding_directory"],
            request["embedding_manifest_sha256"],
            TopicConfig(**state["configuration"]),
        )
    if state.get("expected_input") and inputs.info != state["expected_input"]:
        raise ValueError("Study input differs from frozen protocol")
    folder = store.directory(run_id) / "work"
    folder.mkdir()
    inputs.reviews.to_parquet(folder / "input.parquet", index=False)
    np.save(folder / "vectors.npy", inputs.vectors, allow_pickle=False)
    (folder / "request.json").write_bytes(
        canonical_json(
            {
                "input": inputs.info,
                "configuration": state["configuration"],
                "operation": state.get("operation", "grouping"),
                "parent": state.get("parent"),
                "parent_directory": str(
                    store.directory(state["parent"]["run_id"]) / "release"
                )
                if state.get("parent")
                else None,
                "representation_config": state.get("representation_config"),
                "expected_software": state.get("expected_software"),
                "expected_encoder": state.get("expected_encoder"),
                "models_directory": str(store.root / "models/sentence-transformers"),
            }
        )
    )
    store.update(run_id, state="running", started_at=now(), input=inputs.info)
    return {"run_id": run_id, "selection_sha256": inputs.info["selection_sha256"]}


def fit_run(run_id, *, store=None, worker=None):
    store = store if store is not None else local_store()
    worker = worker if worker is not None else bounded_worker
    state = store.load(run_id)
    if state["state"] != "running" or state.get("fit_started_at"):
        raise ValueError("Fit cannot be replayed. Create a fresh run")
    store.update(run_id, fit_started_at=now())
    folder = store.directory(run_id) / "work"
    started = monotonic()
    worker(folder, TopicConfig(**state["configuration"]))
    report = json.loads((folder / "fit.json").read_bytes())
    store.update(run_id, fit=report, worker_wall_seconds=monotonic() - started)
    return {"run_id": run_id, "counts": report["counts"]}


def finish_run(run_id, *, store=None):
    store = store if store is not None else local_store()
    state = store.load(run_id)
    if state["state"] != "running" or not state.get("fit"):
        raise ValueError("No completed fit to export")
    folder = store.directory(run_id) / "work"
    inputs = read_run_input(run_id, store=store)
    execution = {
        key: state[key]
        for key in (
            "run_id",
            "input",
            "configuration",
            "code_identity",
            "git_commit",
            "git_status",
            "zenml_run_id",
            "mlflow_run_id",
            "fit",
            "started_at",
            "worker_wall_seconds",
        )
    }
    if state.get("operation") == "representation":
        from .representation_artifacts import export_representation

        execution["representation_config"] = state["representation_config"]
        reference = export_representation(
            store.directory(state["parent"]["run_id"]) / "release",
            state["parent"]["manifest_sha256"],
            json.loads((folder / "topics.json").read_bytes()),
            execution,
            store.directory(run_id) / "release",
        )
        return store.update(
            run_id, state="completed", completed_at=now(), result=reference
        )
    reference = export_topics(
        inputs,
        pd.read_parquet(folder / "assignments.parquet"),
        json.loads((folder / "topics.json").read_bytes()),
        np.load(folder / "coordinates.npy", allow_pickle=False),
        execution,
        store.directory(run_id) / "release",
    )
    return store.update(run_id, state="completed", completed_at=now(), result=reference)


def inspect_run(run_id, *, store=None):
    store = store if store is not None else local_store()
    state = store.load(run_id)
    if state["state"] == "completed":
        result = state["result"]
        bundle = read_topics(
            result["directory"], manifest_sha256=result["manifest_sha256"]
        )
        if bundle.info["run_id"] != run_id or bundle.info["input"] != state["input"]:
            raise ValueError("Producer and exported lineage differ")
    return state


def execute_local(
    run_id, *, snapshot=None, store=None, worker=None, read_dataset=None, select=None
):
    """Explicit untracked execution for offline validation. Never claims workflow tracking."""
    store = store if store is not None else local_store()
    if store.load(run_id)["state"] != "created":
        raise ValueError("Producer already launched")
    try:
        pin_run(run_id, snapshot, store=store, read_dataset=read_dataset, select=select)
        fit_run(run_id, store=store, worker=worker)
        return finish_run(run_id, store=store)
    except (Exception, KeyboardInterrupt) as error:
        store.update(
            run_id,
            state="failed",
            error={"type": type(error).__name__, "message": str(error)[:2000]},
        )
        raise


def read_run_input(run_id, *, store=None):
    """Read the pinned working input without models or tracking services."""
    store = store if store is not None else local_store()
    state = store.load(run_id)
    if not state.get("input"):
        raise ValueError("Producer input has not been pinned")
    folder = store.directory(run_id) / "work"
    inputs = TopicInput(
        pd.read_parquet(folder / "input.parquet"),
        np.load(folder / "vectors.npy", allow_pickle=False),
        state["input"],
    )
    inputs.validate()
    return inputs


def create_variant(
    parent_run_id,
    parent_manifest_sha256,
    config,
    *,
    store,
    representation_config=None,
    provenance=None,
):
    """Create a fresh run with a verified immutable parent and identical input."""
    parent = read_topics(
        store.directory(parent_run_id) / "release",
        manifest_sha256=parent_manifest_sha256,
    )
    if parent.info["run_id"] != parent_run_id:
        raise ValueError("Parent producer identity differs from release")
    if parent.info["schema_version"] != "voc-local-topics-v1":
        raise ValueError("Variant parent must be a grouping result")
    inputs = read_run_input(parent_run_id, store=store)
    if inputs.info != parent.info["input"] or not inputs.reviews.equals(parent.reviews):
        raise ValueError("Parent input differs from release")
    if (
        config.sample_size != inputs.info["sample_size"]
        or config.sample_seed != inputs.info["sample_seed"]
    ):
        raise ValueError("Variant must preserve selection")
    if representation_config is not None:
        representation_config.__post_init__()
        if config.as_dict() != parent.info["configuration"]:
            raise ValueError("Representation must preserve grouping configuration")
    original = store.load(parent_run_id)
    state = create_run(
        inputs.info["dataset"]["artifact_id"],
        original["requested"]["embedding_directory"],
        inputs.info["embedding_manifest_sha256"],
        config,
        store=store,
        provenance=provenance,
    )
    return store.update(
        state["run_id"],
        operation="representation" if representation_config else "grouping",
        parent={"run_id": parent_run_id, "manifest_sha256": parent_manifest_sha256},
        expected_input=inputs.info,
        expected_software=parent.execution["fit"]["software"],
        representation_config=representation_config.as_dict()
        if representation_config
        else None,
    )
