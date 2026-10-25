"""Stable entry point for shared dataset reads and local collection."""

from .collection.raw import get_raw_dataset, initialize_raw_store
from .collection.shared.config import load_config
from .collection.update import UpdateResult, update

__all__ = [
    "UpdateResult",
    "get_dataset",
    "get_raw_dataset",
    "initialize_raw_store",
    "load_config",
    "update",
]


def get_dataset(stage="prepared", version="latest", *, source=None, store=None):
    from .datasets import get_dataset as read
    from .datasets import selected_source
    source = selected_source(source or ("personal" if store is not None else None))
    if stage not in ("raw", "prepared"):
        raise ValueError("stage must be raw or prepared")
    if source == "s3":
        if store is not None:
            raise ValueError("store selects a raw database; use source='personal'")
        from .datasets.reading.reader import SharedDatasetReader
        return SharedDatasetReader().read(version, stage=stage)
    if stage == "raw":
        return get_raw_dataset(store, version)
    return read(version, source="personal")

