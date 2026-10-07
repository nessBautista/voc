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


def get_demo(prototype, version="latest", *, release_id):
    """Fetch a verified demo matching the dataset already loaded by the notebook."""
    from .datasets.demos import DemoSnapshots
    return DemoSnapshots(prototype).fetch(version, release_id=release_id)


def publish_demo(prototype, folder, *, release_id, keys=None):
    """Explicitly publish a complete demo checkpoint, never a dataset release."""
    from .datasets.demos import DemoSnapshots
    return DemoSnapshots(prototype).publish(folder, release_id=release_id, keys=keys)
