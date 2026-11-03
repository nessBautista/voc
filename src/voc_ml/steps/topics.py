"""Thin local workflow steps around reusable topic discovery functions."""

from typing import Annotated

from zenml import get_step_context, step

from voc_ml.topics.production import fit_run, pin_run
from voc_ml.topics.store import TopicRunStore


@step(enable_cache=False)
def pin_topics(run_id: str, runtime_root: str) -> Annotated[dict, "topic_input"]:
    store = TopicRunStore(runtime_root)
    store.update(run_id, zenml_run_id=str(get_step_context().pipeline_run.id))
    return pin_run(run_id, store=store)


@step(enable_cache=False)
def fit_topics(reference: dict, runtime_root: str) -> Annotated[dict, "topic_fit"]:
    store = TopicRunStore(runtime_root)
    state = store.load(reference["run_id"])
    if state["input"]["selection_sha256"] != reference["selection_sha256"]:
        raise ValueError("Workflow selection differs from producer")
    return fit_run(reference["run_id"], store=store)


@step(enable_cache=False, experiment_tracker="voc-mlflow")
def track_topics(
    reference: dict, runtime_root: str
) -> Annotated[dict, "topic_tracking"]:
    import mlflow

    active = mlflow.active_run()
    if active is None:
        raise RuntimeError("Missing MLflow tracking run")
    store = TopicRunStore(runtime_root)
    state = store.update(reference["run_id"], mlflow_run_id=active.info.run_id)
    if reference["counts"] != state["fit"]["counts"]:
        raise ValueError("Tracked counts differ from producer")
    mlflow.set_tags(
        {
            "topic_run_id": state["run_id"],
            "zenml_run_id": state["zenml_run_id"],
            "code_identity": state["code_identity"],
        }
    )
    mlflow.log_params(
        state["configuration"]
        | {
            "dataset_artifact_id": state["input"]["dataset"]["artifact_id"],
            "embedding_release_id": state["input"]["embedding_release_id"],
            "selection_sha256": state["input"]["selection_sha256"],
        }
    )
    mlflow.log_metrics(
        state["fit"]["counts"]
        | {
            "worker_wall_seconds": state["worker_wall_seconds"],
            "peak_worker_rss_bytes": state["fit"]["peak_worker_rss_bytes"],
        }
    )
    mlflow.log_dict(state["fit"], "topic-fit.json")
    mlflow.log_dict(state["input"], "topic-input.json")
    return {"run_id": state["run_id"], "mlflow_run_id": active.info.run_id}
