"""Holdout opening preserves development, budgets, evidence and no-replay rules."""

from copy import deepcopy
from decimal import Decimal

import pytest

from tests.unit.test_topic_interpretation_development import complete, initial
from tests.unit.test_topic_interpretation_production import draft as base_draft
from tests.unit.test_topic_interpretation_production import (
    policy as policy,  # noqa: PLC0414
)
from voc.embeddings.shared.contracts import fingerprint
from voc_ml.topics.interpretation.development import (
    CONDITIONS,
    enable_development,
    run_development,
)
from voc_ml.topics.interpretation.execution_plan import freeze_protocol
from voc_ml.topics.interpretation.ledger import InterpretationStore
from voc_ml.topics.interpretation.production import execute_request, inspect_study
from voc_ml.topics.interpretation.reserve import (
    create_reserve,
    reserve_plan,
    run_reserve,
)


@pytest.fixture
def prepared(tmp_path, policy):
    draft = base_draft.__wrapped__()
    cases = deepcopy(draft["plan"]["cases"]["development"])
    for i, case in enumerate(cases):
        case["topic_id"] = 10000 + i
        case["evidence"][0]["record_id"] = f"reserved-{i}"
        case["evidence_sha256"] = fingerprint(case["evidence"])
    draft["plan"]["cases"]["reserve"] = cases
    draft["plan"]["cases_sha256"]["reserve"] = fingerprint(cases)
    draft["plan_sha256"] = fingerprint(draft["plan"])
    parent = InterpretationStore(tmp_path / "execution")
    parent.initialize(freeze_protocol(draft, policy), {})
    initial(parent)
    enable_development(parent)
    for condition in CONDITIONS:
        run_development(condition, store=parent, runner=complete)
    store = InterpretationStore(tmp_path / "reserve-execution")
    decision = {
        "prompt": "P0",
        "development_protocol_sha256": parent.load()["protocol_sha256"],
        "selected_by": "owner",
        "selected_at": "2026-11-04",
        "rationale": "prototype continuity",
    }
    return parent, store, draft, decision


def opened(prepared):
    parent, store, draft, decision = prepared
    create_reserve(parent=parent, store=store, draft=draft, decision=decision)
    return parent, store


def test_open_preserves_parent_and_exact_p0_and_caps_allocation(prepared):
    parent, store, draft, decision = prepared
    before = (parent.root / "ledger.json").read_bytes()
    plan = create_reserve(parent=parent, store=store, draft=draft, decision=decision)
    assert (parent.root / "ledger.json").read_bytes() == before
    assert len(plan["pending"]) == 12 and plan["allocated_usd"] == "0.1419648"
    protocol = store.load()["protocol"]
    assert protocol["provider"]["max_attempts"] == 12
    assert Decimal(plan["allocated_usd"]) + Decimal(plan["combined_accounted_usd"]) <= 1
    assert all(
        r["prompt"] == "P0" and r["representation"] == "R2"
        for r in protocol["requests"].values()
    )
    assert all(
        r["body"]["messages"][0]["content"] == "P0"
        for r in protocol["requests"].values()
    )
    assert inspect_study(store)["reserve_enabled"] is True
    with pytest.raises(ValueError, match="already exists"):
        create_reserve(parent=parent, store=store, draft=draft, decision=decision)


@pytest.mark.parametrize(
    "field", ["prompt", "development_protocol_sha256", "selected_by"]
)
def test_invalid_selection_never_creates_store(prepared, field):
    parent, store, draft, decision = prepared
    decision[field] = "P2" if field == "prompt" else ""
    with pytest.raises(ValueError, match="selection"):
        create_reserve(parent=parent, store=store, draft=draft, decision=decision)
    assert not (store.root / "ledger.json").exists()


def test_changed_draft_and_alternative_store_rejected(prepared, tmp_path):
    parent, store, draft, decision = prepared
    with pytest.raises(ValueError, match="single reserve"):
        create_reserve(
            parent=parent,
            store=InterpretationStore(tmp_path / "other"),
            draft=draft,
            decision=decision,
        )
    draft["plan"]["cases"]["reserve"][0]["evidence"][0]["text"] = "modified"
    draft["plan_sha256"] = fingerprint(draft["plan"])
    with pytest.raises(ValueError, match="differs"):
        create_reserve(parent=parent, store=store, draft=draft, decision=decision)


def test_incomplete_development_blocks_open(prepared):
    parent, store, draft, decision = prepared
    state = parent.load()
    state["attempts"].pop("0-R0-P0")
    parent.save(state)
    with pytest.raises(ValueError, match="48 development"):
        create_reserve(parent=parent, store=store, draft=draft, decision=decision)


def test_twelve_workflows_once_and_parent_unchanged(prepared):
    parent, store = opened(prepared)
    before = (parent.root / "ledger.json").read_bytes()
    calls = []

    def runner(key, **kwargs):
        calls.append(key)
        assert 0 < kwargs["seconds"] <= 120
        complete(key, **kwargs)

    result = run_reserve(parent=parent, store=store, runner=runner)
    assert result["state"] == "completed" and len(calls) == len(set(calls)) == 12
    assert (
        run_reserve(parent=parent, store=store, runner=runner)["state"]
        == "already_completed"
    )
    assert len(calls) == 12 and (parent.root / "ledger.json").read_bytes() == before
    assert reserve_plan(parent=parent, store=store)["pending"] == []
    with pytest.raises(ValueError, match="outside frozen"):
        execute_request(
            "59-R2-P0",
            store=store,
            transport=lambda b: pytest.fail(),
            catalog_reader=lambda p: {},
        )


def test_uncertain_send_stops_without_replay(prepared):
    parent, store = opened(prepared)
    calls = []

    def runner(key, **kwargs):
        calls.append(key)
        execute_request(
            key,
            store=store,
            transport=lambda b: {"transport_error": "uncertain"},
            catalog_reader=lambda p: {},
        )

    result = run_reserve(parent=parent, store=store, runner=runner)
    assert result["state"] == "stopped" and len(calls) == 1
    with pytest.raises(ValueError, match="uncertain"):
        run_reserve(parent=parent, store=store, runner=runner)
    assert len(calls) == 1


def test_deadline_resume_does_not_repeat(prepared):
    parent, store = opened(prepared)
    time = [0]

    def runner(key, **kwargs):
        complete(key, **kwargs)
        time[0] = 901

    result = run_reserve(
        parent=parent, store=store, runner=runner, clock=lambda: time[0]
    )
    assert result["reason"] == "batch_deadline" and len(result["completed_now"]) == 1
    result = run_reserve(parent=parent, store=store, runner=complete)
    assert result["state"] == "completed" and len(result["completed_now"]) == 11


def test_parent_change_blocks_execution(prepared):
    parent, store = opened(prepared)
    state = parent.load()
    state["attempts"]["0-R0-P0"]["started_at"] = "changed"
    parent.save(state)
    with pytest.raises(ValueError, match="parent identity"):
        run_reserve(parent=parent, store=store, runner=lambda *a, **kw: pytest.fail())


def test_reserve_reader_and_assisted_acceptance_require_evidence(prepared):
    from voc_ml.topics.interpretation.comparison import (
        CRITERIA,
        apply_review_updates,
        evaluate_reserve,
        read_reserve_results,
        review_template,
    )

    parent, store = opened(prepared)
    sha = store.load()["protocol_sha256"]
    with pytest.raises(ValueError, match="pending"):
        read_reserve_results(parent=parent, store=store, protocol_sha256=sha)
    run_reserve(parent=parent, store=store, runner=complete)
    result = read_reserve_results(parent=parent, store=store, protocol_sha256=sha)
    assert len(result["rows"]) == 12 and result["pair_coverage"] == []
    assert result["reserve_opened"] and result["model_requests"] == 0
    review = review_template(result)
    assert len(review["entries"]) == 12
    assert evaluate_reserve(review, result)["state"] == "review_pending"
    updates = [
        {
            "topic_id": e["topic_id"],
            "label": e["label"],
            "scores": dict.fromkeys(CRITERIA, 2),
            "critical_error": False,
            "evidence_labels": ["E1"],
            "note": "Synthetic test judgment",
            "reviewer": "test",
            "review_kind": "assisted",
            "reviewed_at": "2026-11-04",
        }
        for e in review["entries"]
    ]
    review = apply_review_updates(review, updates, result)
    evaluated = evaluate_reserve(review, result)
    assert (
        evaluated["state"] == "criteria_met" and evaluated["human_validation_pending"]
    )
    assert evaluated["automatic_promotion"] is False
    review["entries"][0]["critical_error"] = True
    assert evaluate_reserve(review, result)["state"] == "criteria_not_met"
    review["entries"][0]["review_kind"] = "human"
    with pytest.raises(ValueError, match="separate"):
        evaluate_reserve(review, result)


def test_reviewed_length_failure_is_retained_and_batch_resumes(prepared):
    from tests.unit.test_topic_interpretation_production import valid_response
    from voc_ml.topics.interpretation.comparison import (
        read_reserve_results,
        review_template,
    )
    from voc_ml.topics.interpretation.production import review_length_failure

    parent, store = opened(prepared)
    key = next(iter(store.load()["protocol"]["requests"]))
    response = valid_response()
    response["choices"][0]["finish_reason"] = "length"
    execute_request(
        key,
        store=store,
        transport=lambda b: response,
        catalog_reader=lambda p: {},
        tracking={"zenml_run_id": "fixture-z", "mlflow_run_id": "fixture-m"},
    )
    state = store.load()
    state["attempts"][key]["tracking_verified"] = True
    store.save(state)
    result = review_length_failure(key, store=store)
    assert result["reserve_enabled"] and result["replay_enabled"] is False
    batch = run_reserve(parent=parent, store=store, runner=complete)
    assert (
        batch["state"] == "completed_with_failures"
        and len(batch["completed_now"]) == 11
    )
    results = read_reserve_results(
        parent=parent, store=store, protocol_sha256=store.load()["protocol_sha256"]
    )
    assert (
        sum(e["status"] == "unscorable" for e in review_template(results)["entries"])
        == 1
    )
