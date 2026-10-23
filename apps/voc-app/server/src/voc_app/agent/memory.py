"""Remember what each answer named, so a follow-up can point at it without a number."""

from .answers import named_topics, opened_topic
from .contracts import Plan, TopicReference, plan_from_stored


def remember(turns):
    """What each completed answer named and opened, oldest first; derived, never stored."""
    memory = []
    for turn in turns:
        answer = turn.get("answer")
        if turn.get("status") != "completed" or not answer:
            continue
        plan = plan_from_stored(answer["plan"])
        memory.append(
            {
                "named": named_topics(plan, answer["results"]),
                "opened": opened_topic(plan, answer["results"]),
            }
        )
    return memory


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
