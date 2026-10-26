"""Existing persisted producer requests and incrementally recorded run state."""

from typing import Literal, NotRequired, TypedDict

from ..shared.contracts import EncodingReference, FeatureReference, InputReference


class ProducerRequest(TypedDict):
    dataset_version: str
    source: str
    profile: str
    limit: int | None
    batch_size: int
    local_files_only: bool


class ProducerPaths(TypedDict):
    run: str
    cache: str
    models: str


class ProducerRun(TypedDict):
    schema_version: Literal["voc-embedding-producer-v1"]
    producer_run_id: str
    state: Literal["running", "completed", "failed"]
    created_at: str
    updated_at: str
    zenml_run_id: str | None
    mlflow_run_id: str | None
    requested: ProducerRequest
    paths: ProducerPaths
    execution: str
    started_at: NotRequired[str]
    completed_at: NotRequired[str]
    failed_at: NotRequired[str]
    experiment_id: NotRequired[str]
    experiment_name: NotRequired[str]
    project_identity: NotRequired[str]
    input_reference: NotRequired[InputReference]
    feature_reference: NotRequired[FeatureReference]
    encoding_reference: NotRequired[EncodingReference]
    dataset: NotRequired[dict]
    selection: NotRequired[dict]
    feature: NotRequired[dict]
    profile: NotRequired[dict]
    encoder_id: NotRequired[str]
    counts: NotRequired[dict[str, int]]
    step_errors: NotRequired[dict[str, dict[str, str | None]]]
    error: NotRequired[dict[str, str]]
