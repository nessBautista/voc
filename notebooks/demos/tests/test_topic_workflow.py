import json
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest
from jsonschema import ValidationError

from voc_demo.llm.topic_interpretation import (
    attach_interpretation,
    evidence_request,
    interpreted_topics,
    join_topic_sentiment,
    request_interpretation,
)
from voc_demo.sentiment import aggregate_sentiment, build_topic_objects, make_prediction


@pytest.fixture
def evidence():
    frame = pd.DataFrame(
        {
            "vector_row": [0, 1],
            "record_id": ["a", "b"],
            "topic_id": [0, 0],
            "cluster_id": [3, 3],
            "embedding_text": ["No puedo entrar", "No llega el código"],
        }
    )
    model = SimpleNamespace(
        get_topic=lambda _: [("cuenta", 0.6)],
        get_representative_docs=lambda _: ["No puedo entrar"],
    )
    topic = build_topic_objects(
        frame, model, topic_run_id="t1", dataset_release_id="r1", embedding_run_id="e1"
    )[0]
    return topic, frame


def attempt(topic, frame, **changes):
    return {
        "attempt_id": "a1",
        "created_at": "2026-10-01T00:00:00Z",
        "prompt_version": "app-feature-v1",
        "requested_model": "provider/model",
        "request": evidence_request(topic, frame),
        "status": "completed",
        "response": {
            "proposed_feature": "Acceso",
            "topic_description": "No llega el código de acceso",
            "supporting_review_ids": ["b"],
            "assessment": "single_feature",
        },
        **changes,
    }


def test_interpretation_grows_copy_without_changing_evidence(evidence):
    topic, frame = evidence
    before = deepcopy(topic)
    result = attach_interpretation(topic, attempt(topic, frame))
    assert topic == before
    assert result["interpretation_status"] == "completed"
    assert result["review_status"] == "proposed"
    for key in [
        "topic_id",
        "cluster_id",
        "keywords",
        "review_count",
        "assigned_review_ids",
    ]:
        assert result[key] == before[key]
    assert interpreted_topics([topic], []) == [topic]


def test_rejects_invented_citation_and_extra_model_fields(evidence):
    topic, frame = evidence
    row = attempt(topic, frame)
    row["response"]["supporting_review_ids"] = ["invented"]
    with pytest.raises(ValueError, match="not supplied"):
        attach_interpretation(topic, row)
    row = attempt(topic, frame)
    row["response"]["review_count"] = 500
    with pytest.raises(ValidationError):
        attach_interpretation(topic, row)


def test_mixed_cannot_force_one_feature_and_failures_preserve_topic(evidence):
    topic, frame = evidence
    row = attempt(topic, frame)
    row["response"]["assessment"] = "mixed"
    with pytest.raises(ValidationError):
        attach_interpretation(topic, row)
    row["status"] = "failed"
    result = attach_interpretation(topic, row)
    assert result["interpretation_status"] == "failed"
    assert result["proposed_feature"] is None
    assert result["keywords"] == topic["keywords"]


def test_wrong_topic_run_is_rejected(evidence):
    topic, frame = evidence
    row = attempt(topic, frame)
    row["request"]["topic_run_id"] = "other"
    with pytest.raises(ValueError, match="different topic"):
        attach_interpretation(topic, row)


def test_bounded_evidence_records_truncation(evidence):
    topic, frame = evidence
    request = evidence_request(topic, frame, max_reviews=1, max_chars=5)
    assert request["evidence"] == [
        {"record_id": "a", "text": "No pu", "truncated": True}
    ]


def test_request_and_response_validation_without_network(evidence):
    topic, frame = evidence
    response = attempt(topic, frame)["response"]
    calls = []

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id="req-1",
                model="returned/model",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=json.dumps(response), refusal=None
                        ),
                    )
                ],
            )

    result = request_interpretation(
        topic,
        evidence_request(topic, frame),
        "provider/model",
        client_factory=lambda **_: Client(),
    )
    assert result["status"] == "completed"
    assert calls[0]["response_format"]["type"] == "json_schema"
    response["supporting_review_ids"] = ["fake"]
    rejected = request_interpretation(
        topic,
        evidence_request(topic, frame),
        "provider/model",
        client_factory=lambda **_: Client(),
    )
    assert rejected["status"] == "failed"
    assert rejected["raw_response"]


def test_final_join_uses_run_topic_and_release(evidence):
    topic, frame = evidence
    rows = [
        make_prediction(
            rid,
            "Text",
            sentiment_run_id="s1",
            dataset_release_id="r1",
            probabilities={"negative": 0.8, "neutral": 0.1, "positive": 0.1},
        )
        for rid in ["a", "b"]
    ]
    summaries = aggregate_sentiment(
        [topic], rows, sentiment_run_id="s1", dataset_release_id="r1"
    )
    view = join_topic_sentiment(
        [attach_interpretation(topic, attempt(topic, frame))], summaries
    )[0]
    assert view["topic"]["proposed_feature"] == "Acceso"
    assert view["sentiment_summary"]["net_sentiment"] == -1
    summaries[0]["dataset_release_id"] = "wrong"
    with pytest.raises(ValueError, match="releases differ"):
        join_topic_sentiment([topic], summaries)
