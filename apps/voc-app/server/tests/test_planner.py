"""The planner classifies the demo flow in fixture mode and parses it in model mode."""

import json
import logging
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_chat import ask, payload
from voc_app.agent.contracts import Plan, Selection, TopicReference
from voc_app.agent.planner import SYSTEM, Planner, fixture_plan
from voc_app.main import create_app

NONE = {"platform": None, "selection": None, "topic": None, "limit": None}
DEMO = [
    ("¿Cómo se sienten, en general, quienes dejaron reseñas?", "mood", {}),
    ("¿Cuál es la queja principal?", "main_complaints", {}),
    ("¿Cuándo aumentaron las quejas?", "trend", {}),
    (
        "¿Qué dicen exactamente los clientes?",
        "topic_reviews",
        {"topic": {"topic_id": None, "reference": "last_highlighted"}},
    ),
    ("¿Qué problemas más específicos aparecen?", "specific_problems", {}),
    ("¿Dónde convendría poner atención primero?", "focus", {}),
]


def selection(**fields):
    return {
        "platforms": None,
        "labels": None,
        "month_from": None,
        "month_to": None,
        "search": None,
        **fields,
    }


@pytest.mark.parametrize(
    "question,intent,fields",
    [
        *DEMO,
        (
            "¿Cómo se sienten en Google Play y App Store?",
            "mood",
            {"selection": selection(platforms=["app_store", "google_play"])},
        ),
        (
            "¿Cuál es la queja principal desde mayo de 2026?",
            "main_complaints",
            {"selection": selection(month_from="2026-05")},
        ),
        (
            "Muéstrame las reseñas positivas del tema 73",
            "topic_reviews",
            {
                "selection": selection(labels=["positive"]),
                "topic": {"topic_id": 73, "reference": None},
            },
        ),
        ("Las tres prioridades", "focus", {"limit": 3}),
        (
            "Tendencia entre enero de 2026 y marzo de 2026",
            "trend",
            {"selection": selection(month_from="2026-01", month_to="2026-03")},
        ),
        (
            "Reseñas del tema abierto",
            "topic_reviews",
            {"topic": {"topic_id": None, "reference": "last_opened"}},
        ),
        ("¿Cuándo aumentaron las quejas en mayo?", "clarify", {}),
        ("Recalcula los temas", "unsupported", {}),
        ("Compara mayo de 2026 con junio de 2026", "unsupported", {}),
        ("Hola", "clarify", {}),
    ],
)
def test_fixture_classifies_the_demo_flow(question, intent, fields):
    assert fixture_plan(question, []).model_dump() == {
        "intent": intent,
        **NONE,
        **fields,
    }


def test_fixture_follow_up_keeps_an_analysis_intent():
    history = [
        {
            "question": "¿Cómo se sienten?",
            "plan": fixture_plan("¿Cómo se sienten?", []).model_dump(),
        }
    ]
    plan = fixture_plan("¿y App Store?", history)
    assert plan.intent == "mood"
    assert plan.selection.platforms == ["app_store"]
    # Before any analysis answer, the same words are ambiguous.
    assert fixture_plan("¿y App Store?", []).intent == "clarify"


@pytest.mark.parametrize(
    "plan",
    [
        {"intent": "overview", **NONE, "selection": selection(platforms=["x"])},
        {"intent": "mood", **NONE, "selection": selection(labels=["negative"])},
        {"intent": "mood", **NONE, "selection": selection(platforms=[])},
        {"intent": "mood", **NONE, "selection": selection(platforms=["x", "x"])},
        {"intent": "trend", **NONE, "selection": selection(month_from="2026-13")},
        {
            "intent": "trend",
            **NONE,
            "selection": selection(month_from="2026-05", month_to="2026-04"),
        },
        {"intent": "trend", **NONE, "selection": selection(search=" ")},
        {"intent": "topic_reviews", **NONE},
        {
            "intent": "topic_reviews",
            **NONE,
            "topic": {"topic_id": 3, "reference": "last_opened"},
        },
        {
            "intent": "topic_reviews",
            **NONE,
            "topic": {"topic_id": -2, "reference": None},
        },
        {"intent": "mood", **NONE, "topic": {"topic_id": 3, "reference": None}},
        {"intent": "mood", **NONE, "limit": 3},
        {"intent": "focus", **NONE, "limit": 11},
        {"intent": "focus", **NONE, "limit": 0},
        {
            "intent": "focus",
            "platform": "x",
            "selection": None,
            "topic": None,
            "limit": None,
        },
    ],
)
def test_plan_refuses_fields_outside_their_intent(plan):
    with pytest.raises(ValidationError):
        Plan.model_validate(plan)


def test_schema_is_strict_and_lists_every_property():
    schema = Plan.model_json_schema()
    assert schema["required"] == ["intent", "platform", "selection", "topic", "limit"]
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Selection"]["required"] == [
        "platforms",
        "labels",
        "month_from",
        "month_to",
        "search",
    ]
    assert schema["$defs"]["TopicReference"]["required"] == ["topic_id", "reference"]
    assert "default" not in json.dumps(schema)
    for intent in (
        "mood",
        "main_complaints",
        "trend",
        "topic_reviews",
        "specific_problems",
        "focus",
    ):
        assert intent in SYSTEM


def test_selection_filters_omit_nulls():
    chosen = Selection(
        platforms=["x"], labels=None, month_from=None, month_to="2026-03", search=None
    )
    assert chosen.filters() == {"platforms": ["x"], "month_to": "2026-03"}
    assert TopicReference(topic_id=-1, reference=None).topic_id == -1


@pytest.mark.parametrize(
    "content,expected",
    [
        (
            {
                "intent": "main_complaints",
                "platform": None,
                "selection": selection(platforms=["google_play"]),
                "topic": None,
                "limit": 2,
            },
            "completed",
        ),
        (
            {
                "intent": "overview",
                "platform": None,
                "selection": selection(),
                "topic": None,
                "limit": None,
            },
            "failed",
        ),
        ({"intent": "mood", "platform": None}, "failed"),
    ],
)
def test_model_mode_parses_the_new_schema(settings, v2_active, content, expected):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 300, "completion_tokens": 40},
            },
        )

    settings = replace(settings, planner_mode="model", llm_api_key="test-secret")
    planner = Planner(settings, httpx.MockTransport(respond))
    with TestClient(create_app(settings, planner=planner)) as client:
        turn = ask(client, payload("¿Cuál es la queja principal en Google Play?"))[
            "turns"
        ][0]
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 512
    assert (
        requests[0]["response_format"]["json_schema"]["schema"]["required"][-1]
        == "limit"
    )
    assert set(json.loads(requests[0]["messages"][1]["content"])) == {
        "question",
        "history",
    }
    assert turn["status"] == expected, turn
    if expected == "completed":
        assert turn["answer"]["plan"] == content
        assert [r["kind"] for r in turn["answer"]["results"]] == [
            "summary",
            "topic_list",
        ]
        assert turn["answer"]["results"][0]["selection"]["platforms"] == ["google_play"]
        assert turn["answer"]["usage"]["prompt_tokens"] == 300
    else:
        assert turn["error"]["code"] == "invalid_plan"
        assert "test-secret" not in json.dumps(turn)


def canned(content, status=200):
    def respond(request):
        if status != 200:
            return httpx.Response(
                status, json={"error": {"message": "provider detail"}}
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 900, "completion_tokens": 60},
            },
        )

    return httpx.MockTransport(respond)


def model_turn(settings, transport, question="¿Cómo se sienten en general?"):
    settings = replace(settings, planner_mode="model", llm_api_key="test-secret-key")
    planner = Planner(settings, transport)
    with TestClient(create_app(settings, planner=planner)) as client:
        return ask(client, payload(question))["turns"][0]


def test_model_all_null_selection_is_stored_as_null(settings, v2_active):
    turn = model_turn(
        settings,
        canned(
            {
                "intent": "mood",
                "platform": None,
                "selection": selection(),
                "topic": None,
                "limit": None,
            }
        ),
    )
    assert turn["status"] == "completed", turn
    assert turn["answer"]["plan"]["selection"] is None
    assert turn["answer"]["usage"]["prompt_tokens"] == 900


def test_provider_failures_log_status_and_fields_only(settings, v2_active, caplog):
    question = "¿Cómo se sienten en general? texto-privado-de-la-pregunta"
    with caplog.at_level(logging.WARNING, logger="voc_app.agent.planner"):
        failed = model_turn(settings, canned(None, status=400), question)
        assert failed["error"]["code"] == "model_unavailable"
        rejected = model_turn(
            settings,
            canned(
                {
                    "intent": "mood",
                    "platform": None,
                    "selection": selection(labels=["negative"]),
                    "topic": None,
                    "limit": None,
                }
            ),
            question,
        )
        assert rejected["error"]["code"] == "invalid_plan"
    messages = [record.getMessage() for record in caplog.records]
    assert any("answered 400" in message for message in messages)
    assert any(
        "Model plan rejected at" in message and "sentiment label" in message
        for message in messages
    )
    joined = "\n".join(messages)
    assert "test-secret-key" not in joined
    assert "texto-privado" not in joined and "provider detail" not in joined
