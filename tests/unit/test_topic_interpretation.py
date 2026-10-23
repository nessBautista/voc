"""No paid calls: exercise stored interpretations with a bounded fake provider."""

import json
import shutil
from dataclasses import replace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from tests.unit.test_analysis_store import candidate as candidate  # noqa: PLC0414
from voc.analysis.queries import query
from voc.analysis.store import read_analysis, write_analysis
from voc.datasets.manifest import file_hash
from voc_ml.analysis import interpretation as workflow
from voc_ml.analysis.representation import preserved_content
from voc_ml.cli import main
from voc_ml.llm import openrouter
from voc_ml.llm import topic_interpretation as prompts


def reply(body, assessment="single_feature"):
    evidence = json.loads(body["messages"][1]["content"])["evidence"]
    result = {
        "name": "Acceso a la aplicación",
        "topic_description": "Problemas de acceso en los ejemplos.",
        "proposed_feature": "Acceso" if assessment == "single_feature" else None,
        "assessment": assessment,
        "supporting_review_ids": [evidence[0]["record_id"]],
    }
    return {
        "id": "provider-request",
        "model": body["model"],
        "usage": {"prompt_tokens": 70, "completion_tokens": 30, "total_tokens": 100},
        "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(result)}}
        ],
    }


@pytest.fixture
def setup(candidate, tmp_path, monkeypatch):
    candidate["topics"] = [
        replace(t, name="Original title") for t in candidate["topics"]
    ]
    parent = read_analysis(write_analysis(**candidate).analysis_id)
    config = tmp_path / "interpretation.toml"
    config.write_text('model = "google/gemini-2.5-flash"\n')
    transport = Mock()
    create = Mock(return_value=transport)
    request = Mock(side_effect=lambda client, body: reply(body))
    tracker = Mock()
    monkeypatch.setattr(openrouter, "create_client", create)
    monkeypatch.setattr(openrouter, "request", request)
    monkeypatch.setattr(
        workflow, "_start_tracking", lambda *args: (tracker, "tracking-id")
    )
    monkeypatch.setattr(workflow, "_finish_tracking", Mock())
    return parent, config, create, request


def test_plan_is_offline_and_rejects_bad_ids_before_credentials(setup, tmp_path):
    parent, config, create, request = setup
    plan = workflow.interpret(parent.manifest.analysis_id, [0, 1], config)
    assert plan["state"] == "dry_run" and plan["maximum_requests"] == 2
    assert plan["sdk_retries"] == 0
    assert not (tmp_path / "interpretation-runs").exists()
    for ids in ([], [-1], [0, 0], [500], [0, 1, 2]):
        with pytest.raises(ValueError):
            workflow.interpret(parent.manifest.analysis_id, ids, config, live=True)
    create.assert_not_called()
    request.assert_not_called()


def test_saved_results_are_reused_and_originals_preserved(setup, tmp_path):
    parent, config, create, request = setup
    original_hash = file_hash(
        tmp_path / "analyses" / parent.manifest.analysis_id / "manifest.json"
    )
    first = workflow.interpret(parent.manifest.analysis_id, [0, 1], config, live=True)
    assert first["state"] == "completed", first
    child = read_analysis(first["analysis_id"])
    assert child.manifest.schema_version == "voc-analysis-v2"
    assert parent.manifest.schema_version == "voc-analysis-v1"
    assert child.topics == parent.topics
    assert preserved_content(child) == preserved_content(parent)
    assert len(child.interpretations) == 2 and request.call_count == 2
    create.reset_mock()
    create.side_effect = AssertionError("Reuse must not read credentials")
    second = workflow.interpret(parent.manifest.analysis_id, [0, 1], config, live=True)
    assert second["state"] == "completed", second
    assert (
        second["counts"]["reused_topics"] == 2
        and second["counts"]["provider_attempts"] == 0
    )
    assert request.call_count == 2
    assert all(o["new_usage"]["total_tokens"] == 0 for o in second["outcomes"])
    copied = read_analysis(second["analysis_id"])
    assert all(
        r["provider"]["usage"]["total_tokens"] == 100 for r in copied.interpretations
    )
    assert all(
        r["reuse_origin"]["analysis_id"] == first["analysis_id"]
        for r in copied.interpretations
    )
    assert (
        file_hash(tmp_path / "analyses" / parent.manifest.analysis_id / "manifest.json")
        == original_hash
    )
    assert (
        query(parent.manifest.analysis_id, "topics")["data"][0]["interpretation_status"]
        == "not_run"
    )
    rows = query(second["analysis_id"], "topics")["data"]
    assert all(r["name"] == "Original title" for r in rows)
    assert (
        next(r for r in rows if r["topic_id"] == 0)["display_name"]
        == "Acceso a la aplicación"
    )


def test_refresh_and_changed_prompt_make_new_calls(setup, monkeypatch):
    parent, config, _create, request = setup
    workflow.interpret(parent.manifest.analysis_id, [0], config, live=True)
    plan = workflow.interpret(parent.manifest.analysis_id, [0], config, refresh=True)
    assert plan["maximum_requests"] == 1 and request.call_count == 1
    refreshed = workflow.interpret(
        parent.manifest.analysis_id, [0], config, refresh=True, live=True
    )
    assert refreshed["counts"]["provider_attempts"] == 1 and request.call_count == 2
    match = workflow.interpret(parent.manifest.analysis_id, [0], config)
    assert match["requests"][0]["reused_from"] == refreshed["analysis_id"]
    monkeypatch.setattr(
        prompts, "SYSTEM_PROMPT", prompts.SYSTEM_PROMPT + " Nuevo texto."
    )
    assert (
        workflow.interpret(parent.manifest.analysis_id, [0], config)["maximum_requests"]
        == 1
    )


def test_versioned_retry_preserves_unselected_success(setup, monkeypatch):
    parent, config, _create, request = setup
    current_version = prompts.PROMPT_VERSION
    monkeypatch.setattr(prompts, "PROMPT_VERSION", "voc-topic-es-v1")
    monkeypatch.setattr(workflow, "PROMPT_VERSION", "voc-topic-es-v1")
    request.side_effect = lambda client, body: (
        {"error": "provider_error"}
        if json.loads(body["messages"][1]["content"])["topic_id"] == 0
        else reply(body)
    )
    first = workflow.interpret(parent.manifest.analysis_id, [0, 1], config, live=True)
    assert first["state"] == "partial"
    before = read_analysis(first["analysis_id"])
    existing = next(r for r in before.interpretations if r["topic_id"] == 1)

    monkeypatch.setattr(prompts, "PROMPT_VERSION", current_version)
    monkeypatch.setattr(workflow, "PROMPT_VERSION", current_version)
    request.reset_mock()
    request.side_effect = lambda client, body: reply(body)
    retried = workflow.interpret(first["analysis_id"], [0], config, live=True)
    assert retried["state"] == "completed"
    assert request.call_count == 1
    after = read_analysis(retried["analysis_id"])
    records = {r["topic_id"]: r for r in after.interpretations}
    assert records[1] == existing
    assert records[0]["prompt_version"] == current_version
    assert records[0]["status"] == "completed"
    assert preserved_content(after) == preserved_content(before)


@pytest.mark.parametrize(
    "failure",
    [
        "foreign_id",
        "malformed",
        "mixed_feature",
        "refusal",
        "length",
        "provider",
        "array",
        "long_feature",
        "missing_feature",
        "altered_id",
    ],
)
def test_invalid_responses_stay_failed_and_are_not_reused(setup, failure):
    parent, config, _create, request = setup

    def respond(client, body):
        raw = reply(body)
        value = json.loads(raw["choices"][0]["message"]["content"])
        if failure == "provider":
            return {"error": "provider_error"}
        if failure == "foreign_id":
            value["supporting_review_ids"] = [
                "review-002"
            ]  # Topic 1, not selected topic 0.
        if failure == "mixed_feature":
            value["assessment"] = "mixed"
        if failure == "long_feature":
            value["proposed_feature"] = "x" * 301
        if failure == "missing_feature":
            value["proposed_feature"] = None
        if failure == "altered_id":
            value["supporting_review_ids"][0] += "-altered"
        if failure == "array":
            value = [value]
        raw["choices"][0]["message"]["content"] = (
            "invalid JSON" if failure == "malformed" else json.dumps(value)
        )
        if failure == "refusal":
            raw["choices"][0]["message"]["refusal"] = "refused"
        if failure == "length":
            raw["choices"][0]["finish_reason"] = "length"
        return raw

    request.side_effect = respond
    state = workflow.interpret(parent.manifest.analysis_id, [0], config, live=True)
    assert state["state"] == "partial", state
    assert state["counts"]["failed_topics"] == 1
    record = read_analysis(state["analysis_id"]).interpretations[0]
    assert record["result"] is None and record["status"] == "failed"
    rows = query(state["analysis_id"], "topics")["data"]
    row = next(r for r in rows if r["topic_id"] == 0)
    assert (
        row["display_name"] == "Original title" and row["wording_source"] == "original"
    )
    assert (
        workflow.interpret(parent.manifest.analysis_id, [0], config)["maximum_requests"]
        == 1
    )


def test_partial_success_and_mixed_topic_are_saved(setup):
    parent, config, _create, request = setup
    request.side_effect = lambda client, body: (
        reply(body, "mixed")
        if json.loads(body["messages"][1]["content"])["topic_id"] == 0
        else {"error": "provider_error"}
    )
    state = workflow.interpret(parent.manifest.analysis_id, [0, 1], config, live=True)
    assert state["state"] == "partial", state
    child = read_analysis(state["analysis_id"])
    assert child.interpretations[0]["result"]["proposed_feature"] is None
    assert child.interpretations[0]["result"]["assessment"] == "mixed"
    assert child.interpretations[1]["status"] == "failed"
    plan = workflow.interpret(parent.manifest.analysis_id, [0, 1], config)
    assert plan["maximum_requests"] == plan["reusable_topics"] == 1


def test_portable_child_needs_no_source_candidate(setup, tmp_path, monkeypatch):
    parent, config, _create, _request = setup
    workflow.interpret(parent.manifest.analysis_id, [0], config, live=True)
    reused = workflow.interpret(parent.manifest.analysis_id, [0], config, live=True)
    destination = tmp_path / "fresh"
    shutil.copytree(
        tmp_path / "analyses" / reused["analysis_id"],
        destination / "analyses" / reused["analysis_id"],
    )
    monkeypatch.setenv("VOC_DATA_DIR", str(destination))
    child = read_analysis(reused["analysis_id"])
    assert child.interpretations[0]["reuse_origin"]
    assert query(child.manifest.analysis_id, "topics")["state"] == "ok"
    assert (
        workflow.interpret(child.manifest.analysis_id, [0], config)["maximum_requests"]
        == 0
    )


def test_corrupt_source_fails_before_new_requests(setup, tmp_path):
    parent, config, _create, request = setup
    state = workflow.interpret(parent.manifest.analysis_id, [0], config, live=True)
    child = read_analysis(state["analysis_id"])
    (
        tmp_path
        / "analyses"
        / state["analysis_id"]
        / child.interpretations[0]["response_artifact"]
    ).write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        workflow.interpret(parent.manifest.analysis_id, [0], config, live=True)
    assert request.call_count == 1


def test_tracking_failure_keeps_attempts_without_completed_candidate(
    setup, tmp_path, monkeypatch
):
    parent, config, _create, _request = setup
    monkeypatch.setattr(
        workflow, "_finish_tracking", Mock(side_effect=ValueError("private diagnostic"))
    )
    state = workflow.interpret(parent.manifest.analysis_id, [0], config, live=True)
    assert state["state"] == "failed"
    assert not (tmp_path / "analyses" / state["candidate_id"]).exists()
    assert list(
        (tmp_path / "interpretation-runs" / state["run_id"]).glob("*-response.json")
    )
    assert "private diagnostic" not in json.dumps(state)


def test_cli_default_is_dry_and_errors_are_json(setup):
    parent, config, _create, request = setup
    args = [
        "analysis",
        "interpret",
        parent.manifest.analysis_id,
        "--topics",
        "0,1",
        "--config",
        str(config),
        "--json",
    ]
    response = CliRunner().invoke(main, args)
    assert response.exit_code == 0, response.output
    assert json.loads(response.stdout)["state"] == "dry_run"
    response = CliRunner().invoke(main, args + ["--dry-run", "--live"])
    assert response.exit_code != 0
    assert json.loads(response.stdout)["state"] == "error"
    request.assert_not_called()


def test_transport_sanitizes_sdk_errors_and_disables_retries(monkeypatch):
    import openai

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret-never-log")
    factory = Mock()
    monkeypatch.setattr(openai, "OpenAI", factory)
    openrouter.create_client()
    assert factory.call_args.kwargs["max_retries"] == 0
    client = Mock()
    client.chat.completions.create.side_effect = RuntimeError("test-secret-never-log")
    assert openrouter.request(client, {}) == {"error": "provider_error"}


def test_sdk_serializes_exact_planned_body_and_never_retries(candidate):
    import httpx
    from openai import OpenAI

    parent = read_analysis(write_analysis(**candidate).analysis_id)
    planned = prompts.make_requests(parent, [0], prompts.InterpretationConfig())[0][
        "request"
    ]["body"]
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(503, json={"error": {"message": "temporary failure"}})

    with OpenAI(
        api_key="test-key",
        base_url=openrouter.BASE_URL,
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        result = openrouter.request(client, planned)
    assert result == {"error": "provider_error"}
    assert len(seen) == 1
    assert seen[0]["provider"] == {"require_parameters": True, "allow_fallbacks": False}
    assert seen[0]["max_tokens"] == 800
    assert seen[0]["response_format"]["json_schema"]["strict"] is True


def test_request_identity_includes_settings_keywords_and_evidence(setup):
    parent, _, _, _ = setup
    config = prompts.InterpretationConfig()
    original = prompts.make_requests(parent, [0], config)[0]["request_fingerprint"]
    changed_model = replace(config, model="google/gemini-other-test")
    assert (
        prompts.make_requests(parent, [0], changed_model)[0]["request_fingerprint"]
        != original
    )
    assert (
        prompts.make_requests(parent, [0], replace(config, temperature=0.5))[0][
            "request_fingerprint"
        ]
        != original
    )
    reviews = parent.reviews.copy()
    reviews.loc[0, "model_text"] += " changed evidence"
    assert (
        prompts.make_requests(replace(parent, reviews=reviews), [0], config)[0][
            "request_fingerprint"
        ]
        != original
    )
    from voc.analysis.contracts import Keyword

    topics = tuple(
        replace(t, keywords=(Keyword("nuevo", 0.5),)) if t.topic_id == 0 else t
        for t in parent.topics
    )
    assert (
        prompts.make_requests(replace(parent, topics=topics), [0], config)[0][
            "request_fingerprint"
        ]
        != original
    )
