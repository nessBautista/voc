from zenml.client import Client
from voc_ml.preparation.dataset import COLUMNS
from voc.datasets import publish
from voc.collection.store import RawStore
import hashlib

def validated_run(run_id):
    """Load and validate completed outputs without changing any catalog."""
    run = Client().get_pipeline_run(run_id)
    if str(run.status.value) != "completed":
        raise RuntimeError("Only completed pipelines can publish")
    if "prepare" not in run.steps or "track" not in run.steps:
        raise ValueError("Not a dataset pipeline run")
    artifact = run.steps["prepare"].outputs["workable"][0]
    report = run.steps["prepare"].outputs["preparation_report"][0].load()
    tracking = run.steps["track"].outputs["tracking_report"][0].load()
    reference = run.steps["raw_revision"].outputs["raw_reference"][0].load()
    if (
        reference["raw_revision_id"] != report["raw_revision_id"]
        or reference["raw_store_id"] != report["raw_store_id"]
    ):
        raise ValueError("Raw reference does not match preparation report")
    RawStore(reference["raw_store_id"]).revision(reference["raw_revision_id"])
    if tracking["artifact_id"] != str(artifact.id):
        raise ValueError("Tracker references a different artifact")
    frame = artifact.load()
    checksum = hashlib.sha256(
        frame.to_json(orient="records", force_ascii=False).encode()
    ).hexdigest()
    if (
        list(frame) != COLUMNS
        or len(frame) != report["row_count"]
        or checksum != report["content_sha256"]
    ):
        raise ValueError("Workable artifact does not match its manifest")
    info = {
        key: report[key]
        for key in (
            "raw_store_id",
            "raw_revision_id",
            "schema_version",
            "row_count",
            "content_sha256",
            "rules",
            "counts_by_platform",
            "project_identity",
        )
    }
    info.update(
        run_id=str(run.id),
        artifact_id=str(artifact.id),
        mlflow_run_id=tracking["mlflow_run_id"],
    )
    return info, frame


def publish_run(run_id):
    info, _ = validated_run(run_id)
    publish(info)
    return info
