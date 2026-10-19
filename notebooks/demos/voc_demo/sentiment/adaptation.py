"""Small domain-adaptation experiment, independent of topics and shared storage."""

import hashlib
import json

from .robertuito import LABEL_MAP, MAX_LENGTH, MODEL_ID, MODEL_REVISION

LABELS = ("negative", "neutral", "positive")


def prepare_splits(frame, seed=42):
    """Respect supplied splits, or split manually labeled unique texts 60/20/20."""
    from pysentimiento.preprocessing import preprocess_tweet
    from sklearn.model_selection import train_test_split

    required = {"record_id", "text", "label", "platform"}
    if not required <= set(frame.columns):
        raise ValueError(f"Faltan columnas: {sorted(required - set(frame.columns))}")
    optional = [name for name in ("split", "rating", "label_source") if name in frame]
    data = frame[sorted(required) + optional].copy()
    if data[sorted(required)].isna().any().any():
        raise ValueError(
            "Completa todos los identificadores, textos, etiquetas y plataformas."
        )
    for column in required:
        data[column] = data[column].astype(str).str.strip()
    if data[sorted(required)].eq("").any().any() or data.record_id.duplicated().any():
        raise ValueError("No se permiten campos vacíos ni record_id repetidos.")
    if not set(data.platform) <= {"app_store", "google_play", "x", "youtube"}:
        raise ValueError(
            "Las plataformas admitidas son app_store, google_play, x y youtube."
        )
    if set(data.label) != set(LABELS):
        raise ValueError(
            f"Completa las tres clases con etiquetas: {', '.join(LABELS)}."
        )
    data["prepared_text"] = data.text.map(
        lambda text: preprocess_tweet(text, lang="es")
    )
    if data.prepared_text.str.strip().eq("").any():
        raise ValueError("Hay textos vacíos después del preprocesamiento.")
    # Case/whitespace variants and identical encoder inputs stay in one split.
    data["text_key"] = data.prepared_text.map(
        lambda text: " ".join(text.casefold().split())
    )
    if "split" in data:
        # Star-derived labels may disagree for the same text. Preserve the user's
        # rating rule and all reviews, but never let that text cross partitions.
        if set(data.split) != {"train", "test", "validation"}:
            raise ValueError("split debe contener únicamente train, test y validation.")
        if data.groupby("text_key").split.nunique().gt(1).any():
            raise ValueError("Hay textos equivalentes en distintas particiones.")
        parts = {
            name: data.loc[data.split.eq(name)].copy()
            for name in ("train", "validation", "test")
        }
        if any(set(part.label) != set(LABELS) for part in parts.values()):
            raise ValueError(
                "Cada partición debe incluir las tres clases de sentimiento."
            )
        return parts
    if data.groupby("text_key").label.nunique().gt(1).any():
        raise ValueError("Hay textos equivalentes con etiquetas contradictorias.")
    data = data.drop_duplicates("text_key").reset_index(drop=True)
    if data.label.value_counts().reindex(LABELS, fill_value=0).min() < 10:
        raise ValueError(
            "Necesitas al menos 10 textos distintos por clase; es un mínimo técnico."
        )
    train, held = train_test_split(
        data, test_size=0.4, stratify=data.label, random_state=seed
    )
    validation, test = train_test_split(
        held, test_size=0.5, stratify=held.label, random_state=seed
    )
    return {"train": train, "validation": validation, "test": test}


def predict(frame, tokenizer, model, batch_size=8):
    """Return one label and three probabilities per record, preserving IDs/order."""
    import pandas as pd
    import torch
    from pysentimiento.preprocessing import preprocess_tweet

    if not {"record_id", "text"} <= set(frame.columns) or frame.empty:
        raise ValueError(
            "El CSV debe contener record_id y text, y al menos una reseña."
        )
    if (
        frame[["record_id", "text"]].isna().any().any()
        or frame.record_id.duplicated().any()
    ):
        raise ValueError("No se permiten valores nulos ni identificadores repetidos.")
    if frame.record_id.astype(str).str.strip().eq("").any():
        raise ValueError("Los identificadores no pueden estar vacíos.")
    texts = [preprocess_tweet(str(text), lang="es") for text in frame.text]
    if any(not text.strip() for text in texts):
        raise ValueError("No se permiten reseñas vacías.")
    mapping = {int(i): LABEL_MAP[label] for i, label in model.config.id2label.items()}
    device = next(model.parameters()).device
    rows = []
    model.eval()
    for start in range(0, len(texts), batch_size):
        inputs = tokenizer(
            texts[start : start + batch_size],
            padding=True,
            truncation=True,
            max_length=MAX_LENGTH,
            return_tensors="pt",
        ).to(device)
        with torch.inference_mode():
            scores = model(**inputs).logits.softmax(-1).cpu().tolist()
        for record_id, values in zip(
            frame.record_id.iloc[start : start + batch_size], scores
        ):
            probabilities = {mapping[i]: value for i, value in enumerate(values)}
            rows.append(
                {
                    "record_id": record_id,
                    "label": max(probabilities, key=probabilities.get),
                    **{f"p_{label}": probabilities[label] for label in LABELS},
                }
            )
    return pd.DataFrame(rows)


def adapt(
    splits,
    output,
    cache,
    epochs=3,
    batch_size=8,
    learning_rate=2e-5,
    seed=42,
    on_epoch=None,
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
    baseline_predictions.to_csv(output / "baseline_test_predictions.csv", index=False)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    train = splits["train"]
    best_score, best_epoch, history = -1.0, None, []
    for epoch in range(1, epochs + 1):
        model.train()
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
