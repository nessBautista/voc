import pandas as pd
import pytest

from voc_demo.sentiment.adaptation import prepare_splits


def labeled_reviews():
    return pd.DataFrame(
        [
            {
                "record_id": f"{label}-{i}",
                "text": f"{label} caso {i} de la aplicación",
                "label": label,
                "platform": "google_play",
            }
            for label in ("negative", "neutral", "positive")
            for i in range(20)
        ]
    )


def test_splits_are_disjoint_and_stratified_even_with_duplicate_texts():
    frame = labeled_reviews()
    duplicate = frame.iloc[[0]].assign(record_id="duplicate")
    splits = prepare_splits(pd.concat([frame, duplicate], ignore_index=True))
    keys = [set(part.text_key) for part in splits.values()]
    assert [len(part) for part in splits.values()] == [36, 12, 12]
    assert not (keys[0] & keys[1] or keys[0] & keys[2] or keys[1] & keys[2])
    assert all(
        set(part.label) == {"negative", "neutral", "positive"}
        for part in splits.values()
    )


def test_conflicting_labels_do_not_leak_across_splits():
    frame = labeled_reviews()
    conflict = frame.iloc[[0]].assign(record_id="conflict", label="positive")
    with pytest.raises(ValueError, match="contradictorias"):
        prepare_splits(pd.concat([frame, conflict], ignore_index=True))


def test_too_few_distinct_examples_are_rejected():
    with pytest.raises(ValueError, match="10 textos"):
        prepare_splits(labeled_reviews().groupby("label").head(4))


def test_unsupported_platform_is_rejected():
    frame = labeled_reviews()
    frame.loc[0, "platform"] = "instagram"
    with pytest.raises(ValueError, match="plataformas"):
        prepare_splits(frame)


def test_manual_social_data_can_be_used_after_labeling():
    frame = labeled_reviews().assign(platform="youtube")
    assert sum(len(part) for part in prepare_splits(frame).values()) == len(frame)


def test_preassigned_splits_are_respected_and_leakage_is_rejected():
    frame = labeled_reviews()
    frame["split"] = (
        frame.groupby("label")
        .cumcount()
        .map(lambda i: "train" if i < 12 else "test" if i < 16 else "validation")
    )
    result = prepare_splits(frame)
    assert (
        result["test"].record_id.tolist()
        == frame.loc[frame.split.eq("test"), "record_id"].tolist()
    )
    duplicate = frame.iloc[[0]].assign(record_id="leaked", split="test")
    with pytest.raises(ValueError, match="distintas particiones"):
        prepare_splits(pd.concat([frame, duplicate], ignore_index=True))


def test_training_evaluation_save_reload_and_inference_offline(tmp_path, monkeypatch):
    import json

    import torch
    from transformers import (
        BatchEncoding,
        RobertaConfig,
        RobertaForSequenceClassification,
    )

    from voc_demo.sentiment import robertuito
    from voc_demo.sentiment.adaptation import adapt, predict

    class TinyTokenizer:
        def __call__(self, texts, **kwargs):
            ids = torch.tensor([[0, 4 + len(text) % 10, 2] for text in texts])
            return BatchEncoding(
                {"input_ids": ids, "attention_mask": torch.ones_like(ids)}
            )

        def save_pretrained(self, path):
            (path / "tokenizer-test.json").write_text("{}")

    config = RobertaConfig(
        vocab_size=32,
        hidden_size=8,
        num_hidden_layers=1,
        num_attention_heads=2,
        intermediate_size=16,
        max_position_embeddings=32,
        num_labels=3,
        id2label={0: "NEG", 1: "NEU", 2: "POS"},
        label2id={"NEG": 0, "NEU": 1, "POS": 2},
    )
    monkeypatch.setattr(
        robertuito,
        "load_model",
        lambda cache: (TinyTokenizer(), RobertaForSequenceClassification(config)),
    )
    splits = prepare_splits(labeled_reviews())
    output = tmp_path / "run"
    tokenizer, model, report = adapt(
        splits, output, tmp_path / "cache", epochs=1, batch_size=16
    )
    assert report["best_epoch"] == 1
    assert 0 <= report["test"]["macro avg"]["f1-score"] <= 1
    assert (output / "model/model.safetensors").is_file()
    assert (
        json.loads((output / "metrics.json").read_text())["source_revision"]
        == robertuito.MODEL_REVISION
    )
    predictions = predict(splits["test"], tokenizer, model)
    assert predictions.record_id.tolist() == splits["test"].record_id.tolist()
    assert predictions.filter(like="p_").sum(axis=1).between(0.99999, 1.00001).all()
    with pytest.raises(FileExistsError):
        adapt(splits, output, tmp_path / "cache", epochs=1)
