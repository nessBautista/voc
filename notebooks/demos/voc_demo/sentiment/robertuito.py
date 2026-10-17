"""Local RoBERTuito inference; no topic keywords or generated labels enter the model."""

from .contracts import make_prediction

MODEL_ID = "pysentimiento/robertuito-sentiment-analysis"
MODEL_REVISION = "a2cc0f67ebd705c55191e25a05ba23d885fcc09b"
MAX_LENGTH = 128
LABEL_MAP = {"NEG": "negative", "NEU": "neutral", "POS": "positive"}


def load_model(cache_dir):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    options = {
        "revision": MODEL_REVISION,
        "cache_dir": str(cache_dir),
        "trust_remote_code": False,
    }
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, **options)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_ID, use_safetensors=True, **options
    )
    model.to("cpu").eval()
    tokenizer.truncation_side = "right"
    return tokenizer, model


def classify_reviews(
    reviews, tokenizer, model, *, sentiment_run_id, dataset_release_id, batch_size=8
):
    """One terminal record per review plus separate token/truncation diagnostics.

    Use the model authors' Spanish preprocessing. Keep complete prepared text in
    the hash; the manifest records the tokenizer and right-truncation policy.
    Infrastructure/inference errors stop the run rather than publishing partial results.
    """
    import torch
    from pysentimiento.preprocessing import preprocess_tweet

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if reviews["record_id"].duplicated().any():
        raise ValueError("Review IDs must be unique")
    mapping = {int(i): LABEL_MAP[label] for i, label in model.config.id2label.items()}
    if set(mapping.values()) != set(LABEL_MAP.values()) or len(mapping) != 3:
        raise ValueError("Unexpected sentiment model label mapping")
    common = {
        "sentiment_run_id": sentiment_run_id,
        "dataset_release_id": dataset_release_id,
    }
    pending, results, diagnostics = [], {}, []
    for row in reviews.itertuples(index=False):
        text = row.embedding_text
        if not isinstance(text, str) or not text.strip():
            results[row.record_id] = make_prediction(
                row.record_id, None, status="excluded", reason="empty_text", **common
            )
            continue
        try:
            prepared = preprocess_tweet(text, lang="es")
        except (TypeError, ValueError):
            results[row.record_id] = make_prediction(
                row.record_id,
                None,
                status="failed",
                reason="preprocessing_error",
                **common,
            )
            continue
        if not prepared.strip():
            results[row.record_id] = make_prediction(
                row.record_id,
                prepared,
                status="excluded",
                reason="empty_preprocessed_text",
                **common,
            )
            continue
        length = len(
            tokenizer(
                prepared, truncation=False, padding=False, add_special_tokens=True
            )["input_ids"]
        )
        diagnostics.append(
            {
                "record_id": row.record_id,
                "prepared_text": prepared,
                "input_token_count": length,
                "truncated": length > MAX_LENGTH,
            }
        )
        pending.append((row.record_id, prepared))
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        inputs = tokenizer(
            [text for _, text in batch],
            padding=True,
            truncation=True,
            max_length=MAX_LENGTH,
            return_tensors="pt",
        )
        with torch.inference_mode():
            scores = model(**inputs).logits.softmax(dim=-1).cpu().tolist()
        if len(scores) != len(batch):
            raise ValueError("Model returned an unexpected number of predictions")
        for (record_id, prepared), values in zip(batch, scores):
            probabilities = {mapping[i]: value for i, value in enumerate(values)}
            results[record_id] = make_prediction(
                record_id, prepared, probabilities=probabilities, **common
            )
    return [results[rid] for rid in reviews["record_id"]], diagnostics
