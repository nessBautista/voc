"""Query the same immutable four-review population through every result view."""

from dataclasses import replace
from uuid import uuid4

import pytest

from tests.unit.test_analysis_store import candidate as candidate  # noqa: PLC0414
from voc.analysis import ReviewSentiment
from voc.analysis import queries as q
from voc.analysis.store import write_analysis
from voc.datasets.manifest import frame_hash
from voc.embeddings.contracts import fingerprint


@pytest.fixture
def analysis(candidate):
    return write_analysis(**candidate)


def rehash(candidate):
    info = candidate["inputs"]
    info["reviews_sha256"] = frame_hash(candidate["reviews"])
    info["input_id"] = fingerprint({k: v for k, v in info.items() if k != "input_id"})


def test_overview_denominators_and_metadata(analysis):
    result = q.overview(analysis.analysis_id)
    assert result["schema_version"] == q.SCHEMA_VERSION
    assert result["state"] == "ok" and result["error"] is None
    data = result["data"]
    assert data["selected_reviews"] == 4
    assert data["classified"] == data["sentiment_denominator"] == 3
    assert data["excluded"] == 1 and data["failed"] == 0
    assert data["unassigned_reviews"] == 1 and data["topic_count"] == 2
    assert data["sentiment_proportions"] == {
        "negative": 2 / 3,
        "neutral": 0,
        "positive": 1 / 3,
    }
    assert q.metadata(analysis.analysis_id)["data"] == analysis.as_dict()
    assert q.inspect_analysis(analysis.analysis_id)["data"] == analysis.as_dict()
    assert q.list_candidates()["data"][0]["analysis_id"] == analysis.analysis_id


def test_filters_recompute_population_and_keep_pinned_coverage(analysis):
    result = q.overview(analysis.analysis_id, topic_id=0)
    assert result["analysis_coverage"]["selected_reviews"] == 4
    assert result["coverage"]["selected_reviews"] == 2
    assert result["data"]["sentiment_proportions"]["negative"] == 1
    excluded = q.overview(analysis.analysis_id, topic_id=-1)
    assert excluded["data"]["selected_reviews"] == 1
    assert excluded["data"]["sentiment_denominator"] == 0
    assert set(excluded["data"]["sentiment_proportions"].values()) == {None}
    assert (
        q.overview(analysis.analysis_id, platform="app_store", sentiment="negative")[
            "data"
        ]["selected_reviews"]
        == 0
    )
    assert (
        q.reviews(analysis.analysis_id, status="excluded")["data"][0]["sentiment"]
        is None
    )


def test_topic_with_excluded_member_uses_classified_denominator(candidate):
    candidate["assignments"][-1] = replace(candidate["assignments"][-1], topic_id=0)
    candidate["topics"] = [t for t in candidate["topics"] if t.topic_id != -1]
    analysis = write_analysis(**candidate)
    row = q.topics(analysis.analysis_id, topic_id=0)["data"][0]
    assert row["selected_reviews"] == 3 and row["classified"] == 2
    assert row["excluded"] == 1 and row["sentiment_proportions"]["negative"] == 1
    assert row["keywords"] == [{"term": "acceso", "weight": 0.2}]
    assert row["evidence_ids"] == ["review-000"]


def test_outlier_classification_and_failure_are_counted(candidate):
    candidate["sentiments"][-1] = replace(
        candidate["sentiments"][2], record_id="review-003"
    )
    candidate["sentiments"][0] = ReviewSentiment(
        "review-000", "failed", reason="inference_error"
    )
    analysis = write_analysis(**candidate)
    data = q.overview(analysis.analysis_id)["data"]
    assert data["classified"] == 3 and data["positive"] == 2 and data["failed"] == 1
    assert q.overview(analysis.analysis_id, topic_id=-1)["data"]["positive"] == 1


@pytest.mark.parametrize(
    "kind", ["overview", "topics", "reviews", "monthly", "platforms"]
)
def test_empty_filters_have_zero_coverage_not_nan(analysis, kind):
    result = q.query(analysis.analysis_id, kind, filters={"platform": "missing"})
    assert result["coverage"]["selected_reviews"] == 0
    assert result["coverage"]["sentiment_denominator"] == 0
    assert set(result["coverage"]["sentiment_proportions"].values()) == {None}
    if kind != "overview":
        assert result["data"] == []


def test_pagination_is_stable_and_preserves_total(analysis):
    pages = [q.reviews(analysis.analysis_id, limit=1, offset=i) for i in range(4)]
    assert [r["data"][0]["record_id"] for r in pages] == [
        f"review-{i:03}" for i in range(4)
    ]
    assert all(r["total"] == 4 and r["coverage"]["classified"] == 3 for r in pages)
    assert q.reviews(analysis.analysis_id, offset=4)["data"] == []
    assert (
        q.reviews(analysis.analysis_id, topic_id=-1)["data"][0]["model_label"] is None
    )


def test_time_platform_groups_reconcile_and_use_utc_dates(candidate):
    candidate["reviews"]["record_date"] = [
        None,
        "invalid",
        "2026-01-31T23:30:00-02:00",
        "2026-02-01T12:00:00Z",
    ]
    candidate["reviews"].loc[0, "platform"] = None
    rehash(candidate)
    analysis = write_analysis(**candidate)
    monthly = q.monthly(analysis.analysis_id)["data"]
    assert [(r["bucket"], r["selected_reviews"]) for r in monthly] == [
        ("2026-02", 2),
        (None, 2),
    ]
    assert (
        sum(r["selected_reviews"] for r in q.platforms(analysis.analysis_id)["data"])
        == 4
    )
    feb = q.overview(analysis.analysis_id, start="2026-02-01", end="2026-02-01")["data"]
    assert feb["selected_reviews"] == 2 and feb["classified"] == 1
    assert (
        q.overview(analysis.analysis_id, end="2026-01-31")["data"]["selected_reviews"]
        == 0
    )


def test_topic_and_group_counts_equal_overview(analysis):
    for kind in ("topics", "monthly", "platforms"):
        result = q.query(analysis.analysis_id, kind)
        for key in (
            "selected_reviews",
            "classified",
            "excluded",
            "failed",
            "negative",
            "positive",
        ):
            assert sum(r[key] for r in result["data"]) == result["coverage"][key]


@pytest.mark.parametrize(
    "options",
    [
        {"kind": "DROP TABLE reviews"},
        {"filters": {"sort": "record_id"}},
        {"filters": []},
        {"filters": {"topic_id": True}},
        {"filters": {"topic_id": -2}},
        {"filters": {"sentiment": "unknown"}},
        {"filters": {"status": "pending"}},
        {"filters": {"platform": ""}},
        {"filters": {"platform": 1}},
        {"filters": {"start": "20261010"}},
        {"filters": {"end": "bad"}},
        {"filters": {"start": 2026}},
        {"filters": {"start": "2026-03-01", "end": "2026-02-01"}},
        {"limit": True},
        {"limit": 0},
        {"limit": 1001},
        {"limit": 1.5},
        {"offset": -1},
        {"offset": True},
        {"offset": 2**64},
        {"kind": "metadata", "filters": {"topic_id": 0}},
    ],
)
def test_invalid_requests_fail_before_storage(options):
    with pytest.raises(ValueError):
        q.query(str(uuid4()), **options)


def test_sql_like_values_are_literals(analysis):
    result = q.overview(analysis.analysis_id, platform="google_play' OR 1=1 --")
    assert result["data"]["selected_reviews"] == 0
    assert q.overview(analysis.analysis_id)["data"]["selected_reviews"] == 4


def test_absent_and_unsafe_ids():
    with pytest.raises(LookupError):
        q.overview(str(uuid4()))
    with pytest.raises(ValueError):
        q.overview("../escape")
