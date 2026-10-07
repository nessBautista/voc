from copy import deepcopy

import pandas as pd
import pytest

from voc_ml.sentiment import (
    aggregate_sentiment,
    build_topic_objects,
    make_prediction,
    validate_prediction,
)


class TopicModel:
    def get_topic(self, topic_id):
        return [("acceso", 0.6), ("cuenta", 0.2)]

    def get_representative_docs(self, topic_id):
        return ["No puedo entrar"]


def topic():
    frame = pd.DataFrame(
        {
            "vector_row": [0, 1, 2],
            "record_id": ["a", "b", "outlier"],
            "embedding_text": ["No puedo entrar", "Funciona", "Otro"],
            "topic_id": [0, 0, -1],
            "cluster_id": [3, 3, -1],
        }
    )
    return build_topic_objects(
        frame,
        TopicModel(),
        topic_run_id="topics-1",
        dataset_release_id="release-1",
        embedding_run_id="emb-1",
    )


def prediction(rid, **kwargs):
    return make_prediction(
        rid,
        "Texto",
        sentiment_run_id="sent-1",
        dataset_release_id="release-1",
        **kwargs,
    )


def aggregate(rows, topics=None):
    return aggregate_sentiment(
        topic() if topics is None else topics,
        rows,
        sentiment_run_id="sent-1",
        dataset_release_id="release-1",
    )


def test_contract_join_preserves_topic_and_reports_coverage():
    topics = topic()
    before = deepcopy(topics)
    rows = [
        prediction(
            "a", probabilities={"negative": 0.8, "neutral": 0.1, "positive": 0.1}
        ),
        prediction("b", status="failed", reason="preprocessing_error"),
    ]
    summary = aggregate(rows, topics)[0]
    assert topics == before
    assert topics[0]["interpretation_status"] == "pending"
    assert topics[0]["assigned_review_ids"] == ["a", "b"]
    assert summary["coverage"]["classified_fraction"] == 0.5
    assert summary["label_counts"] == {"negative": 1, "neutral": 0, "positive": 0}
    assert summary["net_sentiment"] == -1


def test_no_predictions_is_unknown_not_neutral():
    summary = aggregate(
        [prediction(rid, status="excluded", reason="empty_text") for rid in ["a", "b"]]
    )[0]
    assert summary["label_proportions"] is None
    assert summary["net_sentiment"] is None
    assert summary["coverage"]["classified_fraction"] == 0


@pytest.mark.parametrize("ids", [["a"], ["a", "a"], ["a", "b", "extra"]])
def test_incomplete_duplicate_or_extra_predictions_rejected(ids):
    with pytest.raises(ValueError):
        aggregate([prediction(rid, status="failed", reason="error") for rid in ids])


def test_mixed_release_rejected():
    rows = [prediction(rid, status="failed", reason="error") for rid in ["a", "b"]]
    rows[0]["dataset_release_id"] = "another-release"
    with pytest.raises(ValueError, match="selected run and release"):
        aggregate(rows)


def test_probability_validation_and_label_consistency():
    with pytest.raises(ValueError, match="sum to one"):
        prediction(
            "a", probabilities={"negative": 0.2, "neutral": 0.2, "positive": 0.2}
        )
    row = prediction(
        "a", probabilities={"negative": 0.8, "neutral": 0.1, "positive": 0.1}
    )
    row["label"] = "positive"
    with pytest.raises(ValueError, match="argmax"):
        validate_prediction(row)


def test_classifier_uses_config_labels_and_records_truncation():
    from types import SimpleNamespace

    import torch

    from voc_ml.sentiment.robertuito import classify_reviews

    calls = []

    class Tokenizer:
        def __call__(self, text, **kwargs):
            calls.append((text, kwargs))
            return {} if isinstance(text, list) else {"input_ids": list(range(130))}

    class Model:
        config = SimpleNamespace(id2label={0: "POS", 1: "NEG", 2: "NEU"})

        def __call__(self, **kwargs):
            return SimpleNamespace(logits=torch.tensor([[4.0, 1.0, 0.0]]))

    frame = pd.DataFrame(
        {"record_id": ["a", "b"], "embedding_text": ["No puedo entrar 😢", "  "]}
    )
    predictions, diagnostics = classify_reviews(
        frame,
        Tokenizer(),
        Model(),
        sentiment_run_id="sent-1",
        dataset_release_id="release-1",
    )
    assert (
        predictions[0]["label"] == "positive"
    )  # Comes from config, not a presumed index order.
    assert predictions[1]["status"] == "excluded"
    assert predictions[1]["probabilities"] is None
    assert diagnostics[0]["truncated"] is True
    assert "emoji" in diagnostics[0]["prepared_text"]
    assert calls[-1][1]["max_length"] == 128
    assert calls[-1][1]["truncation"] is True
