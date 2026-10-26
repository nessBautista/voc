"""Stored-embedding reading entry point over the S3/cache adapter."""

from ..shared.contracts import EmbeddingBundle


def get_embeddings(
    *,
    dataset,
    profile="minilm-verbatim-v1",
    version="latest",
    source="s3",
    allow_subset=False,
) -> EmbeddingBundle:
    """Return verified canonical-order reviews, vectors and release metadata.

    Explicit subset releases require allow_subset=True. No inference or producer
    runtime is needed. Pass a full, unmodified shared workable Snapshot as dataset.
    """
    if source != "s3":
        raise ValueError("Embedding reads currently require source='s3'")

    from .reader import SharedEmbeddingReader

    return SharedEmbeddingReader().read(
        dataset=dataset, profile=profile, version=version, allow_subset=allow_subset
    )
