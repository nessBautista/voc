"""Collect bounded source observations and commit complete raw updates."""

from .contracts import UpdateResult
from .operations import update

__all__ = ["UpdateResult", "update"]
