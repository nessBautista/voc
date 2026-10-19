"""Save/reload one tiny MLflow file without collecting or publishing datasets."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import click
from mlflow.tracking import MlflowClient

from voc.storage import load_storage

from voc_ml.tracking import ensure_experiment, experiment_name, tracking_uri

CONTENT = b"VOC MLflow artifact storage check\n"
ARTIFACT = "storage-check.txt"


def verify_run(client, run_id, storage):
    """Use a pinned run and compare the downloaded bytes with the original file."""
    run = client.get_run(run_id)
    if run.info.status != "FINISHED":
        raise ValueError("MLflow storage check run did not finish")
    experiment = client.get_experiment(run.info.experiment_id)
    if experiment.name != experiment_name(storage):
        raise ValueError("Saved check belongs to a different MLflow experiment")
    if not run.info.artifact_uri.startswith(
        storage.mlflow_artifact_uri.rstrip("/") + "/"
    ):
        raise ValueError("Artifact URI is outside the configured MLflow artifact root")
    with TemporaryDirectory(prefix="voc-mlflow-read-") as directory:
        downloaded = client.download_artifacts(run_id, ARTIFACT, dst_path=directory)
        if Path(downloaded).read_bytes() != CONTENT:
            raise ValueError("MLflow artifact contents did not match")
    return {
        "status": "passed",
        "run_id": run_id,
        "experiment_name": experiment.name,
        "artifact_destination": storage.artifact_destination,
        "artifact_uri": run.info.artifact_uri.rstrip("/") + "/" + ARTIFACT,
        "size_bytes": len(CONTENT),
        "installation_id": storage.installation_id,
    }


def create_check(client, storage):
    experiment_id = ensure_experiment(client, storage)
    run = client.create_run(
        experiment_id,
        tags={"voc.check": "artifact-storage", "voc.member_id": storage.member_id},
        run_name="artifact-storage-check",
    )
    run_id = run.info.run_id
    try:
        with TemporaryDirectory(prefix="voc-mlflow-write-") as directory:
            artifact = Path(directory) / ARTIFACT
            artifact.write_bytes(CONTENT)
            client.log_artifact(run_id, str(artifact))
        client.set_terminated(run_id, status="FINISHED")
        return verify_run(client, run_id, storage)
    except Exception:
        client.set_terminated(run_id, status="FAILED")
        raise


def check(reload_existing=False):
    """Check the configured MLflow store and save a pinned run reference."""
    storage = load_storage()
    (storage.runtime_root / "mlflow").mkdir(parents=True, exist_ok=True)
    client = MlflowClient(tracking_uri=tracking_uri(storage))
    report_path = (
        storage.runtime_root / "checks" / f"mlflow-{storage.artifact_destination}.json"
    )
    if reload_existing:
        if not report_path.exists():
            raise click.ClickException("No saved check. Run without --reload first.")
        saved = json.loads(report_path.read_text())
        report = verify_run(client, saved["run_id"], storage)
    else:
        report = create_check(client, storage)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    click.echo(json.dumps(report, indent=2))



import pytest

@pytest.mark.live
def test_storage_roundtrip():
    from voc_dev.bootstrap import bootstrap
    bootstrap()
    check(False)
    check(True)
