"""Publish only after ZenML reports the entire run completed successfully."""

import hashlib
from pathlib import Path

from zenml.client import Client

from src.preparation.dataset import COLUMNS
from voc.datasets import publish
from voc.store import RawStore


def code_identity():
    root = Path(__file__).resolve().parents[2]
    # Include working-tree source contents: prototype changes may not be committed yet.
    h = hashlib.sha256()
    for area in ("voc", "src"):
        for path in sorted((root / area).rglob("*.py")):
            h.update(str(path.relative_to(root)).encode())
            h.update(path.read_bytes())
    for name in ("pyproject.toml", "ml-project/pyproject.toml", "uv.lock"):
        path = root / name
        h.update(name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


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


def share_run(run_id):
    """Share an already-published local result; collection is never invoked here."""
    from voc.datasets import resolve
    from voc.shared import publish_release
    from voc.storage import load_storage
    from voc.store import Snapshot

    info, frame = validated_run(run_id)
    if resolve(info["artifact_id"], source="local") != info:
        raise ValueError("Local publication differs from validated pipeline output")
    raw = RawStore(info["raw_store_id"]).read(info["raw_revision_id"])
    return publish_release(raw, Snapshot(frame, info), load_storage())
