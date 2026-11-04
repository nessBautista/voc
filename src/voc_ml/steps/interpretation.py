"""Thin tracked step around the bounded interpretation operation."""

from typing import Annotated

from zenml import get_step_context, step


@step(enable_cache=False, retry=None, experiment_tracker="voc-mlflow")
def interpret_topic(
    key: str, ledger_root: str
) -> Annotated[dict, "interpretation_receipt"]:
    import mlflow

    from voc_ml.topics.interpretation.ledger import InterpretationStore
    from voc_ml.topics.interpretation.production import execute_request
    from voc_ml.topics.interpretation.provider import (
        check_catalog,
        request_once,
        request_prototype_once,
    )
    from voc_ml.topics.provenance import code_provenance

    active = mlflow.active_run()
    if active is None:
        raise ValueError("Missing interpretation MLflow run")
    lineage = {
        "zenml_run_id": str(get_step_context().pipeline_run.id),
        "mlflow_run_id": active.info.run_id,
    }
    provenance = code_provenance()
    lineage.update(
        code_identity=provenance["code_identity"], git_commit=provenance["git_commit"]
    )
    store = InterpretationStore(ledger_root)
    receipt = execute_request(
        key,
        store=store,
        transport=(
            request_prototype_once
            if store.load()["protocol"]["schema_version"]
            == "voc-fixed-topic-interpretation-v1"
            else request_once
        ),
        catalog_reader=check_catalog,
        tracking=lineage,
    )
    mlflow.set_tags(
        {
            "interpretation_attempt_id": receipt["attempt_id"],
            "zenml_run_id": lineage["zenml_run_id"],
            "interpretation_state": receipt["state"],
            "protocol_sha256": store.load()["protocol_sha256"],
        }
    )
    mlflow.log_params({"request_key": key, "request_sha256": receipt["request_sha256"]})
    mlflow.log_metrics(
        {
            "accounted_or_reserved_usd": float(receipt["charged_usd"]),
            "wall_seconds": receipt["wall_seconds"],
        }
    )
    # Track metadata only. Reviews and complete responses remain in local receipts.
    mlflow.log_dict(
        {
            "state": receipt["state"],
            "manifest_sha256": receipt["manifest_sha256"],
            "release": receipt["release"],
        },
        "interpretation-receipt.json",
    )
    return receipt
