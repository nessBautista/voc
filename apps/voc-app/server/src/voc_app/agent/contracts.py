"""Validate plans and evidence before a question can become a displayed answer."""

import hashlib
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from voc.publications.selection import ANALYSIS_KINDS, canonical_selection

DATASET_KINDS = ("overview", "sources", "coverage", "freshness", "embeddings")
# The agent names only kinds the query API serves; the library owns the list.
KINDS = DATASET_KINDS + ANALYSIS_KINDS
ANALYSIS_INTENTS = (
    "mood",
    "main_complaints",
    "trend",
    "topic_reviews",
    "specific_problems",
    "focus",
)
LIMITED_INTENTS = ("main_complaints", "specific_problems", "focus")
PLATFORMS = {
    "google_play": "Google Play",
    "app_store": "App Store",
    "x": "X",
    "youtube": "YouTube",
    "unknown": "plataforma desconocida",
}
# One entry per web registry component, with the kinds it draws, in order.
VIEWS = {
    "dataset_summary": ["overview"],
    "sources": ["sources"],
    "coverage": ["coverage"],
    "preparation": ["freshness", "embeddings"],
    "kpi_tiles": ["summary"],
    "topic_list": ["topic_list"],
    "timeline": ["timeline"],
    "platforms": ["platforms"],
    "mood_ring": ["summary"],
    "topic_detail": ["topic_detail", "reviews"],
}
QUERY_VERSIONS = {kind: "voc-data-query-v1" for kind in DATASET_KINDS} | {
    kind: "voc-data-query-v2" for kind in ANALYSIS_KINDS
}
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_SEARCH_CHARS = 100


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Selection(Contract):
    """The dashboard's filters, minus assessments, which the templates decide."""

    # Every field is required and nullable: the model's strict schema lists all of them.
    platforms: list[Literal["google_play", "app_store", "x", "youtube"]] | None
    labels: list[Literal["negative", "neutral", "positive"]] | None
    month_from: str | None
    month_to: str | None
    search: str | None

    @field_validator("platforms", "labels")
    @classmethod
    def distinct_values(cls, value):
        if value is not None and (not value or len(set(value)) != len(value)):
            raise ValueError("Name each value once, or leave the list null")
        return value

    @field_validator("month_from", "month_to")
    @classmethod
    def month_format(cls, value):
        if value is not None and not _MONTH.match(value):
            raise ValueError("Months are written YYYY-MM")
        return value

    @field_validator("search")
    @classmethod
    def clean_search(cls, value):
        if value is None:
            return None
        value = value.strip()
        if (
            not value
            or len(value) > MAX_SEARCH_CHARS
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("search is one to one hundred printable characters")
        return value

    @model_validator(mode="after")
    def ordered_months(self):
        if self.month_from and self.month_to and self.month_from > self.month_to:
            raise ValueError("month_from must not be later than month_to")
        return self

    def filters(self):
        """The fields that are set, as the query API reads them."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class TopicReference(Contract):
    """A topic by number, or by its place in the conversation's memory."""

    topic_id: int | None
    reference: Literal["last_highlighted", "last_opened"] | None

    @model_validator(mode="after")
    def one_of_two(self):
        if (self.topic_id is None) == (self.reference is None):
            raise ValueError("Name a topic by number or by reference, not both")
        if self.topic_id is not None and self.topic_id < -1:
            raise ValueError("topic_id is -1 or more")
        return self


class Plan(Contract):
    intent: Literal[
        "overview",
        "sources",
        "coverage",
        "freshness",
        "embeddings",
        "mood",
        "main_complaints",
        "trend",
        "topic_reviews",
        "specific_problems",
        "focus",
        "unsupported",
        "clarify",
    ]
    platform: Literal["google_play", "app_store", "x", "youtube", "unknown"] | None
    selection: Selection | None
    topic: TopicReference | None
    limit: int | None

    @model_validator(mode="after")
    def check_scope(self):
        # The source row is the only filter a version 1 intent takes.
        if self.platform is not None and self.intent not in ("sources", "freshness"):
            raise ValueError("This intent cannot select a platform")
        if self.selection is not None and self.intent not in ANALYSIS_INTENTS:
            raise ValueError("This intent takes no selection")
        if (
            self.selection is not None
            and self.selection.labels is not None
            and self.intent != "topic_reviews"
        ):
            raise ValueError("Only topic_reviews filters by sentiment label")
        if self.intent == "topic_reviews" and self.topic is None:
            raise ValueError("topic_reviews names a topic")
        if self.topic is not None and self.intent not in ("topic_reviews", "clarify"):
            raise ValueError("This intent takes no topic")
        if self.limit is not None and (
            self.intent not in LIMITED_INTENTS or not 1 <= self.limit <= 10
        ):
            raise ValueError("limit is one to ten on a ranking intent")
        return self


def plan_from_stored(value):
    """Read a stored plan, including two-key plans written before the selection."""
    return Plan.model_validate(
        {"selection": None, "topic": None, "limit": None, **dict(value)}
    )


class Ask(Contract):
    request_id: str
    question: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = None
    # A request field with a default: clients that do not know it get Daily.
    mode: Literal["daily", "research"] = "daily"

    @field_validator("request_id", "conversation_id")
    @classmethod
    def canonical_id(cls, value):
        if value is not None and str(UUID(value)) != value:
            raise ValueError("Use a canonical UUID")
        return value

    @field_validator("question")
    @classmethod
    def clean_question(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Write a question")
        return value


class Assessment(Contract):
    """What the model returns between research steps: the next plan, or nothing."""

    next: Plan | None


class AgentError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_result(result, publication_id, kind, selection=None):
    """Check identity, the plan's selection and the library's canonical digest."""
    if kind not in KINDS:
        raise ValueError("Unknown query kind")
    analysis = kind in ANALYSIS_KINDS
    keys = {"schema_version", "publication_id", "kind", "result_id", "data", "warnings"}
    if analysis:
        keys.add("selection")
    if not isinstance(result, dict) or set(result) != keys:
        raise ValueError("Invalid result envelope")
    if (
        result["publication_id"] != publication_id
        or result["kind"] != kind
        or result["schema_version"] != QUERY_VERSIONS[kind]
        or not isinstance(result["warnings"], list)
    ):
        raise ValueError("Result identity does not match the question")
    if analysis:
        # The evidence must answer the selection the plan asked, not a neighbour.
        if result["selection"] != canonical_selection(selection or {}, kind):
            raise ValueError("Result selection does not match the plan")
    elif selection:
        raise ValueError("This kind takes no selection")
    for warning in result["warnings"]:
        if set(warning) != {"code", "message"} or not all(
            isinstance(v, str) for v in warning.values()
        ):
            raise ValueError("Invalid warning")
    payload = {key: value for key, value in result.items() if key != "result_id"}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    if hashlib.sha256(encoded).hexdigest() != result["result_id"]:
        raise ValueError("Result digest does not match its evidence")
    return result
