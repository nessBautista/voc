"""VOC: collection and dataset queries, independent of ML project code."""

__version__ = "0.1.0"


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
    from .embeddings.reader import get_embeddings as read

    return read(
        dataset=dataset,
        profile=profile,
        version=version,
        source=source,
        allow_subset=allow_subset,
    )
