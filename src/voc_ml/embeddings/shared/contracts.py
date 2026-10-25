"""Existing dictionary references passed between local producer stages."""

from typing import TypedDict


class InputReference(TypedDict):
    directory: str
    sha256: str
    dataset: dict
    selection: dict


class FeatureReference(TypedDict):
    directory: str
    report_sha256: str
    feature_id: str


class EncodingReference(TypedDict):
    directory: str
    report_sha256: str
    encoding_run_id: str
