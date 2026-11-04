"""Provider-free behavior checks of budgets, durable sends and portable receipts."""

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from voc.embeddings.shared.contracts import fingerprint
from voc.topics.interpretation_results import read_interpretation
from voc_ml.topics.interpretation.execution_plan import freeze_protocol
from voc_ml.topics.interpretation.ledger import InterpretationStore
from voc_ml.topics.interpretation.production import execute_request, inspect_study
from voc_ml.topics.interpretation.provider import (
    check_catalog,
    load_provider,
    request_once,
)


@pytest.fixture
def policy():
    return load_provider("config/topics/interpretation-provider.toml")


@pytest.fixture
def draft():
    cases = []
    for tid in [59, 1440, *range(10)]:
        evidence = [
            {
                "label": "E1",
                "record_id": f"private-{tid}",
                "text": "No recibo avisos",
                "truncated": False,
            }
        ]
        cases.append(
            {
                "topic_id": tid,
                "size": 10,
                "evidence": evidence,
                "evidence_sha256": fingerprint(evidence),
                "keywords": {
                    "R0": [{"term": "aviso", "weight": 0.2}],
                    "R2": [{"term": "notificación", "weight": 0.8}],
                },
            }
        )
    prompts = {
        p: {
            "version": p + "-v1",
            "text": p,
            "sha256": hashlib.sha256(p.encode()).hexdigest(),
        }
        for p in ["P0", "P1", "P2"]
    }
    plan = {
        "state": "draft",
        "generation": {
            "model": "google/gemini-2.5-flash",
            "temperature": 0.0,
            "max_tokens": 800,
        },
        "cases": {"development": cases, "reserve": [{"secret": "UNOPENED"}] * 12},
        "cases_sha256": {"reserve": "b" * 64},
        "prompts": prompts,
        "input": {"identity": "test"},
    }
    return {"plan": plan, "plan_sha256": fingerprint(plan)}


@pytest.fixture
def store(tmp_path, draft, policy):
    result = InterpretationStore(tmp_path / "execution")
    result.initialize(freeze_protocol(draft, policy), {"fixture": True})
    return result


def valid_response():
    return {
        "id": "generation-fixture",
        "model": "google/gemini-2.5-flash",
        "provider": "Google AI Studio",
        "usage": {"prompt_tokens": 200, "completion_tokens": 80, "cost": 0.00026},
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "name": "Notificaciones",
                            "topic_description": "Se informa ausencia de avisos.",
                            "assessment": "single_feature",
                            "proposed_feature": "Notificaciones",
                            "supporting_evidence": ["E1"],
                        }
                    )
                },
            }
        ],
    }


def run(store, response=None, key="59-R2-P0", transport=None, **kwargs):
    return execute_request(
        key,
        store=store,
        transport=transport or (lambda body: response or valid_response()),
        catalog_reader=lambda policy: {"fixture": True},
        **kwargs,
    )


def test_freeze_preserves_evidence_and_never_builds_reserve_requests(draft, policy):
    protocol = freeze_protocol(draft, policy)
    assert len(protocol["requests"]) == 48
    assert "UNOPENED" not in json.dumps(protocol)
    assert protocol["max_study_allowance_usd"] == "0.8517888"
    assert protocol["per_attempt_allowance_usd"] == "0.0118304"
    request = protocol["requests"]["59-R2-P0"]["body"]
    assert request["provider"]["allow_fallbacks"] is False
    assert request["reasoning"] == {"enabled": False}
    assert "private-59" not in json.dumps(request)


def test_success_cache_and_portable_read(store):
    calls = []

    def transport(body):
        pending = store.load()["attempts"]["59-R2-P0"]
        assert pending["state"] == "sending" and pending["charged_usd"] == "0.0118304"
        calls.append(body)
        return valid_response()

    result = run(store, transport=transport)
    assert result["state"] == "accepted"
    assert result["outcome"]["description"]["supporting_review_ids"] == ["private-59"]
    assert run(store, transport=transport)["origin"] == "cache"
    assert len(calls) == 1
    receipt = read_interpretation(
        store.root / result["release"], manifest_sha256=result["manifest_sha256"]
    )
    assert receipt["response"] == valid_response()
    assert inspect_study(store)["accounted_or_reserved_usd"] == "0.00026"
    assert run(store, key="1440-R2-P0")["state"] == "accepted"
    with pytest.raises(ValueError, match="two initial"):
        run(store, key="0-R2-P0")
    assert len(store.load()["attempts"]) == 2


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("missing_cost", "uncertain"),
        ("nan_cost", "uncertain"),
        ("overspend", "uncertain"),
        ("prompt_limit", "uncertain"),
        ("token_type", "uncertain"),
        ("truncated", "invalid"),
        ("bad_json", "invalid"),
        ("bad_citation", "invalid"),
        ("wrong_provider", "invalid"),
        ("wrong_model", "invalid"),
        ("refusal", "invalid"),
    ],
)
def test_failure_never_replays_or_allows_next_request(store, mutation, expected):
    response = valid_response()
    if mutation == "missing_cost":
        del response["usage"]["cost"]
    elif mutation == "nan_cost":
        response["usage"]["cost"] = "NaN"
    elif mutation == "overspend":
        response["usage"]["cost"] = 2
    elif mutation == "prompt_limit":
        response["usage"]["prompt_tokens"] = 40000
    elif mutation == "token_type":
        response["usage"]["completion_tokens"] = True
    elif mutation == "truncated":
        response["choices"][0]["finish_reason"] = "length"
    elif mutation == "bad_json":
        response["choices"][0]["message"]["content"] = "not JSON"
    elif mutation == "bad_citation":
        response["choices"][0]["message"]["content"] = response["choices"][0][
            "message"
        ]["content"].replace("E1", "E9")
    elif mutation == "wrong_provider":
        response["provider"] = "other"
    elif mutation == "wrong_model":
        response["model"] = "other"
    elif mutation == "refusal":
        response["choices"][0]["message"]["refusal"] = "refused"
    result = run(store, response)
    assert result["state"] == expected
    for key in ["59-R2-P0", "1440-R2-P0"]:
        with pytest.raises(ValueError):
            run(store, key=key, transport=lambda body: pytest.fail("must not call"))
    assert len(store.load()["attempts"]) == 1
    inspect_study(store)


def test_secret_exception_not_persisted(store):
    def transport(body):
        raise RuntimeError("SECRET_HEADER_AND_KEY")

    result = run(store, transport=transport)
    assert result["state"] == "uncertain"
    assert inspect_study(store)["accounted_or_reserved_usd"] == "0.0118304"
    for path in store.root.rglob("*.json"):
        assert "SECRET_HEADER_AND_KEY" not in path.read_text()


def test_interrupted_send_remains_charged(store, monkeypatch):
    from voc_ml.topics.interpretation import production

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(production, "atomic_json", fail)
    with pytest.raises(OSError):
        run(store)
    assert store.load()["attempts"]["59-R2-P0"]["state"] == "sending"
    assert inspect_study(store)["accounted_or_reserved_usd"] == "0.0118304"
    with pytest.raises(ValueError):
        run(store)


def test_tracking_failure_blocks_following_send(store):
    result = run(store, tracking={"zenml_run_id": "z", "mlflow_run_id": "m"})
    assert result["state"] == "accepted"
    with pytest.raises(ValueError, match="incomplete"):
        run(store, key="1440-R2-P0")


def test_budget_attempt_limit_and_create_no_reset(store):
    state = store.load()
    state["protocol"]["provider"]["max_cost_usd"] = "0.001"
    state["protocol_sha256"] = fingerprint(state["protocol"])
    store.save(state)
    with pytest.raises(ValueError, match="Cost budget"):
        run(store)
    assert not store.load()["attempts"]
    state["protocol"]["provider"]["max_cost_usd"] = "1.0"
    state["protocol"]["provider"]["max_attempts"] = 0
    state["protocol_sha256"] = fingerprint(state["protocol"])
    store.save(state)
    with pytest.raises(ValueError, match="Attempt budget"):
        run(store)
    with pytest.raises(ValueError, match="already exists"):
        store.initialize(state["protocol"], {})


def test_cache_corruption_cannot_trigger_provider(store):
    result = run(store)
    path = store.root / result["release"] / "result.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        run(store, transport=lambda body: pytest.fail("corrupt cache must not send"))


def test_ledger_billing_tamper_is_detected(store):
    run(store)
    state = store.load()
    state["attempts"]["59-R2-P0"]["charged_usd"] = "0"
    store.save(state)
    with pytest.raises(ValueError, match="accounting"):
        inspect_study(store)


def test_cross_process_lock_prevents_concurrent_send(store):
    code = """
import sys
from voc_ml.topics.interpretation.ledger import InterpretationStore
try:
 with InterpretationStore(sys.argv[1]).lock():
  raise AssertionError('concurrent ownership')
except ValueError:
 pass
"""
    with store.lock():
        result = subprocess.run(
            [sys.executable, "-c", code, str(store.root)],
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
        )
    assert result.returncode == 0, result.stderr


def test_catalog_rejects_drift(policy):
    endpoint = {
        "tag": "google-ai-studio",
        "status": 0,
        "supported_parameters": [
            "response_format",
            "structured_outputs",
            "temperature",
            "max_tokens",
            "reasoning",
        ],
        "pricing": {"prompt": "0.0000003", "completion": "0.0000025"},
        "context_length": 1048576,
        "max_completion_tokens": 65535,
    }
    catalog = {"data": {"id": policy["model"], "endpoints": [endpoint]}}
    assert check_catalog(policy, fetch=lambda: catalog)["model_requests"] == 0
    for change in [
        {"pricing": {"prompt": "0.1", "completion": "0.1"}},
        {"status": 1},
        {"supported_parameters": []},
        {"context_length": 1},
    ]:
        altered = deepcopy(catalog)
        altered["data"]["endpoints"][0].update(change)
        with pytest.raises(ValueError):
            check_catalog(policy, fetch=lambda altered=altered: altered)


def test_transport_timeout_sanitized_and_no_retry(monkeypatch):
    from voc_ml.topics.interpretation import provider

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-not-real")
    calls = []

    def failed(*args, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired("transport", 60)

    monkeypatch.setattr(provider.subprocess, "run", failed)
    assert request_once({}) == {"transport_error": "uncertain"}
    assert len(calls) == 1 and calls[0]["timeout"] == 60


def test_portable_reader_in_new_process_without_producer_or_sdk(store):
    result = run(store)
    code = """
import importlib.abc,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in {'voc_ml','openai','httpx','zenml','mlflow','torch'}:
   raise AssertionError(fullname)
sys.meta_path.insert(0,Block())
from voc.topics.interpretation_results import read_interpretation
r=read_interpretation(sys.argv[1],manifest_sha256=sys.argv[2])
assert r['outcome']['state']=='accepted'
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(store.root / result["release"]),
            result["manifest_sha256"],
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
    )
    assert process.returncode == 0, process.stderr


def test_receipt_revalidates_citations_even_with_rehashed_files(store):
    from voc_ml.topics.interpretation.ledger import atomic_json

    result = run(store)
    directory = store.root / result["release"]
    content = json.loads((directory / "result.json").read_bytes())
    content["outcome"]["description"]["supporting_review_ids"] = ["unknown"]
    atomic_json(directory / "result.json", content)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["result_sha256"] = fingerprint(content)
    atomic_json(directory / "manifest.json", manifest)
    with pytest.raises(ValueError, match="validation differs"):
        read_interpretation(directory, manifest_sha256=fingerprint(manifest))


def test_workflow_cache_needs_no_tracking_sdk(store):
    from voc_ml.topics.interpretation.tracking import execute_workflow

    run(store)
    assert execute_workflow("59-R2-P0", store=store)["origin"] == "cache"


def test_catalog_failure_consumes_no_attempt(store):
    def unavailable(policy):
        raise ValueError("catalog unavailable")

    with pytest.raises(ValueError, match="catalog"):
        execute_request(
            "59-R2-P0",
            store=store,
            transport=lambda b: pytest.fail("no send"),
            catalog_reader=unavailable,
        )
    assert store.load()["attempts"] == {}


def test_fixed_create_cli_rejects_old_experiment_plan(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from voc_ml.topics.interpretation import cli
    from voc_ml.topics.interpretation.files import write_draft

    plan = {"state": "draft"}
    draft = {
        "schema_version": "voc-topic-interpretation-draft-v1",
        "plan": plan,
        "plan_sha256": fingerprint(plan),
    }
    path = tmp_path / "plan.json"
    write_draft(path, draft)
    result = CliRunner().invoke(cli.interpretation, ["create", "--plan", str(path)])
    assert result.exit_code != 0 and "No such option: --plan" in result.output


def test_inspect_cli_read_only_receipts(store, monkeypatch):
    from click.testing import CliRunner

    from voc_ml.topics.interpretation import cli

    run(store)
    before = (store.root / "ledger.json").read_bytes()
    monkeypatch.setattr(cli, "_store", lambda name: store)
    result = CliRunner().invoke(cli.interpretation, ["history", "development"])
    assert (
        result.exit_code == 0
        and json.loads(result.output)["accounted_or_reserved_usd"] == "0.00026"
    )
    assert (store.root / "ledger.json").read_bytes() == before


def test_missing_key_fails_before_transport(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="not available"):
        request_once({})


def test_interpretation_experiment_uses_existing_local_location():
    from types import SimpleNamespace

    from voc_ml.tracking import ensure_interpretation_experiment

    storage = SimpleNamespace(
        artifact_destination="local", mlflow_artifact_uri="file:///local/artifacts"
    )

    class Client:
        def get_experiment_by_name(self, name):
            assert name == "voc-interpretations"
            return SimpleNamespace(
                lifecycle_stage="active",
                artifact_location="file:///local/artifacts",
                experiment_id="12",
            )

    assert ensure_interpretation_experiment(Client(), storage) == "12"
    storage.artifact_destination = "s3"
    with pytest.raises(ValueError, match="local"):
        ensure_interpretation_experiment(Client(), storage)
