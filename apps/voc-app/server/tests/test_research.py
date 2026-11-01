"""Research mode: a bounded chain of Daily plans, scripted in fixture mode and parsed in model mode."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_chat import app_for, ask, payload, wait
from voc_app.agent.answers import validate_answer
from voc_app.agent.contracts import Assessment, Lead, Plan, Selection
from voc_app.agent.graph import MAX_STEPS, RESEARCH_BUDGET
from voc_app.agent.planner import (
    RESEARCH_FIRST,
    SYSTEM_ASSESS,
    SYSTEM_LEAD,
    Planner,
    mend_plan,
    mend_research_step,
    fixture_assess,
    fixture_plan,
    grounded,
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


# Distinct months, so each scripted step is a new plan rather than a repeat.
MONTHS = ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04"]


class ScriptedPlanner:
    """A fixture planner whose assessment always wants one more step of a given intent,
    each narrowed to the next month so no step repeats an earlier one."""

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
        month = MONTHS[(self.assessments - 1) % len(MONTHS)]
        following = Plan(
            intent=self.intent,
            platform=None,
            selection=Selection(
                platforms=None,
                labels=None,
                month_from=month,
                month_to=month,
                search=None,
            ),
            topic=None,
            limit=None,
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
        # The opening, written from the same summaries: September 2025 had the most
        # reviews (5) and no feature topic qualifies in that month.
        {
            "text": "En septiembre de 2025, el mes con más reseñas (5), ningún tema "
            "de una funcionalidad reúne reseñas suficientes para destacarlo.",
            "steps": [
                "Mes con más reseñas: septiembre de 2025, con 5",
                "Ningún tema destacado en ese mes",
            ],
        },
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
        "requests": 4,
        "prompt_tokens": 4000,
        "completion_tokens": 200,
        "duration_ms": answer["usage"]["duration_ms"],
    }
    assert len(requests) == 4
    assert answer["lead"] == scripted[3]
    # The opening is asked for last, from the same summaries the assessments saw.
    assert requests[3]["messages"][0]["content"] == SYSTEM_LEAD
    opening = json.loads(requests[3]["messages"][1]["content"])
    assert set(opening) == {"question", "steps", "history"}
    assert [s["plan"]["intent"] for s in opening["steps"]] == [
        "trend",
        "main_complaints",
    ]
    flat = json.dumps(opening, ensure_ascii=False)
    for forbidden in ("items", "keywords", "description", "text", "test-secret-key"):
        assert forbidden not in flat, forbidden
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
    # The fourth call asks for the opening; the script has no reply for it, so the
    # answer stands without one and the failed call is not counted.
    assert len(calls) == 4 and answer["usage"]["requests"] == 3
    assert "lead" not in answer
    validate_answer(
        answer, Plan.model_validate(answer["plan"]), answer["publication_id"]
    )


def test_an_opening_with_a_figure_the_steps_do_not_hold_is_refused():
    steps = [
        {
            "plan": {"intent": "main_complaints"},
            "summary": {
                "summary": {"reviews_in_view": 264, "negative_share": 0.992},
                "topic_list": {
                    "topics": [{"topic_id": 73, "negative": 262, "classified": 264}]
                },
            },
        }
    ]

    def opening(text, *lines):
        return Lead(text=text, steps=list(lines))

    assert grounded(opening("«Pagos», tema 73: 99% de 264 reseñas negativas."), steps)
    assert grounded(
        opening("262 de 264 son negativas, al menos 95% de confianza."), steps
    )
    # A share, a count or a decimal that no summary states is refused.
    assert not grounded(opening("El 87% de las reseñas son negativas."), steps)
    assert not grounded(opening("Hay 1,500 reseñas negativas."), steps)
    assert not grounded(opening("Sube 2.5 puntos."), steps)
    assert not grounded(opening("Bien.", "Cayó al 12%"), steps)


def test_fixture_mode_writes_no_opening(settings, v2_active):
    with TestClient(create_app(settings)) as client:
        answer = ask(client, research())["turns"][0]["answer"]
        daily = ask(client, payload("¿Cuál es la queja principal?"))["turns"][0]
    assert "lead" not in answer and answer["usage"]["requests"] == 0
    assert "lead" not in daily["answer"]


def test_a_next_step_filtered_to_negatives_is_mended_not_refused(
    settings, v2_active, caplog
):
    """The log case: the model narrows a ranking step to negative reviews only."""
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
                    "labels": ["negative"],
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
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        content = scripted[min(len(calls), len(scripted)) - 1]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    settings = replace(settings, planner_mode="model", llm_api_key="test-secret-key")
    planner = Planner(settings, httpx.MockTransport(respond))
    with TestClient(create_app(settings, planner=planner)) as client:
        turn = ask(client, research())["turns"][0]
    assert turn["status"] == "completed", turn
    second = turn["answer"]["steps"][1]["plan"]
    assert second["intent"] == "main_complaints"
    assert second["selection"]["labels"] is None
    assert second["selection"]["month_from"] == "2025-09"
    assert "a label filter on main_complaints was dropped" in caplog.text


def test_step_labels_and_limits_stay_where_they_belong():
    reviews = {
        "next": {
            "intent": "topic_reviews",
            "selection": {"labels": ["negative"]},
            "limit": None,
        }
    }
    assert mend_research_step(reviews) == reviews
    assert mend_research_step({"next": None}) == {"next": None}
    trend = {"next": {"intent": "trend", "selection": {"labels": ["negative"]}}}
    assert mend_research_step(trend)["next"]["selection"]["labels"] is None
    # The log case: a ranking's limit copied onto the next step.
    copied = {"next": {"intent": "topic_reviews", "selection": None, "limit": 1}}
    assert mend_research_step(copied)["next"]["limit"] is None
    ranking = {"next": {"intent": "focus", "selection": None, "limit": 3}}
    assert mend_research_step(ranking) == ranking
    assert (
        mend_research_step({"next": {"intent": "focus", "limit": 25}})["next"]["limit"]
        == 10
    )
    assert (
        mend_research_step({"next": {"intent": "focus", "limit": 0}})["next"]["limit"]
        == 1
    )


class RepeatingPlanner(ScriptedPlanner):
    """The log case: the model asks again for the step it just ran."""

    async def assess(self, question, steps, history):
        self.assessments += 1
        return Assessment(next=Plan.model_validate(steps[-1]["plan"])), {
            "mode": "fixture",
            "model": None,
            "requests": 0,
            "prompt_tokens": None,
            "completion_tokens": None,
            "duration_ms": 0,
        }


def test_a_repeated_step_ends_the_chain_without_duplicate_evidence(
    settings, v2_active, caplog
):
    planner = RepeatingPlanner(settings, "trend")
    with TestClient(app_for(settings, planner)) as client:
        turn = ask(client, research("¿Cuándo aumentaron las quejas?"))["turns"][0]
    assert turn["status"] == "completed", turn
    answer = turn["answer"]
    assert len(answer["steps"]) == 1
    ids = [r["result_id"] for r in answer["results"]]
    assert len(ids) == len(set(ids))
    assert "the next step repeats an earlier one" in caplog.text


def test_a_limit_copied_onto_the_next_step_is_mended_not_refused(
    settings, v2_active, caplog
):
    """The second log case: specific_problems with a limit, then reviews with the same limit."""
    scripted = [
        {
            "intent": "specific_problems",
            "platform": None,
            "selection": None,
            "topic": None,
            "limit": 1,
        },
        {
            "next": {
                "intent": "topic_reviews",
                "platform": None,
                "selection": None,
                "topic": {"topic_id": None, "reference": "last_highlighted"},
                "limit": 1,
            }
        },
        {"next": None},
    ]
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        content = scripted[min(len(calls), len(scripted)) - 1]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    settings = replace(settings, planner_mode="model", llm_api_key="test-secret-key")
    planner = Planner(settings, httpx.MockTransport(respond))
    with TestClient(create_app(settings, planner=planner)) as client:
        turn = ask(client, research("¿Cuál es el problema más específico?"))["turns"][0]
    assert turn["status"] == "completed", turn
    assert "a limit on topic_reviews was dropped" in caplog.text
    steps = turn["answer"]["steps"]
    assert steps[0]["plan"]["intent"] == "specific_problems"
    assert all(step["plan"]["limit"] in (None, 1) for step in steps)


def test_research_plans_are_mended_field_by_field():
    """Each slip is a field in the wrong place; the step's question stays the same."""
    reference = {"topic_id": None, "reference": "last_highlighted"}
    # The third log case: specific_problems pointing at the main complaint.
    mended = mend_plan({"intent": "specific_problems", "topic": reference, "limit": 1})
    assert mended["topic"] is None and mended["limit"] == 1
    assert (
        mend_plan({"intent": "topic_reviews", "topic": reference})["topic"] == reference
    )
    # A platform written at the top moves into an analysis step's selection.
    moved = mend_plan({"intent": "mood", "platform": "x", "selection": None})
    assert moved["platform"] is None
    assert moved["selection"]["platforms"] == ["x"]
    assert mend_plan({"intent": "sources", "platform": "x"})["platform"] == "x"
    # A selection on an intent that takes none goes.
    assert (
        mend_plan({"intent": "overview", "selection": {"labels": None}})["selection"]
        is None
    )
    # A valid plan comes back unchanged.
    valid = {
        "intent": "main_complaints",
        "platform": None,
        "selection": None,
        "topic": None,
        "limit": 3,
    }
    assert mend_plan(valid) == valid


def test_a_topic_on_the_next_ranking_step_is_mended_not_refused(
    settings, v2_active, caplog
):
    """The third log case, end to end."""
    scripted = [
        {
            "intent": "main_complaints",
            "platform": None,
            "selection": None,
            "topic": None,
            "limit": None,
        },
        {
            "next": {
                "intent": "specific_problems",
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
        content = scripted[min(len(calls), len(scripted)) - 1]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    settings = replace(settings, planner_mode="model", llm_api_key="test-secret-key")
    planner = Planner(settings, httpx.MockTransport(respond))
    with TestClient(create_app(settings, planner=planner)) as client:
        turn = ask(client, research("¿Cuál es el problema más específico?"))["turns"][0]
    assert turn["status"] == "completed", turn
    assert "a topic on specific_problems was dropped" in caplog.text
    steps = turn["answer"]["steps"]
    assert [s["plan"]["intent"] for s in steps] == [
        "main_complaints",
        "specific_problems",
    ]
    assert steps[1]["plan"]["topic"] is None


def test_daily_plans_keep_the_contract_as_it_is(settings, v2_active):
    """The repair is research only: a daily plan with a misplaced topic still fails."""
    content = {
        "intent": "specific_problems",
        "platform": None,
        "selection": None,
        "topic": {"topic_id": None, "reference": "last_highlighted"},
        "limit": None,
    }

    def respond(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    settings = replace(settings, planner_mode="model", llm_api_key="test-secret-key")
    planner = Planner(settings, httpx.MockTransport(respond))
    with TestClient(create_app(settings, planner=planner)) as client:
        turn = ask(client, payload("¿Cuál es el problema más específico?"))["turns"][0]
    assert turn["status"] == "failed"
    assert turn["error"]["code"] == "invalid_plan"


def test_steps_that_need_the_same_data_share_it(settings, v2_active):
    """The fourth case: main_complaints, then specific_problems, which runs the same
    queries. Each query runs once; the second step names the first step's results."""
    plan = {"platform": None, "selection": None, "topic": None, "limit": None}
    scripted = [
        {"intent": "main_complaints", **plan},
        {"next": {"intent": "specific_problems", **plan}},
        {"next": None},
    ]
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        content = scripted[min(len(calls), len(scripted)) - 1]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    settings = replace(settings, planner_mode="model", llm_api_key="test-secret-key")
    planner = Planner(settings, httpx.MockTransport(respond))
    with TestClient(create_app(settings, planner=planner)) as client:
        turn = ask(client, research("¿Cuál es el problema más específico?"))["turns"][0]
    assert turn["status"] == "completed", turn
    answer = turn["answer"]
    first, second = answer["steps"]
    assert second["plan"]["intent"] == "specific_problems"
    assert second["result_ids"] == first["result_ids"]
    ids = [r["result_id"] for r in answer["results"]]
    assert ids == first["result_ids"] and len(ids) == len(set(ids))
    assert len(answer["trace"]) == len(ids)
    validate_answer(
        answer, Plan.model_validate(answer["plan"]), answer["publication_id"]
    )
    # Naming a result the answer does not carry is refused.
    broken = {**answer, "steps": [first, {**second, "result_ids": ["0" * 64] * 2}]}
    with pytest.raises(ValueError):
        validate_answer(
            broken, Plan.model_validate(answer["plan"]), answer["publication_id"]
        )


def test_best_rated_topic_reviews_chain_on_the_fixture(settings, v2_active):
    """The question that failed: praise first, then the best-rated topic's positive reviews."""
    with TestClient(create_app(settings)) as client:
        turn = ask(
            client,
            research("¿Qué dicen las reseñas positivas del tema mejor valorado?"),
        )["turns"][0]
    assert turn["status"] == "completed", turn
    answer = turn["answer"]
    intents = [s["plan"]["intent"] for s in answer["steps"]]
    assert intents == ["praise", "topic_reviews"]
    reviews = answer["steps"][1]["plan"]
    assert reviews["selection"]["labels"] == ["positive"]
    assert "Paso 1 · Temas mejor valorados." in answer["text"]
    assert (
        "El tema mejor valorado entre los de una funcionalidad es el tema"
        in answer["text"]
    )
    # The reviews step opens the topic the praise step put first.
    named = [
        line for line in answer["text"].split("\n\n") if line.startswith("Paso 1")
    ][0]
    assert f"tema {reviews['topic']['topic_id']}," in named
    validate_answer(
        answer, Plan.model_validate(answer["plan"]), answer["publication_id"]
    )


def test_praise_is_a_daily_answer_too(settings, v2_active):
    with TestClient(create_app(settings)) as client:
        answer = ask(client, payload("¿Qué es lo que más valoran los clientes?"))[
            "turns"
        ][0]["answer"]
    assert answer["plan"]["intent"] == "praise"
    assert [v["component"] for v in answer["views"]] == ["topic_list"]
    assert answer["text"].startswith("Qué dicen los datos.")
    assert "proporción positiva" in answer["text"]


def test_memory_reads_research_steps_that_share_results():
    """A step that reuses an earlier step's results is remembered with them."""
    from voc_app.agent.memory import answer_steps

    plan = {"platform": None, "selection": None, "topic": None, "limit": None}
    answer = {
        "mode": "research",
        "plan": {"intent": "main_complaints", **plan},
        "steps": [
            {"plan": {"intent": "main_complaints", **plan}, "result_ids": ["a", "b"]},
            {"plan": {"intent": "specific_problems", **plan}, "result_ids": ["a", "b"]},
            {"plan": {"intent": "trend", **plan}, "result_ids": ["c"]},
        ],
        "results": [
            {"result_id": "a", "kind": "summary"},
            {"result_id": "b", "kind": "topic_list"},
            {"result_id": "c", "kind": "timeline"},
        ],
    }
    steps = answer_steps(answer)
    assert [[r["result_id"] for r in results] for _, results in steps] == [
        ["a", "b"],
        ["a", "b"],
        ["c"],
    ]
