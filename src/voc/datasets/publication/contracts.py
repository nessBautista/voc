"""Receipt for publishing an exact raw/workable pair and promoting its pointer."""

from typing import Literal, TypedDict


class DatasetPublication(TypedDict):
    status: Literal["published"]
    format_version: int
    release_id: str
    manifest_key: str
    manifest_sha256: str
    dataset_uri: str
    raw_revision_id: str
    artifact_id: str
    raw_row_count: int
    workable_row_count: int
