"""Local synchronous producer lifecycle. Successful encoding alone is not completion."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from voc.embeddings.contracts import canonical_json, require_positive_int, require_uuid
from voc.embeddings.profiles import resolve_profile
from voc.paths import data_root


def now():
    return datetime.now(UTC).isoformat()


def run_directory(run_id):
    require_uuid(run_id, "producer_run_id")
    return data_root() / "embeddings/runs" / run_id


def load_run(run_id):
    state = json.loads((run_directory(run_id) / "run.json").read_bytes())
    if (
        state["schema_version"] != "voc-embedding-producer-v1"
        or state["producer_run_id"] != run_id
    ):
        raise ValueError("Producer record identity mismatch")
    return state


def save_run(state):
    directory = run_directory(state["producer_run_id"])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(canonical_json(state))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(directory / "run.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update_run(run_id, **changes):
    """One synchronous local owner; steps and launcher update sequentially."""
    state = load_run(run_id)
    state.update(changes, updated_at=now())
    save_run(state)
    return state


def create_run(
    *,
    dataset_version="latest",
    source="s3",
    profile="minilm-verbatim-v1",
    limit=None,
    batch_size=32,
    local_files_only=False,
):
    resolve_profile(profile)
    if source != "s3":
        raise ValueError("Embedding inputs must use source=s3")
    if dataset_version != "latest":
        require_uuid(dataset_version, "dataset_version")
    if limit is not None:
        require_positive_int(limit, "limit")
    require_positive_int(batch_size, "batch_size")
    run_id = str(uuid4())
    folder = run_directory(run_id)
    folder.mkdir(parents=True)
    state = {
        "schema_version": "voc-embedding-producer-v1",
        "producer_run_id": run_id,
        "state": "running",
        "created_at": now(),
        "updated_at": now(),
        "zenml_run_id": None,
        "mlflow_run_id": None,
        "requested": {
            "dataset_version": dataset_version,
            "source": source,
            "profile": profile,
            "limit": limit,
            "batch_size": batch_size,
            "local_files_only": local_files_only,
        },
        "paths": {
            "run": str(folder),
            "cache": str(data_root() / "cache/embeddings/vectors.sqlite"),
            "models": str(data_root() / "models/sentence-transformers"),
        },
        "execution": "synchronous local orchestrator; shared persistent filesystem",
    }
    save_run(state)
    return state


def _launch(state):
    from mlflow.tracking import MlflowClient
    from zenml.client import Client

    from voc.storage import load_storage
    from voc_ml.identity import code_identity
    from voc_ml.pipelines.embeddings import embeddings_pipeline
    from voc_ml.tracking import (
        embedding_experiment_name,
        ensure_embedding_experiment,
        tracking_uri,
    )

    storage = load_storage()
    client = Client()
    stack = client.active_stack
    tracker = stack.experiment_tracker
    if (
        stack.orchestrator.flavor != "local"
        or tracker is None
        or tracker.name != "voc-mlflow"
        or tracker.config.tracking_uri != tracking_uri(storage)
        or stack.artifact_store.path.rstrip("/")
        != storage.zenml_artifact_uri.rstrip("/")
    ):
        raise ValueError(
            "Active stack differs from local VOC configuration; run voc-ml setup"
        )
    tracking = MlflowClient(tracking_uri=tracking_uri(storage))
    experiment_id = ensure_embedding_experiment(tracking, storage)
    update_run(
        state["producer_run_id"],
        experiment_id=experiment_id,
        experiment_name=embedding_experiment_name(storage),
        project_identity=code_identity(),
    )
    try:
        result = embeddings_pipeline.with_options(
            run_name="embeddings-" + state["producer_run_id"]
        )(
            producer_run_id=state["producer_run_id"],
            experiment_name=embedding_experiment_name(storage),
        )
    except Exception:
        # Preserve step diagnostics locally so inspect does not need a live server.
        try:
            failed = client.get_pipeline_run("embeddings-" + state["producer_run_id"])
            errors = {
                name: {
                    "source": item.exception_info.source,
                    "message": (item.exception_info.message or "")[:4000],
                }
                for name, item in failed.steps.items()
                if item.exception_info is not None
            }
            update_run(
                state["producer_run_id"],
                zenml_run_id=str(failed.id),
                step_errors=errors,
            )
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Could not retrieve failed ZenML step details"
            )
        raise
    if result is None:
        raise RuntimeError("ZenML did not return a completed pipeline run")
    run = client.get_pipeline_run(result.id)
    update_run(state["producer_run_id"], zenml_run_id=str(run.id))
    if run.status.value != "completed":
        raise RuntimeError("ZenML embedding pipeline did not complete")
    receipt = run.steps["track_embeddings"].outputs["tracking_report"][0].load()
    tracked = tracking.get_run(receipt["mlflow_run_id"])
    if (
        tracked.info.status != "FINISHED"
        or tracked.info.experiment_id != experiment_id
        or tracked.data.tags.get("producer_run_id") != state["producer_run_id"]
        or tracked.data.tags.get("zenml_run_id") != str(run.id)
    ):
        raise ValueError("MLflow run is incomplete or has different producer lineage")
    current = load_run(state["producer_run_id"])
    if (
        current["mlflow_run_id"] != receipt["mlflow_run_id"]
        or current["encoding_reference"] != receipt["encoding_reference"]
    ):
        raise ValueError("Tracked output differs from producer output")
    return current


def execute_run(run_id):
    """Persist failures, including Ctrl-C. A killed process stays incomplete/running."""
    state = load_run(run_id)
    if state["state"] != "running" or state.get("started_at"):
        raise ValueError(
            "Producer already launched; create a fresh run to reuse the cache"
        )
    update_run(run_id, started_at=now())
    try:
        state = _launch(state)
        from .artifacts import read_encoding

        report = read_encoding(
            state["encoding_reference"],
            expected_input=state["input_reference"],
            expected_profile=state["requested"]["profile"],
        )
        if report["batch_size"] != state["requested"]["batch_size"]:
            raise ValueError("Encoding batch size differs from request")
        return update_run(
            run_id,
            state="completed",
            completed_at=now(),
            dataset=report["dataset"],
            selection=report["selection"],
            feature=report["feature"],
            profile=report["profile"],
            encoder_id=report["encoder_id"],
            counts={
                key: report[key]
                for key in (
                    "rows",
                    "unique_texts",
                    "encoded_unique",
                    "reused_unique",
                    "truncated_rows",
                )
            },
        )
    except (Exception, KeyboardInterrupt, SystemExit) as error:  # noqa: BLE001 - persist every failed producer
        return update_run(
            run_id,
            state="failed",
            failed_at=now(),
            error={"type": type(error).__name__, "message": str(error)[:4000]},
        )


def inspect_run(run_id):
    """Read local state and revalidate completed outputs, without contacting services."""
    state = load_run(run_id)
    if state["state"] == "completed":
        from .artifacts import read_encoding

        if (
            not state.get("zenml_run_id")
            or not state.get("mlflow_run_id")
            or not state.get("completed_at")
        ):
            raise ValueError("Completed producer is missing tracking provenance")
        read_encoding(
            state["encoding_reference"],
            expected_input=state["input_reference"],
            expected_profile=state["requested"]["profile"],
        )
    return state
