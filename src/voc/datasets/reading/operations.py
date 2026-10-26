"""Resolve metadata or materialize a snapshot using its selected source."""

from voc.models import Snapshot

from ..catalog import resolve_local
from .contracts import selected_source
from .zenml_reader import ZenMLReader


def resolve(version="latest", *, source=None, path=None):
    # An explicit catalog path is an existing local API, preserved for callers.
    source = selected_source(source or ("personal" if path is not None else None))
    if source == "s3":
        if path is not None:
            raise ValueError("path selects a personal catalog; use source='personal'")
        from .reader import SharedDatasetReader

        return SharedDatasetReader().resolve(version)
    return resolve_local(version, path=path)


def get_dataset(version="latest", *, source=None, reader=None, path=None):
    source = selected_source(
        source or ("personal" if reader is not None or path is not None else None)
    )
    if source == "s3":
        if reader is not None or path is not None:
            raise ValueError("reader/path belong to source='personal'")
        from .reader import SharedDatasetReader

        return SharedDatasetReader().read(version)
    info = resolve_local(version, path=path)
    frame = (reader or ZenMLReader()).read(info["artifact_id"])
    return Snapshot(frame, info)
