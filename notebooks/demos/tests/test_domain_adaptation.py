import json

import pandas as pd
import pytest
from voc_demo.sentiment.domain_adaptation import (
    adapter_identity,
    cached_experiments,
    classify_with_adapters,
    export_splits,
    inspect_manual_data,
    stratified_manual_splits,
)


def manual_frame():
    return pd.DataFrame(
        [
            {
                "record_id": f"{label}-{i}",
                "text": f"Reseña {label} número {i} sobre la aplicación",
                "label": label,
                "platform": "x" if i % 2 else "youtube",
            }
            for label, count in (("negative", 180), ("neutral", 160), ("positive", 60))
            for i in range(count)
        ]
    )


def test_original_eda_and_stratified_disjoint_exact_partitions(tmp_path):
    path = tmp_path / "manual.csv"
    original = manual_frame()
    original.to_csv(path, index=False)
    frame, audit = inspect_manual_data(path)
    assert audit["rows"] == 400
    assert audit["class_counts"] == {"negative": 180, "neutral": 160, "positive": 60}
    splits = stratified_manual_splits(frame)
    assert {name: len(part) for name, part in splits.items()} == {
        "train": 240,
        "test": 80,
        "validation": 80,
    }
    combined = pd.concat(splits.values())
    assert combined.record_id.is_unique and combined.text_key.is_unique
    assert set(combined.record_id) == set(original.record_id)
    for part in splits.values():
        assert (
            part.label.value_counts(normalize=True).sort_index()
            - original.label.value_counts(normalize=True).sort_index()
        ).abs().max() < 0.02
    repeat = stratified_manual_splits(frame)
    for name in splits:
        pd.testing.assert_frame_equal(splits[name], repeat[name])
    export_splits(splits, tmp_path / "partitions")
    for name, part in splits.items():
        saved = pd.read_csv(tmp_path / "partitions" / f"{name}.csv")
        assert saved.record_id.tolist() == part.record_id.tolist()


def test_small_minority_class_is_retained_without_rounding_away_examples(tmp_path):
    source = manual_frame()
    source = source.loc[
        ~source.label.eq("positive")
        | source.record_id.isin([f"positive-{i}" for i in range(18)])
    ]
    path = tmp_path / "manual.csv"
    source.to_csv(path, index=False)
    frame, _ = inspect_manual_data(path)
    splits = stratified_manual_splits(frame)
    selected = pd.concat(splits.values())
    assert selected.label.value_counts().to_dict() == {
        "negative": 180,
        "neutral": 160,
        "positive": 18,
    }
    assert len(selected) == len(source)
    assert all(part.label.eq("positive").any() for part in splits.values())


def test_corrected_labels_are_not_translated(tmp_path):
    frame = manual_frame()
    frame.loc[0, "label"] = "negativo"
    path = tmp_path / "manual.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="not translated"):
        inspect_manual_data(path)


def test_duplicate_text_stays_in_one_partition(tmp_path):
    frame = manual_frame()
    frame.loc[1, "text"] = frame.loc[0, "text"]
    path = tmp_path / "manual.csv"
    frame.to_csv(path, index=False)
    prepared, audit = inspect_manual_data(path)
    assert audit["duplicate_texts"] == 1
    splits = stratified_manual_splits(prepared)
    combined = pd.concat(splits.values())
    assert len(combined) == len(frame)
    assert combined.groupby("text_key").split.nunique().max() == 1


def test_conflicting_duplicate_labels_are_rejected(tmp_path):
    frame = manual_frame()
    frame.loc[180, "text"] = frame.loc[0, "text"]
    path = tmp_path / "manual.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="conflicting labels"):
        inspect_manual_data(path)


def test_adapter_identity_changes_and_partial_caches_are_not_selected(tmp_path):
    results = {}
    for name in ("rated", "social"):
        output = tmp_path / "runs" / "run" / name
        (output / "model").mkdir(parents=True)
        for filename in (
            "model.safetensors",
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
        ):
            (output / "model" / filename).write_text(name)
        results[name] = {"output": str(output)}
    index = tmp_path / "runs/run/experiments.json"
    index.write_text(json.dumps(results))
    assert set(cached_experiments(tmp_path)) == {"rated", "social"}
    before = adapter_identity(results)
    (tmp_path / "runs/run/social/model/model.safetensors").write_text("new weights")
    assert before != adapter_identity(results)
    (tmp_path / "runs/run/social/model/tokenizer.json").unlink()
    assert set(cached_experiments(tmp_path)) == {"rated"}


def test_platform_routing_preserves_interleaved_review_ids(monkeypatch):
    from voc_demo.sentiment import experiments, robertuito

    calls = []

    def load(output):
        return None, output.name

    def classify(reviews, tokenizer, model, **kwargs):
        calls.append((model, reviews.record_id.tolist()))
        return [{"record_id": rid, "model": model} for rid in reviews.record_id], []

    monkeypatch.setattr(experiments, "load_trained_model", load)
    monkeypatch.setattr(robertuito, "classify_reviews", classify)
    reviews = pd.DataFrame(
        {
            "record_id": ["a", "b", "c", "d"],
            "platform": ["x", "google_play", "youtube", "app_store"],
            "embedding_text": ["a", "b", "c", "d"],
        }
    )
    results = {name: {"output": name} for name in ("rated", "social")}
    rows, _ = classify_with_adapters(
        reviews, results, sentiment_run_id="s", dataset_release_id="d"
    )
    assert calls == [("rated", ["b", "d"]), ("social", ["a", "c"])]
    assert [row["record_id"] for row in rows] == ["a", "b", "c", "d"]
    assert [row["model"] for row in rows] == ["social", "rated", "social", "rated"]


def notebook_cell(name):
    import ast
    from pathlib import Path

    notebook = Path(__file__).resolve().parents[1] / "workflow01.py"
    tree = ast.parse(notebook.read_text())
    node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    node.decorator_list = []
    namespace = {}
    exec(  # noqa: S102 - Execute only named cells from this repository's notebook.
        compile(ast.Module(body=[node], type_ignores=[]), str(notebook), "exec"),
        namespace,
    )
    return namespace[name]


def test_notebook_selects_models_without_loading_training_data(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import marimo as mo

    monkeypatch.setenv("DEMO_DATA_DIR", str(tmp_path))
    options, social, stores = notebook_cell("sentiment_model_selection")(
        mo, SimpleNamespace(value=False)
    )
    assert social.value == stores.value == "Original RoBERTuito"
    assert "Hugging Face · v2" in options["stores"]
    assert "Hugging Face · v1" in options["social"]
    assert not (tmp_path / "sentiment").exists()
