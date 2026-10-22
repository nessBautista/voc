"""Synchronous discovery lifecycle, with finalization after verified tracking.

A run is allocated before work. Only the atomic result-store writer creates a
candidate, after ZenML and MLflow finish. Failed runs retain computational caches.
"""

import json
import os
import tempfile
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from voc.embeddings.contracts import canonical_json, fingerprint, require_uuid
from voc.embeddings.profiles import resolve_profile
from voc.paths import data_root
from voc_ml.sentiment import SentimentConfig

from . import artifacts
from .config import AnalysisConfig, SelectionConfig


def now():
    return datetime.now(UTC).isoformat()


def run_directory(run_id):
    require_uuid(run_id, "run_id")
    return data_root() / "analysis-runs" / run_id


def load_run(run_id):
    state = json.loads((run_directory(run_id) / "run.json").read_bytes())
    if (
        state["schema_version"] != "voc-analysis-producer-v1"
        or state["run_id"] != run_id
    ):
        raise ValueError("Workflow record identity mismatch")
    return state


def save_run(state):
    directory = run_directory(state["run_id"])
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
    state = load_run(run_id)
    state.update(changes, updated_at=now())
    save_run(state)
    return state


def create_run(
    *,
    kind="analysis",
    dataset_version="latest",
    embedding_version="latest",
    profile="minilm-verbatim-v1",
    source="s3",
    limit=None,
    config=None,
    sentiment_config=None,
    local_files_only=True,
):
    if kind not in ("analysis", "sentiment") or source != "s3":
        raise ValueError("Use an analysis or sentiment workflow with source=s3")
    for value in (dataset_version, embedding_version):
        if value != "latest":
            require_uuid(value, "release version")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer")
    if type(local_files_only) is not bool:
        raise ValueError("local_files_only must be boolean")
    resolve_profile(profile)
    config = AnalysisConfig() if config is None else config
    sentiment_config = (
        SentimentConfig() if sentiment_config is None else sentiment_config
    )
    config = AnalysisConfig.from_dict(config.as_dict())
    sentiment_config.__post_init__()
    # CLI --limit explicitly overrides the TOML selection, always deterministically.
    if limit is not None:
        config = replace(config, selection=SelectionConfig("first-n", limit))
    if kind == "analysis" and config.selection.kind != "full":
        config.validate_row_count(config.selection.size)
    run_id = str(uuid4())
    run_directory(run_id).mkdir(parents=True, exist_ok=False)
    state = {
        "schema_version": "voc-analysis-producer-v1",
        "run_id": run_id,
        "kind": kind,
        "state": "running",
        "created_at": now(),
        "updated_at": now(),
        "analysis_id": None,
        "zenml_run_id": None,
        "mlflow_run_id": None,
        "requested": {
            "dataset_version": dataset_version,
            "embedding_version": embedding_version,
            "profile": profile,
            "source": source,
            "limit": limit,
            "local_files_only": local_files_only,
        },
        "configuration": {
            "analysis": config.as_dict(),
            "sentiment": asdict(sentiment_config),
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
    from voc_ml.pipelines.analysis import analysis_pipeline, sentiment_pipeline
    from voc_ml.tracking import (
        analysis_experiment_name,
        ensure_analysis_experiment,
        tracking_uri,
    )

    storage, client = load_storage(), Client()
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
    experiment_id = ensure_analysis_experiment(tracking, storage, state["kind"])
    update_run(
        state["run_id"],
        experiment_id=experiment_id,
        experiment_name=analysis_experiment_name(storage, state["kind"]),
        project_identity=code_identity(),
    )
    pipeline = analysis_pipeline if state["kind"] == "analysis" else sentiment_pipeline
    name = state["kind"] + "-" + state["run_id"]
    try:
        result = pipeline.with_options(run_name=name)(
            run_id=state["run_id"],
            experiment_name=analysis_experiment_name(storage, state["kind"]),
        )
    except Exception:
        try:
            failed = client.get_pipeline_run(name)
            update_run(
                state["run_id"],
                zenml_run_id=str(failed.id),
                step_errors={
                    n: {
                        "source": s.exception_info.source,
                        "message": (s.exception_info.message or "")[:4000],
                    }
                    for n, s in failed.steps.items()
                    if s.exception_info is not None
                },
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
    update_run(state["run_id"], zenml_run_id=str(run.id))
    if run.status.value != "completed":
        raise RuntimeError("Discovery pipeline did not complete")
    current = load_run(state["run_id"])
    verify_tracking(tracking, current)
    return update_run(state["run_id"], tracking_verified=True)


def verify_tracking(client, state):
    """Require finished lineage and round-trip the uploaded evidence before visibility."""
    tracked = client.get_run(state["mlflow_run_id"])
    if (
        tracked.info.status != "FINISHED"
        or tracked.info.experiment_id != state["experiment_id"]
        or tracked.data.tags.get("producer_run_id") != state["run_id"]
        or tracked.data.tags.get("zenml_run_id") != state["zenml_run_id"]
    ):
        raise ValueError("MLflow run is incomplete or has different lineage")
    expected = artifacts.read_json(state["evidence_reference"])
    with tempfile.TemporaryDirectory() as directory:
        path = client.download_artifacts(
            state["mlflow_run_id"], "workflow-evidence.json", directory
        )
        actual = json.loads(Path(path).read_bytes())
    if fingerprint(actual) != fingerprint(expected):
        raise ValueError("Uploaded MLflow evidence differs from validated outputs")


def finalize(state):
    """The only candidate publication point. No S3 analysis publication occurs here."""
    if not state.get("tracking_verified"):
        raise ValueError("Tracking evidence must be verified before finalization")
    from voc.analysis.store import write_analysis

    evidence = artifacts.read_json(state["evidence_reference"])
    expected = {
        "input": state["input_reference"],
        "sentiment": state["sentiment_reference"],
        "topics": state.get("topic_reference"),
    }
    if (
        evidence["references"] != expected
        or evidence["run_id"] != state["run_id"]
        or evidence["configuration"] != state["configuration"]
        or evidence["code_sha256"] != state["project_identity"]
        or evidence["zenml_run_id"] != state["zenml_run_id"]
    ):
        raise ValueError("Validated evidence no longer matches producer outputs")
    if state["kind"] == "sentiment":
        from .workflow import read_sentiment_input

        reviews, _ = read_sentiment_input(state["input_reference"])
        artifacts.read_sentiments(state["sentiment_reference"], reviews.record_id)
        return {"counts": evidence["sentiment"]["counts"]}
    inputs = artifacts.read_input(state["input_reference"])
    topics = artifacts.read_topics(state["topic_reference"], inputs)
    sentiments, _ = artifacts.read_sentiments(
        state["sentiment_reference"], inputs.reviews.record_id
    )
    manifest = write_analysis(
        inputs.reviews,
        topics.assignments,
        topics.topics,
        sentiments,
        analysis_id=state["candidate_id"],
        inputs=inputs.info,
        configuration=state["configuration"],
        code_sha256=state["project_identity"],
        software=evidence["software"],
        provenance={
            "run_id": state["run_id"],
            "zenml_run_id": state["zenml_run_id"],
            "mlflow_run_id": state["mlflow_run_id"],
            "tracking_verified": True,
        },
        reports={"workflow-evidence.json": canonical_json(evidence)},
    )
    return {"analysis_id": manifest.analysis_id, "counts": manifest.coverage}


def execute_run(run_id):
    state = load_run(run_id)
    if state["state"] != "running" or state.get("started_at"):
        raise ValueError("Run already launched; create a fresh run to reuse caches")
    # A distinct candidate ID makes a storage commit discoverable after interruption.
    update_run(
        run_id,
        started_at=now(),
        candidate_id=str(uuid4()) if state["kind"] == "analysis" else None,
    )
    try:
        state = _launch(load_run(run_id))
        result = finalize(state)
        return update_run(run_id, state="completed", completed_at=now(), **result)
    except (Exception, KeyboardInterrupt, SystemExit) as error:  # noqa: BLE001 - persist failed lifecycle
        return update_run(
            run_id,
            state="failed",
            failed_at=now(),
            error={"type": type(error).__name__, "message": str(error)[:4000]},
        )


def inspect_run(run_id):
    """Local inspection, including incomplete runs, without a live tracking service."""
    state = load_run(run_id)
    if state["state"] == "completed":
        if not all(
            state.get(k) for k in ("zenml_run_id", "mlflow_run_id", "tracking_verified")
        ):
            raise ValueError("Completed run is missing tracking provenance")
        artifacts.read_json(state["evidence_reference"])
        if state["kind"] == "analysis":
            from voc.analysis.store import read_analysis

            result = read_analysis(state["analysis_id"])
            if result.manifest.provenance["run_id"] != run_id:
                raise ValueError("Analysis belongs to a different producer")
        else:
            from .workflow import read_sentiment_input

            reviews, _ = read_sentiment_input(state["input_reference"])
            artifacts.read_sentiments(state["sentiment_reference"], reviews.record_id)
    return state
