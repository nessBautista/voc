"""Validate plans and evidence before a question can become a displayed answer."""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KINDS = ("overview", "sources", "coverage", "freshness", "embeddings")
PLATFORMS = {
    "google_play": "Google Play",
    "app_store": "App Store",
    "x": "X",
    "youtube": "YouTube",
    "unknown": "plataforma desconocida",
}
VIEWS = {
    "dataset_summary": ["overview"],
    "sources": ["sources"],
    "coverage": ["coverage"],
    "preparation": ["freshness", "embeddings"],
}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Plan(Contract):
    intent: Literal[
        "overview",
        "sources",
        "coverage",
        "freshness",
        "embeddings",
        "unsupported",
        "clarify",
    ]
    platform: Literal["google_play", "app_store", "x", "youtube", "unknown"] | None

    @model_validator(mode="after")
    def check_scope(self):
        # The initial API supports a source row, not arbitrary filtered analytics.
        if self.platform is not None and self.intent not in ("sources", "freshness"):
            raise ValueError("This intent cannot select a platform")
        return self


class Ask(Contract):
    request_id: str
    question: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = None

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


class AgentError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_result(result, publication_id, kind):
    """Check identity and the library's canonical digest across the HTTP boundary."""
    if not isinstance(result, dict) or set(result) != {
        "schema_version",
        "publication_id",
        "kind",
        "result_id",
        "data",
        "warnings",
    }:
        raise ValueError("Invalid result envelope")
    if (
        result["publication_id"] != publication_id
        or result["kind"] != kind
        or kind not in KINDS
        or result["schema_version"] != "voc-data-query-v1"
        or not isinstance(result["warnings"], list)
    ):
        raise ValueError("Result identity does not match the question")
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
