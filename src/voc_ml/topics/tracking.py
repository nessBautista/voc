"""ZenML/MLflow adapter for one synchronous local topic workflow."""


def run_tracked_pipeline(run_id, store):
    from mlflow.tracking import MlflowClient
    from zenml.client import Client

    from voc.storage import load_storage
    from voc_ml.pipelines.topics import topics_pipeline
    from voc_ml.tracking import ensure_topic_experiment, tracking_uri

    storage = load_storage()
    client = Client()
    stack = client.active_stack
    tracker = stack.experiment_tracker
    uri = tracking_uri(storage)
    if (
        storage.artifact_destination != "local"
        or stack.orchestrator.flavor != "local"
        or tracker is None
        or tracker.name != "voc-mlflow"
        or tracker.config.tracking_uri != uri
        or stack.artifact_store.path.rstrip("/")
        != storage.zenml_artifact_uri.rstrip("/")
    ):
        raise ValueError(
            "Topics require the configured local VOC stack. Inspect storage-info and setup"
        )
    tracking = MlflowClient(tracking_uri=uri)
    experiment_id = ensure_topic_experiment(tracking, storage)
    result = topics_pipeline.with_options(run_name="topics-" + run_id)(
        run_id=run_id, experiment_name="voc-topics", runtime_root=str(store.root)
    )
    if result is None:
        raise RuntimeError("ZenML did not return a completed topic run")
    run = client.get_pipeline_run(result.id)
    if run.status.value != "completed":
        raise RuntimeError("Topic workflow did not complete")
    receipt = run.steps["track_topics"].outputs["topic_tracking"][0].load()
    tracked = tracking.get_run(receipt["mlflow_run_id"])
    if (
        receipt["run_id"] != run_id
        or tracked.info.status != "FINISHED"
        or tracked.info.experiment_id != experiment_id
        or tracked.data.tags.get("topic_run_id") != run_id
        or tracked.data.tags.get("zenml_run_id") != str(run.id)
    ):
        raise ValueError("Tracking lineage or completion mismatch")
    return {
        "run_id": run_id,
        "zenml_run_id": str(run.id),
        "mlflow_run_id": receipt["mlflow_run_id"],
    }
