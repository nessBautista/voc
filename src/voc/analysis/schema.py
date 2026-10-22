"""Query envelope and vocabulary shared by the CLI and the query module.

This module imports no database library, so the CLI can describe its options
and report a machine-readable error even where the publications extra is absent.
"""

from .contracts import SENTIMENT_LABELS, SENTIMENT_STATUSES

SCHEMA_VERSION = "voc-analysis-query-v1"
KINDS = (
    "overview",
    "topics",
    "reviews",
    "monthly",
    "platforms",
    "metadata",
    "diagnostics",
    "highlight",
)
ASSESSMENTS = ("single_feature", "mixed", "unclear", "not_run", "failed")
EVIDENCE = ("representative", "cited")
REVIEW_SORTS = ("source", "sentiment", "model_confidence")
FILTERS = (
    "platform",
    "sentiment",
    "status",
    "topic_id",
    "start",
    "end",
    "search",
    "assessment",
    "evidence",
)
PAGED = ("reviews",)

__all__ = [
    "ASSESSMENTS",
    "EVIDENCE",
    "FILTERS",
    "KINDS",
    "PAGED",
    "REVIEW_SORTS",
    "SCHEMA_VERSION",
    "SENTIMENT_LABELS",
    "SENTIMENT_STATUSES",
    "envelope",
]


def envelope(kind, data, *, analysis_id=None, state="ok", error=None):
    """One portable envelope for both Python/CLI successes and CLI errors."""
    return {
        "schema_version": SCHEMA_VERSION,
        "state": state,
        "analysis_id": analysis_id,
        "kind": kind,
        "data": data,
        "error": error,
    }
