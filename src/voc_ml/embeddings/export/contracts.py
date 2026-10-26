"""The durable create-only export intent and completed local release receipt."""

from typing import Literal, TypedDict


class ExportIntent(TypedDict):
    schema_version: Literal["voc-embedding-export-intent-v1"]
    producer_run_id: str
    source_sha256: str
    embedding_release_id: str


class ExportReference(TypedDict):
    schema_version: Literal["voc-embedding-export-v1"]
    state: Literal["exported"]
    producer_run_id: str
    embedding_release_id: str
    directory: str
    manifest_sha256: str
    dataset_release_id: str
    profile_id: str
    coverage: dict[str, int]
