"""A bounded statistical signal for review, never a business-priority score."""

from math import sqrt

POLICY = {
    "eligible_assessments": ["single_feature", "mixed"],
    "minimum_support": 5,
    "denominator": "effective sentiment labels, including human corrections",
    "confidence_level": 0.95,
    "tie_break": "larger sentiment denominator, then lower analysis-local topic ID",
    "scope": "current review filters",
}


def wilson_lower(negative, total):
    """Lower endpoint of the two-sided 95% Wilson score interval."""
    if total <= 0:
        return None
    z = 1.959963984540054
    p = negative / total
    lower = (
        p
        + z * z / (2 * total)
        - z * sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    ) / (1 + z * z / total)
    return max(0.0, min(1.0, lower))


def highlighted_topic(topics):
    eligible = []
    for topic in topics:
        interpretation = topic.get("interpretation") or {}
        assessment = (interpretation.get("result") or {}).get("assessment")
        total = topic["sentiment_denominator"]
        if (
            topic["topic_id"] >= 0
            and interpretation.get("status") == "completed"
            and assessment in POLICY["eligible_assessments"]
            and total >= POLICY["minimum_support"]
        ):
            eligible.append(
                topic | {"wilson_lower_95": wilson_lower(topic["negative"], total)}
            )
    eligible.sort(
        key=lambda t: (
            -t["wilson_lower_95"],
            -t["sentiment_denominator"],
            t["topic_id"],
        )
    )
    return {
        "topic": eligible[0] if eligible else None,
        "eligible_topics": len(eligible),
        "policy": dict(POLICY),
        "reason": None
        if eligible
        else (
            "No topic in this selection has a completed single_feature or mixed "
            "assessment and at least five usable sentiment labels."
        ),
    }
