"""Provider-independent contracts for evidence-backed topic descriptions."""

from voc.embeddings.shared.contracts import canonical_json, fingerprint

RESPONSE_VERSION = "voc-topic-description-v1"
TEXT_LIMITS = {"name": 160, "topic_description": 2000}
FIELDS = {*TEXT_LIMITS, "proposed_feature", "assessment", "supporting_evidence"}


def response_schema(labels):
    if (
        not isinstance(labels, (list, tuple))
        or not labels
        or any(not isinstance(label, str) or not label.strip() for label in labels)
        or len(set(labels)) != len(labels)
    ):
        raise ValueError("Evidence labels must be nonempty and unique")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(FIELDS),
        "properties": {
            **{
                k: {"type": "string", "minLength": 1, "maxLength": limit}
                for k, limit in TEXT_LIMITS.items()
            },
            "proposed_feature": {"type": ["string", "null"], "maxLength": 300},
            "assessment": {"enum": ["single_feature", "mixed", "unclear"]},
            "supporting_evidence": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "enum": list(labels)},
            },
        },
    }


def normalize_response(result, evidence):
    """Validate a decoded response and return only local supporting review IDs.

    Structural validation does not establish that the cited text supports claims.
    """
    if not isinstance(result, dict) or set(result) != FIELDS:
        raise ValueError("Response fields differ from the description contract")
    for name, limit in TEXT_LIMITS.items():
        value = result[name]
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"Invalid {name}")
    assessment = result["assessment"]
    if assessment not in ("single_feature", "mixed", "unclear"):
        raise ValueError("Invalid assessment")
    feature = result["proposed_feature"]
    if assessment == "single_feature":
        if not isinstance(feature, str) or not feature.strip() or len(feature) > 300:
            raise ValueError("Single feature requires a feature name")
    elif feature is not None:
        raise ValueError("Mixed/unclear must not propose one feature")
    if (
        not isinstance(evidence, list)
        or not evidence
        or any(
            not isinstance(item, dict)
            or any(
                not isinstance(item.get(k), str) or not item[k].strip()
                for k in ("label", "record_id")
            )
            for item in evidence
        )
    ):
        raise ValueError("Invalid evidence mapping")
    labels = {item["label"]: item["record_id"] for item in evidence}
    if len(labels) != len(evidence) or len(set(labels.values())) != len(evidence):
        raise ValueError("Invalid evidence mapping")
    cited = result["supporting_evidence"]
    if (
        not isinstance(cited, list)
        or not cited
        or any(not isinstance(label, str) or label not in labels for label in cited)
        or len(set(cited)) != len(cited)
    ):
        raise ValueError("Citations must be unique supplied evidence labels")
    canonical_json(result)
    return {
        **{k: v for k, v in result.items() if k != "supporting_evidence"},
        "supporting_review_ids": [labels[label] for label in cited],
    }


def validate_case(case):
    """Reject changed, empty or inconsistently labelled preparation outputs."""
    if type(case["topic_id"]) is not int or case["topic_id"] < 0:
        raise ValueError("Invalid topic ID")
    evidence = case["evidence"]
    if not evidence or fingerprint(evidence) != case["evidence_sha256"]:
        raise ValueError("Invalid evidence identity")
    for i, item in enumerate(evidence, 1):
        if (
            item["label"] != f"E{i}"
            or not isinstance(item["text"], str)
            or not item["text"].strip()
        ):
            raise ValueError("Invalid evidence text or label")
    if len({e["record_id"] for e in evidence}) != len(evidence):
        raise ValueError("Repeated evidence ID")
    if set(case["keywords"]) != {"R0", "R2"}:
        raise ValueError("Expected R0/R2 words from a common G3")
    canonical_json(case)
