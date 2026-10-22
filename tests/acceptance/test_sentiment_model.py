"""Explicit, bounded offline acceptance of the real pinned RoBERTuito weights.

Run after populating the configured model cache:
VOC_RUN_SENTIMENT_MODEL_TEST=1 pytest -q tests/acceptance/test_sentiment_model.py
VOC_SENTIMENT_MODEL_DIR may override data/models/sentiment. No downloads occur.
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from voc.embeddings.contracts import text_hash
from voc.paths import data_root
from voc_ml.sentiment import SentimentConfig, classify_reviews


@pytest.mark.skipif(
    os.environ.get("VOC_RUN_SENTIMENT_MODEL_TEST") != "1",
    reason="Explicit opt-in required for real RoBERTuito inference",
)
def test_pinned_model_probabilities_and_repeat(tmp_path):
    model_dir = Path(
        os.environ.get("VOC_SENTIMENT_MODEL_DIR", data_root() / "models/sentiment")
    )
    reviews = pd.DataFrame(
        {
            "record_id": ["positive", "negative", "long", "duplicate"],
            "model_text": [
                "@banco Excelente aplicación, funciona muy bien 😊.",
                "La aplicación falla y no puedo entrar.",
                "La aplicación no funciona correctamente. " * 100,
                "@banco Excelente aplicación, funciona muy bien 😊.",
            ],
            "topic_id": [0, -1, 1, 0],
        }
    )
    options = {
        "cache_path": tmp_path / "predictions.sqlite",
        "run_root": tmp_path / "runs",
        "model_dir": model_dir,
        "local_files_only": True,
    }
    result = classify_reviews(reviews, SentimentConfig(batch_size=2), **options)
    assert result.report["counts"]["classified"] == 4
    assert result.report["counts"]["computed_unique"] == 3
    assert result.report["counts"]["truncated_rows"] == 1
    from pysentimiento.preprocessing import preprocess_tweet

    for source, row in zip(reviews.model_text, result.sentiments, strict=True):
        assert row.input_sha256 == text_hash(preprocess_tweet(source, lang="es"))
    for row in result.sentiments:
        row.__post_init__()
        assert abs(sum(row.probabilities.values()) - 1) < 1e-6

    def no_model(*args, **kwargs):
        raise AssertionError("Cached predictions must not initialize a model")

    repeated = classify_reviews(reviews, classifier_factory=no_model, **options)
    assert repeated.sentiments == result.sentiments
    assert repeated.report["counts"]["reused_unique"] == 3
