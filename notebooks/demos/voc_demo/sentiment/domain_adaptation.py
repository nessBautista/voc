"""Workflow 01 data audit, stratified holdouts and domain-specific inference."""

import hashlib
import json
import os
from io import BytesIO
from pathlib import Path

import pandas as pd
from pysentimiento.preprocessing import preprocess_tweet

from .adaptation import LABELS
from .training_data import LABEL_BY_RATING, assign_rating_splits


def manual_csv_path():
    override = os.environ.get("VOC_MANUAL_SENTIMENT_CSV")
    if override:
        return Path(override).expanduser()
    return (
        Path(__file__).resolve().parents[4] / "data/Social_reviews_manual_labeled.csv"
    )


def inspect_manual_data(path):
    """Read the corrected CSV exactly as labeled; no language/label aliases."""
    raw = path.read_bytes()
    frame = pd.read_csv(
        BytesIO(raw), dtype=str, keep_default_na=False, encoding="utf-8-sig"
    )
    required = {"record_id", "text", "label", "platform"}
    if not required <= set(frame):
        raise ValueError(f"Missing columns: {sorted(required - set(frame))}")
    for column in required:
        frame[column] = frame[column].str.strip()
    if frame[sorted(required)].eq("").any().any():
        raise ValueError("Review IDs, text, labels and platforms must not be blank.")
    if not frame.record_id.is_unique:
        raise ValueError("Review IDs must be unique.")
    if set(frame.label) != set(LABELS):
        raise ValueError(
            "Use exactly negative, neutral and positive; labels are not translated."
        )
    if not set(frame.platform) <= {"x", "youtube"}:
        raise ValueError("The manual dataset must contain only X and YouTube reviews.")
    frame["prepared_text"] = frame.text.map(
        lambda text: preprocess_tweet(text, lang="es")
    )
    frame["text_key"] = frame.prepared_text.map(
        lambda text: " ".join(text.casefold().split())
    )
    if frame.text_key.eq("").any():
        raise ValueError("Some reviews become empty after RoBERTuito preprocessing.")
    if frame.groupby("text_key").label.nunique().gt(1).any():
        raise ValueError(
            "Equivalent texts have conflicting labels; review them before splitting."
        )
    audit = {
        "rows": len(frame),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "class_counts": frame.label.value_counts()
        .reindex(LABELS, fill_value=0)
        .to_dict(),
        "platform_counts": frame.platform.value_counts().to_dict(),
        "duplicate_texts": int(frame.text_key.duplicated().sum()),
        "median_characters": float(frame.text.str.len().median()),
        "p95_characters": float(frame.text.str.len().quantile(0.95)),
    }
    return frame, audit


def stratified_manual_splits(frame, seed=42):
    """Keep every review and preserve label proportions across 60/20/20 splits."""
    if frame.label.value_counts().reindex(LABELS, fill_value=0).min() < 5:
        raise ValueError(
            "At least five reviews per class are required for three holdouts."
        )
    assigned = assign_rating_splits(frame, seed)
    splits = {
        name: assigned.loc[assigned.split.eq(name)]
        .sample(frac=1, random_state=seed)
        .reset_index(drop=True)
        for name in ("train", "test", "validation")
    }
    combined = pd.concat(splits.values())
    assert len(combined) == len(frame) and combined.record_id.is_unique
    assert combined.groupby("text_key").split.nunique().max() == 1
    return splits


def rated_splits(workable, seed=42):
    """Reproduce the star-label experiment from Workflow 01's pinned release."""
    rows = workable.loc[
        workable.platform.isin(["app_store", "google_play"])
        & workable.rating.isin(LABEL_BY_RATING)
    ].copy()
    rows["label"] = rows.rating.map(LABEL_BY_RATING)
    rows["prepared_text"] = rows.text.map(
        lambda text: preprocess_tweet(text, lang="es")
    )
    rows["text_key"] = rows.prepared_text.map(
        lambda text: " ".join(text.casefold().split())
    )
    rows = assign_rating_splits(rows.loc[rows.text_key.ne("")], seed)
    return {
        name: rows.loc[rows.split.eq(name)].copy()
        for name in ("train", "test", "validation")
    }


def export_splits(splits, folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name, frame in splits.items():
        frame.to_csv(folder / f"{name}.csv", index=False, encoding="utf-8-sig")


def cached_experiments(folder):
    results = {}
    for index in sorted((folder / "runs").glob("*/experiments.json")):
        saved = json.loads(index.read_text())
        for name, result in saved.items():
            model = Path(result["output"]) / "model"
            if all(
                (model / filename).is_file()
                for filename in (
                    "model.safetensors",
                    "config.json",
                    "tokenizer.json",
                    "tokenizer_config.json",
                )
            ):
                results[name] = result
    return results


def adapter_identity(results):
    """Inference cache changes when either adapter's weights or tokenizer changes."""
    digest = hashlib.sha256()
    for name in ("rated", "social"):
        digest.update(name.encode())
        model = Path(results[name]["output"]) / "model"
        for filename in (
            "model.safetensors",
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
        ):
            with (model / filename).open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()


def classify_with_adapters(reviews, results, *, sentiment_run_id, dataset_release_id):
    """Route stores/social reviews to their own adapter and preserve row identity."""
    from .experiments import load_trained_model
    from .robertuito import classify_reviews

    if not set(reviews.platform) <= {"app_store", "google_play", "x", "youtube"}:
        raise ValueError("No sentiment adapter is defined for one or more platforms.")
    predictions, diagnostics = {}, []
    for name, platforms in (
        ("rated", {"app_store", "google_play"}),
        ("social", {"x", "youtube"}),
    ):
        selected = reviews.loc[reviews.platform.isin(platforms)]
        if selected.empty:
            continue
        tokenizer, model = load_trained_model(Path(results[name]["output"]))
        rows, info = classify_reviews(
            selected[["record_id", "embedding_text"]],
            tokenizer,
            model,
            sentiment_run_id=sentiment_run_id,
            dataset_release_id=dataset_release_id,
            batch_size=16,
        )
        predictions.update({row["record_id"]: row for row in rows})
        diagnostics.extend(info)
        del tokenizer, model
    return [predictions[rid] for rid in reviews.record_id], diagnostics
