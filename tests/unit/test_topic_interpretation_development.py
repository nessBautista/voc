"""Development expansion preserves frozen requests, budgets and closed reserve."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Imported fixtures are discovered by pytest in this module.
from tests.unit.test_topic_interpretation_production import (
    draft as draft,  # noqa: PLC0414
)
from tests.unit.test_topic_interpretation_production import (
    policy as policy,  # noqa: PLC0414
)
from tests.unit.test_topic_interpretation_production import (
    store as store,  # noqa: PLC0414
)
from tests.unit.test_topic_interpretation_production import (
    valid_response,
)
from voc_ml.topics.interpretation.batch_process import run_process
from voc_ml.topics.interpretation.development import (
    CONDITIONS,
    development_plan,
    enable_development,
    run_development,
)
from voc_ml.topics.interpretation.production import execute_request


def complete(key, *, store, seconds=120, log_path=None):
    result = execute_request(
        key,
        store=store,
        transport=lambda b: valid_response(),
        catalog_reader=lambda p: {"fixture": True},
        tracking={"zenml_run_id": "fixture-z", "mlflow_run_id": "fixture-m"},
    )
    with store.lock():
        state = store.load()
        state["attempts"][key]["tracking_verified"] = True
        store.save(state)
    return result


def initial(store):
    complete("59-R2-P0", store=store)
    complete("1440-R2-P0", store=store)


def test_enable_requires_both_verified_initial_cases(store):
    with pytest.raises(ValueError, match="both initial"):
        enable_development(store)
    complete("59-R2-P0", store=store)
    with pytest.raises(ValueError, match="both initial"):
        enable_development(store)
    complete("1440-R2-P0", store=store)
    before = store.load()
    result = enable_development(store)
    after = store.load()
    assert result["pending"] == 46 and result["completed"] == 2
    assert before["protocol"] == after["protocol"]
    assert before["attempts"] == after["attempts"]
    assert result["reserve_enabled"] is False
    assert len(result["conditions"]["R2-P0"]["pending"]) == 10
    assert result["remaining_allowance_usd"] == "0.5441984"
    assert enable_development(store) == result
    assert store.load()["development_gate"] == after["development_gate"]


def test_unverified_tracking_blocks_gate(store):
    execute_request(
        "59-R2-P0",
        store=store,
        transport=lambda b: valid_response(),
        catalog_reader=lambda p: {},
    )
    with pytest.raises(ValueError, match="unverified"):
        enable_development(store)


def test_four_conditions_complete_exactly_48_requests_without_replaying_initial(store):
    initial(store)
    enable_development(store)
    calls = []

    def runner(key, **kwargs):
        calls.append(key)
        assert 0 < kwargs["seconds"] <= 120
        complete(key, **kwargs)

    for condition in CONDITIONS:
        result = run_development(condition, store=store, runner=runner)
        assert result["state"] == "completed"
    assert len(calls) == 46 and len(set(calls)) == 46
    assert "59-R2-P0" not in calls and "1440-R2-P0" not in calls
    assert development_plan(store)["pending"] == 0
    assert development_plan(store)["completed"] == 48
    assert store.load()["protocol"]["reserve_enabled"] is False
    assert (
        run_development(
            "R2-P2", store=store, runner=lambda *a, **kw: pytest.fail("must reuse")
        )["state"]
        == "already_completed"
    )


def test_failure_stops_before_next_send(store):
    initial(store)
    enable_development(store)
    calls = []

    def runner(key, **kwargs):
        calls.append(key)
        execute_request(
            key,
            store=kwargs["store"],
            transport=lambda b: {"transport_error": "uncertain"},
            catalog_reader=lambda p: {},
        )

    result = run_development("R0-P0", store=store, runner=runner)
    assert result["state"] == "stopped" and len(calls) == 1
    assert result["stopped_at_key"] == calls[0]
    with pytest.raises(ValueError, match="uncertain"):
        run_development("R0-P0", store=store, runner=runner)
    assert len(calls) == 1


def test_deadline_stops_and_resume_skips_completed(store):
    initial(store)
    enable_development(store)
    time = [0]

    def runner(key, **kwargs):
        complete(key, **kwargs)
        time[0] = 901

    first = run_development("R0-P0", store=store, runner=runner, clock=lambda: time[0])
    assert first["state"] == "stopped" and first["reason"] == "batch_deadline"
    assert len(first["completed_now"]) == 1
    second = run_development("R0-P0", store=store, runner=complete)
    assert second["state"] == "completed" and len(second["completed_now"]) == 11
    assert first["completed_now"][0] in second["reused"]


def test_abnormal_workflow_exit_blocks_batch(store):
    initial(store)
    enable_development(store)

    def runner(*a, **kw):
        raise RuntimeError("PRIVATE_PAYLOAD_MUST_NOT_BE_LOGGED")

    result = run_development("R0-P0", store=store, runner=runner)
    assert result["state"] == "stopped"
    assert len(store.load()["attempts"]) == 2
    assert "PRIVATE_PAYLOAD" not in json.dumps(result)


def test_batch_requires_gate_and_known_condition(store):
    with pytest.raises(ValueError, match="Enable development"):
        run_development("R0-P0", store=store, runner=complete)
    with pytest.raises(ValueError, match="Unknown"):
        run_development("R1-P2", store=store, runner=complete)


def test_expansion_rejects_insufficient_budget(store):
    initial(store)
    # Do not invalidate receipt lineage: test insufficient balance through the inspected summary port.
    from voc_ml.topics.interpretation import development

    original = development.inspect_study
    from unittest.mock import patch

    with (
        patch.object(
            development,
            "inspect_study",
            lambda s: {**original(s), "remaining_usd": "0.01"},
        ),
        pytest.raises(ValueError, match="cost budget"),
    ):
        enable_development(store)
    assert not store.load()["development_enabled"]


def test_two_batch_processes_cannot_own_scheduler(store):
    code = """
import sys
from voc_ml.topics.interpretation.ledger import InterpretationStore
try:
 with InterpretationStore(sys.argv[1]).lock(name='.batch.lock'):
  raise AssertionError('concurrent scheduler')
except ValueError:
 pass
"""
    with store.lock(name=".batch.lock"):
        result = subprocess.run(
            [sys.executable, "-c", code, str(store.root)],
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
        )
    assert result.returncode == 0, result.stderr


def test_process_deadline_stops_owned_workflow(tmp_path):
    import time

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            seconds=0.1,
            log_path=tmp_path / "child.log",
        )
    assert time.monotonic() - started < 5


def test_old_experiment_cli_cannot_enable_or_execute(store, monkeypatch):
    from click.testing import CliRunner

    from voc_ml.topics.interpretation import cli
    initial(store)
    before = (store.root / "ledger.json").read_bytes()
    for command in ["development-plan", "enable-development", "run-development", "reserve"]:
        result = CliRunner().invoke(cli.interpretation, [command])
        assert result.exit_code != 0 and "No such command" in result.output
    assert (store.root / "ledger.json").read_bytes() == before


def test_successful_process_without_receipt_stops_batch(store):
    initial(store)
    enable_development(store)
    result = run_development("R0-P0", store=store, runner=lambda *a, **kw: None)
    assert result["state"] == "stopped"
    assert result["completed_now"] == []
    assert len(store.load()["attempts"]) == 2


def length_failure(store, *, response=None, verified=True):
    response = response if response is not None else valid_response()
    if response.get("choices"):
        response["choices"][0]["finish_reason"] = "length"
    result = execute_request(
        "0-R0-P0",
        store=store,
        transport=lambda b: response,
        catalog_reader=lambda p: {},
        tracking={"zenml_run_id": "fixture-z", "mlflow_run_id": "fixture-m"},
    )
    with store.lock():
        state = store.load()
        state["attempts"]["0-R0-P0"]["tracking_verified"] = verified
        store.save(state)
    return result


def test_review_retains_failure_cost_and_receipt_then_runs_only_pending(store):
    from voc_ml.topics.interpretation.production import (
        inspect_study,
        review_length_failure,
    )

    initial(store)
    enable_development(store)
    failed = length_failure(store)
    before = store.load()
    files = {p: p.read_bytes() for p in (store.root / failed["release"]).iterdir()}
    with pytest.raises(ValueError, match="invalid"):
        development_plan(store)
    review = review_length_failure("0-R0-P0", store=store)
    assert review["attempt_state"] == "invalid" and review["model_requests"] == 0
    assert review_length_failure("0-R0-P0", store=store) == review
    assert store.load()["attempts"] == before["attempts"]
    assert store.load()["protocol"] == before["protocol"]
    assert all(p.read_bytes() == raw for p, raw in files.items())
    plan = development_plan(store)
    assert plan["completed"] == 2 and plan["attempted"] == 3 and plan["pending"] == 45
    assert plan["conditions"]["R0-P0"]["reviewed_failures"] == ["0-R0-P0"]
    calls = []

    def runner(key, **kwargs):
        calls.append(key)
        complete(key, **kwargs)

    batch = run_development("R0-P0", store=store, runner=runner)
    assert batch["state"] == "completed_with_failures"
    assert len(calls) == 11 and "0-R0-P0" not in calls
    assert batch["reviewed_failures"] == ["0-R0-P0"]
    summary = inspect_study(store)
    assert summary["attempts"]["0-R0-P0"]["charged_usd"] == failed["charged_usd"]
    assert len(summary["attempts"]) == 14
    with pytest.raises(ValueError, match="no replay"):
        complete("0-R0-P0", store=store)
    repeat = run_development("R0-P0", store=store, runner=runner)
    assert repeat["state"] == "already_completed_with_failures"
    assert repeat["completed"] == 11
    assert len(calls) == 11


@pytest.mark.parametrize(
    "kind", ["uncertain", "over_budget", "unverified", "accepted", "other_invalid"]
)
def test_failure_review_cannot_bypass_other_blockers(store, kind):
    from voc_ml.topics.interpretation.production import review_length_failure

    initial(store)
    enable_development(store)
    response = valid_response()
    if kind == "uncertain":
        response.pop("usage")
    elif kind == "over_budget":
        response["usage"]["cost"] = 10
    elif kind == "other_invalid":
        response["provider"] = "unapproved"
    if kind == "accepted":
        complete("0-R0-P0", store=store)
    else:
        length_failure(store, response=response, verified=kind != "unverified")
    before = (store.root / "ledger.json").read_bytes()
    with pytest.raises(ValueError, match="Only a verified length"):
        review_length_failure("0-R0-P0", store=store)
    assert (store.root / "ledger.json").read_bytes() == before


@pytest.mark.parametrize(
    "field", ["manifest_sha256", "protocol_sha256", "charged_usd", "attempt_id"]
)
def test_review_binding_is_checked_before_new_send(store, field):
    from voc_ml.topics.interpretation.production import (
        inspect_study,
        review_length_failure,
    )

    initial(store)
    enable_development(store)
    length_failure(store)
    review_length_failure("0-R0-P0", store=store)
    state = store.load()
    state["failure_reviews"]["0-R0-P0"][field] = "altered"
    store.save(state)
    with pytest.raises(ValueError, match="Failure review differs"):
        inspect_study(store)
    with pytest.raises(ValueError, match="Failure review differs"):
        complete("1-R0-P0", store=store)


def test_reviewed_failure_does_not_hide_later_failure(store):
    from voc_ml.topics.interpretation.production import review_length_failure

    initial(store)
    enable_development(store)
    length_failure(store)
    review_length_failure("0-R0-P0", store=store)

    def fail(key, **kwargs):
        execute_request(
            key,
            store=store,
            transport=lambda b: {"transport_error": "uncertain"},
            catalog_reader=lambda p: {},
        )

    next_key = development_plan(store)["conditions"]["R0-P0"]["pending"][0]
    result = run_development("R0-P0", store=store, runner=fail)
    assert result["state"] == "stopped" and result["stopped_at_key"] == next_key
    with pytest.raises(ValueError, match="uncertain"):
        development_plan(store)


def test_fixed_cli_rejects_old_experiment_request_keys(store, monkeypatch):
    from click.testing import CliRunner

    from voc_ml.topics.interpretation import cli

    initial(store)
    enable_development(store)
    length_failure(store)
    monkeypatch.setattr(cli, "_store", lambda: store)
    with store.lock(name=".batch.lock"):
        result = CliRunner().invoke(cli.interpretation, ["review-failure", "0-R0-P0"])
        assert result.exit_code != 0
    result = CliRunner().invoke(cli.interpretation, ["review-failure", "0-R0-P0"])
    assert result.exit_code != 0 and "not a valid integer" in result.output
    assert len(store.load()["attempts"]) == 3
