"""Completed-producer adapter for shared embedding publication."""

from voc.embeddings.publisher import publish_release
from voc.storage import load_storage

from .export import export_run


def publish_run(producer_run_id, *, promote=False):
    """Revalidate local outputs and tracking, then publish without encoding."""
    reference = export_run(producer_run_id)
    return publish_release(reference, load_storage(), promote=promote)
