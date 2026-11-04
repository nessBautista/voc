"""Local ZenML/MLflow adapter, constructed only for an explicit workflow run."""

from .production import inspect_study


def local_tracking():
    from mlflow.tracking import MlflowClient
    from zenml.client import Client

    from voc.storage import load_storage
    from voc_ml.tracking import tracking_uri

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
        raise ValueError("Interpretation requires the configured local VOC stack")
    return storage, client, MlflowClient(tracking_uri=uri)


def execute_workflow(key, *, store):
    """Repeated accepted requests return verified receipts without a new pipeline."""
    summary = inspect_study(store)
    if key in summary["attempts"]:
        prior = summary["attempts"][key]
        if prior["state"] != "accepted":
            raise ValueError("Attempt already recorded; inspect it, do not resend")
        return {"origin": "cache", **prior}
    from voc_ml.pipelines.interpretation import interpretation_pipeline
    from voc_ml.tracking import ensure_interpretation_experiment

    from .provider import credential_available

    if not credential_available():
        raise ValueError("OPENROUTER_API_KEY unavailable through container injection")
    storage, client, tracking = local_tracking()
    experiment_id = ensure_interpretation_experiment(tracking, storage)
    result = interpretation_pipeline(key=key, ledger_root=str(store.root))
    if result is None:
        raise ValueError(
            "ZenML returned no interpretation run; inspect before continuing"
        )
    run = client.get_pipeline_run(result.id)
    if run.status.value != "completed":
        raise ValueError(
            "Interpretation tracking did not complete; inspect before continuing"
        )
    receipt = run.steps["interpret_topic"].outputs["interpretation_receipt"][0].load()
    lineage = receipt["tracking"]
    tracked = tracking.get_run(lineage["mlflow_run_id"])
    if (
        lineage["zenml_run_id"] != str(run.id)
        or tracked.info.status != "FINISHED"
        or tracked.info.experiment_id != experiment_id
        or tracked.data.tags.get("interpretation_attempt_id") != receipt["attempt_id"]
        or tracked.data.tags.get("zenml_run_id") != str(run.id)
    ):
        raise ValueError("Interpretation tracking lineage differs")
    with store.lock():
        state = store.load()
        state["attempts"][key]["tracking_verified"] = True
        store.save(state)
    return {"origin": receipt["origin"], **store.load()["attempts"][key]}
