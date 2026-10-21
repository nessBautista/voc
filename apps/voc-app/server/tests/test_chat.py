"""Exercise the real graph, HTTP query adapter and durable conversation boundary."""

import asyncio
import copy
import json
import time
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from voc_app.agent.answers import validate_answer
from voc_app.agent.contracts import Plan
from voc_app.agent.planner import Planner, fixture_plan
from voc_app.conversations import Conversations
from voc_app.main import create_app

from voc.publications.store import activate_bundle


class CountPlanner:
    def __init__(self, settings, delay=0):
        self.delegate = Planner(settings)
        self.calls = 0
        self.delay = delay

    async def plan(self, question, history):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return await self.delegate.plan(question, history)


def app_for(settings, planner=None):
    return create_app(settings, planner=planner)


def payload(question="¿Cuántos registros tenemos de Google Play?", conversation=None):
    return {
        "request_id": str(uuid4()),
        "question": question,
        "conversation_id": conversation,
    }


def wait(client, identity):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/conversations/{identity}")
        assert response.status_code == 200, response.text
        conversation = response.json()
        if not any(turn["status"] == "running" for turn in conversation["turns"]):
            return conversation
        time.sleep(0.02)
    pytest.fail("Conversation did not finish")


def ask(client, request):
    response = client.post("/api/v1/ask", json=request)
    assert response.status_code == 202, response.text
    return wait(client, response.json()["conversation_id"])


@pytest.mark.parametrize(
    "question,intent,platform",
    [
        ("¿Cuántos registros tenemos de Google Play?", "sources", "google_play"),
        ("¿Cuántas reseñas tenemos de Google Play?", "sources", "google_play"),
        ("¿De dónde provienen los datos?", "sources", None),
        ("¿Cuál es la última actualización?", "freshness", None),
        ("¿Cuándo se recolectó App Store?", "freshness", "app_store"),
        ("¿Qué cobertura mensual tenemos?", "coverage", None),
        ("Estado de embeddings", "embeddings", None),
        ("Resumen del dataset", "overview", None),
        ("Sentimiento por mes", "unsupported", None),
        ("Cambia UMAP a cuatro dimensiones", "unsupported", None),
        ("Cobertura de 2026", "unsupported", None),
        ("Cobertura mensual Google Play", "clarify", None),
        ("¿y App Store?", "clarify", None),
        ("Hola", "clarify", None),
    ],
)
def test_fixture_examples(question, intent, platform):
    assert fixture_plan(question, []).model_dump() == {
        "intent": intent,
        "platform": platform,
    }


@pytest.mark.parametrize(
    "plan",
    [
        {"intent": "execute_pipeline", "platform": None},
        {"intent": "sources", "platform": "Google Play"},
        {"intent": "sources", "platform": None, "sql": "DROP TABLE dataset"},
        {"intent": "coverage", "platform": "google_play"},
    ],
)
def test_plan_rejects_unavailable_work(plan):
    with pytest.raises(ValidationError):
        Plan.model_validate(plan)


def test_follow_up_pins_A_and_new_conversation_selects_B(settings, active, bundles):
    planner = CountPlanner(settings)
    with TestClient(app_for(settings, planner)) as client:
        first = ask(client, payload())
        identity = first["conversation_id"]
        answer = first["turns"][0]["answer"]
        assert "8 registros de Google Play" in answer["text"]
        assert [item["operation"] for item in answer["trace"]] == ["sources"]
        assert answer["usage"]["requests"] == 0
        activate_bundle(bundles[1], settings.publications_dir, settings.state_dir)
        second = ask(client, payload("¿y de App Store?", identity))
        assert second["publication_id"] == active["publication_id"]
        assert "4 registros de App Store" in second["turns"][-1]["answer"]["text"]
        newest = ask(client, payload("¿Cuántos registros de App Store?"))
        assert newest["publication_id"] != first["publication_id"]
        assert "5 registros de App Store" in newest["turns"][0]["answer"]["text"]
        assert planner.calls == 3
    # A fresh server returns the saved answer without invoking another model.
    with TestClient(app_for(settings, planner)) as client:
        assert client.get(f"/api/v1/conversations/{identity}").json() == second
        assert planner.calls == 3


def test_retry_after_lost_response_is_idempotent_and_other_content_conflicts(
    settings, active
):
    planner = CountPlanner(settings, delay=0.15)
    request = payload()
    with TestClient(app_for(settings, planner)) as client:
        response = client.post("/api/v1/ask", json=request)
        identity = response.json()["conversation_id"]
        # Ignore the first answer, just like a socket closing after acceptance.
        retry = client.post("/api/v1/ask", json=request)
        assert retry.json()["conversation_id"] == identity
        busy = client.post("/api/v1/ask", json=payload("Otra pregunta", identity))
        assert busy.status_code == 409
        assert busy.json()["error"]["code"] == "conversation_busy"
        conflict = client.post(
            "/api/v1/ask", json={**request, "question": "Otra pregunta"}
        )
        assert conflict.status_code == 409
        conversation = wait(client, identity)
        assert conversation["turns"][0]["status"] == "completed"
        assert client.post("/api/v1/ask", json=request).status_code == 200
        assert len(client.get(f"/api/v1/conversations/{identity}").json()["turns"]) == 1
        assert planner.calls == 1


def test_pending_turn_interrupted_on_shutdown_and_not_replayed(settings, active):
    planner = CountPlanner(settings, delay=10)
    request = payload()
    with TestClient(app_for(settings, planner)) as client:
        identity = client.post("/api/v1/ask", json=request).json()["conversation_id"]
        time.sleep(0.05)
    with TestClient(app_for(settings, planner)) as client:
        retry = client.post("/api/v1/ask", json=request)
        assert retry.status_code == 200
        assert retry.json()["turns"][0]["status"] == "interrupted"
        assert planner.calls == 1
        assert client.get(f"/api/v1/conversations/{identity}").status_code == 200


def test_crash_marker_is_interrupted_on_start(settings, active):
    # An abrupt exit leaves running persisted; startup performs no model replay.
    service = Conversations(settings, None, None)
    service.start()
    with service.database() as db:
        db.execute(
            "INSERT INTO conversations VALUES (?,?,?)",
            (str(uuid4()), active["publication_id"], "2026-01-01T00:00:00Z"),
        )
        identity = db.execute("SELECT id FROM conversations").fetchone()[0]
        db.execute(
            "INSERT INTO turns (request_id,conversation_id,request,question,status,planner_mode,created_at) VALUES (?,?,?,'pending','running','model',?)",
            (str(uuid4()), identity, "{}", "2026-01-01T00:00:00Z"),
        )
    service.lock.close()
    service.lock = None
    with TestClient(app_for(settings)) as client:
        turn = client.get(f"/api/v1/conversations/{identity}").json()["turns"][0]
        assert turn["status"] == "interrupted"
        assert turn["answer"] is None


def test_two_workers_do_not_interrupt_the_owner(settings, active):
    owner = Conversations(settings, None, None)
    owner.start()
    other = Conversations(settings, None, None)
    with pytest.raises(BlockingIOError):
        other.start()
    asyncio.run(owner.close())


@pytest.mark.parametrize("question", ["Sentimiento por mes", "Hola"])
def test_out_of_scope_has_no_queries_or_views(settings, active, question):
    with TestClient(app_for(settings)) as client:
        answer = ask(client, payload(question))["turns"][0]["answer"]
        assert answer["results"] == answer["trace"] == answer["views"] == []


def test_preparation_preserves_unknown_and_version_semantics(settings, active):
    with TestClient(app_for(settings)) as client:
        answer = ask(client, payload("Última actualización"))["turns"][0]["answer"]
        assert "fecha de recolección desconocida" in answer["text"]
        assert "no prueba que se hayan obtenido datos nuevos" in answer["text"]
        assert len(answer["trace"]) == 2
        assert answer["views"][0]["component"] == "preparation"
        answer = ask(client, payload("Estado de embeddings"))["turns"][0]["answer"]
        assert "no significa que no existan" in answer["text"]


def test_answer_rejects_tampered_visual_text_and_result(settings, active):
    with TestClient(app_for(settings)) as client:
        answer = ask(client, payload())["turns"][0]["answer"]
    for mutate in (
        lambda item: item["views"][0]["result_ids"].__setitem__(0, "0" * 64),
        lambda item: item.__setitem__("text", "Invented facts"),
        lambda item: item["results"][0]["data"][0].__setitem__("record_count", 900),
    ):
        invalid = copy.deepcopy(answer)
        mutate(invalid)
        with pytest.raises(ValueError):
            validate_answer(
                invalid, Plan.model_validate(answer["plan"]), active["publication_id"]
            )


def test_timeout_is_terminal_and_dashboard_still_works(settings, active):
    settings = replace(settings, turn_timeout_seconds=1)
    with TestClient(app_for(settings, CountPlanner(settings, delay=2))) as client:
        turn = ask(client, payload())["turns"][0]
        assert turn["status"] == "failed"
        assert turn["error"]["code"] == "turn_timeout"
        assert client.get("/api/v1/publications/current").status_code == 200


def test_unavailable_model_fails_without_fallback_or_secret(settings, active):
    settings = replace(settings, planner_mode="model")
    with TestClient(app_for(settings)) as client:
        turn = ask(client, payload())["turns"][0]
        assert turn["status"] == "failed"
        assert turn["error"]["code"] == "model_unavailable"
        assert turn["planner_mode"] == "model"
        assert client.get("/health/ready").status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"intent":"sources","platform":"google_play"}'
                    },
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 13},
        },
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"intent":"execute_pipeline","platform":null}'
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"intent":"sources","platform":null}'},
                }
            ]
        },
        {"error": "provider secret details"},
    ],
)
def test_model_adapter_contract_and_bounded_context(settings, active, body):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=body)

    settings = replace(
        settings, planner_mode="model", llm_api_key="never-return-this-secret"
    )
    planner = Planner(settings, httpx.MockTransport(respond))
    with TestClient(app_for(settings, planner)) as client:
        turn = ask(client, payload())["turns"][0]
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 256
    context = json.loads(requests[0]["messages"][1]["content"])
    assert set(context) == {"question", "history"}
    assert "never-return-this-secret" not in json.dumps(turn)
    if "usage" in body:
        assert turn["status"] == "completed"
        assert turn["answer"]["usage"]["prompt_tokens"] == 120
    else:
        assert turn["status"] == "failed"
        assert turn["error"]["code"] == "invalid_plan"


def test_provider_failure_has_no_automatic_retry(settings, active):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503, text="secret provider body")

    settings = replace(settings, planner_mode="model", llm_api_key="test-secret")
    with TestClient(
        app_for(settings, Planner(settings, httpx.MockTransport(respond)))
    ) as client:
        turn = ask(client, payload())["turns"][0]
        assert turn["error"]["code"] == "model_unavailable"
        assert len(requests) == 1
        assert "secret" not in json.dumps(turn)
        assert client.get("/health/ready").status_code == 200


def test_invalid_requests_and_cross_origin_do_not_start_work(settings, active):
    with TestClient(app_for(settings)) as client:
        assert client.post("/api/v1/ask", json=payload(" ")).status_code == 422
        assert client.post("/api/v1/ask", json=payload("x" * 1001)).status_code == 422
        assert (
            client.post(
                "/api/v1/ask", json={**payload(), "sql": "SELECT 1"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/ask",
                json=payload(),
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert client.get("/api/v1/conversations/not-a-uuid").status_code == 422
        assert client.get(f"/api/v1/conversations/{uuid4()}").status_code == 404


def test_mismatched_evidence_never_becomes_a_completed_answer(
    settings, active, monkeypatch
):
    # A library result naming another publication must fail the turn, not answer it.
    monkeypatch.setattr(
        "voc_app.client.queries.query",
        lambda catalog, publication_id, kind: {"publication_id": str(uuid4())},
    )
    with TestClient(create_app(settings)) as client:
        turn = ask(client, payload())["turns"][0]
        assert turn["status"] == "failed"
        assert turn["answer"] is None


def test_corrupt_conversation_storage_keeps_publication_api_available(settings, active):
    (settings.state_dir / "conversations.sqlite3").write_bytes(b"not a sqlite database")
    with TestClient(app_for(settings)) as client:
        assert client.get("/health/ready").status_code == 200
        response = client.post("/api/v1/ask", json=payload())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "chat_unavailable"


@pytest.mark.parametrize(
    "question", ["Cobertura de los últimos tres meses", "Registros de julio"]
)
def test_fixture_does_not_silently_drop_date_filters(question):
    assert fixture_plan(question, []).intent == "unsupported"
