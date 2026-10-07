"""Workflow 01 artifacts: whole-review sentiment, not aspect sentiment.

Schemas mirror workflow01-TopicModellingAndSentimentAnalysis.md. Keep the
original topic object separate; a presentation joins it to its sentiment summary.
"""

import hashlib
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

LABELS = ("negative", "neutral", "positive")
_ROOT = Path(__file__).parent
_TOPIC = Draft202012Validator(json.loads((_ROOT / "topic.schema.json").read_text()))
_SENTIMENT = Draft202012Validator(
    json.loads((_ROOT / "sentiment.schema.json").read_text())
)


def validate_prediction(record):
    """Validate shape, probabilities and the recorded deterministic argmax policy."""
    _SENTIMENT.validate(record)
    if record["schema_version"] != "voc-review-sentiment-v1":
        raise ValueError("Expected a review prediction")
    probabilities = record["probabilities"]
    if probabilities is not None:
        values = list(probabilities.values())
        if not all(math.isfinite(p) and 0 <= p <= 1 for p in values):
            raise ValueError("Probabilities must be finite and between zero and one")
        if not math.isclose(sum(values), 1.0, abs_tol=1e-6):
            raise ValueError("Probabilities must sum to one")
        if record["status"] == "classified" and record["label"] != max(
            LABELS, key=probabilities.get
        ):
            raise ValueError(
                "Label does not match argmax (ties: negative, neutral, positive)"
            )
    return record


def make_prediction(
    record_id,
    prepared_text,
    *,
    sentiment_run_id,
    dataset_release_id,
    probabilities=None,
    status="classified",
    reason=None,
):
    """Build one terminal record; failure/exclusion never becomes neutral."""
    distribution = (
        {k: float(probabilities[k]) for k in LABELS}
        if probabilities is not None
        else None
    )
    record = {
        "schema_version": "voc-review-sentiment-v1",
        "sentiment_run_id": sentiment_run_id,
        "dataset_release_id": dataset_release_id,
        "run_manifest_ref": "./sentiment-manifest.json",
        "record_id": record_id,
        "input_hash": hashlib.sha256(prepared_text.encode("utf-8")).hexdigest()
        if prepared_text is not None
        else None,
        "status": status,
        "label": max(LABELS, key=distribution.get)
        if status == "classified" and distribution
        else None,
        "probabilities": distribution,
        "reason": reason,
    }
    return validate_prediction(record)


def build_topic_objects(
    assignments, model, *, topic_run_id, dataset_release_id, embedding_run_id
):
    """Adapt computed evidence to the contract, leaving feature interpretation pending."""
    if assignments["record_id"].duplicated().any():
        raise ValueError("Review IDs must be unique in the analyzed sample")
    topics = []
    for topic_id, members in assignments.loc[assignments["topic_id"].ge(0)].groupby(
        "topic_id", sort=True
    ):
        members = members.sort_values("vector_row")
        if members["cluster_id"].nunique() != 1:
            raise ValueError("A topic must map to one original cluster")
        representatives = []
        for text in model.get_representative_docs(int(topic_id)) or []:
            matches = members.loc[members["embedding_text"].eq(text)]
            if matches.empty:
                raise ValueError("Representative text is not a member of its topic")
            representatives.append(
                {"record_id": str(matches.iloc[0]["record_id"]), "embedding_text": text}
            )
        keywords = [
            {"term": term, "c_tf_idf_weight": float(weight)}
            for term, weight in model.get_topic(int(topic_id))
            if term.strip()
        ]
        keywords.sort(key=lambda item: item["c_tf_idf_weight"], reverse=True)
        topic = {
            "schema_version": "voc-topic-v1",
            "topic_run_id": topic_run_id,
            "dataset_release_id": dataset_release_id,
            "embedding_run_id": embedding_run_id,
            "run_manifest_ref": "./topic-manifest.json",
            "cluster_id": int(members.iloc[0]["cluster_id"]),
            "topic_id": int(topic_id),
            "keywords": keywords,
            "assigned_review_ids": members["record_id"].tolist(),
            "review_count": len(members),
            "representative_examples": representatives,
            "proposed_feature": None,
            "topic_description": None,
            "supporting_review_ids": [],
            "assessment": None,
            "interpretation_status": "pending",
            "interpretation_provenance": None,
            "review_status": "unreviewed",
        }
        _TOPIC.validate(topic)
        topics.append(topic)
    return topics


def aggregate_sentiment(topics, predictions, *, sentiment_run_id, dataset_release_id):
    """Require a complete, unique join; each classified review receives equal weight."""
    by_id = {}
    for prediction in predictions:
        validate_prediction(prediction)
        if (
            prediction["sentiment_run_id"] != sentiment_run_id
            or prediction["dataset_release_id"] != dataset_release_id
        ):
            raise ValueError("Predictions must come from the selected run and release")
        record_id = prediction["record_id"]
        if record_id in by_id:
            raise ValueError("Duplicate prediction for a review")
        by_id[record_id] = prediction
    expected = []
    topic_keys = set()
    for topic in topics:
        _TOPIC.validate(topic)
        if topic["dataset_release_id"] != dataset_release_id:
            raise ValueError("Topic and prediction releases differ")
        key = (topic["topic_run_id"], topic["topic_id"])
        if key in topic_keys:
            raise ValueError("Duplicate topic")
        topic_keys.add(key)
        if topic["review_count"] != len(topic["assigned_review_ids"]):
            raise ValueError("Topic count does not match its review IDs")
        expected.extend(topic["assigned_review_ids"])
    if len({key[0] for key in topic_keys}) > 1:
        raise ValueError("Select a single topic run")
    if len(expected) != len(set(expected)):
        raise ValueError("This workflow assigns each review to at most one topic")
    if set(expected) != set(by_id):
        raise ValueError("Missing or unexpected prediction rows")
    summaries = []
    for topic in topics:
        rows = [by_id[rid] for rid in topic["assigned_review_ids"]]
        statuses = {
            status: sum(p["status"] == status for p in rows)
            for status in ("classified", "abstained", "failed", "excluded")
        }
        counts = {
            label: sum(
                p["status"] == "classified" and p["label"] == label for p in rows
            )
            for label in LABELS
        }
        n = statuses["classified"]
        summary = {
            "schema_version": "voc-topic-sentiment-v1",
            "sentiment_run_id": sentiment_run_id,
            "topic_run_id": topic["topic_run_id"],
            "topic_id": topic["topic_id"],
            "dataset_release_id": dataset_release_id,
            "run_manifest_ref": "./sentiment-manifest.json",
            "predictions_ref": "./review-sentiment.json",
            "scope": "whole_review",
            "coverage": {
                "assigned_reviews": len(rows),
                **{f"{k}_reviews": v for k, v in statuses.items()},
                "classified_fraction": n / len(rows),
            },
            "label_counts": counts,
            "label_proportions": {k: v / n for k, v in counts.items()} if n else None,
            "net_sentiment": (counts["positive"] - counts["negative"]) / n
            if n
            else None,
            "aggregation_method": "equal_weight_per_classified_review_v1",
        }
        _SENTIMENT.validate(summary)
        summaries.append(summary)
    return summaries
