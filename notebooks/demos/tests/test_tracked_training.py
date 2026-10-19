"""Check configurable partitions, preservation of reviews, and tracking content."""

import json

import pandas as pd
import pytest

pytest.importorskip("zenml")  # Workflow 03 uses the main workspace ML dependencies.

from voc_demo.sentiment.tracked_training import (
    class_distribution,
    prepare_dataset,
)


def reviews(domain="social"):
    return pd.DataFrame(
        [
            {
                "record_id": f"{label}-{i}",
                "text": f"Review {label} example {i}",
                "label": label,
                "platform": "x" if domain == "social" else "app_store",
            }
            for label, count in [("negative", 60), ("neutral", 30), ("positive", 10)]
            for i in range(count)
        ]
    )


def test_custom_percentages_keep_all_rows_classes_and_text_groups():
    frame = reviews()
    frame.loc[1, "text"] = frame.loc[0, "text"]
    parts, digest = prepare_dataset(
        frame.to_csv(index=False).encode(), "social", [70, 15, 15]
    )
    assert [len(parts[n]) for n in ("train", "validation", "test")] == [70, 15, 15]
    combined = pd.concat(parts.values())
    assert len(combined) == len(frame)
    assert combined.record_id.is_unique
    assert combined.groupby("text_key").split.nunique().max() == 1
    assert all(
        set(part.label) == {"positive", "neutral", "negative"}
        for part in parts.values()
    )
    assert len(digest) == 64
    distribution = class_distribution(parts)
    assert distribution.difference_pp.abs().max() < 5
    assert distribution.loc[distribution.split.eq("all"), "reviews"].sum() == 100


def test_rating_labels_and_old_splits_are_recomputed():
    frame = reviews("stores")
    frame["rating"] = frame.label.map({"negative": 1, "neutral": 3, "positive": 5})
    frame = frame.drop(columns="label").assign(split="train")
    parts, _ = prepare_dataset(
        frame.to_csv(index=False).encode(), "stores", [60, 20, 20]
    )
    assert [len(p) for p in parts.values()] == [60, 20, 20]
    assert set(pd.concat(parts.values()).label) == {"positive", "neutral", "negative"}


@pytest.mark.parametrize("percentages", [[70, 20, 20], [100, 0, 0], [60, -5, 45]])
def test_invalid_percentages(percentages):
    with pytest.raises(ValueError, match="percentages|100%"):
        prepare_dataset(reviews().to_csv(index=False).encode(), "social", percentages)


def test_domain_and_invalid_labels_are_checked():
    with pytest.raises(ValueError, match="platforms"):
        prepare_dataset(reviews().to_csv(index=False).encode(), "stores", [60, 20, 20])
    frame = reviews().replace({"positive": "positivo"})
    with pytest.raises(ValueError, match="English labels"):
        prepare_dataset(frame.to_csv(index=False).encode(), "social", [60, 20, 20])


def test_step_records_epoch_metrics_model_and_both_tracking_ids(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import mlflow
    from voc_demo.sentiment import tracked_training as training

    parts, digest = prepare_dataset(
        reviews().to_csv(index=False).encode(), "social", [60, 20, 20]
    )
    for name, part in parts.items():
        part.to_csv(tmp_path / f"{name}.csv", index=False)
    (tmp_path / "dataset.json").write_text(
        json.dumps(
            {
                "source_sha256": digest,
                "percentages": [60, 20, 20],
                "split_seed": 42,
                "cache": str(tmp_path / "cache"),
            }
        )
    )
    metrics = {
        "source_model": "tiny-test",
        "source_revision": "test",
        "device": "cpu",
        "max_length": 8,
        "best_epoch": 1,
        "validation_macro_f1": 0.7,
        "baseline_test_macro_f1": 0.5,
        "test": {
            "macro avg": {"f1-score": 0.6},
            "accuracy": 0.7,
            **{
                label: {"precision": 0.5, "recall": 0.6, "f1-score": 0.55}
                for label in ("negative", "neutral", "positive")
            },
        },
    }

    def fake_adapt(splits, output, cache, on_epoch, **kwargs):
        assert set(splits) == {"train", "validation", "test"}
        (output / "model").mkdir(parents=True)
        (output / "model/test.txt").write_text("tiny test artifact")
        on_epoch({"epoch": 1, "loss": 0.8, "validation_macro_f1": 0.7})
        return None, None, metrics

    monkeypatch.setattr(training, "adapt", fake_adapt)
    monkeypatch.setattr(
        training,
        "get_step_context",
        lambda: SimpleNamespace(pipeline_run=SimpleNamespace(id="zenml-test")),
    )
    # A real isolated MLflow store checks persistence without altering the user's dashboard.
    previous = mlflow.get_tracking_uri()
    try:
        mlflow.set_tracking_uri(f"sqlite:///{tmp_path}/mlflow.db")
        with mlflow.start_run() as run:
            result = training.train_sentiment.entrypoint(
                str(tmp_path),
                "social",
                {"epochs": 1, "batch_size": 8, "learning_rate": 2e-5, "seed": 42},
            )
        client = mlflow.MlflowClient()
        recorded = client.get_run(run.info.run_id)
        assert recorded.data.tags["zenml_run_id"] == "zenml-test"
        assert recorded.data.params["source_sha256"] == digest
        assert recorded.data.metrics["test_macro_f1"] == 0.6
        assert client.get_metric_history(run.info.run_id, "train_loss")[0].step == 1
        assert (
            client.list_artifacts(run.info.run_id, "model")[0].path == "model/test.txt"
        )
        assert result["mlflow_run_id"] == run.info.run_id
        assert (
            json.loads((tmp_path / "tracking.json").read_text())["domain"] == "social"
        )
    finally:
        mlflow.set_tracking_uri(previous)
