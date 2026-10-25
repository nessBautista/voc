"""The existing workable-v1 schema and preparation policy contract."""

from dataclasses import dataclass

import pandas as pd

COLUMNS = [
    "record_id",
    "platform",
    "app_id",
    "country",
    "text",
    "title",
    "model_text",
    "text_hash",
    "rating",
    "record_date",
    "date_basis",
    "date_precision",
    "date_timezone",
    "language",
    "language_basis",
    "relevance",
    "preferred_provider",
    "identity_basis",
]
DEFAULT_RULES = {
    "columns": COLUMNS,
    "blank_text": "exclude_and_report",
    "combine_title_body": False,
    "platforms": ["app_store", "google_play", "x", "youtube"],
    "language_filter": None,
    "relevance_filter": None,
}


@dataclass
class Prepared:
    data: pd.DataFrame
    report: dict


def resolve_rules(rules):
    """Merge and check policy before inspecting any input rows."""
    rules = DEFAULT_RULES | (rules or {})
    if set(rules) - set(DEFAULT_RULES):
        raise ValueError("Unknown preparation rule")
    if rules["columns"] != COLUMNS:
        raise ValueError("workable-v1 requires all 18 columns in schema order")
    if rules["blank_text"] != "exclude_and_report":
        raise ValueError("Unsupported blank-text rule")
    if type(rules["combine_title_body"]) is not bool:
        raise ValueError("Expected boolean title rule")
    if not set(rules["platforms"]) <= set(DEFAULT_RULES["platforms"]):
        raise ValueError("Unknown platform")
    return rules
