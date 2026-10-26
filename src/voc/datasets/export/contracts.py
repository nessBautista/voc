"""Metadata for an exact, retry-stable exported dataframe."""

from typing import TypedDict


class FrameExport(TypedDict):
    row_count: int
    size_bytes: int
    sha256: str
    columns: list[dict]
