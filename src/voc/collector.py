"""Stable entry point for shared dataset reads and local collection."""
def get_dataset(stage="prepared", version="latest", *, source=None, store=None):
    from .datasets import selected_source, get_dataset as read
    source = selected_source(source or ("personal" if store is not None else None))
    if stage not in ("raw", "prepared"):
        raise ValueError("stage must be raw or prepared")
    if source == "s3":
        if store is not None:
            raise ValueError("store selects a raw database; use source='personal'")
        from .datasets.shared_reader import SharedDatasetReader
        return SharedDatasetReader().read(version, stage=stage)
    if stage == "raw":
        return get_raw_dataset(store, version)
    return read(version, source="personal")

from .collection.service import UpdateResult, get_raw_dataset, initialize_raw_store, load_config, update
