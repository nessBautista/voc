"""Shared embedding types and profiles; no encoding, publication or reader I/O."""

from .contracts import DatasetIdentity, EmbeddingBundle, InputSelection
from .profiles import EmbeddingProfile, resolve_profile

__all__ = [
    "DatasetIdentity",
    "EmbeddingBundle",
    "EmbeddingProfile",
    "InputSelection",
    "resolve_profile",
]
