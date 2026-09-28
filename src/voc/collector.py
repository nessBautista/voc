"""Stable notebook entry point; collection is introduced separately."""
def get_dataset(stage="prepared", version="latest", *, source=None, store=None):
    from .datasets import selected_source, get_dataset as read
    source = selected_source(source or ("local" if store is not None else None))
    if stage not in ("raw", "prepared"):
        raise ValueError("stage must be raw or prepared")
    if source == "s3":
        if store is not None:
            raise ValueError("store selects a raw database; use source='local'")
        from .datasets.shared_reader import SharedDatasetReader
        return SharedDatasetReader().read(version, stage=stage)
    if stage == "raw":
        raise LookupError("Raw collection is introduced in lesson 7")
    return read(version, source="local")
