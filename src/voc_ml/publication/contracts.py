"""Metadata produced by the existing dataset tracking and validation steps."""

from typing import TypedDict


class TrackingReport(TypedDict):
    mlflow_run_id: str
    artifact_id: str


class ValidatedDatasetMetadata(TypedDict):
    raw_store_id: str
    raw_revision_id: str
    schema_version: str
    row_count: int
    content_sha256: str
    rules: dict
    counts_by_platform: dict[str, int]
    project_identity: str
    run_id: str
    artifact_id: str
    mlflow_run_id: str
