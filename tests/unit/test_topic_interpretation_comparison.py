"""Comparison preserves failures, paired denominators and explicit review authorship."""

from copy import deepcopy

import pytest

from tests.unit.test_topic_interpretation_development import (
    complete,
    initial,
    length_failure,
)
from tests.unit.test_topic_interpretation_production import (
    draft as draft,  # noqa: PLC0414
)
from tests.unit.test_topic_interpretation_production import (
    policy as policy,  # noqa: PLC0414
)
from tests.unit.test_topic_interpretation_production import (
    store as store,  # noqa: PLC0414
)
from voc_ml.topics.interpretation.comparison import (
    CRITERIA,
    apply_review_updates,
    read_development_comparison,
    review_template,
    validate_review,
)
from voc_ml.topics.interpretation.development import (
    CONDITIONS,
    enable_development,
    run_development,
)
from voc_ml.topics.interpretation.production import review_length_failure


@pytest.fixture
def comparison(store):
    initial(store)
    enable_development(store)
    length_failure(store)
    review_length_failure("0-R0-P0", store=store)
    for condition in CONDITIONS:
        run_development(condition, store=store, runner=complete)
    return read_development_comparison(
        store=store, protocol_sha256=store.load()["protocol_sha256"]
    )


def test_comparison_retains_failures_and_denominators(comparison, store):
    assert len(comparison["rows"]) == 48
    assert [r["accepted"] for r in comparison["summaries"]] == [11, 12, 12, 12]
    assert [p["available_pairs"] for p in comparison["pair_coverage"]] == [11, 12, 12]
    assert comparison["pair_coverage"][0]["missing_topic_ids"] == [0]
    failed = next(r for r in comparison["rows"] if r["request_key"] == "0-R0-P0")
    assert failed["description"] is None and failed["failure_reviewed"]
    assert comparison["model_requests"] == 0 and comparison["reserve_opened"] is False
    before = (store.root / "ledger.json").read_bytes()
    again = read_development_comparison(
        store=store, protocol_sha256=store.load()["protocol_sha256"]
    )
    assert again == comparison
    assert (store.root / "ledger.json").read_bytes() == before


def test_pending_and_wrong_protocol_are_rejected(store):
    with pytest.raises(ValueError, match="Unexpected"):
        read_development_comparison(store=store, protocol_sha256="a" * 64)
    with pytest.raises(ValueError, match="pending"):
        read_development_comparison(
            store=store, protocol_sha256=store.load()["protocol_sha256"]
        )


def test_review_never_fabricates_scores_or_silently_drops_failures(comparison):
    review = review_template(comparison)
    assert review == review_template(comparison)
    assert sum(e["status"] == "unscorable" for e in review["entries"]) == 1
    assert all(all(v is None for v in e["scores"].values()) for e in review["entries"])
    assert apply_review_updates(review, [], comparison) == review
    bad = deepcopy(review)
    bad["source_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="source"):
        validate_review(bad, comparison)
    bad = deepcopy(review)
    next(e for e in bad["entries"] if e["status"] == "unscorable")["scores"][
        "usefulness"
    ] = 0
    with pytest.raises(ValueError, match="unscorable"):
        validate_review(bad, comparison)


def test_review_preserves_labels_and_requires_author_evidence_and_complete_scores(
    comparison,
):
    review = review_template(comparison)
    entry = next(e for e in review["entries"] if e["status"] == "pending")
    update = {
        "topic_id": entry["topic_id"],
        "label": entry["label"],
        "scores": dict.fromkeys(CRITERIA, 1),
        "critical_error": False,
        "evidence_labels": ["E1"],
        "note": "Fixture evaluation, not an actual assessment.",
        "reviewer": "test",
        "review_kind": "assisted",
        "reviewed_at": "2026-11-04T00:00:00+00:00",
    }
    result = apply_review_updates(review, [update], comparison)
    assert sum(e["status"] == "scored" for e in result["entries"]) == 1
    assert all(e["status"] != "scored" for e in review["entries"])
    for field, value in (
        ("reviewer", ""),
        ("review_kind", None),
        ("evidence_labels", ["E99"]),
        ("critical_error", None),
    ):
        with pytest.raises(ValueError):
            apply_review_updates(review, [{**update, field: value}], comparison)
    with pytest.raises(ValueError, match="integers"):
        apply_review_updates(
            review, [{**update, "scores": dict.fromkeys(CRITERIA, True)}], comparison
        )
    bad = deepcopy(review)
    bad["entries"][0]["request_key"] = bad["entries"][1]["request_key"]
    with pytest.raises(ValueError, match="mapping"):
        validate_review(bad, comparison)


def test_corrupt_receipt_blocks_comparison(comparison, store):
    key = next(iter(store.load()["attempts"]))
    entry = store.load()["attempts"][key]
    (store.root / entry["release"] / "result.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        read_development_comparison(
            store=store, protocol_sha256=store.load()["protocol_sha256"]
        )
