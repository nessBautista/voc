"""Aggregate result contract; summary generation does not read or modify storage."""

from typing import TypedDict


class DatasetSummary(TypedDict):
    """Existing summary fields, including the original snapshot metadata."""

    info: dict
    platform_counts: dict[str, int]
    month_counts: dict[str, int]
    unknown_dates: int
