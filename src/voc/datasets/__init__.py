"""Read shared releases or personally published artifacts through one query API."""
from typing import Protocol
import pandas as pd
from voc.models import Snapshot
from .catalog import publish, resolve_local
from .zenml_reader import ZenMLReader

class ArtifactReader(Protocol):
    def read(self, artifact_id: str) -> pd.DataFrame: ...



def selected_source(source=None):
    if source is None:
        from voc.storage import load_storage

        source = load_storage(destination="local").dataset_source
    # Compatibility for notebooks written before the personal-source naming.
    if source == "local":
        source = "personal"
    if source not in ("personal", "s3"):
        raise ValueError("source must be personal or s3")
    return source



def resolve(version="latest", *, source=None, path=None):
    # An explicit catalog path is an existing local API, preserved for callers.
    source = selected_source(source or ("personal" if path is not None else None))
    if source == "s3":
        if path is not None:
            raise ValueError("path selects a personal catalog; use source='personal'")
        from .shared_reader import SharedDatasetReader

        return SharedDatasetReader().resolve(version)
    return resolve_local(version, path=path)



def get_dataset(version="latest", *, source=None, reader=None, path=None):
    source = selected_source(
        source or ("personal" if reader is not None or path is not None else None)
    )
    if source == "s3":
        if reader is not None or path is not None:
            raise ValueError("reader/path belong to source='personal'")
        from .shared_reader import SharedDatasetReader

        return SharedDatasetReader().read(version)
    info = resolve_local(version, path=path)
    frame = (reader or ZenMLReader()).read(info["artifact_id"])
    return Snapshot(frame, info)



def summary(snapshot):
    """Source/month counts shared by the CLI and dashboard; no review bodies."""
    frame = snapshot.data
    dates = pd.to_datetime(
        frame["record_date"], errors="coerce", utc=True, format="mixed"
    )
    return {
        "info": snapshot.info,
        "platform_counts": frame.platform.value_counts().to_dict(),
        "month_counts": dates.dt.strftime("%Y-%m")
        .value_counts()
        .sort_index()
        .to_dict(),
        "unknown_dates": int(dates.isna().sum()),
    }
