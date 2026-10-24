"""Remember what each answer named, so a follow-up can point at it without a number."""

from .answers import named_topics, opened_topic
from .contracts import Plan, TopicReference, plan_from_stored


def _entry(steps):
    """What a list of (plan, results) steps named, in order, and the last topic it opened."""
    named, opened = [], None
    for plan, results in steps:
        named.extend(named_topics(plan, results))
        opened = opened_topic(plan, results) or opened
    return {"named": named, "opened": opened}


def answer_steps(answer):
    """The (plan, results) steps of a stored answer: one for Daily, several for research."""
    results = answer["results"]
    if answer.get("mode") != "research":
        return [(plan_from_stored(answer["plan"]), results)]
    steps, cursor = [], 0
    for stored in answer["steps"]:
        count = len(stored["result_ids"])
        steps.append(
            (plan_from_stored(stored["plan"]), results[cursor : cursor + count])
        )
        cursor += count
    return steps


def remember(turns):
    """What each completed answer named and opened, oldest first; derived, never stored."""
    return [
        _entry(answer_steps(turn["answer"]))
        for turn in turns
        if turn.get("status") == "completed" and turn.get("answer")
    ]


def remember_steps(steps):
    """The same memory for the steps of the turn in progress."""
    return [_entry(steps)] if steps else []


def resolve(plan, memory):
    """Turn a topic reference into a number, or ask instead of guessing."""
    if plan.topic is None or plan.topic.reference is None:
        return plan
    for entry in reversed(memory):
        if plan.topic.reference == "last_highlighted":
            found = entry["named"][0] if entry["named"] else None
        else:
            found = entry["opened"]
        if found is not None:
            return plan.model_copy(
                update={
                    "topic": TopicReference(topic_id=found["topic_id"], reference=None)
                }
            )
    return Plan(
        intent="clarify", platform=None, selection=None, topic=plan.topic, limit=None
    )
