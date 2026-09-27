"""Dataset orchestration: small raw reference, full workable artifact, tracked summary."""

import hashlib
import json
from typing import Annotated

import pandas as pd
from zenml import get_step_context, pipeline, step
from zenml.client import Client

from src.preparation.dataset import prepare_dataset
from voc import collector
from voc.storage import load_storage
from voc.store import RawStore

from .tracking import experiment_name


@step(enable_cache=False)
def raw_revision(
    config: dict, refresh: bool, operation_id: str
) -> Annotated[dict, "raw_reference"]:
    if refresh:
        result = collector.update(
            store=config["raw_store"], config=config, operation_id=operation_id
        )
        result.require_success()
        rid = result.revision_id
        report = result.report
    else:
        raw = RawStore(config["raw_store"])
        revision = raw.revision()
        rid = revision["id"]
        report = json.loads(revision["report"])
    return {
        "raw_store_id": config["raw_store"],
        "raw_revision_id": rid,
        "collection_report": report,
    }


@step(enable_cache=False)
def prepare(
    reference: dict, rules: dict, project_identity: str
) -> tuple[Annotated[pd.DataFrame, "workable"], Annotated[dict, "preparation_report"]]:
    raw = collector.get_raw_dataset(
        reference["raw_store_id"], reference["raw_revision_id"]
    )
    result = prepare_dataset(raw.data, rules)
    # Row content checksum is independent of Parquet writer metadata.
    checksum = hashlib.sha256(
        result.data.to_json(orient="records", force_ascii=False).encode()
    ).hexdigest()
    result.report.update(
        raw_store_id=reference["raw_store_id"],
        raw_revision_id=reference["raw_revision_id"],
        content_sha256=checksum,
        project_identity=project_identity,
    )
    return result.data, result.report


@step(
    enable_cache=False,
    experiment_tracker="voc-mlflow",
)
def track(report: dict, project_identity: str) -> Annotated[dict, "tracking_report"]:
    import mlflow

    context = get_step_context()
    # Resolve the already-materialized producer artifact without loading a second dataframe.
    run = Client().get_pipeline_run(context.pipeline_run.id)
    artifact = run.steps["prepare"].outputs["workable"][0]
    mlflow.log_params(
        {
            "schema_version": report["schema_version"],
            "raw_revision_id": report["raw_revision_id"],
            "workable_artifact_id": str(artifact.id),
            "project_identity": project_identity,
            "combine_title_body": report["rules"]["combine_title_body"],
        }
    )
    mlflow.log_metrics(
        {"rows": report["row_count"], "excluded_rows": report["excluded_count"]}
    )
    mlflow.set_tag("zenml_run_id", str(context.pipeline_run.id))
    return {
        "mlflow_run_id": mlflow.active_run().info.run_id,
        "artifact_id": str(artifact.id),
    }


@pipeline(enable_cache=False)
def dataset_pipeline(
    config: dict, rules: dict, refresh: bool, operation_id: str, project_identity: str
):
    reference = raw_revision(config, refresh, operation_id)
    _workable, report = prepare(reference, rules, project_identity)
    # Record the selected experiment in this run's step configuration.
    track.with_options(
        settings={
            "experiment_tracker": {"experiment_name": experiment_name(load_storage())}
        }
    )(report, project_identity)
