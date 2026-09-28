"""Dataset query API. Tool-specific reads are imported only when selected."""
import pandas as pd
from voc.storage import load_storage

def selected_source(source=None):
    source = source or load_storage(destination="local").dataset_source
    if source not in ("local", "s3"):
        raise ValueError("source must be local or s3")
    return source

def resolve(version="latest", *, source=None):
    if selected_source(source) != "s3":
        raise LookupError("Personal publication is introduced in lesson 10")
    from .shared_reader import SharedDatasetReader
    return SharedDatasetReader().resolve(version)

def get_dataset(version="latest", *, source=None):
    if selected_source(source) != "s3":
        raise LookupError("Personal publication is introduced in lesson 10")
    from .shared_reader import SharedDatasetReader
    return SharedDatasetReader().read(version)

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
