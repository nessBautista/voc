"""Existing publication receipt and promotion-attempt dictionary contracts."""

from typing import Literal, NotRequired, TypedDict


class PromotionAttempt(TypedDict):
    """Durable promotion identity and its original concurrency precondition."""

    attempt_id: str
    expected_etag: str | None
    created_at: str
    state: Literal["pending", "promoted", "failed", "conflict"]
    completed_at: NotRequired[str]
    error: NotRequired[str]


class EmbeddingPublication(TypedDict):
    """Receipt returned after successful upload, optionally with promotion.

    These types describe the existing dictionaries; they do not replace runtime
    validation or change persisted data. Failed operations still raise errors.
    """

    schema_version: Literal["voc-embedding-publication-v1"]
    state: Literal["published", "promoted"]
    publication_id: str
    producer_run_id: str
    embedding_release_id: str
    dataset_release_id: str
    profile_id: str
    manifest_sha256: str
    manifest_uri: str
    scope_uri: str
    latest_uri: str
    coverage: dict[str, int]
    promotion: PromotionAttempt | None
    attempts_database: str
