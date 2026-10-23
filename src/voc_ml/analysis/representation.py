"""Synchronous representation experiments over verified local candidates.

This small workflow has its own MLflow record. It does not rerun the discovery
ZenML pipeline. Candidate creation follows successful tracking verification.
"""

import json
import platform
import tempfile
from dataclasses import asdict, replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from voc.analysis.contracts import validate_results
from voc.analysis.store import read_analysis, write_analysis
from voc.datasets.manifest import file_hash, frame_hash
from voc.embeddings.contracts import canonical_json, fingerprint, require_uuid
from voc.paths import data_root
from voc_ml.identity import code_identity
from voc_ml.topics.representation import load_config, refine


def preserved_content(saved):
    """Hash the values we promise to preserve, independently of DuckDB bytes."""
    return {
        "reviews": frame_hash(saved.reviews),
        "assignments": fingerprint([asdict(row) for row in saved.assignments]),
        "sentiments": fingerprint([asdict(row) for row in saved.sentiments]),
        "evidence": fingerprint(
            [
                {
                    "topic_id": t.topic_id,
                    "evidence_ids": t.evidence_ids,
                    "name": t.name,
                    "description": t.description,
                }
                for t in saved.topics
            ]
        ),
    }


def _start_tracking(run_id, parent_id, configuration, child_id):
    from mlflow.tracking import MlflowClient

    from voc.storage import load_storage
    from voc_ml.tracking import ensure_analysis_experiment, tracking_uri

    storage = load_storage()
    client = MlflowClient(tracking_uri=tracking_uri(storage))
    experiment = ensure_analysis_experiment(client, storage)
    run = client.create_run(
        experiment,
        tags={
            "mlflow.runName": "representation-" + run_id,
            "workflow_kind": "representation",
            "producer_run_id": run_id,
            "parent_analysis_id": parent_id,
            "analysis_id": child_id,
        },
    )
    return client, run.info.run_id


def _finish_tracking(client, tracking_id, report):
    for key, value in report["configuration"]["representation"].items():
        client.log_param(tracking_id, "representation." + key, value)
    refinement = report["refinement"]
    client.log_metric(tracking_id, "changed_topics", refinement["changed_topics"])
    client.log_metric(tracking_id, "duration_seconds", refinement["duration_seconds"])
    encoder = refinement["encoder"]
    if encoder:
        for key in (
            "encoded_unique",
            "reused_texts",
            "inference_seconds",
            "truncated_unique",
        ):
            client.log_metric(tracking_id, key, encoder[key])
    client.log_dict(tracking_id, report, "representation-evidence.json")
    with tempfile.TemporaryDirectory() as directory:
        actual = client.download_artifacts(
            tracking_id, "representation-evidence.json", directory
        )
        if fingerprint(json.loads(Path(actual).read_bytes())) != fingerprint(report):
            raise ValueError("MLflow representation evidence round-trip differs")
    client.set_terminated(tracking_id, status="FINISHED")
    if client.get_run(tracking_id).info.status != "FINISHED":
        raise ValueError("Representation tracking did not finish")


def execute_representation(parent_id, config_path, *, local_files_only=True):
    require_uuid(parent_id, "parent_analysis_id")
    config = load_config(config_path)
    parent = read_analysis(parent_id)
    root = data_root() / "analyses" / parent_id
    parent_checksum = file_hash(root / "manifest.json")
    run_id, child_id = str(uuid4()), str(uuid4())
    directory = data_root() / "representation-runs" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    configuration = parent.manifest.configuration | {"representation": asdict(config)}
    state = {
        "schema_version": "voc-representation-producer-v1",
        "state": "running",
        "run_id": run_id,
        "parent_analysis_id": parent_id,
        "candidate_id": child_id,
        "configuration": configuration,
        "tracking_verified": False,
        "created_at": datetime.now(UTC).isoformat(),
        "directory": str(directory),
        "execution": "synchronous local representation workflow",
        "local_files_only": local_files_only,
    }

    def save():
        state["updated_at"] = datetime.now(UTC).isoformat()
        temporary = directory / "state.tmp"
        temporary.write_bytes(canonical_json(state))
        temporary.replace(directory / "state.json")

    save()
    client, tracking_id = None, None
    try:
        client, tracking_id = _start_tracking(
            run_id, parent_id, configuration, child_id
        )
        state["mlflow_run_id"] = tracking_id
        save()
        before = preserved_content(parent)
        topics, refinement = refine(parent, config, local_files_only=local_files_only)
        coverage = validate_results(
            parent.reviews.record_id.tolist(),
            parent.assignments,
            topics,
            parent.sentiments,
        )

        after = preserved_content(replace(parent, topics=topics))
        if before != after or file_hash(root / "manifest.json") != parent_checksum:
            raise ValueError(
                "Representation refinement changed protected parent content"
            )
        software = {
            name: version(name)
            for name in (
                "bertopic",
                "scikit-learn",
                "numpy",
                "pandas",
                "duckdb",
                "mlflow",
            )
        } | {"python": platform.python_version()}
        code_sha256 = code_identity()
        report = {
            "schema_version": "voc-representation-evidence-v1",
            "run_id": run_id,
            "parent_analysis_id": parent_id,
            "analysis_id": child_id,
            "parent_manifest_sha256": parent_checksum,
            "configuration": configuration,
            "coverage": coverage,
            "preserved_content": {"parent": before, "child": after},
            "refinement": refinement,
            "software": software,
            "code_sha256": code_sha256,
            "topics": [asdict(t) for t in topics],
        }
        (directory / "evidence.json").write_bytes(canonical_json(report))
        _finish_tracking(client, tracking_id, report)
        state["tracking_verified"] = True
        save()
        reports = {"representation-evidence.json": canonical_json(report)}
        # The parent's discovery diagnostics are not copied: this child is a
        # rewording, and its own evidence file says so.
        for interpretation in parent.interpretations:
            for key in ("request_artifact", "response_artifact"):
                relative = interpretation[key]
                reports[relative.removeprefix("artifacts/")] = (
                    root / relative
                ).read_bytes()
        child = write_analysis(
            parent.reviews,
            parent.assignments,
            topics,
            parent.sentiments,
            inputs=parent.manifest.inputs,
            configuration=configuration,
            code_sha256=code_sha256,
            software=software,
            provenance={
                "run_id": run_id,
                "mlflow_run_id": tracking_id,
                "kind": "representation",
                "parent_manifest_sha256": parent_checksum,
            },
            analysis_id=child_id,
            parent_analysis_id=parent_id,
            reports=reports,
            interpretations=parent.interpretations
            if parent.manifest.schema_version != "voc-analysis-v1"
            else None,
        )
        state.update(
            state="completed",
            analysis_id=child.analysis_id,
            counts=child.coverage,
            refinement=refinement,
        )
        save()
    except (Exception, KeyboardInterrupt, SystemExit) as error:  # noqa: BLE001 - persist interrupted or failed experiment
        if client is not None and tracking_id is not None:
            try:
                client.set_terminated(tracking_id, status="FAILED")
            except Exception as cleanup_error:  # noqa: BLE001 - retain original failure
                state["tracking_cleanup_error"] = str(cleanup_error)
        state.update(
            state="failed", error={"type": type(error).__name__, "message": str(error)}
        )
        save()
    return state
