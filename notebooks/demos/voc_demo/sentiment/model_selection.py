"""Select existing sentiment checkpoints; this module only performs inference."""

import hashlib
import json
from pathlib import Path

from .robertuito import MODEL_ID, MODEL_REVISION, classify_reviews

MODEL_FILES = (
    "model.safetensors",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
)
PLATFORMS = {"stores": {"app_store", "google_play"}, "social": {"x", "youtube"}}


def available_models(root, domain):
    """Offer the original, published versions, and completed local experiments."""
    options = {"Original RoBERTuito": {"source": MODEL_ID, "revision": MODEL_REVISION}}
    repo = f"Banamex-voc-sentiment/voc-robertuito-sentiment-{domain}-es"
    for version in ("v1", "v2"):
        options[f"Hugging Face · {version}"] = {"source": repo, "revision": version}
    for report in sorted(Path(root).rglob("metrics.json")):
        folder = report.parent
        model = folder / "model"
        if not all((model / name).is_file() for name in MODEL_FILES):
            continue
        # Earlier workflows use rated/social; Workflow 03 uses stores-*/training.
        if folder.name in {"rated", "social"}:
            detected = "stores" if folder.name == "rated" else "social"
            name = folder.parent.name
        elif folder.name == "training" and folder.parent.name.startswith(
            ("stores-", "social-")
        ):
            detected = folder.parent.name.split("-", 1)[0]
            name = folder.parent.name
        else:
            continue
        if detected == domain:
            label = f"Local · {name}"
            if label in options:
                label += f" · {folder.relative_to(root)}"
            options[label] = {"source": str(model.resolve()), "revision": "local"}
    return options


def resolve_models(selected):
    """Pin Hub tags to commits and fingerprint local weights before cache reuse."""
    from huggingface_hub import HfApi

    resolved = {}
    for domain, choice in selected.items():
        item = dict(choice)
        if item["revision"] == "local":
            digest = hashlib.sha256()
            for name in MODEL_FILES:
                with (Path(item["source"]) / name).open("rb") as stream:
                    digest.update(hashlib.file_digest(stream, "sha256").digest())
            item["fingerprint"] = digest.hexdigest()
        else:
            if item["source"] != MODEL_ID or item["revision"] != MODEL_REVISION:
                item["revision"] = (
                    HfApi().model_info(item["source"], revision=item["revision"]).sha
                )
            item["fingerprint"] = item["revision"]
        resolved[domain] = item
    identity = hashlib.sha256(json.dumps(resolved, sort_keys=True).encode()).hexdigest()
    return resolved, identity


def load_selected_model(choice, cache):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    options = {"cache_dir": str(cache), "trust_remote_code": False}
    if choice["revision"] != "local":
        options["revision"] = choice["revision"]
    tokenizer = AutoTokenizer.from_pretrained(choice["source"], **options)
    model = (
        AutoModelForSequenceClassification.from_pretrained(
            choice["source"], use_safetensors=True, **options
        )
        .to("cpu")
        .eval()
    )
    tokenizer.truncation_side = "right"
    return tokenizer, model


def classify_selected_models(
    reviews, selected, cache, *, sentiment_run_id, dataset_release_id
):
    """Route each platform to its selected checkpoint and preserve review order."""
    if not set(reviews.platform) <= set().union(*PLATFORMS.values()):
        raise ValueError("No sentiment model is defined for one or more platforms.")
    if reviews.record_id.duplicated().any():
        raise ValueError("Review IDs must be unique.")
    predictions, diagnostics = {}, []
    for domain, platforms in PLATFORMS.items():
        subset = reviews.loc[reviews.platform.isin(platforms)]
        if subset.empty:
            continue
        tokenizer, model = load_selected_model(selected[domain], cache)
        rows, info = classify_reviews(
            subset[["record_id", "embedding_text"]],
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
