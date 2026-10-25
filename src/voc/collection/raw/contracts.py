"""Committed raw revision metadata returned by the store."""

from typing import TypedDict


class RawRevision(TypedDict):
    seq: int
    id: str
    parent: str | None
    operation: str
    committed_at: str
    report: str
