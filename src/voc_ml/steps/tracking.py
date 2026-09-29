"""Track dataset preparation results in MLflow.

This step expects a producer named prepare with an output named workable.
It is dataset-specific, rather than a generic tracker for every future pipeline.
"""

from typing import Annotated

from zenml import get_step_context, step
from zenml.client import Client


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

