"""Selectors and the bounded inventory and release inspection report shapes."""

from typing import Literal, NotRequired, TypedDict

from voc.embeddings.shared.contracts import require_uuid
from voc.embeddings.shared.profiles import resolve_profile

ScopeName = Literal["datasets", "embeddings", "personal"]


class InspectionError(TypedDict):
    message: str
    operation: NotRequired[str]
    code: NotRequired[str]


class ObjectRecord(TypedDict):
    key: str
    relative_key: str
    uri: str
    bytes: int
    last_modified: str | None
    storage_class: str


class ListingPage(TypedDict):
    objects: list[ObjectRecord]
    listed_objects: int
    listed_bytes: int
    is_truncated: bool
    next_continuation_token: str | None


class ScopeInspection(TypedDict):
    name: ScopeName
    prefix: str | None
    uri: str | None
    status: Literal["ok", "error", "unconfigured"]
    objects: list[ObjectRecord]
    listed_objects: int | None
    listed_bytes: int | None
    is_truncated: bool | None
    next_continuation_token: str | None
    error: NotRequired[InspectionError]


class ReleaseCoverage(TypedDict):
    source_rows: int
    selected_rows: int
    unique_texts: int


class ReleaseSelection(TypedDict):
    kind: Literal["full", "first-n"]
    limit: int | None
    row_count: int
    rows_sha256: str
    selection_id: str


class ArtifactInspection(TypedDict):
    name: Literal["vectors", "reviews"]
    key: str
    uri: str
    declared_bytes: int
    observed_bytes: int | None
    present: bool | None


class ReleaseIssue(TypedDict):
    status: Literal["error", "invalid", "incomplete"]
    operation: str
    message: str


class ReleaseMetadata(TypedDict):
    embedding_release_id: str
    dataset_release_id: str
    profile_id: str
    manifest_uri: str
    manifest_sha256: str | None
    coverage: ReleaseCoverage | None
    selection: ReleaseSelection | None
    artifacts: list[ArtifactInspection]
    latest_release_id: str | None
    is_latest: bool | None
    latest_manifest_checksum_matches: bool | None
    artifact_content_verified: Literal[False]
    issues: list[ReleaseIssue]


class ReleaseInspection(ReleaseMetadata):
    status: Literal["error", "invalid", "incomplete", "metadata_consistent"]


class StorageInspection(TypedDict):
    schema_version: Literal["voc-storage-inspection-v1"]
    observed_at: str
    bucket: str
    region: str
    limit_per_scope: int
    scopes: list[ScopeInspection]
    release: ReleaseInspection | None
    state: Literal["ok", "partial", "error"]


def validate_selectors(
    scope, dataset_version, profile, release, limit, continuation_token
):
    """Validate before creating an AWS client or issuing any requests."""
    if scope not in ("all", "embeddings", "datasets", "personal"):
        raise ValueError("Unknown storage scope")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if scope != "embeddings" and any(
        value is not None for value in (dataset_version, profile, release)
    ):
        raise ValueError("Dataset/profile/release selectors require --scope embeddings")
    if (profile is not None or release is not None) and dataset_version is None:
        raise ValueError("--profile and --release require --dataset-version")
    if dataset_version is not None:
        require_uuid(dataset_version, "dataset_version")
        resolve_profile(profile or "minilm-verbatim-v1")
    if release is not None:
        require_uuid(release, "embedding_release_id")
    if continuation_token is not None and scope == "all":
        raise ValueError("--continuation-token requires a single explicit scope")
