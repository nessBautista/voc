"""Tracked production use case, independent of tracking client construction."""

from time import monotonic

from .production import finish_run
from .store import local_store
from .tracking import run_tracked_pipeline


def execute_workflow(run_id, *, store=None, run_workflow=None):
    store = store if store is not None else local_store()
    run_workflow = run_workflow if run_workflow is not None else run_tracked_pipeline
    if store.load(run_id)["state"] != "created":
        raise ValueError("Producer already launched")
    try:
        receipt = run_workflow(run_id, store)
        current = store.load(run_id)
        if (
            receipt["run_id"] != run_id
            or current["zenml_run_id"] != receipt["zenml_run_id"]
            or current["mlflow_run_id"] != receipt["mlflow_run_id"]
        ):
            raise ValueError("Tracking lineage or completion mismatch")
        began = monotonic()
        finish_run(run_id, store=store)
        return store.update(run_id, export_seconds=monotonic() - began)
    except (Exception, KeyboardInterrupt) as error:
        store.update(
            run_id,
            state="failed",
            error={"type": type(error).__name__, "message": str(error)[:2000]},
        )
        raise
