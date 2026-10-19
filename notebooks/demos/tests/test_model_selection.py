"""Existing model discovery, immutable inference caches and platform routing."""

from types import SimpleNamespace

import pandas as pd
from voc_demo.sentiment import model_selection as selection


def save_model(folder, complete=True):
    (folder / "model").mkdir(parents=True)
    for name in selection.MODEL_FILES:
        (folder / "model" / name).write_text(name)
    if complete:
        (folder / "metrics.json").write_text("{}")


def test_discovery_ignores_in_progress_models_and_filters_domains(tmp_path):
    save_model(tmp_path / "runs/old/rated")
    save_model(tmp_path / "tracked-runs/social-new/training")
    save_model(tmp_path / "tracked-runs/stores-running/training", complete=False)
    stores = selection.available_models(tmp_path, "stores")
    social = selection.available_models(tmp_path, "social")
    assert "Local · old" in stores
    assert "Local · social-new" in social
    assert not any("running" in name for name in stores)
    assert "Local · old" not in social
    assert stores["Hugging Face · v2"]["revision"] == "v2"


def test_local_identity_changes_when_tokenizer_or_weights_change(tmp_path):
    save_model(tmp_path / "runs/old/rated")
    selected = {"stores": selection.available_models(tmp_path, "stores")["Local · old"]}
    _, before = selection.resolve_models(selected)
    (tmp_path / "runs/old/rated/model/tokenizer.json").write_text("changed tokenizer")
    _, after = selection.resolve_models(selected)
    assert before != after


def test_hub_versions_are_resolved_to_commits(monkeypatch):
    import huggingface_hub

    calls = []

    def info(repo, revision):
        calls.append((repo, revision))
        return SimpleNamespace(sha="immutable-commit")

    monkeypatch.setattr(
        huggingface_hub, "HfApi", lambda: SimpleNamespace(model_info=info)
    )
    resolved, _ = selection.resolve_models(
        {"stores": {"source": "org/stores", "revision": "v2"}}
    )
    assert resolved["stores"]["revision"] == "immutable-commit"
    assert calls == [("org/stores", "v2")]


def test_selected_models_preserve_interleaved_order(monkeypatch):
    calls = []
    monkeypatch.setattr(
        selection, "load_selected_model", lambda choice, cache: (None, choice["source"])
    )

    def classify(frame, tokenizer, model, **kwargs):
        calls.append((model, frame.record_id.tolist()))
        return [{"record_id": rid, "model": model} for rid in frame.record_id], []

    monkeypatch.setattr(selection, "classify_reviews", classify)
    frame = pd.DataFrame(
        {
            "record_id": ["a", "b", "c"],
            "platform": ["x", "app_store", "youtube"],
            "embedding_text": ["one", "two", "three"],
        }
    )
    rows, _ = selection.classify_selected_models(
        frame,
        {"stores": {"source": "stores-model"}, "social": {"source": "social-model"}},
        "/tmp/cache",
        sentiment_run_id="s",
        dataset_release_id="d",
    )
    assert calls == [("stores-model", ["b"]), ("social-model", ["a", "c"])]
    assert [row["record_id"] for row in rows] == ["a", "b", "c"]
    assert [row["model"] for row in rows] == [
        "social-model",
        "stores-model",
        "social-model",
    ]
