"""A bounded LangGraph: interpret, resolve, query, then compose; research loops through assess."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .answers import compose, compose_research, operations, summarize, validate_answer
from .contracts import Plan
from .memory import remember_steps, resolve

# Limits are constants, not settings: no request can raise them.
MAX_STEPS = 6
DAILY_BUDGET, RESEARCH_BUDGET = 3, 6
DAILY_RECURSION_LIMIT, RESEARCH_RECURSION_LIMIT = 8, 24
RESEARCH_TIMEOUT_SECONDS = 90
RECURSION_LIMIT = DAILY_RECURSION_LIMIT


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
        for kind, selection in operations(state["plan"]):
            results.append(
                await client.query(
                    state["publication_id"],
                    kind,
                    trace,
                    selection,
                    budget_for(state.get("mode", "daily")),
                )
            )
        return {
            "results": results,
            "trace": trace,
            "steps": [*state["steps"], (state["plan"], results)],
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
        affordable = len(state["trace"]) + len(operations(following)) <= RESEARCH_BUDGET
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
    graph.add_edge("validate", END)
    return graph.compile()
