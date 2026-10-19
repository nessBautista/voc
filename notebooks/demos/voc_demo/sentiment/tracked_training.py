"""Small, explicit dataset preparation and ZenML/MLflow training pipeline."""

import gc
import hashlib
import json
import math
import uuid
from io import BytesIO
from pathlib import Path

import pandas as pd
from pysentimiento.preprocessing import preprocess_tweet
from zenml import get_step_context, pipeline, step
from zenml.client import Client

from .adaptation import LABELS, adapt
from .training_data import LABEL_BY_RATING, assign_rating_splits

PLATFORMS = {"stores": {"app_store", "google_play"}, "social": {"x", "youtube"}}


def prepare_dataset(content, domain, percentages, seed=42):
    """Read a CSV and recompute grouped, stratified splits from all its rows."""
    if domain not in PLATFORMS:
        raise ValueError("Select stores or social.")
    if len(percentages) != 3 or any(
        not math.isfinite(p) or p <= 0 for p in percentages
    ):
        raise ValueError("All three split percentages must be positive.")
    if not math.isclose(sum(percentages), 100):
        raise ValueError("Train + validation + test must equal 100%.")
    frame = pd.read_csv(
        BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig"
    )
    required = {"record_id", "text", "platform"}
    if not required <= set(frame):
        raise ValueError(f"Missing columns: {sorted(required - set(frame))}")
    for column in required | ({"label"} if "label" in frame else set()):
        frame[column] = frame[column].str.strip()
    if domain == "stores" and "label" not in frame and "rating" in frame:
        ratings = pd.to_numeric(frame.rating, errors="coerce")
        frame["label"] = ratings.map(LABEL_BY_RATING).fillna("")
    if "label" not in frame or set(frame.label) != set(LABELS):
        raise ValueError(
            "Include the three English labels: negative, neutral, positive. Stores may instead supply rating (1–5)."
        )
    if frame[list(required)].eq("").any().any() or not frame.record_id.is_unique:
        raise ValueError(
            "IDs, texts and platforms must be complete; IDs must be unique."
        )
    if not set(frame.platform) <= PLATFORMS[domain]:
        raise ValueError(f"Allowed platforms for {domain}: {sorted(PLATFORMS[domain])}")
    frame["prepared_text"] = frame.text.map(
        lambda text: preprocess_tweet(text, lang="es")
    )
    frame["text_key"] = frame.prepared_text.map(
        lambda text: " ".join(text.casefold().split())
    )
    if frame.text_key.eq("").any():
        raise ValueError("Some texts are empty after preprocessing.")
    # Ignore a previous split column: the controls define the new partition.
    try:
        assigned = assign_rating_splits(
            frame, seed, percentages[0] / 100, percentages[1] / 100
        )
    except ValueError as error:
        raise ValueError(
            f"Cannot create these stratified splits. Increase the minority class or adjust the percentages. {error}"
        ) from error
    splits = {
        name: assigned.loc[assigned.split.eq(name)].reset_index(drop=True)
        for name in ("train", "validation", "test")
    }
    if any(set(part.label) != set(LABELS) for part in splits.values()):
        raise ValueError(
            "Every split must contain all three classes. Adjust the percentages or add labeled reviews."
        )
    return splits, hashlib.sha256(content).hexdigest()


def class_distribution(splits):
    """Show counts and deviations in percentage points from the full dataset."""
    full = pd.concat(splits.values())
    reference = full.label.value_counts(normalize=True).reindex(LABELS, fill_value=0)
    rows = []
    for name, part in {"all": full, **splits}.items():
        for label in LABELS:
            count = int(part.label.eq(label).sum())
            share = 100 * count / len(part)
            rows.append(
                {
                    "split": name,
                    "class": label,
                    "reviews": count,
                    "class_percent": share,
                    "difference_pp": share - 100 * reference[label],
                    "split_reviews": len(part),
                    "split_percent": 100 * len(part) / len(full),
                }
            )
    return pd.DataFrame(rows)


@step(enable_cache=False, experiment_tracker="voc-mlflow")
def train_sentiment(snapshot: str, domain: str, parameters: dict) -> dict:
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
def sentiment_training_pipeline(snapshot: str, domain: str, parameters: dict):
    train_sentiment.with_options(
        settings={"experiment_tracker": {"experiment_name": f"voc-sentiment-{domain}"}}
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
    _ensure_experiment(client, storage, f"voc-sentiment-{domain}")
    folder = Path(output_root).resolve() / f"{domain}-{uuid.uuid4().hex[:12]}"
    folder.mkdir(parents=True, exist_ok=False)
    for name, part in splits.items():
        part.to_csv(folder / f"{name}.csv", index=False)
    manifest = {
        "source_sha256": source_sha256,
        "percentages": percentages,
        "split_seed": split_seed,
        "cache": str(Path(cache).resolve()),
    }
    (folder / "dataset.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    run = sentiment_training_pipeline.with_options(run_name=folder.name)(
        snapshot=str(folder),
        domain=domain,
        parameters=parameters,
    )
    # Read our small receipt; ZenML also materializes the returned report.
    result = json.loads((folder / "tracking.json").read_text())
    result["zenml_status"] = str(Client().get_pipeline_run(run.id).status)
    return result
