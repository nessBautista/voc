"""Fixed-prompt integration, persistence, SDK wire format and historical isolation."""

import hashlib
import json

import pytest
from click.testing import CliRunner

from tests.unit.test_topic_interpretation_preparation import (
    config as config_fixture,
)
from tests.unit.test_topic_interpretation_preparation import (
    pair as pair_fixture,
)
from tests.unit.test_topic_interpretation_preparation import (
    reader_for,
)
from tests.unit.test_topic_interpretation_production import valid_response
from voc.embeddings.shared.contracts import fingerprint
from voc.topics.interpretation_results import read_interpretation
from voc_ml.topics.interpretation import cli, prototype, provider
from voc_ml.topics.interpretation.ledger import InterpretationStore
from voc_ml.topics.interpretation.production import (
    execute_request,
)


@pytest.fixture
def config():
    return config_fixture.__wrapped__()


@pytest.fixture
def pair():
    return pair_fixture.__wrapped__()


@pytest.fixture
def policy():
    return prototype.provider_policy("config/topics/interpretation-provider.toml")


@pytest.fixture
def protocol(config, pair, policy):
    return prototype.prepare(
        config, [0], prototype.Configuration(), policy, reader=reader_for(pair)
    )


def test_same_prompt_and_settings_as_pinned_reference():
    assert (
        prototype.PROMPT_SHA256
        == "36287305fc812f7e67edf3a011412f1351ca3af4b0a143b646c8932648df550b"
    )
    assert (
        hashlib.sha256(prototype.SYSTEM_PROMPT.encode()).hexdigest()
        == prototype.PROMPT_SHA256
    )
    assert (
        prototype.load_configuration("config/topics/interpretation.toml")
        == prototype.Configuration()
    )


def test_representatives_then_stored_order_preserve_duplicate_texts(
    config, pair, policy
):
    pair[0].assignments = pair[0].assignments.iloc[[4, 3, 2, 1, 0]]
    pair[1].assignments = pair[0].assignments.copy()
    p = prototype.prepare(
        config, [0], prototype.Configuration(), policy, reader=reader_for(pair)
    )
    evidence = p["cases"][0]["evidence"]
    assert [e["record_id"] for e in evidence] == ["r2", "r1", "r5", "r4", "r3"]
    assert evidence[-1]["text"] == evidence[-2]["text"] == "recovery failed"
    body = json.loads(p["requests"]["0"]["body_json"])
    payload = json.loads(body["messages"][1]["content"])
    assert [e["record_id"] for e in payload["evidence"]] == [
        e["record_id"] for e in evidence
    ]
    assert payload["dataset_release_id"] == "dataset"


@pytest.mark.parametrize("ids", [[], [-1], [0, 0], [99], list(range(21))])
def test_invalid_selection_no_provider(config, pair, policy, ids):
    with pytest.raises(ValueError):
        prototype.prepare(
            config, ids, prototype.Configuration(), policy, reader=reader_for(pair)
        )


def test_new_execution_does_not_touch_historical_files(protocol, tmp_path):
    old = tmp_path / "execution"
    old.mkdir()
    (old / "ledger.json").write_text("historical evidence")
    with pytest.raises(ValueError, match="separate"):
        prototype.create(
            protocol, store=InterpretationStore(old), catalog_reader=lambda p: {}
        )
    store = InterpretationStore(tmp_path / "prototype-execution")
    prototype.create(protocol, store=store, catalog_reader=lambda p: {})
    assert (old / "ledger.json").read_text() == "historical evidence"
    with pytest.raises(ValueError, match="already exists"):
        prototype.create(protocol, store=store, catalog_reader=lambda p: {})


def test_order_survives_ledger_and_receipt_reuse(protocol, tmp_path):
    store = InterpretationStore(tmp_path / "prototype-execution")
    prototype.create(protocol, store=store, catalog_reader=lambda p: {})
    calls = []

    def transport(body):
        calls.append(body)
        assert (
            list(body["response_format"]["json_schema"]["schema"]["properties"])
            == prototype.FIELD_ORDER
        )
        assert (
            body["response_format"]["json_schema"]["schema"]["required"]
            == prototype.FIELD_ORDER
        )
        assert "provider" not in body and "provider" in body["extra_body"]
        return valid_response()

    result = execute_request(
        "0", store=store, transport=transport, catalog_reader=lambda p: {}
    )
    assert result["state"] == "accepted"
    receipt = read_interpretation(
        store.root / result["release"], manifest_sha256=result["manifest_sha256"]
    )
    assert receipt["schema_version"] == "voc-interpretation-result-v2"
    assert receipt["outcome"]["description"]["supporting_review_ids"] == ["r2"]
    assert (
        execute_request(
            "0", store=store, transport=transport, catalog_reader=lambda p: {}
        )["origin"]
        == "cache"
    )
    assert len(calls) == 1
    assert prototype.inspect(store)["pending_topics"] == []


def test_length_failure_is_not_repaired_or_replayed(protocol, tmp_path):
    store = InterpretationStore(tmp_path / "prototype-execution")
    prototype.create(protocol, store=store, catalog_reader=lambda p: {})
    response = valid_response()
    response["choices"][0]["finish_reason"] = "length"
    response["choices"][0]["message"]["content"] = '{"topic_description":"unfinished'
    result = execute_request(
        "0", store=store, transport=lambda b: response, catalog_reader=lambda p: {}
    )
    assert result["state"] == "invalid" and result["outcome"]["description"] is None
    with pytest.raises(ValueError, match="no replay"):
        execute_request(
            "0",
            store=store,
            transport=lambda b: pytest.fail("must not send"),
            catalog_reader=lambda p: {},
        )


def test_receipt_rejects_changed_ordered_body(protocol, tmp_path):
    store = InterpretationStore(tmp_path / "prototype-execution")
    prototype.create(protocol, store=store, catalog_reader=lambda p: {})
    result = execute_request(
        "0",
        store=store,
        transport=lambda b: valid_response(),
        catalog_reader=lambda p: {},
    )
    folder = store.root / result["release"]
    data = json.loads((folder / "result.json").read_text())
    data["request"]["body_json"] = "{}"
    from voc_ml.topics.interpretation.ledger import atomic_json

    atomic_json(folder / "result.json", data)
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["result_sha256"] = fingerprint(data)
    atomic_json(folder / "manifest.json", manifest)
    with pytest.raises(ValueError, match="Ordered"):
        read_interpretation(folder, manifest_sha256=fingerprint(manifest))


def test_cli_plan_and_create_are_offline_until_catalog(protocol, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_prepare", lambda *a: protocol)
    store = InterpretationStore(tmp_path / "prototype-execution")
    monkeypatch.setattr(cli, "_store", lambda: store)
    monkeypatch.setattr(provider, "check_catalog", lambda p: {"fixture": True})
    runner = CliRunner()
    out = runner.invoke(cli.interpretation, ["plan", "--topic-id", "0"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["prompt"]["fixed"] is True
    assert not store.root.exists()
    out = runner.invoke(cli.interpretation, ["create", "--topic-id", "0"])
    assert out.exit_code == 0, out.output
    assert store.load()["attempts"] == {}
    before = (store.root / "ledger.json").read_bytes()
    out = runner.invoke(cli.interpretation, ["inspect"])
    assert out.exit_code == 0 and (store.root / "ledger.json").read_bytes() == before


def test_sdk_adapter_preserves_options_closes_client_and_disables_retries(
    protocol, monkeypatch
):
    from types import SimpleNamespace

    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-not-a-credential")
    captured = {}

    class Client:
        def __init__(self, **options):
            captured["options"] = options
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            captured["closed"] = True

        def create(self, **body):
            captured["body"] = body
            return SimpleNamespace(model_dump=lambda **kw: valid_response())

    body = json.loads(protocol["requests"]["0"]["body_json"])
    assert provider.sdk_request(body, client_factory=Client) == valid_response()
    assert captured["options"]["max_retries"] == 0
    assert captured["options"]["timeout"] == 60.0 and captured["closed"]
    assert (
        list(captured["body"]["response_format"]["json_schema"]["schema"]["properties"])
        == prototype.FIELD_ORDER
    )


def test_sdk_http_wire_preserves_schema_and_flattens_extra_body(protocol, monkeypatch):
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-not-a-credential")
    calls = []

    def handler(request):
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "synthetic-response",
                "object": "chat.completion",
                "created": 0,
                "model": "google/gemini-2.5-flash",
                "provider": "Google AI Studio",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "{}"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "cost": 0.000008,
                },
            },
        )

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        return openai.OpenAI(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )

    body = json.loads(protocol["requests"]["0"]["body_json"])
    result = provider.sdk_request(body, client_factory=factory)
    assert len(calls) == 1
    wire = calls[0]
    assert "extra_body" not in wire
    assert wire["provider"] == body["extra_body"]["provider"]
    assert wire["reasoning"] == {"enabled": False}
    assert wire["messages"] == body["messages"]
    assert wire["max_tokens"] == 800 and wire["temperature"] == 0
    schema = wire["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert list(schema["schema"]["properties"]) == prototype.FIELD_ORDER
    assert result["provider"] == "Google AI Studio"
    assert result["usage"]["cost"] == 0.000008


def test_python_api_rejects_changed_configuration(config, pair, policy):
    with pytest.raises(ValueError, match="fixed"):
        prototype.prepare(
            config,
            [0],
            prototype.Configuration(max_reviews=3),
            policy,
            reader=reader_for(pair),
        )
