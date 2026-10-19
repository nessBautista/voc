"""Verify frozen weights, actual fine-tuning and reproducible language screening."""

from io import BytesIO

import pandas as pd
import pytest
from voc_demo.sentiment.language_audit import audit_languages, select_language_rows
from voc_demo.sentiment.transfer_adaptation import configure_layers, frozen_layers_eval


def tiny_model():
    from transformers import RobertaConfig, RobertaForSequenceClassification

    return RobertaForSequenceClassification(
        RobertaConfig(
            vocab_size=32,
            hidden_size=8,
            num_hidden_layers=2,
            num_attention_heads=2,
            intermediate_size=16,
            max_position_embeddings=32,
            num_labels=3,
            id2label={0: "NEG", 1: "NEU", 2: "POS"},
            label2id={"NEG": 0, "NEU": 1, "POS": 2},
        )
    )


@pytest.mark.parametrize("mode", ["head", "last_n", "full"])
def test_exact_trainable_layers_and_frozen_dropout(mode):
    model = tiny_model()
    report = configure_layers(model, mode, 1)
    names = report["trainable_parameter_names"]
    assert (
        all(name.startswith("classifier.") for name in names)
        if mode == "head"
        else True
    )
    assert any(name.startswith("classifier.") for name in names)
    if mode == "last_n":
        assert any(name.startswith("roberta.encoder.layer.1.") for name in names)
        assert not any(name.startswith("roberta.encoder.layer.0.") for name in names)
        assert not any(name.startswith("roberta.embeddings.") for name in names)
    assert (report["trainable_parameters"] == report["total_parameters"]) == (
        mode == "full"
    )
    model.train()
    frozen_layers_eval(model, mode, 1)
    assert model.classifier.training
    if mode != "full":
        assert not model.roberta.embeddings.training
        assert not model.roberta.encoder.layer[0].training
    assert model.roberta.encoder.layer[-1].training == (mode != "head")


def test_invalid_layer_settings_are_rejected():
    with pytest.raises(ValueError, match="between"):
        configure_layers(tiny_model(), "last_n", 3)
    with pytest.raises(ValueError, match="Choose head"):
        configure_layers(tiny_model(), "invalid", 1)


@pytest.mark.parametrize("mode", ["head", "last_n"])
def test_training_changes_only_selected_parameters(tmp_path, monkeypatch, mode):
    import torch
    from transformers import BatchEncoding
    from voc_demo.sentiment import robertuito
    from voc_demo.sentiment.adaptation import prepare_splits
    from voc_demo.sentiment.transfer_adaptation import adapt

    class Tokenizer:
        def __call__(self, texts, **kwargs):
            ids = torch.tensor([[0, 4 + len(text) % 20, 2] for text in texts])
            return BatchEncoding(
                {"input_ids": ids, "attention_mask": torch.ones_like(ids)}
            )

        def save_pretrained(self, folder):
            (folder / "test-tokenizer.json").write_text("{}")

    original = {}

    def load(cache):
        model = tiny_model()
        original.update(
            {name: p.detach().clone() for name, p in model.named_parameters()}
        )
        return Tokenizer(), model

    monkeypatch.setattr(robertuito, "load_model", load)
    frame = pd.DataFrame(
        [
            {
                "record_id": f"{label}-{i}",
                "text": f"{label} application review {i}",
                "label": label,
                "platform": "x",
            }
            for label in ("negative", "neutral", "positive")
            for i in range(10)
        ]
    )
    splits = prepare_splits(frame)
    progress = []
    _, adapted, report = adapt(
        splits,
        tmp_path / "run",
        tmp_path / "cache",
        epochs=1,
        batch_size=18,
        training_mode=mode,
        last_layers=1,
        on_epoch=progress.append,
    )
    changed = {
        name
        for name, parameter in adapted.named_parameters()
        if not torch.equal(parameter.detach().cpu(), original[name].cpu())
    }
    assert changed
    assert changed <= set(report["trainable_parameter_names"])
    assert report["training_mode"] == mode
    assert len(report["confusion_matrix"]) == 3
    assert report["baseline_test"]["macro avg"]["support"] == 6
    assert len(progress) == 1


def language_csv():
    return (
        pd.DataFrame(
            {
                "record_id": ["es", "en", "short"],
                "text": [
                    "La aplicación del banco funciona correctamente y permite consultar los movimientos y realizar transferencias de dinero sin problemas.",
                    "The banking application works correctly and allows customers to check their account balance and transfer money without any problems.",
                    "ok",
                ],
            }
        )
        .to_csv(index=False)
        .encode()
    )


def test_languages_and_explicit_filter_policy():
    pytest.importorskip("langdetect")
    frame, audit, metadata = audit_languages(language_csv(), full=True)
    assert audit.status.tolist() == ["spanish", "other_language", "short_text"]
    assert audit.detected_language.tolist() == ["es", "en", "und"]
    kept, info = select_language_rows(frame, audit, metadata, spanish_only=True)
    assert pd.read_csv(BytesIO(kept)).record_id.tolist() == ["es"]
    assert info["excluded_rows"] == 2
    original, info = select_language_rows(frame, audit, metadata)
    assert len(pd.read_csv(BytesIO(original))) == 3
    assert info["policy"] == "keep_all"


def test_sample_is_reproducible_and_cannot_filter_unreviewed_rows():
    pytest.importorskip("langdetect")
    frame, audit, metadata = audit_languages(language_csv(), sample_size=2)
    _, again, _ = audit_languages(language_csv(), sample_size=2)
    pd.testing.assert_frame_equal(audit, again)
    assert metadata["audited_rows"] == 2
    with pytest.raises(ValueError, match="full audit"):
        select_language_rows(frame, audit, metadata, spanish_only=True)


def notebook_cell(name):
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "workflow04_transfer_learning.py"
    tree = ast.parse(path.read_text())
    node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    node.decorator_list = []
    namespace = {}
    exec(  # noqa: S102 - Named cells from the local notebook, not user-uploaded code.
        compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace
    )
    return namespace[name]


def test_notebook_controls_default_to_frozen_encoder_and_no_training():
    import marimo as mo

    social, stores, train_social, train_stores = notebook_cell("parameter_controls")(mo)
    assert social.value["training_mode"] == stores.value["training_mode"] == "head"
    assert not train_social.value and not train_stores.value
    full, audit_social, audit_stores, threshold, letters, spanish = notebook_cell(
        "language_controls"
    )(mo)
    assert not full.value and not spanish.value
    assert not audit_social.value and not audit_stores.value
    assert threshold.value == 0.8 and letters.value == 20


def test_evaluation_tables_and_plots_render():
    import marimo as mo

    score = {"precision": 0.7, "recall": 0.6, "f1-score": 0.65, "support": 3}
    report = {
        label: score
        for label in ("negative", "neutral", "positive", "macro avg", "weighted avg")
    }
    report["accuracy"] = 0.7
    metrics = {
        "label_order": ["negative", "neutral", "positive"],
        "training_mode": "head",
        "encoder_blocks_trained": 0,
        "trainable_parameters": 99,
        "total_parameters": 1000,
        "trainable_percent": 9.9,
        "best_epoch": 1,
        "device": "cpu",
        "baseline_test": report,
        "test": report,
        "baseline_test_macro_f1": 0.65,
        "confusion_matrix": [[2, 1, 0], [0, 2, 1], [1, 0, 2]],
        "history": [{"epoch": 1, "loss": 0.8, "validation_macro_f1": 0.6}],
    }
    (show,) = notebook_cell("result_helper")(mo, pd)
    show(
        {
            "domain": "social",
            "metrics": metrics,
            "mlflow_run_id": "m",
            "zenml_run_id": "z",
            "output": "/tmp/model",
        }
    )
