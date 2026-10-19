"""Export one stable local release per verified completed embedding producer."""

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from voc.datasets.manifest import file_hash
from voc.embeddings.contracts import (
    SCHEMA_VERSION,
    canonical_json,
    fingerprint,
    require_uuid,
)
from voc.embeddings.export import sync_directory, write_release
from voc.embeddings.validation import load_release

from .artifacts import read_encoding, read_input
from .producer import load_run, run_directory


def verify_tracking(state):
    """Resolve real finished runs and the tracked encoding reference before export."""
    from mlflow.tracking import MlflowClient
    from zenml.client import Client

    from voc.storage import load_storage
    from voc_ml.tracking import tracking_uri

    run = Client().get_pipeline_run(state["zenml_run_id"])
    if run.status.value != "completed":
        raise ValueError("Export requires a completed ZenML run")
    receipt = run.steps["track_embeddings"].outputs["tracking_report"][0].load()
    if receipt != {
        "mlflow_run_id": state["mlflow_run_id"],
        "encoding_reference": state["encoding_reference"],
    }:
        raise ValueError("Tracked reference differs from completed producer output")
    tracked = MlflowClient(tracking_uri=tracking_uri(load_storage())).get_run(
        state["mlflow_run_id"]
    )
    if (
        tracked.info.status != "FINISHED"
        or tracked.info.experiment_id != state["experiment_id"]
        or tracked.data.tags.get("producer_run_id") != state["producer_run_id"]
        or tracked.data.tags.get("zenml_run_id") != state["zenml_run_id"]
    ):
        raise ValueError("Export requires matching finished MLflow provenance")


def _create_json(path, value):
    """Create-only, atomic local record. Concurrent exporters read the winning ID."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    sync_directory(path.parent)
    return json.loads(path.read_bytes())


def _manifest(state, report, release_id):
    artifacts = {}
    for key, name in (("vectors", "vectors.npy"), ("reviews", "reviews.parquet")):
        artifacts[key] = {
            "key": f"releases/{release_id}/{name}",
            **report["artifacts"][name],
        }
    artifacts["vectors"].update(
        shape=[report["rows"], report["profile"]["config"]["dimensions"]],
        dtype="<f4",
        order="C",
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "embedding_release_id": release_id,
        **{key: report[key] for key in ("dataset", "selection", "feature", "profile")},
        "encoder": {
            "encoder_id": report["encoder_id"],
            "descriptor": report["encoder"],
        },
        "coverage": {
            "source_rows": report["dataset"]["row_count"],
            "selected_rows": report["rows"],
            "unique_texts": report["unique_texts"],
        },
        "artifacts": artifacts,
        "provenance": {
            "status": "completed",
            **{
                key: state[key]
                for key in (
                    "producer_run_id",
                    "zenml_run_id",
                    "mlflow_run_id",
                    "completed_at",
                )
            },
        },
    }


def export_run(producer_run_id):
    """Retry export without inference or new release identities; no S3 writes.

    A create-only intent pins source references and provenance before file creation.
    A missing completion receipt can be recovered after checking all release bytes.
    Existing corrupt output is rejected, never silently replaced.
    """
    state = load_run(producer_run_id)
    if state["state"] != "completed":
        raise ValueError("Only completed producers can be exported")
    report = read_encoding(
        state["encoding_reference"],
        expected_input=state["input_reference"],
        expected_profile=state["requested"]["profile"],
    )
    for key in ("dataset", "selection", "feature", "profile", "encoder_id"):
        if canonical_json(state[key]) != canonical_json(report[key]):
            raise ValueError(f"Completed producer {key} differs from encoding output")
    verify_tracking(state)
    source = {
        "input_reference": state["input_reference"],
        "encoding_reference": state["encoding_reference"],
        "provenance": {
            key: state[key]
            for key in (
                "producer_run_id",
                "zenml_run_id",
                "mlflow_run_id",
                "completed_at",
            )
        },
    }
    folder = run_directory(producer_run_id)
    intent_path = folder / "export-intent.json"
    candidate = {
        "schema_version": "voc-embedding-export-intent-v1",
        "producer_run_id": producer_run_id,
        "source_sha256": fingerprint(source),
        "embedding_release_id": str(uuid4()),
    }
    intent = _create_json(intent_path, candidate)
    if set(intent) != set(candidate) or any(
        intent[key] != candidate[key]
        for key in candidate
        if key != "embedding_release_id"
    ):
        raise ValueError(
            "Export intent differs from completed producer; refusing changed input"
        )
    require_uuid(intent["embedding_release_id"], "embedding_release_id")
    manifest = _manifest(state, report, intent["embedding_release_id"])
    expected = read_input(state["input_reference"]).reviews
    encoding = Path(state["encoding_reference"]["directory"])
    destination = folder / "release"
    # A completed export must not be silently recreated if its files disappear.
    receipt_path = folder / "export-reference.json"
    if not destination.exists() and not receipt_path.exists():
        try:
            write_release(
                destination,
                manifest,
                vectors_path=encoding / "vectors.npy",
                reviews_path=encoding / "reviews.parquet",
                expected_reviews=expected,
            )
        except FileExistsError:
            pass  # A concurrent exporter may have completed the same pinned intent.
    bundle = load_release(
        destination, manifest_sha256=fingerprint(manifest), expected_reviews=expected
    )
    if canonical_json(bundle.info) != canonical_json(manifest):
        raise ValueError("Existing release differs from the pinned export")
    reference = {
        "schema_version": "voc-embedding-export-v1",
        "state": "exported",
        "producer_run_id": producer_run_id,
        "embedding_release_id": intent["embedding_release_id"],
        "directory": str(destination),
        "manifest_sha256": file_hash(destination / "manifest.json"),
        "dataset_release_id": report["dataset"]["release_id"],
        "profile_id": report["profile"]["profile_id"],
        "coverage": manifest["coverage"],
    }
    recorded = _create_json(receipt_path, reference)
    if canonical_json(recorded) != canonical_json(reference):
        raise ValueError("Export completion reference differs from prepared release")
    return reference
