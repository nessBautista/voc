"""A bounded LangGraph: interpret, resolve, query, then compose; research loops through assess."""

import asyncio
import json
import logging
from time import monotonic
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .answers import compose, compose_research, operations, summarize, validate_answer
from .contracts import ANALYSIS_INTENTS, AgentError, Plan
from .memory import remember_steps, resolve

logger = logging.getLogger(__name__)

# Limits are constants, not settings: no request can raise them.
MAX_STEPS = 6
DAILY_BUDGET, RESEARCH_BUDGET = 3, 6
DAILY_RECURSION_LIMIT, RESEARCH_RECURSION_LIMIT = 8, 24
RESEARCH_TIMEOUT_SECONDS = 90
RECURSION_LIMIT = DAILY_RECURSION_LIMIT
# The opening is optional: it gets what is left of the turn, at most this, and never
# the last seconds, so a slow provider cannot cost the answer itself.
LEAD_TIMEOUT_SECONDS = 15
LEAD_MARGIN_SECONDS = 2
LEAD_MINIMUM_SECONDS = 3


class State(TypedDict, total=False):
    question: str
    history: list
    memory: list
    publication_id: str
    mode: str
    plan: Plan
    steps: list
    results: list
    trace: list
    usage: dict
    next: bool
    answer: dict
    deadline: float
    # Results a research chain already fetched, by query: a query runs once.
    fetched: dict


def merge_usage(first, second):
    """Sum two usage records of the same planner mode; unknown counts stay unknown."""

    def add(key):
        a, b = first.get(key), second.get(key)
        return None if a is None and b is None else (a or 0) + (b or 0)

    return {
        "mode": first["mode"],
        "model": first["model"] or second.get("model"),
        "requests": first["requests"] + second["requests"],
        "prompt_tokens": add("prompt_tokens"),
        "completion_tokens": add("completion_tokens"),
        "duration_ms": first["duration_ms"] + second["duration_ms"],
    }


def query_key(kind, selection):
    """One key per query: the kind and its selection, in a stable order."""
    return json.dumps([kind, selection], sort_keys=True)


def budget_for(mode):
    return RESEARCH_BUDGET if mode == "research" else DAILY_BUDGET


def build_graph(planner, client):
    async def interpret(state):
        plan, usage = await planner.plan(
            state["question"], state["history"], state.get("mode", "daily")
        )
        # Revalidate injected planners too; no node accepts arbitrary executable work.
        plan = Plan.model_validate(plan.model_dump())
        return {"plan": plan, "usage": usage, "steps": [], "trace": [], "next": False}

    def resolve_topic(state):
        # A reference becomes a number here, or the plan becomes clarify; no query runs on a guess.
        memory = state.get("memory", []) + remember_steps(state.get("steps", []))
        return {"plan": resolve(state["plan"], memory)}

    async def query(state):
        trace, results = state["trace"], []
        # Within a research chain a query runs once: a later step that needs the same
        # data (main_complaints, then specific_problems) reads what an earlier step
        # fetched, so the evidence never repeats.
        fetched = dict(state.get("fetched", {}))
        for kind, selection in operations(state["plan"]):
            key = query_key(kind, selection)
            if key not in fetched:
                fetched[key] = await client.query(
                    state["publication_id"],
                    kind,
                    trace,
                    selection,
                    budget_for(state.get("mode", "daily")),
                )
            results.append(fetched[key])
        return {
            "results": results,
            "trace": trace,
            "steps": [*state["steps"], (state["plan"], results)],
            "fetched": fetched,
        }

    async def assess(state):
        plan, _ = state["steps"][-1]
        if plan.intent in ("unsupported", "clarify"):
            return {"next": False}
        summaries = [
            {
                "plan": step_plan.model_dump(),
                "summary": {r["kind"]: summarize(r["kind"], r) for r in step_results},
            }
            for step_plan, step_results in state["steps"]
        ]
        assessment, usage = await planner.assess(
            state["question"], summaries, state["history"]
        )
        merged = merge_usage(state["usage"], usage)
        if assessment.next is None:
            return {"next": False, "usage": merged}
        following = Plan.model_validate(assessment.next.model_dump())
        # Resolve a reference before costing the step: a reference has no kinds yet.
        following = resolve(
            following, state.get("memory", []) + remember_steps(state["steps"])
        )
        # The prompt asks for no repeats; a repeated step would run the same queries
        # and carry the same evidence twice, which no reader can be shown. Stop there.
        if any(following == step_plan for step_plan, _ in state["steps"]):
            logger.warning(
                "Research chain stopped: the next step repeats an earlier one"
            )
            return {"next": False, "usage": merged}
        # Only queries not yet fetched cost budget.
        fetched = state.get("fetched", {})
        new = [op for op in operations(following) if query_key(*op) not in fetched]
        affordable = len(state["trace"]) + len(new) <= RESEARCH_BUDGET
        if len(state["steps"]) >= MAX_STEPS or not affordable:
            return {"next": False, "usage": merged}
        return {"next": True, "plan": following, "usage": merged}

    def answer(state):
        if state.get("mode") == "research":
            composed = compose_research(
                state["steps"], state["publication_id"], state["trace"], state["usage"]
            )
        else:
            composed = compose(
                state["plan"],
                state["results"],
                state["publication_id"],
                state["trace"],
                state["usage"],
            )
        return {"answer": composed}

    async def lead(state):
        """Ask for the opening after validation: the verified composition stays as it is."""
        answer = state["answer"]
        # A planner injected for tests may not write openings.
        write = getattr(planner, "lead", None)
        intents = [plan.intent for plan, _ in state["steps"]]
        if write is None:
            return {}
        # Only analysis answers have a finding to lead with; data answers are one sentence.
        if not any(intent in ANALYSIS_INTENTS for intent in intents):
            return {}
        remaining = state.get("deadline", monotonic() + 60) - monotonic()
        budget = min(LEAD_TIMEOUT_SECONDS, remaining - LEAD_MARGIN_SECONDS)
        if budget < LEAD_MINIMUM_SECONDS:
            return {}
        summaries = [
            {
                "plan": step_plan.model_dump(),
                "summary": {r["kind"]: summarize(r["kind"], r) for r in step_results},
            }
            for step_plan, step_results in state["steps"]
        ]
        try:
            written, usage = await asyncio.wait_for(
                write(state["question"], summaries, state["history"]), budget
            )
        except (AgentError, TimeoutError) as error:
            # The answer stands without an opening; the page shows it in full.
            logger.warning("Answer opening skipped: %s", type(error).__name__)
            return {}
        if usage["requests"] == 0:
            return {}
        answer = {**answer, "usage": merge_usage(answer["usage"], usage)}
        if written is not None:
            answer["lead"] = written.model_dump()
        return {"answer": answer}

    def validate(state):
        plan = (
            state["steps"][0][0] if state.get("mode") == "research" else state["plan"]
        )
        return {
            "answer": validate_answer(state["answer"], plan, state["publication_id"])
        }

    graph = StateGraph(State)
    for name, node in (
        ("interpret", interpret),
        ("resolve", resolve_topic),
        ("query", query),
        ("assess", assess),
        ("compose", answer),
        ("validate", validate),
        ("lead", lead),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "interpret")
    graph.add_edge("interpret", "resolve")
    graph.add_edge("resolve", "query")
    graph.add_conditional_edges(
        "query",
        lambda state: "assess" if state.get("mode") == "research" else "compose",
        {"assess": "assess", "compose": "compose"},
    )
    graph.add_conditional_edges(
        "assess",
        lambda state: "resolve" if state.get("next") else "compose",
        {"resolve": "resolve", "compose": "compose"},
    )
    graph.add_edge("compose", "validate")
    graph.add_edge("validate", "lead")
    graph.add_edge("lead", END)
    return graph.compile()
