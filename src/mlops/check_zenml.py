"""Create/reload a tiny ZenML artifact without collecting or publishing datasets."""

import json
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import boto3
import click
import pandas as pd
from zenml import pipeline, step
from zenml.client import Client

from voc.storage import load_storage

from .bootstrap import bootstrap


def expected_frame():
    return pd.DataFrame(
        {"review_id": ["check-1", "check-2"], "text": ["hello", "world"]}
    )


@step(enable_cache=False)
def sample_reviews() -> Annotated[pd.DataFrame, "sample"]:
    return expected_frame()


@pipeline(enable_cache=False)
def storage_check_pipeline():
    sample_reviews()


def verify_artifact(artifact, storage):
    """Verify the registered location, object existence and exact round trip."""
    expected_root = storage.zenml_artifact_uri.rstrip("/") + "/"
    if not artifact.uri.startswith(expected_root):
        raise ValueError("Artifact URI is outside the configured ZenML artifact root")
    frame = artifact.load()
    pd.testing.assert_frame_equal(frame, expected_frame())
    if storage.artifact_destination == "s3":
        uri = urlparse(artifact.uri)
        # ZenML persists an artifact directory containing data and metadata files.
        result = boto3.client("s3", region_name=storage.region).list_objects_v2(
            Bucket=uri.netloc,
            Prefix=uri.path.lstrip("/").rstrip("/") + "/",
            MaxKeys=1,
        )
        if not result.get("Contents"):
            raise ValueError("No S3 objects found under the ZenML artifact URI")
    elif not Path(artifact.uri).exists():
        raise ValueError("Local artifact directory is missing")
    return len(frame)


@click.command()
@click.option(
    "--reload",
    "reload_existing",
    is_flag=True,
    help="Reload the last saved check; do not create a run.",
)
def main(reload_existing):
    """Check the configured ZenML store, then save a pinned artifact reference."""
    storage = load_storage()
    report_path = (
        storage.runtime_root / "checks" / f"zenml-{storage.artifact_destination}.json"
    )
    client = Client()
    if reload_existing:
        if not report_path.exists():
            raise click.ClickException("No saved check. Run without --reload first.")
        report = json.loads(report_path.read_text())
        artifact = client.get_artifact_version(report["artifact_id"])
    else:
        # Repeated setup also exercises matching registration reuse.
        stack_id = bootstrap()
        run = storage_check_pipeline()
        if run is None:
            raise click.ClickException("ZenML did not return a pipeline run")
        run = client.get_pipeline_run(run.id)
        if run.status.value != "completed":
            raise click.ClickException("Storage check pipeline did not complete")
        artifact = run.steps["sample_reviews"].outputs["sample"][0]
        report = {
            "run_id": str(run.id),
            "stack_id": stack_id,
            "artifact_id": str(artifact.id),
        }
    rows = verify_artifact(artifact, storage)
    report.update(
        status="passed",
        artifact_destination=storage.artifact_destination,
        artifact_uri=artifact.uri,
        row_count=rows,
        installation_id=storage.installation_id,
    )
    if not reload_existing:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    click.echo(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
