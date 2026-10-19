"""Independently tracked transfer-learning experiments, separate from Workflow 03."""

import gc
import json
import uuid
from pathlib import Path

import pandas as pd
from zenml import get_step_context, pipeline, step
from zenml.client import Client

from .adaptation import LABELS
from .tracked_training import PLATFORMS, class_distribution
from .transfer_adaptation import adapt


@step(enable_cache=False, experiment_tracker="voc-mlflow")
def train_transfer_sentiment(snapshot: str, domain: str, parameters: dict) -> dict:
    """One ZenML step; its active MLflow run captures this training only."""
    import mlflow

    folder = Path(snapshot)
    manifest = json.loads((folder / "dataset.json").read_text())
    splits = {
        name: pd.read_csv(folder / f"{name}.csv", dtype=str, keep_default_na=False)
        for name in ("train", "validation", "test")
    }
    active = mlflow.active_run()
    if active is None:
        raise RuntimeError("The voc-mlflow experiment tracker is not active.")
    zenml_run_id = str(get_step_context().pipeline_run.id)
    mlflow.set_tags(
        {
            "domain": domain,
            "zenml_run_id": zenml_run_id,
            "source_sha256": manifest["source_sha256"],
        }
    )
    mlflow.log_params(
        {
            **parameters,
            "domain": domain,
            "split_seed": manifest["split_seed"],
            "source_sha256": manifest["source_sha256"],
            **{
                f"{name}_percent": value
                for name, value in zip(splits, manifest["percentages"])
            },
            **{f"{name}_rows": len(part) for name, part in splits.items()},
        }
    )
    mlflow.log_dict(manifest, "dataset.json")
    mlflow.log_dict(manifest.get("language_audit", {}), "language-audit.json")
    mlflow.log_dict(
        class_distribution(splits).to_dict(orient="records"), "class-distribution.json"
    )

    def log_epoch(row):
        mlflow.log_metrics(
            {
                "train_loss": row["loss"],
                "validation_macro_f1": row["validation_macro_f1"],
            },
            step=row["epoch"],
        )

    tokenizer, model, metrics = adapt(
        splits,
        folder / "training",
        Path(manifest["cache"]),
        **parameters,
        on_epoch=log_epoch,
    )
    # Free the model before transferring its files to the configured artifact store.
    del tokenizer, model
    gc.collect()
    mlflow.log_params(
        {
            "source_model": metrics["source_model"],
            "source_revision": metrics["source_revision"],
            "device": metrics["device"],
            "max_length": metrics["max_length"],
        }
    )
    mlflow.log_metrics(
        {
            "trainable_parameters": metrics["trainable_parameters"],
            "total_parameters": metrics["total_parameters"],
            "trainable_percent": metrics["trainable_percent"],
            "best_epoch": metrics["best_epoch"],
            "best_validation_macro_f1": metrics["validation_macro_f1"],
            "baseline_test_macro_f1": metrics["baseline_test_macro_f1"],
            "test_macro_f1": metrics["test"]["macro avg"]["f1-score"],
            "test_accuracy": metrics["test"]["accuracy"],
        }
    )
    for label in LABELS:
        mlflow.log_metrics(
            {
                f"test_{label}_{key}": metrics["test"][label][key]
                for key in ("precision", "recall", "f1-score")
            }
        )
    mlflow.log_dict(metrics, "evaluation.json")
    mlflow.log_dict(
        {"names": metrics["trainable_parameter_names"]}, "trainable-layers.json"
    )
    mlflow.log_artifacts(str(folder / "training/model"), artifact_path="model")
    result = {
        "domain": domain,
        "zenml_run_id": zenml_run_id,
        "mlflow_run_id": active.info.run_id,
        "metrics": metrics,
        "output": str(folder / "training"),
        "artifact_uri": active.info.artifact_uri,
    }
    (folder / "tracking.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


@pipeline(enable_cache=False)
def transfer_learning_pipeline(snapshot: str, domain: str, parameters: dict):
    train_transfer_sentiment.with_options(
        settings={
            "experiment_tracker": {"experiment_name": f"voc-transfer-learning-{domain}"}
        }
    )(snapshot, domain, parameters)


def run_training(
    splits,
    source_sha256,
    domain,
    percentages,
    parameters,
    output_root,
    cache,
    split_seed=42,
    language_audit=None,
):
    """Snapshot the selected data, then execute one independently tracked model."""
    from mlflow.tracking import MlflowClient

    from voc.storage import load_storage
    from voc_ml.tracking import _ensure_experiment, tracking_uri

    if domain not in PLATFORMS:
        raise ValueError("Select stores or social.")
    storage = load_storage()
    client = MlflowClient(tracking_uri=tracking_uri(storage))
    # The existing local stack already defines orchestration and artifact storage.
    _ensure_experiment(client, storage, f"voc-transfer-learning-{domain}")
    folder = Path(output_root).resolve() / f"{domain}-{uuid.uuid4().hex[:12]}"
    folder.mkdir(parents=True, exist_ok=False)
    for name, part in splits.items():
        part.to_csv(folder / f"{name}.csv", index=False)
    manifest = {
        "language_audit": language_audit or {},
        "source_sha256": source_sha256,
        "percentages": percentages,
        "split_seed": split_seed,
        "cache": str(Path(cache).resolve()),
    }
    (folder / "dataset.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    run = transfer_learning_pipeline.with_options(run_name=folder.name)(
        snapshot=str(folder),
        domain=domain,
        parameters=parameters,
    )
    # Read our small receipt; ZenML also materializes the returned report.
    result = json.loads((folder / "tracking.json").read_text())
    result["zenml_status"] = str(Client().get_pipeline_run(run.id).status)
    return result
