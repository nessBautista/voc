"""Transfer learning with an explicitly frozen RoBERTuito encoder."""

import hashlib
import json

from .adaptation import LABELS, predict
from .robertuito import LABEL_MAP, MAX_LENGTH, MODEL_ID, MODEL_REVISION


def configure_layers(model, mode="head", last_layers=1):
    """Choose exactly which parameters the optimizer is allowed to update."""
    if mode not in {"head", "last_n", "full"}:
        raise ValueError("Choose head, last_n or full training.")
    blocks = model.roberta.encoder.layer
    if mode == "last_n" and (
        not isinstance(last_layers, int) or not 1 <= last_layers <= len(blocks)
    ):
        raise ValueError(f"Choose between 1 and {len(blocks)} final encoder blocks.")
    for parameter in model.parameters():
        parameter.requires_grad = mode == "full"
    if mode != "full":
        for parameter in model.classifier.parameters():
            parameter.requires_grad = True
        if mode == "last_n":
            for block in blocks[-last_layers:]:
                for parameter in block.parameters():
                    parameter.requires_grad = True
    total = sum(parameter.numel() for parameter in model.parameters())
    trainable = sum(
        parameter.numel() for parameter in model.parameters() if parameter.requires_grad
    )
    return {
        "training_mode": mode,
        "encoder_blocks_trained": len(blocks)
        if mode == "full"
        else last_layers
        if mode == "last_n"
        else 0,
        "total_parameters": total,
        "trainable_parameters": trainable,
        "trainable_percent": 100 * trainable / total,
        "trainable_parameter_names": [
            name
            for name, parameter in model.named_parameters()
            if parameter.requires_grad
        ],
    }


def frozen_layers_eval(model, mode, last_layers):
    """Disable dropout in frozen encoder modules while the classifier trains."""
    if mode == "head":
        model.roberta.eval()
    elif mode == "last_n":
        model.roberta.embeddings.eval()
        for block in model.roberta.encoder.layer[:-last_layers]:
            block.eval()


def adapt(
    splits,
    output,
    cache,
    epochs=3,
    batch_size=8,
    learning_rate=2e-5,
    seed=42,
    on_epoch=None,
    training_mode="head",
    last_layers=1,
):
    """Fine-tune, select by validation macro F1, then evaluate held-out test data."""
    import pandas as pd
    import torch
    from sklearn.metrics import classification_report, confusion_matrix, f1_score
    from transformers import AutoModelForSequenceClassification, set_seed

    from .robertuito import load_model

    if epochs < 1 or batch_size < 1 or learning_rate <= 0:
        raise ValueError("Épocas, tamaño de lote y learning rate deben ser positivos.")
    output.mkdir(parents=True, exist_ok=False)  # A run never overwrites another model.
    set_seed(seed)
    tokenizer, model = load_model(cache)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    layer_report = configure_layers(model, training_mode, last_layers)
    label_to_id = {
        LABEL_MAP[label]: int(i) for i, label in model.config.id2label.items()
    }

    def score(frame, current_model):
        predictions = predict(frame, tokenizer, current_model, batch_size)
        return float(
            f1_score(
                frame.label,
                predictions.label,
                labels=list(LABELS),
                average="macro",
                zero_division=0,
            )
        )

    baseline_validation = score(splits["validation"], model)
    baseline_predictions = predict(splits["test"], tokenizer, model, batch_size)
    baseline_test = float(
        f1_score(
            splits["test"].label,
            baseline_predictions.label,
            labels=list(LABELS),
            average="macro",
            zero_division=0,
        )
    )
    baseline_test_report = classification_report(
        splits["test"].label,
        baseline_predictions.label,
        labels=list(LABELS),
        output_dict=True,
        zero_division=0,
    )
    baseline_predictions.to_csv(output / "baseline_test_predictions.csv", index=False)
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=learning_rate
    )
    train = splits["train"]
    best_score, best_epoch, history = -1.0, None, []
    for epoch in range(1, epochs + 1):
        model.train()
        frozen_layers_eval(model, training_mode, last_layers)
        shuffled = train.sample(frac=1, random_state=seed + epoch)
        losses = []
        for start in range(0, len(shuffled), batch_size):
            batch = shuffled.iloc[start : start + batch_size]
            inputs = tokenizer(
                batch.prepared_text.tolist(),
                padding=True,
                truncation=True,
                max_length=MAX_LENGTH,
                return_tensors="pt",
            ).to(device)
            targets = torch.tensor(
                [label_to_id[label] for label in batch.label], device=device
            )
            optimizer.zero_grad()
            loss = model(**inputs, labels=targets).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        validation_f1 = score(splits["validation"], model)
        history.append(
            {
                "epoch": epoch,
                "loss": sum(losses) / len(losses),
                "validation_macro_f1": validation_f1,
            }
        )
        if validation_f1 > best_score:
            best_score, best_epoch = validation_f1, epoch
            model.save_pretrained(output / "model", safe_serialization=True)
            tokenizer.save_pretrained(output / "model")
        if on_epoch is not None:
            on_epoch(history[-1])

    del optimizer, model
    adapted = (
        AutoModelForSequenceClassification.from_pretrained(
            output / "model", trust_remote_code=False, use_safetensors=True
        )
        .to(device)
        .eval()
    )
    predictions = predict(splits["test"], tokenizer, adapted, batch_size)
    test_report = classification_report(
        splits["test"].label,
        predictions.label,
        labels=list(LABELS),
        output_dict=True,
        zero_division=0,
    )
    report = {
        **layer_report,
        "source_model": MODEL_ID,
        "source_revision": MODEL_REVISION,
        "seed": seed,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "max_length": MAX_LENGTH,
        "device": device,
        "best_epoch": best_epoch,
        "validation_macro_f1": best_score,
        "baseline_validation_macro_f1": baseline_validation,
        "baseline_test_macro_f1": baseline_test,
        "baseline_test": baseline_test_report,
        "test": test_report,
        "history": history,
        "confusion_matrix": confusion_matrix(
            splits["test"].label, predictions.label, labels=list(LABELS)
        ).tolist(),
        "label_order": list(LABELS),
        "data_sha256": hashlib.sha256(
            pd.concat(splits.values()).to_csv(index=False).encode()
        ).hexdigest(),
    }
    for name, frame in splits.items():
        frame.to_csv(output / f"{name}.csv", index=False)
    predictions.to_csv(output / "adapted_test_predictions.csv", index=False)
    (output / "metrics.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return tokenizer, adapted, report
