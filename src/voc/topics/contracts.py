"""Portable topic-result invariants. No model or orchestration dependencies."""

import math

import numpy as np
import pandas as pd

from voc.embeddings.shared.contracts import ordered_rows_hash

SCHEMA = "voc-local-topics-v1"
SELECTION_COLUMNS = (
    "record_id",
    "model_text",
    "platform",
    "record_date",
    "source_row",
    "period",
)
ASSIGNMENT_COLUMNS = ("record_id", "topic_id", "membership_strength")


def selection_hash(frame):
    values = frame.astype(object).where(frame.notna(), None)
    return ordered_rows_hash(
        values[list(SELECTION_COLUMNS)].itertuples(index=False, name=None)
    )


def validate_selection(frame, *, schema=SCHEMA):
    from .scope import MAX_ROWS, SCHEMA_V2

    if schema not in (SCHEMA, SCHEMA_V2):
        raise ValueError("Unsupported selection schema")
    limit = MAX_ROWS if schema == SCHEMA_V2 else 1000
    if tuple(frame.columns) != SELECTION_COLUMNS or not 1 <= len(frame) <= limit:
        raise ValueError("Invalid selection columns or size")
    for name in ("record_id", "model_text", "platform", "period"):
        if not frame[name].map(lambda x: isinstance(x, str) and bool(x.strip())).all():
            raise ValueError(f"Invalid selection {name}")
    if not frame.record_id.is_unique or frame.record_id.tolist() != sorted(
        frame.record_id
    ):
        raise ValueError("Selection IDs must be unique and sorted")
    positions = frame.source_row
    if (
        not pd.api.types.is_integer_dtype(positions.dtype)
        or positions.isna().any()
        or (positions < 0).any()
        or not positions.is_unique
    ):
        raise ValueError("Invalid source row positions")
    if not frame.platform.isin(["app_store", "google_play", "x", "youtube"]).all():
        raise ValueError("Invalid selection platform")
    dates = pd.to_datetime(frame.record_date, format="mixed", errors="coerce", utc=True)
    expected = dates.dt.strftime("%Y-%m").fillna("unknown")
    if expected.tolist() != frame.period.tolist():
        raise ValueError("Period differs from recorded date")


def validate_results(selection, assignments, topics, coordinates, *, schema=SCHEMA):
    """Reconcile all memberships and references, including the unassigned group."""
    validate_selection(selection, schema=schema)
    if tuple(assignments.columns) != ASSIGNMENT_COLUMNS:
        raise ValueError("Invalid assignment columns")
    if assignments.record_id.tolist() != selection.record_id.tolist():
        raise ValueError("Assignment order differs from selection")
    ids = assignments.topic_id
    strengths = assignments.membership_strength
    if (
        not pd.api.types.is_integer_dtype(ids.dtype)
        or ids.isna().any()
        or (ids < -1).any()
    ):
        raise ValueError("Invalid topic IDs")
    if (
        not pd.api.types.is_numeric_dtype(strengths.dtype)
        or not np.isfinite(strengths).all()
        or not strengths.between(0, 1).all()
    ):
        raise ValueError("Invalid membership strengths")
    if (
        coordinates.ndim != 2
        or coordinates.shape[0] != len(selection)
        or not 2 <= coordinates.shape[1] <= 20
        or coordinates.dtype.kind != "f"
        or not np.isfinite(coordinates).all()
    ):
        raise ValueError("Invalid grouping coordinates")
    if not isinstance(topics, list):
        raise TypeError("Topics must be a list")
    sizes = ids.value_counts().to_dict()
    seen = set()
    membership = dict(zip(assignments.record_id, ids, strict=True))
    texts = dict(zip(selection.record_id, selection.model_text, strict=True))
    for topic in topics:
        if set(topic) != {"topic_id", "size", "keywords", "evidence_ids"}:
            raise ValueError("Invalid topic record")
        tid = topic["topic_id"]
        if type(tid) is not int or tid < -1 or tid in seen:
            raise ValueError("Invalid or duplicate topic record")
        seen.add(tid)
        if (
            type(topic["size"]) is not int
            or topic["size"] != sizes.get(tid, 0)
            or topic["size"] < 1
        ):
            raise ValueError("Topic size differs from assignments")
        keywords = topic["keywords"]
        if not isinstance(keywords, list) or len(keywords) > 30:
            raise ValueError("Invalid keywords")
        terms = []
        for item in keywords:
            if (
                set(item) != {"term", "weight"}
                or not isinstance(item["term"], str)
                or not item["term"].strip()
                or type(item["weight"]) not in (float, int)
                or not math.isfinite(item["weight"])
                or item["weight"] < 0
            ):
                raise ValueError("Invalid keyword weight or term")
            terms.append(item["term"])
        if len(terms) != len(set(terms)):
            raise ValueError("Repeated keyword")
        evidence = topic["evidence_ids"]
        if not isinstance(evidence, list) or not all(
            isinstance(x, str) for x in evidence
        ):
            raise ValueError("Invalid evidence IDs")
        if len(evidence) != len(set(evidence)):
            raise ValueError("Repeated evidence ID")
        if any(membership.get(rid) != tid for rid in evidence):
            raise ValueError("Evidence does not belong to its topic")
        if len({texts[rid] for rid in evidence}) != len(evidence):
            raise ValueError("Evidence must have distinct texts")
    if seen != set(ids):
        raise ValueError("Topic records differ from assigned groups")
    return {
        "rows": len(selection),
        "topic_count": sum(t >= 0 for t in seen),
        "unassigned_rows": int((ids == -1).sum()),
        "unassigned_fraction": float((ids == -1).mean()),
    }
