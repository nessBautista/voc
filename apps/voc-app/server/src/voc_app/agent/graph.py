"""A bounded LangGraph: interpret, query, compose and validate once per turn."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .answers import compose, operations, validate_answer
from .contracts import Plan


class State(TypedDict, total=False):
    question: str
    history: list
    publication_id: str
    plan: Plan
    results: list
    trace: list
    usage: dict
    answer: dict


def build_graph(planner, client):
    async def interpret(state):
        plan, usage = await planner.plan(state["question"], state["history"])
        # Revalidate injected planners too; no node accepts arbitrary executable work.
        plan = Plan.model_validate(plan.model_dump())
        return {"plan": plan, "usage": usage}

    async def query(state):
        trace, results = [], []
        for kind in operations(state["plan"]):
            results.append(await client.query(state["publication_id"], kind, trace))
        return {"results": results, "trace": trace}

    def answer(state):
        return {
            "answer": compose(
                state["plan"],
                state["results"],
                state["publication_id"],
                state["trace"],
                state["usage"],
            )
        }

    def validate(state):
        return {
            "answer": validate_answer(
                state["answer"], state["plan"], state["publication_id"]
            )
        }

    graph = StateGraph(State)
    for name, node in (
        ("interpret", interpret),
        ("query", query),
        ("compose", answer),
        ("validate", validate),
    ):
        graph.add_node(name, node)
    for source, target in (
        (START, "interpret"),
        ("interpret", "query"),
        ("query", "compose"),
        ("compose", "validate"),
        ("validate", END),
    ):
        graph.add_edge(source, target)
    return graph.compile()
