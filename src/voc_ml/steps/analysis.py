"""Thin local steps pass checksum-verified filesystem references."""

from typing import Annotated

from zenml import get_step_context, step

from voc_ml.analysis import workflow
from voc_ml.analysis.producer import load_run, update_run


@step(enable_cache=False)
def pin_analysis_input(run_id: str) -> Annotated[dict, "input_reference"]:
    update_run(run_id, zenml_run_id=str(get_step_context().pipeline_run.id))
    return workflow.pin(run_id)


@step(enable_cache=False)
def discover_analysis_topics(
    input_reference: dict, run_id: str
) -> Annotated[dict, "topic_reference"]:
    return workflow.topics(input_reference, run_id)


@step(enable_cache=False)
def classify_analysis_sentiment(
    input_reference: dict, run_id: str
) -> Annotated[dict, "sentiment_reference"]:
    return workflow.sentiment(input_reference, run_id)


@step(enable_cache=False)
def validate_analysis_outputs(
    input_reference: dict, topic_reference: dict, sentiment_reference: dict, run_id: str
) -> Annotated[dict, "evidence_reference"]:
    return workflow.evidence(
        input_reference, sentiment_reference, run_id, topic_reference
    )


@step(enable_cache=False)
def validate_sentiment_outputs(
    input_reference: dict, sentiment_reference: dict, run_id: str
) -> Annotated[dict, "evidence_reference"]:
    return workflow.evidence(input_reference, sentiment_reference, run_id)


@step(enable_cache=False, experiment_tracker="voc-mlflow")
def track_analysis(
    evidence_reference: dict, run_id: str
) -> Annotated[dict, "tracking_report"]:
    import mlflow

    from voc_ml.analysis.artifacts import read_json

    active = mlflow.active_run()
    if active is None:
        raise RuntimeError("ZenML did not activate MLflow tracking")
    state = load_run(run_id)
    if evidence_reference != state["evidence_reference"]:
        raise ValueError("Tracking reference differs from validated outputs")
    evidence = read_json(evidence_reference)
    update_run(run_id, mlflow_run_id=active.info.run_id)
    mlflow.set_tags(
        {
            "producer_run_id": run_id,
            "zenml_run_id": state["zenml_run_id"],
            "project_identity": state["project_identity"],
            "workflow_kind": state["kind"],
            "memory_scope": evidence["measurement"]["memory_scope"],
            "duration_scope": evidence["measurement"]["duration_scope"],
        }
    )
    params = {
        "dataset_release_id": evidence["inputs"]["dataset"]["release_id"],
        "input_id": evidence["inputs"]["input_id"],
        "selection_kind": evidence["inputs"]["selection"]["kind"],
    }
    if "embedding" in evidence["inputs"]:
        params.update(
            {
                "embedding_release_id": evidence["inputs"]["embedding"]["release_id"],
                "profile_id": evidence["inputs"]["embedding"]["profile_id"],
            }
        )
    for section, values in state["configuration"]["analysis"].items():
        if state["kind"] == "analysis":
            params.update({section + "." + k: v for k, v in values.items()})
    params.update(
        {"sentiment." + k: v for k, v in state["configuration"]["sentiment"].items()}
    )
    mlflow.log_params(params)
    metrics = {
        "selected_rows": evidence["inputs"]["selection"]["size"],
        "source_rows": evidence["inputs"]["dataset"]["row_count"],
        "duration_seconds": evidence["measurement"]["duration_seconds"],
    }
    peak = evidence["measurement"]["peak_process_rss_bytes"]
    if peak is not None:
        metrics["peak_process_rss_bytes"] = peak
    metrics.update(
        {"sentiment." + k: v for k, v in evidence["sentiment"]["counts"].items()}
    )
    if evidence["topics"] is not None:
        metrics.update(
            {k: evidence["topics"][k] for k in ("topic_count", "unassigned_reviews")}
        )
        metrics["reduction_reused"] = int(evidence["topics"]["reduction_reused"])
    mlflow.log_metrics(metrics)
    mlflow.log_dict(evidence, "workflow-evidence.json")
    return {
        "mlflow_run_id": active.info.run_id,
        "evidence_reference": evidence_reference,
    }
