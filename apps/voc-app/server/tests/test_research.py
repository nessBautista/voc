"""Research mode: a bounded chain of Daily plans, scripted in fixture mode and parsed in model mode."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_chat import app_for, ask, payload, wait
from voc_app.agent.answers import validate_answer
from voc_app.agent.contracts import Assessment, Plan
from voc_app.agent.graph import MAX_STEPS, RESEARCH_BUDGET
from voc_app.agent.planner import (
    RESEARCH_FIRST,
    SYSTEM_ASSESS,
    Planner,
    fixture_assess,
    fixture_plan,
)
from voc_app.conversations import Conversations
from voc_app.main import create_app

QUESTION = (
    "Encuentra el mes con mayor proporción de comentarios negativos, "
    "compara sus temas y muestra reseñas del tema destacado"
)


def research(question=QUESTION, conversation=None):
    return {**payload(question, conversation), "mode": "research"}


def step(intent, summary, **fields):
    plan = {
        "intent": intent,
        "platform": None,
        "selection": None,
        "topic": None,
        "limit": None,
        **fields,
    }
    return {"plan": plan, "summary": summary}


def test_fixture_assess_scripts_the_example():
    trend = step(
        "trend",
        {
            "timeline": {
                "comparable": [
                    {"month": "2026-07", "negative_share": 0.43},
                    {"month": "2026-08", "negative_share": 0.50},
                ],
                "busiest": {"month": "2026-08", "total": 8522},
            }
        },
    )
    following = fixture_assess(QUESTION, [trend]).next
    assert following.intent == "main_complaints"
    assert following.selection.month_from == following.selection.month_to == "2026-08"
    # With no comparable month the busiest one is chosen; with no month at all, stop.
    thin = step(
        "trend",
        {"timeline": {"comparable": [], "busiest": {"month": "2025-09", "total": 5}}},
    )
    assert fixture_assess(QUESTION, [thin]).next.selection.month_from == "2025-09"
    assert (
        fixture_assess(
            QUESTION, [step("trend", {"timeline": {"comparable": [], "busiest": None}})]
        ).next
        is None
    )
    complaints = step("main_complaints", {"topic_list": {"topics": [{"topic_id": 73}]}})
    assert (
        fixture_assess(QUESTION, [trend, complaints]).next.topic.reference
        == "last_highlighted"
    )
    assert (
        fixture_assess(
            QUESTION, [step("main_complaints", {"topic_list": {"topics": []}})]
        ).next
        is None
    )
    assert fixture_assess(QUESTION, [step("topic_reviews", {})]).next is None
    assert fixture_plan(QUESTION, []).intent == "trend"


def test_research_turn_chains_steps_on_the_fixture(settings, v2_active):
    with TestClient(app_for(settings)) as client:
        conversation = ask(client, research())
        turn = conversation["turns"][0]
        assert turn["status"] == "completed", turn
        answer = turn["answer"]
        assert answer["mode"] == "research"
        # On the fixture no month is comparable, so the busiest month is read; it names no feature topic.
        assert [s["plan"]["intent"] for s in answer["steps"]] == [
            "trend",
            "main_complaints",
        ]
        assert answer["steps"][1]["plan"]["selection"]["month_from"] == "2025-09"
        assert [r["kind"] for r in answer["results"]] == [
            "timeline",
            "summary",
            "topic_list",
        ]
        assert [v["component"] for v in answer["views"]] == ["timeline", "topic_list"]
        assert answer["plan"] == answer["steps"][0]["plan"]
        assert answer["text"].startswith(
            "Investigación en 2 pasos.\n\nPaso 1 · Evolución mensual. "
        )
        assert (
            "\n\nPaso 2 · Queja principal. En todas las plataformas, en septiembre de 2025"
            in answer["text"]
        )
        assert "la cadena es una lectura posible" in answer["text"]
        assert answer["usage"]["requests"] == 0
        validate_answer(
            answer, Plan.model_validate(answer["plan"]), answer["publication_id"]
        )
        # A Daily follow-up reads the research memory: nothing named, so the reference asks.
        follow = ask(
            client,
            payload(
                "¿Qué dicen exactamente los clientes?", conversation["conversation_id"]
            ),
        )
        assert follow["turns"][-1]["answer"]["plan"]["intent"] == "clarify"
        # The same question in Daily mode is one plan.
        daily = ask(client, payload(QUESTION))["turns"][0]["answer"]
        assert daily["mode"] == "daily" and "steps" not in daily
        assert daily["plan"]["intent"] == "trend" and len(daily["results"]) == 1


class ScriptedPlanner:
    """A fixture planner whose assessment always wants one more step of a given intent."""

    def __init__(self, settings, intent, delay=0):
        self.delegate = Planner(settings)
        self.intent = intent
        self.delay = delay
        self.assessments = 0

    async def plan(self, question, history, mode="daily"):
        return await self.delegate.plan(question, history, mode)

    async def assess(self, question, steps, history):
        self.assessments += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        following = Plan(
            intent=self.intent, platform=None, selection=None, topic=None, limit=None
        )
        return Assessment(next=following), {
            "mode": "fixture",
            "model": None,
            "requests": 0,
            "prompt_tokens": None,
            "completion_tokens": None,
            "duration_ms": 0,
        }


def test_step_cap_and_query_budget_end_the_chain(settings, v2_active):
    # trend costs one query per step: the step cap stops the chain at six.
    planner = ScriptedPlanner(settings, "trend")
    with TestClient(app_for(settings, planner)) as client:
        answer = ask(client, research("¿Cuándo aumentaron las quejas?"))["turns"][0][
            "answer"
        ]
    assert len(answer["steps"]) == MAX_STEPS
    assert len(answer["trace"]) == MAX_STEPS
    assert planner.assessments == MAX_STEPS
    assert answer["text"].startswith(f"Investigación en {MAX_STEPS} pasos.")
    # mood costs two queries per step: the budget stops the chain at three.
    planner = ScriptedPlanner(settings, "mood")
    with TestClient(app_for(settings, planner)) as client:
        answer = ask(client, research("¿Cómo se sienten en general?"))["turns"][0][
            "answer"
        ]
    assert len(answer["steps"]) == RESEARCH_BUDGET // 2
    assert len(answer["trace"]) == RESEARCH_BUDGET
    validate_answer(
        answer, Plan.model_validate(answer["plan"]), answer["publication_id"]
    )


def test_research_timeout_is_terminal(settings, v2_active, monkeypatch):
    monkeypatch.setattr(Conversations, "RESEARCH_TIMEOUT_SECONDS", 0.3)
    planner = ScriptedPlanner(settings, "trend", delay=0.25)
    with TestClient(app_for(settings, planner)) as client:
        response = client.post(
            "/api/v1/ask", json=research("¿Cuándo aumentaron las quejas?")
        )
        assert response.status_code == 202
        turn = wait(client, response.json()["conversation_id"])["turns"][0]
        assert turn["status"] == "failed"
        assert turn["error"]["code"] == "turn_timeout"
        assert client.get("/health/ready").status_code == 200
        # Daily turns keep the settings' timeout and still answer.
        assert ask(client, payload())["turns"][0]["status"] == "completed"


def test_tampered_research_steps_are_refused(settings, v2_active):
    with TestClient(app_for(settings)) as client:
        answer = ask(client, research())["turns"][0]["answer"]
    plan = Plan.model_validate(answer["plan"])
    for tamper in (
        lambda a: a["steps"][1]["result_ids"].reverse(),
        lambda a: a["steps"].pop(),
        lambda a: a["steps"][1]["plan"].update({"selection": None}),
        lambda a: a.update({"text": a["text"].replace("2 pasos", "3 pasos")}),
        lambda a: a.update({"mode": "daily"}),
    ):
        copy = json.loads(json.dumps(answer))
        tamper(copy)
        with pytest.raises(ValueError):
            validate_answer(copy, plan, answer["publication_id"])


def test_model_mode_assesses_with_summaries_only(settings, v2_active):
    requests = []
    scripted = [
        {
            "intent": "trend",
            "platform": None,
            "selection": None,
            "topic": None,
            "limit": None,
        },
        {
            "next": {
                "intent": "main_complaints",
                "platform": None,
                "selection": {
                    "platforms": None,
                    "labels": None,
                    "month_from": "2025-09",
                    "month_to": "2025-09",
                    "search": None,
                },
                "topic": None,
                "limit": None,
            }
        },
        {"next": None},
    ]

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        content = scripted[len(requests) - 1]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 50},
            },
        )

    settings = replace(settings, planner_mode="model", llm_api_key="test-secret-key")
    planner = Planner(settings, httpx.MockTransport(respond))
    with TestClient(create_app(settings, planner=planner)) as client:
        turn = ask(client, research())["turns"][0]
    assert turn["status"] == "completed", turn
    answer = turn["answer"]
    assert [s["plan"]["intent"] for s in answer["steps"]] == [
        "trend",
        "main_complaints",
    ]
    assert answer["usage"] == {
        "mode": "model",
        "model": settings.llm_model,
        "requests": 3,
        "prompt_tokens": 3000,
        "completion_tokens": 150,
        "duration_ms": answer["usage"]["duration_ms"],
    }
    assert len(requests) == 3
    # The first call asks for the first step of a chain; the next two assess.
    assert requests[0]["messages"][0]["content"].endswith(RESEARCH_FIRST)
    assert "Return only the FIRST step" in requests[0]["messages"][0]["content"]
    assert requests[1]["messages"][0]["content"] == SYSTEM_ASSESS
    assert requests[1]["response_format"]["json_schema"]["schema"]["required"] == [
        "next"
    ]
    sent = json.loads(requests[1]["messages"][1]["content"])
    assert set(sent) == {"question", "steps", "history"}
    assert set(sent["steps"][0]["summary"]) == {"timeline"}
    assert "busiest" in sent["steps"][0]["summary"]["timeline"]
    # Step summaries carry counts and names, never texts, keywords or descriptions.
    flat = json.dumps(sent, ensure_ascii=False)
    for forbidden in ("items", "keywords", "description", "text", "test-secret-key"):
        assert forbidden not in flat, forbidden
    second = json.loads(requests[2]["messages"][1]["content"])
    assert [s["plan"]["intent"] for s in second["steps"]] == [
        "trend",
        "main_complaints",
    ]
    assert set(second["steps"][1]["summary"]) == {"summary", "topic_list"}


def test_model_mode_reference_in_a_chain_resolves_or_asks(settings, v2_active):
    """The assessment may name a topic by reference; the loop resolves it before costing the step."""
    scripted = [
        {
            "intent": "trend",
            "platform": None,
            "selection": None,
            "topic": None,
            "limit": None,
        },
        {
            "next": {
                "intent": "main_complaints",
                "platform": None,
                "selection": {
                    "platforms": None,
                    "labels": None,
                    "month_from": "2025-09",
                    "month_to": "2025-09",
                    "search": None,
                },
                "topic": None,
                "limit": None,
            }
        },
        {
            "next": {
                "intent": "topic_reviews",
                "platform": None,
                "selection": None,
                "topic": {"topic_id": None, "reference": "last_highlighted"},
                "limit": None,
            }
        },
        {"next": None},
    ]
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        content = scripted[len(calls) - 1]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 40},
            },
        )

    settings = replace(settings, planner_mode="model", llm_api_key="test-secret-key")
    planner = Planner(settings, httpx.MockTransport(respond))
    with TestClient(create_app(settings, planner=planner)) as client:
        turn = ask(client, research())["turns"][0]
    assert turn["status"] == "completed", turn
    answer = turn["answer"]
    # The fixture's five-review month names no topic, so the reference becomes a
    # clarifying step and the chain stops there without a fourth model call.
    assert [s["plan"]["intent"] for s in answer["steps"]] == [
        "trend",
        "main_complaints",
        "clarify",
    ]
    assert answer["steps"][2]["plan"]["topic"] == {
        "topic_id": None,
        "reference": "last_highlighted",
    }
    assert answer["steps"][2]["result_ids"] == []
    assert "Paso 3 · Aclaración. ¿A qué tema te refieres?" in answer["text"]
    assert len(calls) == 3 and answer["usage"]["requests"] == 3
    validate_answer(
        answer, Plan.model_validate(answer["plan"]), answer["publication_id"]
    )
