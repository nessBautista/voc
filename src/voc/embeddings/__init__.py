"""Shared embedding types and profiles; no encoding, publication or reader I/O."""

from .contracts import DatasetIdentity, EmbeddingBundle, InputSelection
from .profiles import EmbeddingProfile, resolve_profile

__all__ = [
    "DatasetIdentity",
    "EmbeddingBundle",
    "EmbeddingProfile",
    "InputSelection",
    "get_embeddings",
    "resolve_profile",
]


def get_embeddings(
    *,
    dataset,
    profile="minilm-verbatim-v1",
    version="latest",
    source="s3",
    allow_subset=False,
):
    """Load stored embeddings for an unmodified shared workable Snapshot.

    Explicit subset releases require allow_subset=True. Reading never runs models.
    """
    from .reader import get_embeddings as read

    return read(
        dataset=dataset,
        profile=profile,
        version=version,
        source=source,
        allow_subset=allow_subset,
    )
